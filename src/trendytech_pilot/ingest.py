"""Workbook validation and reproducible selection of entire exported lead journeys."""

import re
from collections import Counter, defaultdict
from datetime import datetime
from urllib.parse import urlsplit

import openpyxl

from .storage import digest, write_csv, write_json

REQUIRED = ["Call Duration", "Status", "Call Recording URL", "Call Done By", "CreatedOn", "Lead Number", "Is Converted"]
# 50 complete exported lead groups; exactly 300 calls. The ratio is deliberately enriched.
DEFAULT_QUOTAS = {2: 5, 3: 5, 4: 5, 5: 7, 6: 8, 7: 6, 8: 5, 9: 4, 10: 3, 12: 2}


def duration_seconds(text):
    match = re.fullmatch(r"(\d+)h:(\d+)m:(\d+)s", str(text))
    if not match:
        raise ValueError(f"Invalid call duration: {text!r}")
    h, m, s = map(int, match.groups())
    if m >= 60 or s >= 60:
        raise ValueError("Minutes and seconds must each be below 60")
    return h * 3600 + m * 60 + s


def outcome_flag(calls):
    flags = {c["crm_conversion_flag"] for c in calls}
    if flags == {"Yes"}:
        return "CRM reported converted; unverified"
    if "Yes" in flags:
        return "Mixed CRM flags; unresolved"
    return "Outcome unknown"


def import_workbook(path, store):
    source_hash = digest(path.read_bytes())
    existing = store.path("source.json")
    if existing.exists():
        from .storage import read_json
        if read_json(existing)["sha256"] != source_hash:
            raise ValueError("Source changed. Use a fresh data directory to avoid mixing pilot versions.")
    wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
    sheet = wb.active
    iterator = sheet.iter_rows()
    headers = [c.value for c in next(iterator)]
    missing = set(REQUIRED) - set(headers)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")
    calls, rejects = [], []
    seen = set()
    for row_index, cells in enumerate(iterator, 2):
        if all(c.value is None for c in cells):
            continue
        row = dict(zip(headers, [c.value for c in cells]))
        try:
            if any(c.data_type == "f" for c in cells):
                raise ValueError("Formula in source record; requires review")
            lead = row["Lead Number"]
            if lead is None or isinstance(lead, bool):
                raise ValueError("Missing or invalid Lead Number")
            if isinstance(lead, float) and not lead.is_integer():
                raise ValueError("Fractional Lead Number")
            lead = str(int(lead)) if isinstance(lead, (int, float)) else str(lead).strip()
            url = str(row["Call Recording URL"] or "").strip()
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.hostname != "recordings.mcube.com":
                raise ValueError("Recording URL outside the approved HTTPS source host")
            if parsed.username or parsed.password or parsed.port not in (None, 443):
                raise ValueError("Unexpected recording URL credentials or port")
            if url in seen:
                raise ValueError("Duplicate recording URL")
            timestamp = row["CreatedOn"]
            if not isinstance(timestamp, datetime):
                raise TypeError("CreatedOn is not an Excel date")
            duration = duration_seconds(row["Call Duration"])
            if duration <= 0:
                raise ValueError("Non-positive duration")
            flag = row["Is Converted"]
            if flag not in (None, "", "Yes", "No"):
                raise ValueError("Unrecognised conversion flag")
            seen.add(url)
            calls.append({
                "call_id": "C" + digest(url)[:16], "lead_number": lead,
                "source_row": row_index, "recording_url": url,
                "duration_seconds": duration, "created_on": timestamp.isoformat(),
                "timestamp_timezone": "unconfirmed", "status": row["Status"],
                "direction": row.get("ActivityEvent"), "salesperson": row["Call Done By"],
                "current_owner": row.get("Current Owner"),
                "lead_name": " ".join(str(row.get(x) or "").strip() for x in ["Lead | First Name", "Lead | Last Name"]).strip(),
                "phone": row.get("Lead | Phone Number"), "email": row.get("Lead | Email"),
                "crm_conversion_flag": flag or None,
            })
        except (ValueError, TypeError) as exc:
            rejects.append({"source_row": row_index, "reason": str(exc)})
    wb.close()
    grouped = defaultdict(list)
    for call in calls:
        grouped[call["lead_number"]].append(call)
    audit = {
        "source_filename": path.name, "sha256": source_hash, "sheet": sheet.title,
        "calls": len(calls), "leads": len(grouped), "rejected_rows": rejects,
        "audio_minutes": sum(c["duration_seconds"] for c in calls) / 60,
        "date_range": [min(c["created_on"] for c in calls), max(c["created_on"] for c in calls)] if calls else [],
        "lead_outcome_groups": dict(Counter(outcome_flag(g) for g in grouped.values())),
        "status_counts": dict(Counter(c["status"] for c in calls)),
        "calls_per_lead": dict(Counter(len(g) for g in grouped.values())),
    }
    write_json(store.path("source.json"), audit)
    write_json(store.path("calls.json"), calls)
    return audit


