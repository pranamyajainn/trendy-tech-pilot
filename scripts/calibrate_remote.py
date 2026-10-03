"""Development-only Gemini trial with the production prompt. Writes data/experiments/, never extractions."""

import argparse
import time

from dotenv import load_dotenv
from pydantic import ValidationError

from trendytech_pilot.artifacts import current_transcript
from trendytech_pilot.experiments import completed_experiment_exists
from trendytech_pilot.extract import (
    PROMPT_VERSION,
    SYSTEM,
    evidence_error_message,
    extraction_fingerprint,
    extraction_user_prompt,
    generation_config,
    parse_json_response,
    retry_feedback,
)
from trendytech_pilot.remote import BudgetExceeded, GeminiExtractor
from trendytech_pilot.schema import EVIDENCE_RULES_VERSION, validate_evidence
from trendytech_pilot.storage import Store, digest, read_json, write_json

EXPERIMENT = "gemini-production-prompt-v1"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("call_ids", nargs="+")
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args()
    load_dotenv(".env")
    store = Store(args.data_dir)
    selected = {c["call_id"]: c for c in read_json(store.path("selection.json"))["calls"]}
    transcripts = {}
    for cid in args.call_ids:
        if cid not in selected or selected[cid]["split"] != "development":
            raise ValueError("Calibration is restricted to development calls")
        transcripts[cid] = current_transcript(store, cid)
        if transcripts[cid] is None:
            raise ValueError("Calibration requires a current transcript")
    extractor = GeminiExtractor(store)
    generation = generation_config(extractor.model_id)
    for cid, transcript in transcripts.items():
        # The production identity plus the experiment tag, so trials and production cannot drift apart.
        fingerprint = digest([extraction_fingerprint(transcript["fingerprint"], extractor.model_id, None), EXPERIMENT])
        path = store.path("experiments", f"{cid}-gemini-{fingerprint[:12]}.json")
        if completed_experiment_exists(path):
            print(cid, "already tested", flush=True)
            continue
        result = {"call_id": cid, "fingerprint": fingerprint, "experiment": EXPERIMENT, "model": extractor.model_id,
                  "prompt_version": PROMPT_VERSION, "generation": generation, "evidence_rules": EVIDENCE_RULES_VERSION,
                  "transcript_fingerprint": transcript["fingerprint"], "semantic_accuracy": "not_assessed",
                  "status": "failed", "attempts": []}
        user, feedback = extraction_user_prompt(transcript), ""
        start = time.monotonic()
        try:
            # Same two validation attempts and feedback as production extract_call.
            for attempt in range(1, 3):
                record = {"attempt": attempt}
                result["attempts"].append(record)
                attempt_start = time.monotonic()
                try:
                    raw, usage = extractor.generate(SYSTEM, user + feedback)
                    record.update(raw=raw, **usage)
                    parsed = parse_json_response(raw)
                    errors = validate_evidence(parsed, transcript)
                    if errors:
                        raise ValueError(evidence_error_message(parsed.model_dump(), transcript, errors))
                except (ValidationError, ValueError) as exc:
                    record["error"] = str(exc)
                    feedback = retry_feedback(str(exc))
                    continue
                finally:
                    record["wall_seconds"] = time.monotonic() - attempt_start
                result.update(status="evidence_checked_only", extraction=parsed.model_dump())
                break
        except (KeyboardInterrupt, BudgetExceeded) as exc:
            result.update(status="interrupted", error=type(exc).__name__)
            raise
        except Exception as exc:  # noqa: BLE001 -- provider/network failure is not a trial outcome; allow a rerun
            result.update(status="interrupted", error=f"{type(exc).__name__}: {exc}"[:500])
        finally:
            elapsed = time.monotonic() - start
            result["wall_seconds"] = elapsed
            write_json(path, result)
            # Spend is recorded by GeminiExtractor as remote_usage events; this records runtime only.
            store.event(stage="extract", call_id=cid, experiment=EXPERIMENT,
                        status="experimental" if result["status"] == "evidence_checked_only" else result["status"],
                        model=extractor.model_id, wall_seconds=elapsed, external_cost_inr=0)
            print(cid, result["status"], f"{elapsed:.1f}s", flush=True)


if __name__ == "__main__":
    main()
