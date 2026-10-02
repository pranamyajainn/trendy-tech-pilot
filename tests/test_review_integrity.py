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


def test_export_keeps_distinct_concerns_and_labels_response_claim(tmp_path):
    from trendytech_pilot.audio import ASR_VERSION
    from trendytech_pilot.models import LOCAL_REVISIONS
    from trendytech_pilot.reporting import export_tables
    from trendytech_pilot.schema import CallExtraction
    from trendytech_pilot.storage import Store, digest, read_json, write_json

    store = Store(tmp_path)
    calls = []
    asr = "mlx-community/whisper-large-v3-turbo"
    llm = "mlx-community/Qwen3.5-27B-4bit"
    for i, concern in enumerate(["I cannot afford this", "Is there installment interest"]):
        cid = f"Csynthetic{i}"
        calls.append({"call_id": cid, "lead_number": "synthetic", "lead_alias": "Ltest",
                      "created_on": f"2026-01-0{i+1}T10:00:00", "duration_seconds": 60,
                      "salesperson": "Test agent", "split": "development", "crm_conversion_flag": None,
                      "call_number_in_export": i+1, "lead_name": "Test lead", "current_owner": "Test owner",
                      "journey_outcome_label": "Outcome unknown"})
        write_json(store.path("audio", cid + ".json"), {"sha256": cid, "duration_seconds": 60})
        fingerprint = digest([cid, asr, LOCAL_REVISIONS[asr], ASR_VERSION])
        transcript = {"model": asr, "model_revision": LOCAL_REVISIONS[asr], "version": ASR_VERSION,
                      "fingerprint": fingerprint, "duration_seconds": 60,
                      "segments": [{"id": n, "text": text, "start": n*10, "end": (n+1)*10}
                                   for n, text in enumerate([concern, "Payment plans available", "That resolves it"])]}
        write_json(store.path("transcripts", cid + ".json"), transcript)
        ex = CallExtraction(summary="Synthetic affordability discussion", next_action="Clarify remaining concern", objections=[{
             "category": "price", "concern": concern, "evidence": {"segment_id": 0, "quote": concern},
             "response": "Payment plans available", "response_evidence": {"segment_id": 1, "quote": "Payment plans available"},
             "resolution": "resolved" if i else "unresolved",
             "resolution_evidence": {"segment_id": 2, "quote": "That resolves it"} if i else None}])
        write_json(store.path("extractions", cid + ".json"), {"model": llm, "model_revision": LOCAL_REVISIONS[llm],
                   "fingerprint": extraction_fingerprint(fingerprint, llm, LOCAL_REVISIONS[llm]),
                   "transcript_fingerprint": fingerprint, "extraction": ex.model_dump(), "asr_flags": []})
    write_json(store.path("selection.json"), {"sha256": "synthetic", "calls": calls})
    result = export_tables(store)
    worklist = read_json(store.path("exports", "worklist.json"))
    evidence = read_json(store.path("exports", "evidence.json"))
    assert result["complete_extracted_journeys"] == 1
    assert "I cannot afford this" in worklist[0]["open_objections"]
    assert "Is there installment interest" not in worklist[0]["open_objections"]
    assert all(e["claim"] == "Payment plans available" for e in evidence if e["evidence_type"] == "response_evidence")
