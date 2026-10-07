"""One-page internal gate note for the owner: the pilot's two gate questions (Phase 1 cost and extraction accuracy),
every figure read from stored records. Internal only: it names costs and methods, so it never goes in client files.

Cost basis: the ledger records every paid request at published token rates (invoices not reconciled).
- Method v2 (the pilot's three-system method): data/review/method-cost.json, measured on the 62 holdout calls.
- Method v3 (labelled transcription used for the 3,002 comparison calls): transcription cost per audio minute is
  exact from the ledger; the all-in figure adds every other paid request made on the v3 run days and is therefore an
  upper bound (it also includes one-off lead-level analysis).
"""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

from docx import Document
from docx.shared import Inches, Pt

PHASE1_BUDGET_INR, PHASE1_RATE_INR = 35000, 1.19   # proposal SAI-Q-2026-013: remaining archive, per audio minute
V3_RUN_DAYS = ("2026-10-05", "2026-10-06")          # the days the comparison cohorts were transcribed and extracted


def v3_cost(root):
    minutes, transcription, other = 0.0, 0.0, defaultdict(float)
    for line in (root / "ledger.jsonl").open():
        e = json.loads(line)
        if e.get("stage") == "transcribe" and e.get("system") == "gemini-labelled-v1" and e.get("status") == "success":
            minutes += e.get("audio_seconds", 0) / 60
            transcription += e.get("external_cost_inr") or 0
        elif e["recorded_at"][:10] in V3_RUN_DAYS and e.get("stage") not in ("transcribe", "experiment", "qa"):
            # Experiments and pilot QA runs on those days are not production processing.
            other[e["stage"]] += e.get("external_cost_inr") or 0
    return {"minutes": minutes, "transcription_per_min": transcription / minutes,
            "all_in_upper_per_min": (transcription + sum(other.values())) / minutes}


def archive_minutes_remaining(root):
    """Audio minutes in the client export (data/calls.json) not yet processed: the Phase 1 scope."""
    calls = json.loads((root / "calls.json").read_text())
    done = {p.stem for p in (root / "extractions").glob("*.json")}
    remaining = sum(float(c.get("duration_seconds") or 0) for c in calls if c["call_id"] not in done) / 60
    return remaining, len(calls), sum(c["call_id"] in done for c in calls)


def build(root):
    v2 = json.loads((root / "review" / "method-cost.json").read_text())
    v3 = v3_cost(root)
    remaining, total_calls, processed = archive_minutes_remaining(root)
    check = json.loads((root / "qa" / "profile-check.json").read_text())["summary"]
    audit = list(csv.DictReader((root / "qa" / "audit-claims-sample.csv").open(encoding="utf-8-sig")))
    signed = [r for r in audit if r.get("reviewer") and r.get("correct", "").strip().lower() in ("yes", "no")]
    worklist = json.loads((root / "exports" / "internal" / "worklist.json").read_text())

    doc = Document()
    s = doc.sections[0]
    s.top_margin = s.bottom_margin = Inches(.6)
    s.left_margin = s.right_margin = Inches(.8)
    doc.styles["Normal"].font.name = "Arial"
    doc.styles["Normal"].font.size = Pt(10)
    doc.add_heading("Pilot gate note (internal)", level=1)
    doc.add_paragraph("For the owner to present. Not part of the client workbook or report.").runs[0].italic = True

    doc.add_heading("1. Can Phase 1 be done within its API cost?", level=2)
    projection = remaining * v3["all_in_upper_per_min"]
    for text in (
        f"Proposal: the remaining archive within INR {PHASE1_BUDGET_INR:,}, about INR {PHASE1_RATE_INR} per audio minute.",
        f"Archive: {total_calls:,} calls; {processed:,} already processed; about {remaining:,.0f} audio minutes remain.",
        (f"Pilot method (three transcription systems plus verification): INR {v2['inr_per_audio_minute']:.2f} per minute "
         f"(measured on {v2['basis'].split('over the ')[1].split(' (')[0]}). Above the budget rate."),
        (f"Method used for the {v3['minutes']:,.0f} comparison minutes: INR {v3['transcription_per_min']:.2f} per minute "
         f"for transcription; at most INR {v3['all_in_upper_per_min']:.2f} per minute all-in."),
        (f"Projection for the remaining minutes at the all-in upper bound: about INR {projection:,.0f}, against "
         f"INR {PHASE1_BUDGET_INR:,}."),
        ("Basis: our usage records at published rates; provider invoices are not yet reconciled. Present this as a "
         "measured estimate, not as a passed gate."),
    ):
        doc.add_paragraph(text, style="List Bullet")

    doc.add_heading("2. Is extraction accurate?", level=2)
    sample_status = (f"{sum(r['correct'].strip().lower() == 'yes' for r in signed)} of {len(signed)} correct"
                     if len(signed) == len(audit) and audit else f"pending ({len(signed)} of {len(audit)} signed)")
    for text in (
        ("Automatic checks on every call: three-way transcript agreement on pilot calls, every quote matched to its "
         "transcript segment, and a second-pass check of every extracted claim. These measure consistency, not accuracy."),
        (f"Internal spot-check of {check['values_checked']} profile values from {check['leads']} leads against the "
         f"transcripts: {check['verdicts']['correct']} correct ({check['correct']:.0%}, range "
         f"{check['correct_95ci'][0]:.0%}-{check['correct_95ci'][1]:.0%}); not an independent human review."),
        f"Held-out human sample (claims from the 10 locked holdout leads, played against the recordings): {sample_status}.",
        (f"Worklist hand-check (every Hot, Warm and Check-status claim): {worklist['hand_check']['signed']} of "
         f"{worklist['hand_check']['claims']} signed."),
        "Until both human checks are signed, the client files carry the REVIEW DRAFT label.",
    ):
        doc.add_paragraph(text, style="List Bullet")

    doc.add_heading("3. Worklist method", level=2)
    doc.add_paragraph(f"Rule {worklist['rule_hash'][:12]}, frozen {worklist['frozen_at'][:16].replace('T', ' ')} UTC, "
                      f"data as of {worklist['as_of']}. Holdout leads were coded only after the freeze.")
    names = worklist.get("possible_names_to_scan") or []
    if names:
        doc.add_paragraph("Before sharing, scan the client files for these capitalised words that may be names: "
                          + ", ".join(names))
    return doc


if __name__ == "__main__":
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "data").resolve()
    out = root / "deliverables" / "internal" / "Pilot Gate Note.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    build(root).save(out)
    print("Gate note exported to the private deliverables directory.")
