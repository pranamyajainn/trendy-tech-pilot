"""Evidence tables and conservative, retrospective lead worklists."""

import math
import os
from collections import Counter, defaultdict
from datetime import datetime
from itertools import pairwise

from .artifacts import current_extraction, current_source
from .storage import digest, read_json, write_csv, write_json


def last_conversation(calls, analyses):
    """Latest call that reached a person. Voicemail and silent calls carry no buying signal, so a trailing
    voicemail must not hide what the last real conversation showed."""
    live = [c for c in calls if analyses[c["call_id"]]["extraction"].get("conversation_type") != "unusable"]
    return live[-1] if live else None


def priority_for(calls, analyses):
    """Heuristic workflow labels, never conversion probabilities."""
    all_signals = {s["kind"] for a in analyses.values() for s in a["extraction"]["signals"]}
    if "do_not_contact" in all_signals:
        return "Do not contact", "A recorded request to stop contact needs to be respected."
    if any(c["crm_conversion_flag"] == "Yes" for c in calls):
        return "Verify enrollment", "CRM contains a Yes flag; confirm enrollment before further sales follow-up."
    if calls[-1]["call_id"] not in analyses or len(analyses) != len(calls):
        return "Needs review", "The selected journey has calls without validated extraction."
    latest = last_conversation(calls, analyses)
    if latest is None:
        return "No live conversation", "Every recorded call reached voicemail or had no usable conversation."
    call_type = analyses[latest["call_id"]]["extraction"].get("conversation_type", "unclear")
    if call_type in {"learner_support", "administrative"}:
        return "Service follow-up", "The last live conversation concerns learner support or administration."
    kinds = {s["kind"] for s in analyses[latest["call_id"]]["extraction"]["signals"]}
    if "payment_intent" in kinds or ("urgency" in kinds and kinds & {"demo_requested", "followup_agreed"}):
        return "Hot signal", "The last live conversation contains an explicit purchase or urgent next-step signal."
    if kinds & {"low_interest", "not_a_fit"}:
        return "Cold signal", "The last live conversation contains an explicit lack of interest or fit."
    if kinds & {"followup_agreed", "demo_requested", "price_question", "goal"}:
        return "Warm signal", "The last live conversation contains a goal, product question or agreed next step."
    return "Unclassified", "The last live conversation does not support a clear buying-temperature label."


def cost_summary(store, calls):
    events = store.events()
    selected = {c["call_id"] for c in calls}
    # Unique completed transcripts in the current selection form the denominator, not attempt minutes.
    transcript_paths = [store.path("transcripts", c["call_id"] + ".json") for c in calls]
    audio_seconds = sum(read_json(p)["duration_seconds"] for p in transcript_paths if p.exists())
    relevant = [e for e in events if e.get("call_id") in selected]
    external = sum(e.get("external_cost_inr", 0) for e in relevant)
    remote_events = [e for e in events if e.get("stage") == "remote_usage"]
    external += sum(e.get("external_cost_inr", 0) for e in remote_events)
    inference = [e for e in relevant if e.get("stage") in ("transcribe", "extract", "resolve", "verify")]
    wall_hours = sum(e.get("wall_seconds", 0) for e in inference) / 3600
    rate = os.getenv("PILOT_LOCAL_COMPUTE_INR_PER_HOUR")
    local_rate = float(rate) if rate else None
    if local_rate is not None and (not math.isfinite(local_rate) or local_rate < 0):
        raise ValueError("Local compute rate must be finite and nonnegative")
    total = external + wall_hours * local_rate if local_rate is not None else None
    minutes = audio_seconds / 60
    return {"unique_transcribed_audio_minutes": round(minutes, 3),
            "inference_wall_hours_including_retries": round(wall_hours, 4),
            "external_api_spend_inr": round(external, 4),
            "api_cost_basis": "Provider token usage valued at published rates, with INR 125/USD budget factor" if remote_events else "Local inference; no paid API requests",
            "unreconciled_api_budget_reserve_inr": read_json(store.path("api-budget.json"))["committed_inr"] - external if store.path("api-budget.json").exists() else 0,
            "external_api_inr_per_audio_minute": round(external / minutes, 5) if minutes else None,
            "local_compute_inr_per_hour_assumption": local_rate,
            "processing_cost_inr": round(total, 4) if total is not None else None,
            "processing_inr_per_audio_minute": round(total / minutes, 5) if total is not None and minutes else None,
            "commercial_ceiling_inr_per_minute": .60,
            "cost_gate": "Not assessed: local compute allocation and quality approval required",
            "failed_attempts": sum(e.get("status") == "failed" for e in inference),
            "excluded": "Engineering and human QA labour, model-download setup, taxes and storage allocation; record separately"}


