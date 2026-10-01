"""Bounded recording downloads and timestamped local ASR."""

import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .models import local_model_path, local_revision
from .storage import digest, read_json, write_json

ASR_VERSION = "whisper-timestamps-v2"
MAX_AUDIO_BYTES = 180 * 1024 * 1024


def probe_audio(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,channels,sample_rate",
         "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=45,
    )
    info = json.loads(result.stdout)
    streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if not streams:
        raise ValueError("File has no decodable audio stream")
    duration = float(info["format"]["duration"])
    if duration <= 0:
        raise ValueError("Empty audio file")
    return {"duration_seconds": duration, "channels": streams[0].get("channels"),
            "sample_rate": int(streams[0]["sample_rate"])}


def download(call, store):
    dest = store.path("audio", call["call_id"] + Path(urlsplit(call["recording_url"]).path).suffix.lower())
    meta_path = store.path("audio", call["call_id"] + ".json")
    if dest.exists() and meta_path.exists():
        meta = read_json(meta_path)
        if meta["sha256"] == digest(dest.read_bytes()):
            return meta
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(1, 4):
        start = time.monotonic()
        try:
            url = urlsplit(call["recording_url"])
            if url.scheme != "https" or url.hostname != "recordings.mcube.com" or url.port not in (None, 443):
                raise ValueError("Recording URL outside approved source")
            with httpx.stream("GET", call["recording_url"], timeout=60, follow_redirects=False,
                              headers={"User-Agent": "TrendyTechPilot/0.1"}) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0]
                if not (content_type.startswith("audio/") or content_type == "application/octet-stream"):
                    raise ValueError("Download was not audio")
                nbytes = 0
                with tmp.open("wb") as f:
                    for chunk in response.iter_bytes(256 * 1024):
                        nbytes += len(chunk)
                        if nbytes > MAX_AUDIO_BYTES:
                            raise ValueError("Recording exceeds configured byte limit")
                        f.write(chunk)
            info = probe_audio(tmp)
            warnings = []
            if abs(info["duration_seconds"] - call["duration_seconds"]) > max(10, call["duration_seconds"] * .1):
                warnings.append("audio_duration_differs_from_crm")
            meta = {"call_id": call["call_id"], "file": str(dest.resolve()), "bytes": nbytes,
                    "sha256": digest(tmp.read_bytes()), **info, "warnings": warnings}
            os.replace(tmp, dest)
            write_json(meta_path, meta)
            store.event(stage="download", call_id=call["call_id"], attempt=attempt, status="success",
                        wall_seconds=time.monotonic() - start, external_cost_inr=0)
            return meta
        except Exception as exc:  # noqa: BLE001 -- record failure and bound retries at the job boundary
            tmp.unlink(missing_ok=True)
            # Keep provider error bodies and signed URLs out of console/ledger.
            store.event(stage="download", call_id=call["call_id"], attempt=attempt, status="failed",
                        error_type=type(exc).__name__, wall_seconds=time.monotonic() - start, external_cost_inr=0)
            if attempt == 3:
                raise RuntimeError(f"Download failed for {call['call_id']} ({type(exc).__name__})") from None
            time.sleep(attempt)


def transcribe(call, store, model, force=False):
    import mlx_whisper

    meta = read_json(store.path("audio", call["call_id"] + ".json"))
    revision = local_revision(model)
    fingerprint = digest([meta["sha256"], model, revision, ASR_VERSION])
    path = store.path("transcripts", call["call_id"] + ".json")
    if path.exists() and not force:
        prior = read_json(path)
        if prior["fingerprint"] == fingerprint:
            return prior
    start = time.monotonic()
    try:
        result = mlx_whisper.transcribe(
            meta["file"], path_or_hf_repo=local_model_path(model), language="en", task="transcribe",
            temperature=0.0, condition_on_previous_text=False, word_timestamps=False, verbose=None,
        )
        segments = []
        flags = list(meta["warnings"])
        for i, segment in enumerate(result["segments"]):
            text = segment["text"].strip()
            if not text:
                continue
            if segment.get("avg_logprob", 0) < -1 or segment.get("compression_ratio", 0) > 2.4:
                flags.append("asr_segment_needs_review")
            segments.append({"id": i, "start": round(float(segment["start"]), 2),
                             "end": round(float(segment["end"]), 2), "text": text,
                             "avg_logprob": segment.get("avg_logprob"),
                             "no_speech_prob": segment.get("no_speech_prob")})
        if not segments:
            flags.append("no_speech_transcribed")
        # Mono ASR does not perform diarization. Speaker roles are separate, unverified extraction claims.
        transcript = {"call_id": call["call_id"], "fingerprint": fingerprint,
                      "audio_sha256": meta["sha256"], "duration_seconds": meta["duration_seconds"],
                      "model": model, "model_revision": revision, "version": ASR_VERSION, "language": result.get("language"),
                      "speaker_diarization": "not_performed", "segments": segments,
                      "flags": sorted(set(flags)), "wall_seconds": time.monotonic() - start}
        write_json(path, transcript)
        path.with_suffix(".txt").write_text("\n".join(f"[{s['id']} | {s['start']:.2f}-{s['end']:.2f}] {s['text']}" for s in segments))
        store.event(stage="transcribe", call_id=call["call_id"], status="success", model=model,
                    fingerprint=fingerprint, audio_seconds=meta["duration_seconds"],
                    wall_seconds=transcript["wall_seconds"], external_cost_inr=0)
        return transcript
    except Exception as exc:
        store.event(stage="transcribe", call_id=call["call_id"], status="failed", model=model,
                    error_type=type(exc).__name__, wall_seconds=time.monotonic() - start, external_cost_inr=0)
        raise
    except KeyboardInterrupt:
        store.event(stage="transcribe", call_id=call["call_id"], status="interrupted", model=model,
                    wall_seconds=time.monotonic() - start, external_cost_inr=0)
        raise
