"""Small resumable commands. Long runs log call IDs, never customer details."""

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from .storage import Store, digest, read_json, write_json

PROVIDERS = ("local", "gemini", "verified")


def selected_calls(store, split="development", limit=None, call_ids=None, cohort=None):
    if cohort:
        from .cohort import calls_of
        calls, split = calls_of(store, cohort), cohort
    else:
        calls = read_json(store.path("selection.json"))["calls"]
    if split not in ("all", cohort):
        calls = [c for c in calls if c["split"] == split]
    if call_ids:
        missing = set(call_ids) - {c["call_id"] for c in calls}
        if missing:
            raise ValueError(f"Calls not in the {split} split: {sorted(missing)}")
        calls = [c for c in calls if c["call_id"] in call_ids]
    return calls[:limit] if limit is not None else calls


def retire_freeze(store, reason):
    """Archive the active freeze with a reason. Holdout processing stays blocked until a new freeze exists."""
    path = store.path("method-freeze.json")
    if not path.exists():
        raise ValueError("There is no active method freeze to retire")
    previous = read_json(path)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    write_json(store.path("method-freeze-history", f"{stamp}-{previous.get('prompt_version')}.json"),
               {"retired_at": stamp, "reason": reason, "method": previous})
    path.unlink()
    return {"retired": previous.get("prompt_version"), "reason": reason}


def llm_model_for(provider):
    if provider == "gemini":
        from .remote import GeminiExtractor
        return GeminiExtractor.model_id
    if provider == "verified":
        from .ensemble import VERIFIED_MODEL
        return VERIFIED_MODEL
    return os.getenv("PILOT_LLM_MODEL", "mlx-community/Qwen3.5-27B-4bit")


def freeze_method(store, asr_model, llm_model, supersede=None):
    from .artifacts import CONSENSUS_METHOD, TRANSCRIPT_SOURCE
    from .audio import ASR_VERSION
    from .extract import PROMPT_VERSION, SYSTEM, generation_config
    from .models import LOCAL_REVISIONS
    from .schema import EVIDENCE_RULES_VERSION

    selection = read_json(store.path("selection.json"))
    method = {"selection_sha256": selection["sha256"], "asr_model": asr_model, "llm_model": llm_model,
              "llm_revision": LOCAL_REVISIONS.get(llm_model),
              "asr_revision": LOCAL_REVISIONS.get(asr_model),
              "asr_version": ASR_VERSION, "prompt_version": PROMPT_VERSION, "prompt_sha256": digest(SYSTEM),
              "generation": generation_config(llm_model), "evidence_rules": EVIDENCE_RULES_VERSION,
              "transcript_source": TRANSCRIPT_SOURCE,
              "consensus": CONSENSUS_METHOD if TRANSCRIPT_SOURCE == "consensus" else None}
    path = store.path("method-freeze.json")
    if path.exists() and read_json(path) != method:
        if not supersede:
            raise ValueError("Method already frozen. Changing it would invalidate this holdout evaluation; "
                             "use --supersede with a reason to record a new method version.")
        # The earlier freeze is kept, with the reason, rather than deleted.
        previous = read_json(path)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        write_json(store.path("method-freeze-history", f"{stamp}-{previous.get('prompt_version')}.json"),
                   {"superseded_at": stamp, "reason": supersede, "method": previous})
    write_json(path, method)
    return method


