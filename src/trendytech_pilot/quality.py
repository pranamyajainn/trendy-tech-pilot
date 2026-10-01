"""Independent QA worksheets; blank references never become a passing score."""

import csv

from .storage import read_json, write_csv, write_json


def merge_review_template(path, rows, fields, keys):
    existing = []
    if path.exists():
        with path.open(encoding="utf-8-sig") as handle:
            existing = list(csv.DictReader(handle))
    by_key = {tuple(str(row.get(k, "")) for k in keys): row for row in existing}
    merged = []
    for row in rows:
        key = tuple(str(row.get(k, "")) for k in keys)
        prior = by_key.get(key)
        merged.append({**row, **prior} if prior else row)
    # Keep prior reviews, including rows no longer present, for audit rather than silently deleting them.
    new_keys = {tuple(str(row.get(k, "")) for k in keys) for row in rows}
    merged.extend(row for key, row in by_key.items() if key not in new_keys)
    write_csv(path, merged, fields)


def word_error_rate(reference, hypothesis):
    if not reference or not reference.strip():
        return None
    from jiwer import wer
    return wer(reference, hypothesis)


def export_qa(store):
    calls = read_json(store.path("selection.json"))["calls"]
    items = []
    for call in calls:
        if call["split"] != "holdout":
            continue
        path = store.path("transcripts", call["call_id"] + ".json")
        if not path.exists():
            continue
        transcript = read_json(path)
        for segment in transcript["segments"]:
            items.append({"lead_alias": call["lead_alias"], "call_id": call["call_id"],
                          "segment_id": segment["id"], "start_seconds": segment["start"],
                          "end_seconds": segment["end"], "asr_text": segment["text"],
                          "reference_text": "", "speaker_reference": "", "reviewer": "", "reviewed_at": ""})
    target = store.path("qa", "holdout_transcription_review.csv")
    merge_review_template(target, items, ["lead_alias", "call_id", "segment_id", "start_seconds", "end_seconds", "asr_text",
                                         "reference_text", "speaker_reference", "reviewer", "reviewed_at"], ["call_id", "segment_id"])
    field_target = store.path("qa", "holdout_field_review.csv")
    if not field_target.exists():
        fields = []
        for call in calls:
            if call["split"] == "holdout":
                for field in ["goal", "current_role", "experience", "course", "timeline", "objections", "next_action"]:
                    fields.append({"lead_alias": call["lead_alias"], "call_id": call["call_id"], "field": field,
                                   "reference_value": "", "extraction_correct": "", "missed_information": "",
                                   "reviewer": "", "reviewed_at": ""})
        write_csv(field_target, fields)
    summary = evaluate_reviews(store)
    summary.update({"holdout_calls": sum(c["split"] == "holdout" for c in calls), "transcribed_segments": len(items),
               "note": "Schema validity and exact-quote matching are technical checks, not accuracy estimates."}
    )
    write_json(store.path("qa", "status.json"), summary)
    return summary


def evaluate_reviews(store):
    """Score only explicitly signed-off reference rows; report missing review coverage."""
    transcript_file = store.path("qa", "holdout_transcription_review.csv")
    field_file = store.path("qa", "holdout_field_review.csv")
    text_rows = list(csv.DictReader(transcript_file.open(encoding="utf-8-sig"))) if transcript_file.exists() else []
    field_rows = list(csv.DictReader(field_file.open(encoding="utf-8-sig"))) if field_file.exists() else []
    reviewed_text = [r for r in text_rows if r.get("reviewer") and r.get("reviewed_at") and r.get("reference_text", "").strip()]
    reviewed_fields = [r for r in field_rows if r.get("reviewer") and r.get("reviewed_at") and r.get("extraction_correct") in ("yes", "no")]
    error_rate = None
    if reviewed_text:
        from jiwer import wer
        error_rate = wer([r["reference_text"] for r in reviewed_text], [r["asr_text"] for r in reviewed_text])
    return {"reviewed_segments": len(reviewed_text), "total_review_segments": len(text_rows),
            "reviewed_fields": len(reviewed_fields), "total_review_fields": len(field_rows),
            "wer": error_rate,
            "field_accuracy": sum(r["extraction_correct"] == "yes" for r in reviewed_fields) / len(reviewed_fields) if reviewed_fields else None,
            "quality_gate": "Pending independent review and agreed acceptance criteria"}
