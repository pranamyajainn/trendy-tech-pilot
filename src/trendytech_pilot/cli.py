"""Small resumable commands. Long runs log call IDs, never customer details."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .storage import Store, digest, read_json, write_json


def selected_calls(store, split="development", limit=None):
    calls = read_json(store.path("selection.json"))["calls"]
    if split != "all":
        calls = [c for c in calls if c["split"] == split]
    return calls[:limit] if limit is not None else calls


def freeze_method(store, asr_model, llm_model):
    from .audio import ASR_VERSION
    from .extract import PROMPT_VERSION, SYSTEM
    from .models import LOCAL_REVISIONS

    selection = read_json(store.path("selection.json"))
    method = {"selection_sha256": selection["sha256"], "asr_model": asr_model, "llm_model": llm_model,
              "llm_revision": LOCAL_REVISIONS.get(llm_model),
              "asr_revision": LOCAL_REVISIONS.get(asr_model),
              "asr_version": ASR_VERSION, "prompt_version": PROMPT_VERSION, "prompt_sha256": digest(SYSTEM)}
    path = store.path("method-freeze.json")
    if path.exists() and read_json(path) != method:
        raise ValueError("Method already frozen. Changing it would invalidate this holdout evaluation.")
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
    for command in ["download", "transcribe", "extract"]:
        sub = commands.add_parser(command)
        sub.add_argument("--split", choices=["development", "holdout", "all"], default="development")
        sub.add_argument("--limit", type=int)
        if command != "download":
            sub.add_argument("--force", action="store_true")
    commands.add_parser("status")
    commands.add_parser("freeze")
    commands.add_parser("export")
    commands.add_parser("qa")
    args = parser.parse_args()
    store = Store(args.data_dir)
    asr_model = os.getenv("PILOT_ASR_MODEL", "mlx-community/whisper-large-v3-turbo")
    provider = os.getenv("PILOT_EXTRACTOR", "local")
    if provider not in ("local", "gemini"):
        raise ValueError("PILOT_EXTRACTOR must be local or gemini")
    llm_model = "gemini-3.8-flash" if provider == "gemini" else os.getenv("PILOT_LLM_MODEL", "mlx-community/Qwen3.5-27B-4bit")
    if args.command == "audit":
        from .ingest import import_workbook
        print(json.dumps(import_workbook(args.workbook, store), indent=2))
    elif args.command == "select":
        from .ingest import save_selection
        print(json.dumps(save_selection(read_json(store.path("calls.json")), store, args.seed), indent=2))
    elif args.command == "status":
        from .artifacts import current_extraction, current_transcript

        calls = selected_calls(store, "all")
        print(json.dumps({"selected_calls": len(calls), "selected_leads": len({c['lead_number'] for c in calls}),
                          **{stage: len(list(store.path(stage).glob("*.json"))) for stage in ["audio", "transcripts", "extractions"]},
                          "current_transcripts": sum(current_transcript(store, c["call_id"]) is not None for c in calls),
                          "current_extractions": sum(current_extraction(store, c["call_id"]) is not None for c in calls),
                          "method_frozen": store.path("method-freeze.json").exists()}, indent=2))
    elif args.command == "freeze":
        print(json.dumps(freeze_method(store, asr_model, llm_model), indent=2))
    elif args.command == "export":
        from .reporting import export_tables
        print(json.dumps(export_tables(store), indent=2))
    elif args.command == "qa":
        from .quality import export_qa
        print(json.dumps(export_qa(store), indent=2))
    else:
        calls = selected_calls(store, args.split, args.limit)
        if args.command != "download" and (store.path("method-freeze.json").exists()
                                             or any(c["split"] == "holdout" for c in calls)):
            verify_freeze(store, asr_model, llm_model)
        extractor = None
        if args.command == "extract":
            if provider == "gemini":
                from .remote import GeminiExtractor
                extractor = GeminiExtractor(store)
            else:
                from .extract import LocalExtractor
                extractor = LocalExtractor(llm_model)
        failures = []
        for i, call in enumerate(calls, 1):
            try:
                if args.command == "download":
                    from .audio import download
                    download(call, store)
                elif args.command == "transcribe":
                    from .audio import transcribe
                    transcribe(call, store, asr_model, args.force)
                else:
                    from .extract import extract_call
                    extract_call(call, store, extractor, args.force)
                print(f"{i}/{len(calls)} {call['call_id']} {args.command} OK", flush=True)
            except Exception as exc:  # noqa: BLE001 -- isolate one failed call; command still exits nonzero
                failures.append(call["call_id"])
                print(f"{i}/{len(calls)} {call['call_id']} FAILED {type(exc).__name__}", flush=True)
        print(json.dumps({"attempted": len(calls), "failed": len(failures), "failed_call_ids": failures}), flush=True)
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