def verify_freeze(store, asr_model, llm_model):
    if not store.path("method-freeze.json").exists():
        raise ValueError("Freeze the method after development QA before processing held-out calls")
    freeze_method(store, asr_model, llm_model)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="TrendyTech private-data pilot")
    parser.add_argument("--data-dir", default=os.getenv("PILOT_DATA_DIR", "data"))
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("audit")
    audit.add_argument("workbook", type=Path)
    sample = commands.add_parser("select")
    sample.add_argument("--seed", default="trendytech-pilot-v1")
    for command in ["download", "transcribe", "cross-transcribe", "sarvam-transcribe", "gemini-transcribe", "resolve",
                    "extract"]:
        sub = commands.add_parser(command)
        sub.add_argument("--split", choices=["development", "holdout", "all"], default="development")
        sub.add_argument("--cohort", help="Process a fixed post-pilot cohort (see `pilot cohort`) instead of a split")
        sub.add_argument("--limit", type=int)
        sub.add_argument("--calls", help="Comma-separated call IDs within the split or cohort")
        if command != "download":
            sub.add_argument("--force", action="store_true")
    cohort = commands.add_parser("cohort")
    cohort.add_argument("name", choices=["customers"])
    cohort_action = cohort.add_mutually_exclusive_group()
    cohort_action.add_argument("--freeze", action="store_true", help="Freeze the method for a full cohort run")
    cohort_action.add_argument("--supersede", metavar="REASON")
    commands.add_parser("status")
    freeze = commands.add_parser("freeze")
    freeze_action = freeze.add_mutually_exclusive_group()
    freeze_action.add_argument("--supersede", metavar="REASON")
    freeze_action.add_argument("--retire", metavar="REASON")
    commands.add_parser("export")
    commands.add_parser("qa")
    lead_actions = commands.add_parser("lead-actions")
    lead_actions.add_argument("--force", action="store_true")
    commands.add_parser("client")
    args = parser.parse_args()
    store = Store(args.data_dir)
    asr_model = os.getenv("PILOT_ASR_MODEL", "mlx-community/whisper-large-v3-turbo")
    provider = os.getenv("PILOT_EXTRACTOR", "local")
    if provider not in PROVIDERS:
        raise ValueError("PILOT_EXTRACTOR must be one of " + ", ".join(PROVIDERS))
    llm_model = llm_model_for(provider)
    if args.command == "audit":
        from .ingest import import_workbook
        print(json.dumps(import_workbook(args.workbook, store), indent=2))
    elif args.command == "select":
        from .ingest import save_selection
        print(json.dumps(save_selection(read_json(store.path("calls.json")), store, args.seed), indent=2))
    elif args.command == "status":
        from .artifacts import current_consensus, current_extraction, current_transcript

        calls = selected_calls(store, "all")
        print(json.dumps({"selected_calls": len(calls), "selected_leads": len({c['lead_number'] for c in calls}),
                          **{stage: len(list(store.path(stage).glob("*.json"))) for stage in ["audio", "transcripts", "extractions"]},
                          "current_transcripts": sum(current_transcript(store, c["call_id"]) is not None for c in calls),
                          "current_consensus": sum(current_consensus(store, c["call_id"]) is not None for c in calls),
                          "current_extractions": sum(current_extraction(store, c["call_id"]) is not None for c in calls),
                          "method_frozen": store.path("method-freeze.json").exists()}, indent=2))
    elif args.command == "cohort":
        from .cohort import build, freeze
        if args.freeze or args.supersede:
            print(json.dumps(freeze(store, args.name, args.supersede), indent=2))
        else:
            print(json.dumps(build(store, args.name), indent=2))
    elif args.command == "freeze" and args.retire:
        print(json.dumps(retire_freeze(store, args.retire), indent=2))
    elif args.command == "freeze":
        print(json.dumps(freeze_method(store, asr_model, llm_model, args.supersede), indent=2))
    elif args.command == "export":
        from .reporting import export_tables
        print(json.dumps(export_tables(store), indent=2))
    elif args.command == "qa":
        from .quality import export_qa
        print(json.dumps(export_qa(store), indent=2))
    elif args.command == "lead-actions":
        from .client import journeys, lead_action
        from .ensemble import GeminiVerifier

        model, failures = GeminiVerifier(store), []
        leads = journeys(store, selected_calls(store, "all"))
        for i, journey in enumerate(leads.values(), 1):
            try:
                lead_action(store, journey, model, args.force)
                print(f"{i}/{len(leads)} {journey['lead_alias']} lead-action OK", flush=True)
            except Exception as exc:  # noqa: BLE001 -- isolate one failed lead; command still exits nonzero
                failures.append(journey["lead_alias"])
                print(f"{i}/{len(leads)} {journey['lead_alias']} FAILED {type(exc).__name__}", flush=True)
        print(json.dumps({"attempted": len(leads), "failed": len(failures), "failed_leads": failures}), flush=True)
        if failures:
            raise SystemExit(1)
    elif args.command == "client":
        from .client import export_client, validation_pack
        print(json.dumps({**validation_pack(store), **export_client(store)}, indent=2))
    else:
        calls = selected_calls(store, args.split, args.limit, args.calls.split(",") if args.calls else None, args.cohort)
        if args.cohort and args.command in ("gemini-transcribe", "extract"):
            from .cohort import check_frozen
            check_frozen(store, args.cohort, whole_cohort=not args.calls)
        if args.command != "download" and (store.path("method-freeze.json").exists()
                                             or any(c["split"] == "holdout" for c in calls)):
            verify_freeze(store, asr_model, llm_model)
        worker = None
        if args.command == "cross-transcribe":
            from .remote_asr import GeminiTranscriber
            worker = GeminiTranscriber(store)
        elif args.command == "gemini-transcribe":
            from .remote_asr import GeminiLabelledTranscriber
            worker = GeminiLabelledTranscriber(store)
        elif args.command == "resolve":
            from .resolve import GeminiResolver
            worker = GeminiResolver(store)
        elif args.command == "extract":
            if provider == "verified":
                from .ensemble import GeminiVerifier
                from .remote import GeminiExtractor
                worker = (GeminiExtractor(store), GeminiVerifier(store))
            elif provider == "gemini":
                from .remote import GeminiExtractor
                worker = GeminiExtractor(store)
            else:
                from .extract import LocalExtractor
                worker = LocalExtractor(llm_model)
        if args.command == "sarvam-transcribe":
            # Batch jobs of up to 20 calls each, rather than one request per call.
            from .remote_sarvam import SarvamTranscriber
            results = SarvamTranscriber(store).transcribe_batch(calls, args.force)
            for i, call in enumerate(calls, 1):
                outcome = results.get(call["call_id"], "NotRun")
                print(f"{i}/{len(calls)} {call['call_id']} sarvam-transcribe {'OK' if outcome == 'OK' else 'FAILED ' + outcome}")
            failures = [cid for cid, outcome in results.items() if outcome != "OK"]
            print(json.dumps({"attempted": len(calls), "failed": len(failures), "failed_call_ids": failures}), flush=True)
            raise SystemExit(1 if failures else 0)
        failures = []
        for i, call in enumerate(calls, 1):
            try:
                if args.command == "download":
                    from .audio import download
                    download(call, store)
                elif args.command == "transcribe":
                    from .audio import transcribe
                    transcribe(call, store, asr_model, args.force)
                elif args.command in ("cross-transcribe", "gemini-transcribe"):
                    worker.transcribe(call, args.force)
                elif args.command == "resolve":
                    from .artifacts import current_transcript
                    whisper = current_transcript(store, call["call_id"])
                    others = [store.path("asr", name, call["call_id"] + ".json") for name in ("gemini", "sarvam")]
                    if whisper is None or not all(p.exists() for p in others):
                        raise ValueError("All three transcripts must exist before resolution")
                    worker.resolve(call, whisper, *(read_json(p) for p in others), args.force)
                elif provider == "verified":
                    from .ensemble import verified_extract
                    verified_extract(call, store, *worker, args.force)
                else:
                    from .extract import extract_call
                    extract_call(call, store, worker, args.force)
                print(f"{i}/{len(calls)} {call['call_id']} {args.command} OK", flush=True)
            except Exception as exc:  # noqa: BLE001 -- isolate one failed call; command still exits nonzero
                failures.append(call["call_id"])
                print(f"{i}/{len(calls)} {call['call_id']} FAILED {type(exc).__name__}", flush=True)
        print(json.dumps({"attempted": len(calls), "failed": len(failures), "failed_call_ids": failures}), flush=True)
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
