"""Independent full-call references; missing reviews never become passing scores."""

import csv

from .artifacts import current_extraction, current_source
from .storage import digest, read_json, write_csv, write_json


def merge_review_template(path, rows, fields, keys):
    existing = []
    if path.exists():
        with path.open(encoding="utf-8-sig") as handle:
            existing = list(csv.DictReader(handle))
    by_key = {tuple(str(row.get(k, "")) for k in keys): row for row in existing}
    review_fields = {"reference_text", "reviewed_silence", "reviewer", "reviewed_at",
                     "reference_value", "extraction_correct", "missed_information"}
    merged = []
    for row in rows:
        prior = by_key.get(tuple(str(row.get(k, "")) for k in keys), {})
        merged.append({**row, **{k: v for k, v in prior.items() if k in review_fields}})
    new_keys = {tuple(str(row.get(k, "")) for k in keys) for row in rows}
    stale = [row for key, row in by_key.items() if key not in new_keys]
    if stale:
        write_csv(path.with_name(path.stem + "-superseded-" + digest(stale)[:10] + ".csv"), stale)
    write_csv(path, merged, fields)


def word_error_rate(reference, hypothesis):
    if not reference or not reference.strip():
        return None
    from jiwer import wer
    return wer(reference, hypothesis)


def export_qa(store):
    calls = [c for c in read_json(store.path("selection.json"))["calls"] if c["split"] == "holdout"]
    text_rows, field_rows = [], []
    for call in calls:
        cid = call["call_id"]
        transcript = current_source(store, cid)
        if transcript:
            # Full recording coverage catches speech missing from ASR, including empty ASR output.
            # Model text is absent from the independent reference collection sheet.
            text_rows.append({"lead_alias": call["lead_alias"], "call_id": cid,
                              "artifact_fingerprint": transcript["fingerprint"], "start_seconds": 0,
                              "end_seconds": transcript["duration_seconds"], "reference_text": "",
                              "reviewed_silence": "", "reviewer": "", "reviewed_at": ""})
        artifact = current_extraction(store, cid)
        if artifact:
            for field in ["conversation_type", "goal", "current_role", "experience", "course", "timeline",
                          "budget", "signals", "objections", "pitches", "next_action", "summary"]:
                field_rows.append({"lead_alias": call["lead_alias"], "call_id": cid, "field": field,
                                   "artifact_fingerprint": artifact["fingerprint"], "reference_value": "",
                                   "extraction_correct": "", "missed_information": "", "reviewer": "", "reviewed_at": ""})
    merge_review_template(store.path("qa", "holdout_transcription_review.csv"), text_rows,
                          ["lead_alias", "call_id", "artifact_fingerprint", "start_seconds", "end_seconds",
                           "reference_text", "reviewed_silence", "reviewer", "reviewed_at"],
                          ["call_id", "artifact_fingerprint"])
    merge_review_template(store.path("qa", "holdout_field_review.csv"), field_rows,
                          ["lead_alias", "call_id", "artifact_fingerprint", "field", "reference_value",
                           "extraction_correct", "missed_information", "reviewer", "reviewed_at"],
                          ["call_id", "artifact_fingerprint", "field"])
    summary = evaluate_reviews(store)
    summary.update({"holdout_calls": len(calls), "available_call_references": len(text_rows),
                    "note": "Review the entire recording independently, including speech ASR omitted. Mark reviewed_silence=yes only for genuinely silent full calls."})
    write_json(store.path("qa", "status.json"), summary)
    return summary