def quality_coverage(extraction):
    if extraction.get("conversation_type") in {"learner_support", "administrative", "unusable"}:
        return dict.fromkeys(["qualification", "discovery", "pitch", "objection_handling", "script_adherence", "closing"],
                             "Not applicable to this call type")
    facts = {f["field"] for f in extraction["facts"]}
    signals = {s["kind"] for s in extraction["signals"]}
    return {
        "qualification": "Evidence present" if facts & {"current_role", "experience", "technology_interest", "budget"} else "Not established",
        "discovery": "Evidence present" if facts & {"goal", "target_role", "timeline"} else "Not established",
        "pitch": "Evidence present" if extraction["pitches"] else "Not established",
        "objection_handling": "Response documented" if any(o["response"] for o in extraction["objections"]) else "Not assessable",
        "script_adherence": "Unavailable: approved script not supplied",
        "closing": "Next-step signal present" if signals & {"payment_intent", "followup_agreed", "demo_requested"} else "Not established",
    }


SALES_FACING = {"sales", "enrollment_or_payment"}
RESOLUTIONS = ["resolved", "partly_addressed", "unresolved", "unclear"]
PRESENT =("Evidence present", "Response documented", "Next-step signal present")


def findings_summary(calls, analyses, starts, worklist):
    """Counts with traceable examples. They describe this purposive pilot sample, not archive-wide rates."""
    alias = {c["call_id"]: c["lead_alias"] for c in calls}
    ordered = [c["call_id"] for c in sorted(calls, key=lambda c: (c["lead_alias"], c["call_number_in_export"]))
               if c["call_id"] in analyses]

    def example(cid, claim, evidence):
        return {"lead_alias": alias[cid], "call_id": cid, "claim": claim, "quote": evidence["quote"],
                "start_seconds": starts[cid].get(evidence["segment_id"])}

    types, fields, roles, interests, coverage = Counter(), Counter(), Counter(), Counter(), Counter()
    objections = defaultdict(lambda: {"instances": 0, "calls": set(), "examples": [], **dict.fromkeys(RESOLUTIONS, 0)})
    signals = defaultdict(lambda: {"calls": set(), "examples": []})
    sales_calls = calls_with_objections = 0
    for cid in ordered:
        ex = analyses[cid]["extraction"]
        types[ex["conversation_type"]] += 1
        if ex["conversation_type"] in SALES_FACING:
            sales_calls += 1
            calls_with_objections += bool(ex["objections"])
            fields.update({f["field"] for f in ex["facts"]})
            roles.update({f["value"].strip().lower() for f in ex["facts"] if f["field"] == "current_role"})
            interests.update({f["value"].strip().lower() for f in ex["facts"] if f["field"] == "technology_interest"})
            coverage.update(k for k, v in quality_coverage(ex).items() if v.startswith(PRESENT))
        for o in ex["objections"]:
            entry = objections[o["category"]]
            entry["instances"] += 1
            entry[o["resolution"]] += 1
            entry["calls"].add(cid)
            if len(entry["examples"]) < 3:
                entry["examples"].append(example(cid, o["concern"], o["evidence"]))
        for s in ex["signals"]:
            entry = signals[s["kind"]]
            if cid not in entry["calls"] and len(entry["examples"]) < 3:
                entry["examples"].append(example(cid, s["description"], s["evidence"]))
            entry["calls"].add(cid)
    complete = [w for w in worklist if w["calls_analysed"] == w["calls_in_export"]]
    return {
        "note": "Counts describe this purposive pilot sample, not archive-wide rates. Extraction accuracy is not yet "
                "independently measured; every example is traceable to its call and timestamp.",
        "analysed_calls": len(ordered), "sales_facing_calls": sales_calls, "call_types": dict(types.most_common()),
        "objections": sorted(({"category": k, "instances": v["instances"], "calls": len(v["calls"]),
                               **{r: v[r] for r in RESOLUTIONS}, "examples": v["examples"]}
                              for k, v in objections.items()), key=lambda x: (-x["calls"], x["category"])),
        "signals": sorted(({"kind": k, "calls": len(v["calls"]), "examples": v["examples"]} for k, v in signals.items()),
                          key=lambda x: (-x["calls"], x["kind"])),
        "profile_fields_stated_in_sales_calls": dict(fields.most_common()),
        "common_current_roles": dict(roles.most_common(8)), "common_technology_interests": dict(interests.most_common(8)),
        "quality_coverage": [{"dimension": d, "calls_with_evidence": coverage[d],
                              "applicable_calls": calls_with_objections if d == "objection_handling" else sales_calls}
                             for d in ["qualification", "discovery", "pitch", "objection_handling", "closing"]],
        "complete_journeys": len(complete),
        "worklist_priorities": dict(Counter(w["priority"] for w in worklist).most_common()),
        "complete_journeys_with_open_objections": sum(bool(w["open_objections"]) for w in complete),
    }


