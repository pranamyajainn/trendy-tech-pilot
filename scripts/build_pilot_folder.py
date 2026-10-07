"""Browsable private copy of the pilot data: one folder per lead with recordings (links), readable transcripts and
analyses, grouped into potential customers and customers. Writes data/phase0-pilot/ (git-ignored). Recordings are
symlinks because audio metadata stores absolute paths; nothing is moved. Safe to re-run: it rebuilds the folder."""

import shutil
import sys

from trendytech_pilot.artifacts import current_extraction, current_source
from trendytech_pilot.profile import lead_calls
from trendytech_pilot.storage import Store, read_json, write_json

ROLE = {"agent": "COUNSELLOR", "prospect": "LEAD"}
README = """# Phase 0 pilot: everything in one place (private; never share or commit)

potential-customers/pilot-leads      open pilot leads (no converted flag)
potential-customers/non-buyer-sample random non-buyer sample used for the comparison and the worklist draw
customers/pilot-leads                pilot leads flagged converted (mixed flags marked "check status")
customers/customer-cohort            the customer cohort
research-and-qa/                     links to research, method council, QA sheets, deliverables and source files

Each lead: lead.json (group, flags, holdout split, calls), calls/<date>_<call id>.wav|ogg (link to data/audio),
calls/<date>_<call id>.txt (readable transcript, [mm:ss], speaker labels automatic and sometimes swapped),
calls/<date>_<call id>.analysis.json (structured call record), plus profile.json, swot.json and worklist.json
(the coded lead, docs/pipeline/CODING-GUIDE.md) where they exist. Rebuild: .venv/bin/python scripts/build_pilot_folder.py
"""


def stamp(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def write_lead(store, folder, lead, calls, group, label, split):
    d = folder / lead
    (d / "calls").mkdir(parents=True, exist_ok=True)
    rows = []
    for c in calls:
        cid, stem = c["call_id"], f"{c['created_on'][:10]}_{c['call_id']}"
        x = current_extraction(store, cid)["extraction"]
        audio = next((p for p in (store.path("audio", cid + ".wav"), store.path("audio", cid + ".ogg")) if p.exists()), None)
        if audio:
            (d / "calls" / f"{stem}{audio.suffix}").symlink_to(audio.resolve())
        lines = [f"Lead {lead} | call {cid} | {c['created_on']} | {int(c['duration_seconds'])}s | {x['conversation_type']}",
                 "Speaker labels are automatic and sometimes swapped; judge the speaker from what is said.", ""]
        lines += [f"[{stamp(s['start'])}] {ROLE.get(s.get('role'), 'SPEAKER')}: {s['text']}"
                  for s in current_source(store, cid)["segments"]]
        (d / "calls" / f"{stem}.txt").write_text("\n".join(lines) + "\n")
        write_json(d / "calls" / f"{stem}.analysis.json", x)
        rows.append({"call_id": cid, "date": c["created_on"], "duration_seconds": c["duration_seconds"],
                     "conversation_type": x["conversation_type"], "crm_converted_flag": c["crm_conversion_flag"],
                     "recording": bool(audio)})
    write_json(d / "lead.json", {"lead": lead, "group": label, "source_group": group, "pilot_split": split.get(lead),
                                 "crm_flags": sorted({str(r["crm_converted_flag"]) for r in rows}), "calls": rows})
    for sub, name in (("profiles", "profile.json"), ("swot", "swot.json"), ("worklist", "worklist.json")):
        src = store.path("review", sub, f"{lead}.json")
        if src.exists():
            shutil.copyfile(src, d / name)


def main(data="data"):
    store = Store(data)
    root = store.path("phase0-pilot")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    split = {str(c["lead_number"]): c["split"] for c in read_json(store.path("selection.json"))["calls"]}
    for lead, calls in lead_calls(store, "pilot").items():
        flags = [c["crm_conversion_flag"] == "Yes" for c in calls]
        if not any(flags):
            write_lead(store, root / "potential-customers" / "pilot-leads", lead, calls, "pilot", "potential customer", split)
        else:
            label = "customer (CRM converted)" if all(flags) else "customer? mixed CRM flags - check status"
            write_lead(store, root / "customers" / "pilot-leads", lead, calls, "pilot", label, split)
    for lead, calls in lead_calls(store, "open_sample").items():
        write_lead(store, root / "potential-customers" / "non-buyer-sample", lead, calls, "open_sample",
                   "potential customer (random sample of non-buyers)", split)
    for lead, calls in lead_calls(store, "customers").items():
        write_lead(store, root / "customers" / "customer-cohort", lead, calls, "customers", "customer (CRM converted)", split)
    links = root / "research-and-qa"
    links.mkdir()
    for name, target in (("parameter-research", "research/parameters"), ("method-council", "research/method-council"),
                         ("qa-review-sheets", "qa"), ("deliverables", "deliverables"), ("source", "source")):
        (links / name).symlink_to(store.path(*target.split("/")).resolve())
    (root / "README.md").write_text(README)
    print("Pilot folder rebuilt in the private data directory.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data")