def evaluate_reviews(store):
    """Recheck artifact freshness when scoring, not only when creating review templates."""
    calls = {c["call_id"] for c in read_json(store.path("selection.json"))["calls"] if c["split"] == "holdout"}

    def read_rows(name):
        path = store.path("qa", name)
        if not path.exists():
            return []
        with path.open(encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))

    refs, hypotheses, reviewed_fields = [], [], []
    seen_text, seen_fields = set(), set()
    ignored = silence = 0
    text_rows = read_rows("holdout_transcription_review.csv")
    field_rows = read_rows("holdout_field_review.csv")
    for row in text_rows:
        cid = row["call_id"]
        artifact = current_source(store, cid) if cid in calls else None
        if not artifact or row.get("artifact_fingerprint") != artifact["fingerprint"] or cid in seen_text:
            ignored += 1
            continue
        seen_text.add(cid)
        ref = row.get("reference_text", "").strip()
        silent = row.get("reviewed_silence", "").lower() == "yes"
        if not row.get("reviewer") or not row.get("reviewed_at") or (not ref and not silent) or (ref and silent):
            continue
        refs.append(ref)
        hypotheses.append(" ".join(s["text"] for s in artifact["segments"]))
        silence += silent
    for row in field_rows:
        cid = row["call_id"]
        artifact = current_extraction(store, cid) if cid in calls else None
        key = (cid, row.get("field"))
        if not artifact or row.get("artifact_fingerprint") != artifact["fingerprint"] or key in seen_fields:
            ignored += 1
            continue
        seen_fields.add(key)
        verdict = (row.get("extraction_correct") or "").strip().lower()
        if row.get("reviewer") and row.get("reviewed_at") and verdict in ("yes", "no"):
            reviewed_fields.append({**row, "extraction_correct": verdict})
    metrics = {"wer": None, "reference_words": 0, "substitutions": 0, "deletions": 0, "insertions": 0}
    if refs:
        from jiwer import (
            Compose,
            ReduceToListOfListOfWords,
            RemoveMultipleSpaces,
            Strip,
            ToLowerCase,
            process_words,
        )
        transform = Compose([ToLowerCase(), RemoveMultipleSpaces(), Strip(), ReduceToListOfListOfWords()])
        result = process_words(refs, hypotheses, reference_transform=transform, hypothesis_transform=transform)
        words = result.hits + result.substitutions + result.deletions
        metrics.update(wer=result.wer if words else None, reference_words=words,
                       substitutions=result.substitutions, deletions=result.deletions, insertions=result.insertions)
    return {"reviewed_calls": len(refs), "expected_holdout_calls": len(calls), "reviewed_silent_calls": silence,
            "reviewed_fields": len(reviewed_fields), "available_field_rows": len(seen_fields),
            "ignored_stale_or_duplicate_rows": ignored, **metrics,
            "field_accuracy": sum(r["extraction_correct"] == "yes" for r in reviewed_fields) / len(reviewed_fields) if reviewed_fields else None,
            "quality_gate": "Pending independent review and agreed acceptance criteria"}


def wilson_interval(successes, n, z=1.96):
    """95% Wilson score interval; better behaved than the normal approximation near 0% or 100%."""
    if n == 0:
        return None
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def validation_status(store):
    """Signed reviewer verdicts on client claims and on the random holdout claim sample."""
    def rows(name):
        path = store.path("qa", name)
        if not path.exists():
            return []
        with path.open(encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))

    signed = lambda r: r.get("reviewer") and r.get("reviewed_at")
    client = rows("validate-client-claims.csv")
    sample = rows("audit-claims-sample.csv")
    audit = [r for r in sample if signed(r) and r.get("correct", "").strip().lower() in ("yes", "no")]
    correct = sum(r["correct"].strip().lower() == "yes" for r in audit)
    client_signed = [r for r in client if signed(r) and r.get("verdict")]
    return {"client_claims": len(client), "client_claims_reviewed": len(client_signed),
            "client_claims_confirmed": sum(r["verdict"].strip().lower() == "confirmed" for r in client_signed),
            "audit_reviewed": len(audit), "audit_correct": correct,
            "audit_precision": correct / len(audit) if audit else None,
            "audit_precision_95ci": wilson_interval(correct, len(audit)),
            "audit_sample": len(sample),
            # The sample holds up to 100 claims (fewer only if the held-out calls have fewer); all must be reviewed.
            "complete": bool(client) and len(client_signed) == len(client) and len(audit) == len(sample) > 0}
