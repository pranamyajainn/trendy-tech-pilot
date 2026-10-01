import csv
import subprocess
import sys
from pathlib import Path

from trendytech_pilot.extract import extraction_fingerprint
from trendytech_pilot.quality import merge_review_template
from trendytech_pilot.reporting import priority_for
from trendytech_pilot.storage import write_csv


def qa_store(tmp_path, hypothesis):
    from trendytech_pilot.audio import ASR_VERSION
    from trendytech_pilot.models import LOCAL_REVISIONS
    from trendytech_pilot.storage import Store, digest, write_json
    store = Store(tmp_path)
    model = "mlx-community/whisper-large-v3-turbo"
    revision = LOCAL_REVISIONS[model]
    fingerprint = digest(["synthetic-audio", model, revision, ASR_VERSION])
    write_json(store.path("selection.json"), {"calls": [{"call_id": "Ctest", "lead_alias": "Ltest", "split": "holdout"}]})
    write_json(store.path("audio", "Ctest.json"), {"sha256": "synthetic-audio"})
    write_json(store.path("transcripts", "Ctest.json"), {"model": model, "model_revision": revision,
               "version": ASR_VERSION, "fingerprint": fingerprint, "duration_seconds": 60,
               "segments": [{"id": 0, "text": hypothesis}] if hypothesis else []})
    return store, fingerprint


def test_qa_measures_omitted_speech_in_empty_asr(tmp_path):
    from trendytech_pilot.quality import evaluate_reviews, export_qa
    store, _fingerprint = qa_store(tmp_path, "")
    export_qa(store)
    path = store.path("qa", "holdout_transcription_review.csv")
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert len(rows) == 1 and float(rows[0]["end_seconds"]) == 60
    rows[0].update(reference_text="all speech was missed", reviewer="Independent reviewer", reviewed_at="2026-10-02")
    write_csv(path, rows)
    metrics = evaluate_reviews(store)
    assert metrics["wer"] == 1 and metrics["deletions"] == 4


def test_qa_counts_hallucinated_words_over_reviewed_silence(tmp_path):
    from trendytech_pilot.quality import evaluate_reviews
    store, fingerprint = qa_store(tmp_path, "thank you")
    write_csv(store.path("qa", "holdout_transcription_review.csv"), [{"call_id": "Ctest", "artifact_fingerprint": fingerprint,
              "reference_text": "", "reviewed_silence": "yes", "reviewer": "Reviewer", "reviewed_at": "2026-10-02"}])
    metrics = evaluate_reviews(store)
    assert metrics["reviewed_silent_calls"] == 1 and metrics["insertions"] == 2
    assert metrics["wer"] is None  # Zero reference words, not a falsely perfect score.


def test_qa_rejects_stale_review_even_without_regenerating_sheet(tmp_path):
    from trendytech_pilot.quality import evaluate_reviews
    store, _fingerprint = qa_store(tmp_path, "real words")
    write_csv(store.path("qa", "holdout_transcription_review.csv"), [{"call_id": "Ctest", "artifact_fingerprint": "old",
              "reference_text": "real words", "reviewer": "Reviewer", "reviewed_at": "2026-10-02"}])
    metrics = evaluate_reviews(store)
    assert metrics["reviewed_calls"] == 0 and metrics["ignored_stale_or_duplicate_rows"] == 1


def test_model_revision_changes_extraction_identity():
    assert extraction_fingerprint("transcript", "model", "v1") != extraction_fingerprint("transcript", "model", "v2")


def test_review_of_old_output_is_archived_not_applied_to_new_output(tmp_path):
    path = tmp_path / "review.csv"
    old = {"call_id": "synthetic", "artifact_fingerprint": "old", "field": "goal", "reviewer": "Reviewer",
           "reviewed_at": "2026-10-02", "extraction_correct": "yes"}
    write_csv(path, [old])
    new = {**old, "artifact_fingerprint": "new", "reviewer": "", "reviewed_at": "", "extraction_correct": ""}
    merge_review_template(path, [new], list(old), ["call_id", "artifact_fingerprint", "field"])
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
    assert rows == [new]
    assert list(tmp_path.glob("review-superseded-*.csv"))


def test_review_input_survives_same_artifact_export(tmp_path):
    path = tmp_path / "review.csv"
    old = {"call_id": "synthetic", "artifact_fingerprint": "same", "field": "goal", "reviewer": "Reviewer"}
    write_csv(path, [old])
    merge_review_template(path, [{**old, "reviewer": ""}], list(old), ["call_id", "artifact_fingerprint", "field"])
    assert next(csv.DictReader(path.open(encoding="utf-8-sig")))["reviewer"] == "Reviewer"


def test_learner_support_does_not_enter_hot_sales_queue():
    calls = [{"call_id": "synthetic", "crm_conversion_flag": None}]
    analyses = {"synthetic": {"extraction": {"conversation_type": "learner_support", "signals": [{"kind": "payment_intent"}]}}}
    assert priority_for(calls, analyses)[0] == "Service follow-up"


def test_public_scan_reads_staged_blob_not_cleaned_working_file(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    file = tmp_path / "accidental.txt"
    file.write_text("https://" + "recordings.mcube.com/synthetic-test-recording")
    subprocess.run(["git", "add", "accidental.txt"], cwd=tmp_path, check=True)
    file.write_text("The working copy is now clean, but the staged blob is not.")
    scanner = Path(__file__).resolve().parents[1] / "scripts" / "check_public_tree.py"
    result = subprocess.run([sys.executable, str(scanner)], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode != 0 and "accidental.txt" in result.stderr
