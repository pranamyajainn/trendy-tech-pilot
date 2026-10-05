"""Call groups processed after the pilot, kept apart from the frozen pilot selection.

customers: every call of every lead whose calls are all flagged "Is Converted = Yes" in the client export, except
leads already in the pilot. Leads with mixed flags are left out: the flag is not consistent over their calls.
open_sample: the non-buyer side of the scoring yardstick. A seeded random 150 of the leads with no "Yes" flag on any
call and at least one call of 3+ minutes (a real conversation), outside the pilot, with all their calls. "Not
converted" means no purchase recorded by the export date, not "will never buy".
"""

from collections import defaultdict
from datetime import UTC, datetime

from .storage import digest, read_json, write_json

DEFINITIONS = {"customers": "All calls of leads whose every exported call is flagged Is Converted = Yes, "
                            "excluding leads in the pilot selection",
               "open_sample": "All calls of a seeded random 150 leads with no Yes flag and a call of 3+ minutes, "
                              "excluding leads in the pilot selection"}
OPEN_SAMPLE_SIZE, OPEN_MIN_SECONDS = 150, 180


def build(store, name="customers"):
    if name not in DEFINITIONS:
        raise ValueError(f"Unknown cohort {name}; known: {sorted(DEFINITIONS)}")
    calls = read_json(store.path("calls.json"))
    pilot = {c["lead_number"] for c in read_json(store.path("selection.json"))["calls"]}
    leads = defaultdict(list)
    for call in calls:
        leads[call["lead_number"]].append(call)
    if name == "customers":
        keep = lambda group: all(c["crm_conversion_flag"] == "Yes" for c in group)
    else:
        keep = lambda group: (all(c["crm_conversion_flag"] != "Yes" for c in group)
                              and max(c["duration_seconds"] for c in group) >= OPEN_MIN_SECONDS)
    chosen = sorted((lead for lead, group in leads.items() if lead not in pilot and keep(group)),
                    key=lambda lead: digest([name, lead]))
    if name == "open_sample":
        chosen = chosen[:OPEN_SAMPLE_SIZE]
    records = []
    for index, lead in enumerate(chosen, 1):
        group = sorted(leads[lead], key=lambda c: (c["created_on"], c["call_id"]))
        for number, call in enumerate(group, 1):
            records.append({**call, "lead_alias": f"{'K' if name == 'customers' else 'N'}{index:03d}",
                            "call_number_in_export": number, "split": name,
                            "journey_outcome_label": ("CRM reported converted; unverified" if name == "customers"
                                                      else "No conversion recorded by the export date"),
                            "available_calls_for_lead": len(group)})
    manifest = {"name": name, "definition": DEFINITIONS[name], "source_sha256": read_json(store.path("source.json"))["sha256"],
                "sha256": digest(records), "calls": records}
    path = store.path("cohorts", name + ".json")
    if path.exists() and read_json(path)["sha256"] != manifest["sha256"]:
        raise ValueError(f"Cohort {name} is already fixed and the rebuilt list differs; investigate before replacing it")
    write_json(path, manifest)
    return {"cohort": name, "leads": len(chosen), "calls": len(records),
            "audio_minutes": round(sum(c["duration_seconds"] for c in records) / 60)}


def calls_of(store, name):
    path = store.path("cohorts", name + ".json")
    if not path.exists():
        raise ValueError(f"Cohort {name} does not exist; run `pilot cohort {name}` first")
    return read_json(path)["calls"]


def method_v3():
    """Everything that decides a cohort call's output. A full cohort run requires it to be frozen first."""
    from .ensemble import VERIFIED_MODEL
    from .extract import PROMPT_VERSION, SYSTEM, generation_config
    from .labelled import ALIGNMENT
    from .remote_asr import LABELLED_ASR, STITCH
    from .schema import EVIDENCE_RULES_VERSION

    return {"transcript": LABELLED_ASR, "stitch": STITCH, "alignment": ALIGNMENT, "extractor": VERIFIED_MODEL,
            "generation": generation_config(VERIFIED_MODEL), "prompt_version": PROMPT_VERSION,
            "prompt_sha256": digest(SYSTEM), "evidence_rules": EVIDENCE_RULES_VERSION}


def freeze(store, name, supersede=None):
    path = store.path("cohorts", name + "-method.json")
    method = {"cohort_sha256": read_json(store.path("cohorts", name + ".json"))["sha256"], **method_v3()}
    if path.exists() and read_json(path) != method:
        if not supersede:
            raise ValueError("Cohort method already frozen; use --supersede with a reason to record a new version")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        write_json(store.path("cohorts", "method-history", f"{name}-{stamp}.json"),
                   {"superseded_at": stamp, "reason": supersede, "method": read_json(path)})
    write_json(path, method)
    return method


def check_frozen(store, name, whole_cohort):
    """Whole-cohort runs need the frozen method to match the code; named-call validation runs do not."""
    path = store.path("cohorts", name + "-method.json")
    if not path.exists():
        if whole_cohort:
            raise ValueError(f"Freeze the method for cohort {name} (pilot cohort {name} --freeze) before a full run; "
                             "validate it first on named calls with --calls")
        return
    if read_json(path) != {"cohort_sha256": read_json(store.path("cohorts", name + ".json"))["sha256"], **method_v3()}:
        raise ValueError(f"The code's method differs from cohort {name}'s frozen method; refreeze with --supersede")