def select_sample(calls, seed="trendytech-pilot-v1", quotas=None):
    quotas = DEFAULT_QUOTAS if quotas is None else quotas
    groups = defaultdict(list)
    for call in calls:
        groups[call["lead_number"]].append(call)
    selected = []
    agent_counts = Counter()
    outcome_counts = Counter()
    for n_calls, target in sorted(quotas.items(), reverse=True):
        candidates = [lead for lead, g in groups.items() if len(g) == n_calls and lead not in selected]
        if len(candidates) < target:
            raise ValueError(f"Need {target} leads with {n_calls} calls; found {len(candidates)}. Adjust quotas explicitly.")
        for _ in range(target):
            def priority(lead):
                group = groups[lead]
                agents = {c["salesperson"] for c in group}
                return (outcome_counts[outcome_flag(group)], sum(agent_counts[a] for a in agents) / len(agents), digest([seed, lead]))
            chosen = min(candidates, key=priority)
            candidates.remove(chosen)
            selected.append(chosen)
            outcome_counts[outcome_flag(groups[chosen])] += 1
            agent_counts.update({c["salesperson"] for c in groups[chosen]})
    # Holdout assignment is independent of transcript content and stratified by journey length.
    ordered = sorted(selected, key=lambda lead: (len(groups[lead]), digest([seed, "qa", lead])))
    holdout = set(ordered[4::5])
    records = []
    for index, lead in enumerate(sorted(selected, key=lambda x: digest([seed, x])), 1):
        group = sorted(groups[lead], key=lambda c: (c["created_on"], c["call_id"]))
        for call_number, call in enumerate(group, 1):
            records.append({**call, "lead_alias": f"L{index:03d}", "call_number_in_export": call_number,
                            "split": "holdout" if lead in holdout else "development",
                            "journey_outcome_label": outcome_flag(group), "available_calls_for_lead": len(group)})
    return records


def save_selection(calls, store, seed):
    selection_path = store.path("selection.json")
    selected = select_sample(calls, seed)
    if selection_path.exists():
        from .storage import read_json
        if read_json(selection_path)["calls"] != selected:
            raise ValueError("Selection is already frozen. Use a new data directory for a different sample.")
    manifest = {"seed": seed, "sampling": "Purposive multi-call sample; not representative of archive conversion",
                "sha256": digest(selected), "calls": selected}
    write_json(selection_path, manifest)
    write_csv(store.path("exports", "selected_calls.csv"), selected)
    return {"calls": len(selected), "leads": len({c["lead_number"] for c in selected}),
            "audio_minutes": sum(c["duration_seconds"] for c in selected) / 60,
            "holdout_calls": sum(c["split"] == "holdout" for c in selected),
            "holdout_leads": len({c["lead_number"] for c in selected if c["split"] == "holdout"}),
            "salespeople": len({c["salesperson"] for c in selected})}
