"""Third, independent transcript from Sarvam Saaras, an Indian-language speech specialist, via its batch API."""

import os
import time

import httpx

from .budget import Budget
from .remote_asr import opus_audio
from .storage import digest, read_json, write_json

BASE = "https://api.sarvam.ai/speech-to-text/job/v1"
# Product and technology names that general models mishear. A probe showed keyterms fixing "Gen AI" and
# "TrendyTech"; a lone keyterm insertion is outvoted by the other two systems in the consensus.
KEYTERMS = ["TrendyTech", "Sumit Mittal", "Databricks", "PySpark", "Spark", "Azure", "Azure Data Factory", "AWS",
            "GCP", "Snowflake", "Kafka", "Big Data", "Data Engineering", "Gen AI", "Python", "SQL", "LinkedIn",
            "WhatsApp", "LMS", "EMI"]
# Identity of the third transcript: changing any value makes stored outputs stale.
SARVAM_ASR = {"version": "sarvam-batch-v1", "audio": "ogg/opus 24 kbps mono",
              "job_parameters": {"model": "saaras:v4", "mode": "verbatim", "language_code": "en-IN",
                                 "with_diarization": True, "with_timestamps": True, "keyterms": KEYTERMS}}
# Published rates seen 3 Oct 2026 conflict (INR 30/h plus 20% for diarization, or INR 45/h); the higher is used.
INR_PER_MINUTE = 45 / 60


def parse_output(output):
    entries = (output.get("diarized_transcript") or {}).get("entries") or []
    turns = [{"speaker": f"sarvam:{e.get('speaker_id')}", "text": e["transcript"].strip(),
              "start": e.get("start_time_seconds"), "end": e.get("end_time_seconds")}
             for e in entries if e.get("transcript", "").strip()]
    return turns, (output.get("transcript") or " ".join(t["text"] for t in turns)).strip()


class SarvamTranscriber:
    model_id = SARVAM_ASR["job_parameters"]["model"]
    batch_size = 20  # Provider maximum files per job.
    poll_seconds = 5

    def __init__(self, store, client=None):
        if os.getenv("PILOT_ALLOW_REMOTE") != "1":
            raise ValueError("Remote transcription is disabled. Explicit local opt-in is required.")
        self.key = os.getenv("SARVAM_API_KEY")
        if not self.key:
            raise ValueError("Set SARVAM_API_KEY in the ignored local .env; never in chat or Git.")
        self.store, self.budget = store, Budget(store)
        self.client = client or httpx.Client(timeout=180)
        self.headers = {"api-subscription-key": self.key}

    def artifact_path(self, call_id):
        return self.store.path("asr", "sarvam", call_id + ".json")

    def current(self, call_id):
        meta = read_json(self.store.path("audio", call_id + ".json"))
        path = self.artifact_path(call_id)
        if path.exists() and read_json(path)["fingerprint"] == digest([meta["sha256"], SARVAM_ASR]):
            return read_json(path)
        return None

    def _post(self, path, body):
        response = self.client.post(BASE + path, headers=self.headers, json=body)
        response.raise_for_status()
        return response.json()

    def transcribe_batch(self, calls, force=False):
        """Returns {call_id: "OK" or an error type}. Each job covers up to 20 calls."""
        results = {c["call_id"]: "OK" for c in calls if not force and self.current(c["call_id"])}
        pending = [c for c in calls if c["call_id"] not in results]
        for offset in range(0, len(pending), self.batch_size):
            chunk = pending[offset:offset + self.batch_size]
            metas = {c["call_id"]: read_json(self.store.path("audio", c["call_id"] + ".json")) for c in chunk}
            start = time.monotonic()
            try:
                job_id = self._submit(metas)
                state = self._wait(job_id)
                results.update(self._collect(job_id, state, metas, time.monotonic() - start))
            except Exception as exc:  # noqa: BLE001 -- one failed job must not lose the others
                for cid in metas:
                    results[cid] = type(exc).__name__
                    self.store.event(stage="transcribe", call_id=cid, system=self.model_id, model=self.model_id,
                                     status="failed", error_type=type(exc).__name__, external_cost_inr=0,
                                     wall_seconds=(time.monotonic() - start) / len(metas))
        return results

    def _submit(self, metas):
        minutes = sum(m["duration_seconds"] for m in metas.values()) / 60
        job = self._post("", {"job_parameters": SARVAM_ASR["job_parameters"]})
        job_id = job["job_id"]
        with self.budget.reserve(minutes * INR_PER_MINUTE, purpose="asr:" + self.model_id, job_id=job_id,
                                 calls=len(metas)) as reservation:
            urls = self._post("/upload-files", {"job_id": job_id, "files": [f"{cid}.ogg" for cid in metas]})
            for cid, meta in metas.items():
                url = urls["upload_urls"][f"{cid}.ogg"]["file_url"]
                upload = self.client.put(url, content=opus_audio(meta["file"]),
                                         headers={"x-ms-blob-type": "BlockBlob", "Content-Type": "audio/ogg"})
                upload.raise_for_status()
            self._post(f"/{job_id}/start", {})
            # Billing follows processing; the provider reports no usage, so the published-rate estimate is kept.
            reservation.settle(minutes * INR_PER_MINUTE, job_id=job_id, calls=len(metas),
                               cost_basis="Audio duration at INR 45/hour (higher published rate); credits not reconciled")
        return job_id

    def _wait(self, job_id):
        while True:
            response = self.client.get(f"{BASE}/{job_id}/status", headers=self.headers)
            response.raise_for_status()
            state = response.json()
            if state["job_state"] in ("Completed", "Failed"):
                return state
            time.sleep(self.poll_seconds)

    def _collect(self, job_id, state, metas, seconds):
        outputs = {}
        for task in state.get("job_details", []):
            name = (task.get("inputs") or [{}])[0].get("file_name", "")
            if task.get("state") == "Success" and task.get("outputs"):
                outputs[name.removesuffix(".ogg")] = task["outputs"][0]["file_name"]
        urls = self._post("/download-files", {"job_id": job_id, "files": list(outputs.values())})["download_urls"] if outputs else {}
        results = {}
        for cid, meta in metas.items():
            if cid not in outputs:
                results[cid] = "SarvamTaskFailed"
                self.store.event(stage="transcribe", call_id=cid, system=self.model_id, model=self.model_id,
                                 status="failed", error_type="SarvamTaskFailed", external_cost_inr=0,
                                 wall_seconds=seconds / len(metas))
                continue
            download = self.client.get(urls[outputs[cid]]["file_url"])
            download.raise_for_status()
            turns, text = parse_output(download.json())
            fingerprint = digest([meta["sha256"], SARVAM_ASR])
            write_json(self.artifact_path(cid), {
                "call_id": cid, "fingerprint": fingerprint, "system": SARVAM_ASR, "job_id": job_id,
                "audio_sha256": meta["sha256"], "duration_seconds": meta["duration_seconds"], "text": text,
                "turns": turns, "speakers": sorted({t["speaker"] for t in turns}),
                "flags": [] if text else ["no_speech_transcribed"]})
            self.store.event(stage="transcribe", call_id=cid, system=self.model_id, model=self.model_id, status="success",
                             fingerprint=fingerprint, audio_seconds=meta["duration_seconds"], wall_seconds=seconds / len(metas),
                             external_cost_inr=meta["duration_seconds"] / 60 * INR_PER_MINUTE)
            results[cid] = "OK"
        return results
