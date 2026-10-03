"""Local-only development experiment; no holdout access or production promotion."""

import argparse
import time

from trendytech_pilot.artifacts import current_transcript
from trendytech_pilot.experiments import completed_experiment_exists
from trendytech_pilot.extract import LocalExtractor
from trendytech_pilot.referenced import REFERENCE_PROMPT_VERSION, REFERENCE_SYSTEM, expand_references
from trendytech_pilot.storage import Store, digest, read_json, write_json

parser = argparse.ArgumentParser()
parser.add_argument("call_ids", nargs="+")
parser.add_argument("--data-dir", default="data")
parser.add_argument("--model", default="mlx-community/Qwen3.5-9B-4bit")
parser.add_argument("--thinking", action="store_true")
parser.add_argument("--recommended-sampling", action="store_true")
args = parser.parse_args()
store = Store(args.data_dir)
selected = {c["call_id"]: c for c in read_json(store.path("selection.json"))["calls"]}
transcripts = {}
for cid in args.call_ids:
    if cid not in selected or selected[cid]["split"] != "development":
        raise ValueError("Calibration is restricted to development calls")
    transcripts[cid] = current_transcript(store, cid)
    if transcripts[cid] is None:
        raise ValueError("Calibration requires a current transcript")
extractor = LocalExtractor(args.model)
sampling = {"temperature": 1.0 if args.thinking else .7, "top_p": .95 if args.thinking else .8,
            "top_k": 20, "presence_penalty": 1.5, "seed": 42} if args.recommended_sampling else {}
for cid, transcript in transcripts.items():
    fingerprint = digest([transcript["fingerprint"], args.model, extractor.model_revision, REFERENCE_SYSTEM,
                          {"thinking": args.thinking, "max_tokens": 6500 if args.thinking else 2600,
                           "sampling": sampling}])
    path = store.path("experiments", f"{cid}-{fingerprint[:12]}.json")
    if completed_experiment_exists(path):
        print(cid, "already tested", flush=True)
        continue
    user = "<transcript>\n" + "\n".join(f"[{s['id']}] {s['text']}" for s in transcript["segments"]) + "\n</transcript>"
    start = time.monotonic()
    result = {"call_id": cid, "fingerprint": fingerprint, "model": args.model,
              "model_revision": extractor.model_revision, "prompt_version": REFERENCE_PROMPT_VERSION,
              "transcript_fingerprint": transcript["fingerprint"], "semantic_accuracy": "not_assessed",
              "thinking_enabled": args.thinking, "sampling": sampling}
    usage = {}
    try:
        raw, usage = extractor.generate(REFERENCE_SYSTEM, user, max_tokens=6500 if args.thinking else 2600,
                                        enable_thinking=args.thinking, **sampling)
        if args.thinking:
            if "</think>" not in raw:
                raise ValueError("Reasoning did not finish within the token budget; no extraction accepted")
            # Only the final answer is retained. Internal reasoning is not an extraction artifact.
            raw = raw.split("</think>", 1)[1].strip()
        result["raw"] = raw
        result["extraction"] = expand_references(raw, transcript).model_dump()
        result["status"] = "reference_validated_only"
    except Exception as exc:  # noqa: BLE001 -- retain failed experiments for review
        result["status"] = "failed"
        result["error"] = str(exc)
    except KeyboardInterrupt:
        result["status"] = "interrupted"
        raise
    finally:
        elapsed = time.monotonic() - start
        result.update(wall_seconds=elapsed, **usage)
        write_json(path, result)
        store.event(stage="extract", call_id=cid, experiment=REFERENCE_PROMPT_VERSION,
                    status=result["status"] if result["status"] in {"failed", "interrupted"} else "experimental",
                    model=args.model, wall_seconds=elapsed, external_cost_inr=0, **usage)
        print(cid, result["status"], f"{elapsed:.1f}s", flush=True)
