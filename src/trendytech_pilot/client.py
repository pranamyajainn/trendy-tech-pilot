"""Client-facing text guards and the held-out accuracy sample.

The client sheets themselves are built by worklist.py (open-lead worklist, lead journeys, calls); the lead-action
drafting and the analysis sheets that lived here before 7 Oct 2026 were retired with the method review (git history
keeps them). What remains is used by the worklist export, the pattern analysis and `pilot qa`.
"""

import re

from .artifacts import current_extraction, current_source
from .storage import digest, read_json

# Client files carry no processing details: system names, call IDs, links, split labels or snake_case field names.
INTERNAL_TEXT = re.compile(r"gemini|whisper|sarvam|saaras|fingerprint|h[eo]ld[- ]?out|development (?:split|calls?)"
                           r"|\bC[0-9a-f]{16}\b|https?://|\b[a-z]+_[a-z_]+\b", re.IGNORECASE)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def human_date(created_on):
    return f"{int(created_on[8:10])} {MONTHS[int(created_on[5:7]) - 1]} {created_on[:4]}"


def validation_pack(store, sample_size=100, seed="trendytech-audit-v1"):
    """The held-out accuracy sample: a seeded random sample of verified claims from the locked holdout calls, for a
    person to check against the recordings (docs/qa-protocol.md). Signatures already entered are kept."""
    from .quality import merge_review_template

    calls = [c for c in read_json(store.path("selection.json"))["calls"] if c["split"] == "holdout"]
    claims = []
    for call in calls:
        artifact = current_extraction(store, call["call_id"])
        transcript = current_source(store, call["call_id"])
        if not artifact or not transcript:
            continue
        segments = {s["id"]: s for s in transcript["segments"]}
        for collection in ("facts", "signals", "objections", "pitches"):
            for item in artifact["extraction"][collection]:
                segment = segments[item["evidence"]["segment_id"]]
                label = item.get("field") or item.get("kind") or item.get("category") or "pitch"
                text = item.get("value") or item.get("description") or item.get("concern") or item.get("topic")
                claims.append({"call_id": call["call_id"], "artifact_fingerprint": artifact["fingerprint"],
                               "claim": f"{collection[:-1]} ({label.replace('_', ' ')}): {text}",
                               "date": call["created_on"][:10],
                               "timestamp": f"{int(segment['start']) // 60}:{int(segment['start']) % 60:02d}",
                               "quote": segment["text"], "recording_url": call["recording_url"],
                               "correct": "", "note": "", "reviewer": "", "reviewed_at": ""})
    sample = sorted(claims, key=lambda c: digest([seed, c["call_id"], c["claim"]]))[:sample_size]
    if sample:
        merge_review_template(store.path("qa", "audit-claims-sample.csv"), sample, list(sample[0]),
                              ["call_id", "artifact_fingerprint", "claim"])
    return {"audit_sample": len(sample), "holdout_claims": len(claims)}
