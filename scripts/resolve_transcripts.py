"""Build consensus transcripts: Gemini Pro listens to disputed stretches and chooses between Whisper and Gemini
Transcribe. Development calls only until a method freeze covers the cross-check."""

import argparse
from collections import Counter

from dotenv import load_dotenv

from trendytech_pilot.artifacts import current_transcript
from trendytech_pilot.resolve import GeminiResolver
from trendytech_pilot.storage import Store, read_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("call_ids", nargs="*")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    load_dotenv(".env")
    store = Store(args.data_dir)
    selected = {c["call_id"]: c for c in read_json(store.path("selection.json"))["calls"]}
    ids = args.call_ids or sorted(p.stem for p in store.path("asr", "gemini").glob("*.json"))
    if any(cid not in selected or selected[cid]["split"] != "development" for cid in ids):
        raise ValueError("Resolution is restricted to development calls until a method freeze covers it")
    resolver, failures, totals = GeminiResolver(store), [], Counter()
    for i, cid in enumerate(ids, 1):
        try:
            artifact = resolver.resolve(selected[cid], current_transcript(store, cid),
                                        read_json(store.path("asr", "gemini", cid + ".json")))
        except Exception as exc:  # noqa: BLE001 -- isolate one failed call; the run still exits nonzero
            failures.append(cid)
            print(f"{i}/{len(ids)} {cid} FAILED {type(exc).__name__}: {str(exc)[:160]}", flush=True)
            continue
        totals.update(artifact["decisions"])
        print(f"{i}/{len(ids)} {cid} OK spans={artifact['disputed_spans']} {artifact['decisions']} "
              f"roles={artifact['speaker_roles']}", flush=True)
    print({"attempted": len(ids), "failed": len(failures), "failed_call_ids": failures, "decisions": dict(totals)},
          flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
