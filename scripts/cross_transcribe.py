"""Second, independent transcripts with Gemini Transcribe, compared with the Whisper transcripts.

Development calls only until a method freeze includes this cross-check. Agreement locates likely transcription
errors for adjudication and review; it is not an accuracy measurement.
"""

import argparse

from dotenv import load_dotenv

from trendytech_pilot.artifacts import current_transcript
from trendytech_pilot.consensus import transcript_agreement
from trendytech_pilot.remote_asr import GeminiTranscriber
from trendytech_pilot.storage import Store, read_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("call_ids", nargs="*")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    load_dotenv(".env")
    store = Store(args.data_dir)
    selected = {c["call_id"]: c for c in read_json(store.path("selection.json"))["calls"]}
    ids = args.call_ids or [cid for cid, c in selected.items() if c["split"] == "development"][:args.limit]
    if any(cid not in selected or selected[cid]["split"] != "development" for cid in ids):
        raise ValueError("Cross-transcription is restricted to development calls until a method freeze covers it")
    transcriber, failures = GeminiTranscriber(store), []
    for i, cid in enumerate(ids, 1):
        try:
            other = transcriber.transcribe(selected[cid])
        except Exception as exc:  # noqa: BLE001 -- isolate one failed call; the run still exits nonzero
            failures.append(cid)
            print(f"{i}/{len(ids)} {cid} FAILED {type(exc).__name__}", flush=True)
            continue
        whisper = current_transcript(store, cid)
        agreement = transcript_agreement(whisper["segments"], other["text"]) if whisper else None
        print(f"{i}/{len(ids)} {cid} OK speakers={len(other['speakers'])} agreement={agreement:.3f}", flush=True)
    print({"attempted": len(ids), "failed": len(failures), "failed_call_ids": failures}, flush=True)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
