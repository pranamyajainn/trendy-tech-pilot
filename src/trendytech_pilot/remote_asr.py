"""Hosted speech-to-text as an independent second transcript. It never replaces the Whisper transcript in place."""

import base64
import os
import subprocess
import time

import httpx

from .budget import Budget
from .remote import post_with_busy_retries
from .storage import digest, read_json, write_json

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Identity of the second transcript: changing any value makes stored outputs stale. Custom vocabulary is not used:
# the API rejects it together with diarization, and a probe with it returned an empty transcript (3 Oct 2026).
GEMINI_ASR = {"model": "gemini-3.5-transcribe", "version": "gemini-transcribe-v1", "audio": "ogg/opus 24 kbps mono",
              "config": {"mode": "VERBATIM", "diarization": True, "wordTimestamp": True}}


def opus_audio(path):
    """Compress for upload. Gemini downsamples audio internally, so 24 kbps Opus keeps what the model hears."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-c:a", "libopus", "-b:a", "24k",
                           "-f", "ogg", "-"], capture_output=True, check=True, timeout=300).stdout


def offset_seconds(value):
    return float(value.rstrip("s")) if value else None


def parse_turns(result):
    turns = []
    for part in result["candidates"][0]["content"]["parts"]:
        transcription = part.get("audioTranscription")
        if not transcription:
            continue
        words = [{"word": w["word"], "start": offset_seconds(w.get("startOffset")), "end": offset_seconds(w.get("endOffset"))}
                 for w in transcription.get("words", [])]
        text = (part.get("text") or " ".join(w["word"] for w in words)).strip()
        if text:
            turns.append({"speaker": transcription.get("speakerLabel"), "text": text, "words": words,
                          "start": words[0]["start"] if words else None, "end": words[-1]["end"] if words else None})
    return turns


class GeminiTranscriber:
    # Published rates observed 3 Oct 2026: audio input USD 2.00 per 1M tokens; blended about USD 0.005 per minute.
    # https://ai.google.dev/gemini-api/docs/pricing
    model_id = GEMINI_ASR["model"]
    usd_per_million_audio = 2.0
    usd_per_minute_floor = 0.005  # The provider does not report output tokens for this model.
    busy_retry_delays = (5, 15, 45)

    def __init__(self, store, client=None):
        if os.getenv("PILOT_ALLOW_REMOTE") != "1":
            raise ValueError("Remote transcription is disabled. Explicit local opt-in is required.")
        self.key = os.getenv("GEMINI_API_KEY")
        if not self.key:
            raise ValueError("Set GEMINI_API_KEY in the ignored local .env; never in chat or Git.")
        self.store, self.budget = store, Budget(store)
        self.client = client or httpx.Client(timeout=300)
        self.fx_with_buffer = 125.0

    def cost(self, audio_tokens, minutes):
        return max(audio_tokens * self.usd_per_million_audio / 1e6, minutes * self.usd_per_minute_floor) * self.fx_with_buffer

    def transcribe(self, call, force=False):
        cid = call["call_id"]
        meta = read_json(self.store.path("audio", cid + ".json"))
        fingerprint = digest([meta["sha256"], GEMINI_ASR])
        path = self.store.path("asr", "gemini", cid + ".json")
        if path.exists() and not force and read_json(path)["fingerprint"] == fingerprint:
            return read_json(path)
        minutes = meta["duration_seconds"] / 60
        body = {"contents": [{"role": "user", "parts": [{"inlineData": {
                    "mimeType": "audio/ogg", "data": base64.b64encode(opus_audio(meta["file"])).decode()}}]}],
                "generationConfig": {"audioTranscriptionConfig": GEMINI_ASR["config"]}}
        start = time.monotonic()
        try:
            # Reserve twice the expected cost: audio token rates differ between Google's pages (25 vs 32 per second).
            with self.budget.reserve(2 * self.cost(meta["duration_seconds"] * 32, minutes), call_id=cid,
                                     purpose="asr:" + self.model_id) as reservation:
                response, retries = post_with_busy_retries(
                    self.client, URL.format(model=self.model_id), reservation, self.busy_retry_delays,
                    headers={"x-goog-api-key": self.key}, json=body)
                response.raise_for_status()
                result = response.json()
                audio_tokens = result.get("usageMetadata", {}).get("promptTokenCount")
                if type(audio_tokens) is not int or audio_tokens < 0:
                    raise RuntimeError("Provider omitted usage; conservative budget reservation retained")
                cost = self.cost(audio_tokens, minutes)
                reservation.settle(cost, input_tokens=audio_tokens, busy_retries=retries, call_id=cid,
                                   cost_basis="max(audio tokens at USD 2/M, USD 0.005/min published rate) at INR 125/USD")
                if result["candidates"][0].get("finishReason") not in ("STOP", None):
                    raise ValueError("Remote transcription incomplete; usage has been recorded")
                turns = parse_turns(result)
        except BaseException as exc:
            self.store.event(stage="transcribe", call_id=cid, system=self.model_id, model=self.model_id,
                             status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                             error_type=type(exc).__name__, wall_seconds=time.monotonic() - start, external_cost_inr=0)
            raise
        artifact = {"call_id": cid, "fingerprint": fingerprint, "system": GEMINI_ASR, "audio_sha256": meta["sha256"],
                    "duration_seconds": meta["duration_seconds"], "speakers": sorted({t["speaker"] for t in turns} - {None}),
                    "text": " ".join(t["text"] for t in turns), "turns": turns,
                    "flags": [] if turns else ["no_speech_transcribed"]}
        write_json(path, artifact)
        self.store.event(stage="transcribe", call_id=cid, system=self.model_id, model=self.model_id, status="success",
                         fingerprint=fingerprint, audio_seconds=meta["duration_seconds"],
                         wall_seconds=time.monotonic() - start, external_cost_inr=cost)
        return artifact