def export_tables(store):
    selection = read_json(store.path("selection.json"))
    calls = selection["calls"]
    groups = defaultdict(list)
    analyses = {}
    for c in calls:
        groups[c["lead_number"]].append(c)
        artifact = current_extraction(store, c["call_id"])
        if artifact:
            analyses[c["call_id"]] = artifact
    worklist, call_rows, evidence_rows, profile_rows = [], [], [], []
    objection_counts = Counter()
    starts = {}
    for lead, group in groups.items():
        group.sort(key=lambda c: (c["created_on"], c["call_id"]))
        lead_analyses = {c["call_id"]: analyses[c["call_id"]] for c in group if c["call_id"] in analyses}
        priority, reason = priority_for(group, lead_analyses)
        facts = {}
        history = defaultdict(set)
        objections = []
        strengths = []
        signals_all = set()
        for c in group:
            result = analyses.get(c["call_id"])
            record = {"lead_alias": c["lead_alias"], "lead_number": lead, "call_id": c["call_id"],
                      "source_excel_row": c.get("source_row"),
                      "call_number_in_export": c["call_number_in_export"], "created_on": c["created_on"],
                      "duration_seconds_crm": c["duration_seconds"], "salesperson": c["salesperson"],
                      "split": c["split"], "crm_conversion_flag": c["crm_conversion_flag"],
                      "processing_status": "Pending or failed extraction"}
            audio_path = store.path("audio", c["call_id"] + ".json")
            if audio_path.exists():
                record["duration_seconds_audio"] = read_json(audio_path)["duration_seconds"]
            if result:
                ex = result["extraction"]
                record.update({"processing_status": "Evidence checked; semantic QA pending",
                               "conversation_type": ex.get("conversation_type", "unclear"),
                               "summary": ex["summary"], "next_action_suggestion": ex["next_action"],
                               "objections": "; ".join(o["category"] + ": " + o["concern"] for o in ex["objections"]),
                               "asr_flags": "; ".join(result["asr_flags"]),
                               "uncertainties": "; ".join(ex["uncertainties"]), **quality_coverage(ex)})
                # Evidence segment ids and timestamps come from the transcript the extraction was made from.
                transcript = current_source(store, c["call_id"])
                segments = {s["id"]: s for s in transcript["segments"]}
                starts[c["call_id"]] = {s["id"]: s["start"] for s in transcript["segments"]}
                record["duration_seconds_audio"] = transcript["duration_seconds"]
                record["speaker_roles"] = "Inferred from conversation; not diarized or verified"
                ev = ex.get("purpose_evidence")
                if ev:
                    segment = segments[ev["segment_id"]]
                    evidence_rows.append({"lead_alias": c["lead_alias"], "call_id": c["call_id"],
                                          "collection": "purpose", "field": "conversation_type",
                                          "claim": ex["conversation_type"], "evidence_type": "purpose_evidence",
                                          "quote": ev["quote"], "start_seconds": segment["start"],
                                          "end_seconds": segment["end"], "segment_id": ev["segment_id"],
                                          "review_status": "Not independently reviewed"})
                for fact in ex["facts"]:
                    facts[fact["field"]] = fact["value"]
                    history[fact["field"]].add(fact["value"])
                for obj in ex["objections"]:
                    objections.append((obj, c["call_id"]))
                objection_counts.update({obj["category"] for obj in ex["objections"]})
                signals_all.update(s["kind"] for s in ex["signals"])
                strengths.extend(s["description"] for s in ex["signals"] if s["kind"] in {"goal", "urgency", "payment_intent"})
                for collection in ["facts", "signals", "objections", "pitches"]:
                    for item in ex[collection]:
                        label = item.get("field") or item.get("kind") or item.get("category") or item.get("topic")
                        value = item.get("value") or item.get("description") or item.get("concern") or item.get("topic")
                        for key in ["evidence", "response_evidence", "resolution_evidence"]:
                            ev = item.get(key)
                            if not ev:
                                continue
                            segment = segments[ev["segment_id"]]
                            claim = value
                            if key == "response_evidence":
                                claim = item.get("response") or item.get("prospect_response")
                            elif key == "resolution_evidence":
                                claim = f"{item.get('resolution')}: {value}"
                            evidence_rows.append({"lead_alias": c["lead_alias"], "call_id": c["call_id"],
                                                  "collection": collection, "field": label, "claim": claim,
                                                  "evidence_type": key, "quote": ev["quote"],
                                                  "start_seconds": segment["start"], "end_seconds": segment["end"],
                                                  "segment_id": ev["segment_id"], "review_status": "Not independently reviewed"})
            call_rows.append(record)
        # Do not infer that a later objection in the same category resolves an earlier concern.
        open_objections = [(o, cid) for o, cid in objections if o["resolution"] != "resolved"]
        if len(lead_analyses) != len(group):
            next_action = "Finish reviewing the available calls."
        elif (live := last_conversation(group, lead_analyses)) is None:
            next_action = ("No call reached the lead. Confirm the phone number and try another channel such as "
                           "WhatsApp or email before further calls.")
        else:
            unanswered = len(group) - 1 - group.index(live)
            next_action = (f"The last {unanswered} call(s) reached voicemail or no conversation. From the last live "
                           f"conversation: " if unanswered else "") + lead_analyses[live["call_id"]]["extraction"]["next_action"]
        if priority == "Verify enrollment":
            next_action = "Confirm enrollment/payment and current lead status before any further sales outreach. " + next_action
        elif priority == "Do not contact":
            next_action = "Respect the request to stop contact; confirm the CRM suppression setting."
        call_minutes = sum(c["duration_seconds"] for c in group) / 60
        dates = [datetime.fromisoformat(c["created_on"]) for c in group]
        gaps = [(b - a).total_seconds() / 86400 for a, b in pairwise(dates)]
        effort = "Review effort" if call_minutes >= 30 and len(open_objections) >= 2 and not signals_all & {"payment_intent", "payment_claim"} else "No rule triggered"
        incomplete = len(lead_analyses) != len(group)
        profile_rows.append({"lead_alias": group[0]["lead_alias"], "lead_number": lead, **facts,
                             "conflicting_fields": "; ".join(k for k, v in history.items() if len(v) > 1)})
        worklist.append({"lead_alias": group[0]["lead_alias"], "lead_number": lead,
                         "lead_name": group[-1]["lead_name"], "current_owner": group[-1]["current_owner"],
                         "priority": priority, "priority_reason": reason,
                         "as_of_recorded_call": group[-1]["created_on"], "current_status": "Not verified; historical export",
                         "calls_in_export": len(group), "calls_analysed": len(lead_analyses),
                         "total_call_minutes_crm": round(call_minutes, 2),
                         "mean_gap_days": round(sum(gaps) / len(gaps), 2) if gaps else None,
                         "split": group[0]["split"], "outcome": group[0]["journey_outcome_label"],
                         "goal": facts.get("goal"), "current_role": facts.get("current_role"),
                         "target_role": facts.get("target_role"), "experience": facts.get("experience"),
                         "timeline": facts.get("timeline"), "course": facts.get("course"),
                         "open_objections": "; ".join(f"{o['category']}: {o['concern']} ({cid})" for o, cid in open_objections),
                         "objection_history_note": "Unresolved in its source call; later resolution requires cross-call review",
                         "next_action_suggestion": next_action,
                         "strengths": "; ".join(dict.fromkeys(strengths))[:1200],
                         "weaknesses_or_unknowns": "Analysis incomplete" if incomplete else "; ".join(k for k in ["goal", "timeline", "budget"] if k not in facts) or "No missing key discovery fields detected",
                         "opportunity": "Complete the journey review" if incomplete else "Address the documented concern and agree a specific next step" if open_objections else "Confirm current need and readiness before proposing a next step",
                         "threats": "Not assessed: journey extraction incomplete" if incomplete else "; ".join(o["category"] for o, _ in open_objections) or "No explicit unresolved objection extracted",
                         "conflicting_profile_fields": "; ".join(k for k, v in history.items() if len(v) > 1),
                         "effort_review": "Not assessed: journey extraction incomplete" if incomplete else effort, "journey_coverage": "All calls in provided export; lifetime completeness unconfirmed",
                         "review_status": "Requires human review"})
    overview = {"selected_calls": len(calls), "selected_leads": len(groups), "analysed_calls": len(analyses),
                "complete_extracted_journeys": sum(w["calls_analysed"] == w["calls_in_export"] for w in worklist),
                "holdout_leads": sum(w["split"] == "holdout" for w in worklist),
                "objection_mentions_by_call": dict(objection_counts), "selection_sha256": selection["sha256"],
                "accuracy_status": "Not measured against independent references yet",
                "scope": "Extraction pilot; no validated conversion predictions", "costs": cost_summary(store, calls)}
    findings = findings_summary(calls, analyses, starts, worklist)
    summaries = [("objection_summary", [{k: v for k, v in o.items() if k != "examples"} for o in findings["objections"]]),
                 ("signal_summary", [{k: v for k, v in s.items() if k != "examples"} for s in findings["signals"]])]
    for name, rows in [("worklist", worklist), ("calls", call_rows), ("evidence", evidence_rows),
                       ("profiles", profile_rows), *summaries]:
        fields = list(dict.fromkeys(k for row in rows for k in row))
        write_csv(store.path("exports", name + ".csv"), rows, fields)
        write_json(store.path("exports", name + ".json"), rows)
    write_json(store.path("exports", "findings.json"), findings)
    write_json(store.path("exports", "overview.json"), overview)
    write_csv(store.path("exports", "processing_costs.csv"), [overview["costs"]])
    write_json(store.path("exports", "report-fingerprint.json"), {"selection": selection["sha256"], "extractions": digest(analyses)})
    return overview
