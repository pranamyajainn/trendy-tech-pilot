"""Hosted speech-to-text as an independent second transcript. It never replaces the Whisper transcript in place.

Gemini 3.8 Flash transcribes from audio with a verbatim instruction. The dedicated gemini-3.5-transcribe model was
used first, but this project's quota for it is 100 requests per day (3 Oct 2026), too few for 300 calls; its
outputs for 96 development calls are kept under data/superseded/.
"""

import base64
import json
import os
import subprocess
import time

import httpx

from .budget import Budget
from .remote import post_with_busy_retries, provider_schema
from .schema import StrictModel
from .storage import digest, read_json, write_json

URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class Transcription(StrictModel):
    transcript: str


INSTRUCTIONS = """Transcribe this recorded phone call verbatim, as one continuous transcript. It is between an
agent of an IT training company (TrendyTech) and a prospect or learner, in Indian English with some Hindi.
- Write every word that is spoken, once, in the order spoken, including fillers (um, uh, hmm), repetitions and
  false starts. Do not label speakers.
- Do not summarise, paraphrase, correct grammar or translate. Write Hindi words in Latin script as spoken.
- Write numbers, prices and dates exactly as said.
- Write [unclear] for words you cannot make out. Never add words that were not spoken.
- If there is no speech, return an empty transcript.
Return JSON only."""
# Identity of the second transcript: changing any value makes stored outputs stale.
GEMINI_ASR = {"model": "gemini-3.8-flash", "version": "gemini-flash-asr-v2-plain", "audio": "ogg/opus 24 kbps mono",
              "thinking": "low", "instructions_sha256": digest(INSTRUCTIONS)}
SCHEMA = provider_schema(Transcription.model_json_schema())


def opus_audio(path):
    """Compress for upload. Gemini downsamples audio internally, so 24 kbps Opus keeps what the model hears."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-c:a", "libopus", "-b:a", "24k",
                           "-f", "ogg", "-"], capture_output=True, check=True, timeout=300).stdout


class GeminiTranscriber:
    # Published rates observed 3 Oct 2026: USD 0.75 input (audio and text) and 3.75 output incl. thinking per
    # 1M tokens. https://ai.google.dev/gemini-api/docs/pricing
    model_id = GEMINI_ASR["model"]
    input_usd_per_million, output_usd_per_million = 0.75, 3.75
    max_output_tokens = 32768
    busy_retry_delays = (5, 15, 45)

    def __init__(self, store, client=None):
        if os.getenv("PILOT_ALLOW_REMOTE") != "1":
            raise ValueError("Remote transcription is disabled. Explicit local opt-in is required.")
        self.key = os.getenv("GEMINI_API_KEY")
        if not self.key:
            raise ValueError("Set GEMINI_API_KEY in the ignored local .env; never in chat or Git.")
        self.store, self.budget = store, Budget(store)
        self.client = client or httpx.Client(timeout=600)
        self.fx_with_buffer = 125.0

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.input_usd_per_million + output_tokens * self.output_usd_per_million) / 1e6 * self.fx_with_buffer

    def transcribe(self, call, force=False):
        cid = call["call_id"]
        meta = read_json(self.store.path("audio", cid + ".json"))
        fingerprint = digest([meta["sha256"], GEMINI_ASR])
        path = self.store.path("asr", "gemini", cid + ".json")
        if path.exists() and not force and read_json(path)["fingerprint"] == fingerprint:
            return read_json(path)
        body = {"contents": [{"role": "user", "parts": [
                    {"inlineData": {"mimeType": "audio/ogg", "data": base64.b64encode(opus_audio(meta["file"])).decode()}},
                    {"text": INSTRUCTIONS}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA,
                                     "thinkingConfig": {"thinkingLevel": GEMINI_ASR["thinking"]},
                                     "maxOutputTokens": self.max_output_tokens}}
        start, cost = time.monotonic(), 0.0
        try:
            reserve = self.cost(meta["duration_seconds"] * 32 + 2048, self.max_output_tokens)
            with self.budget.reserve(reserve, call_id=cid, purpose="asr:" + self.model_id) as reservation:
                response, retries = post_with_busy_retries(
                    self.client, URL.format(model=self.model_id), reservation, self.busy_retry_delays,
                    headers={"x-goog-api-key": self.key}, json=body)
                response.raise_for_status()
                result = response.json()
                usage = result.get("usageMetadata", {})
                n_in = usage.get("promptTokenCount")
                n_out = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
                if type(n_in) is not int or n_in < 0 or type(n_out) is not int or n_out < 0:
                    raise RuntimeError("Provider omitted usage; conservative budget reservation retained")
                cost = self.cost(n_in, n_out)
                reservation.settle(cost, call_id=cid, busy_retries=retries, input_tokens=n_in, output_tokens=n_out)
                candidate = result["candidates"][0]
                if candidate.get("finishReason") not in ("STOP", None):
                    raise ValueError("Transcription incomplete; a cut-off transcript is never accepted")
                text = "".join(p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought"))
                transcript = Transcription.model_validate(json.loads(text)).transcript.strip()
        except BaseException as exc:
            self.store.event(stage="transcribe", call_id=cid, system=self.model_id, model=self.model_id,
                             status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                             error_type=type(exc).__name__, wall_seconds=time.monotonic() - start, external_cost_inr=cost)
            raise
        # No speaker turns: asking for them made the model repeat sentences across speakers. Roles come from Sarvam.
        artifact = {"call_id": cid, "fingerprint": fingerprint, "system": GEMINI_ASR, "audio_sha256": meta["sha256"],
                    "duration_seconds": meta["duration_seconds"], "text": transcript,
                    "flags": [] if transcript else ["no_speech_transcribed"], "input_tokens": n_in, "output_tokens": n_out}
        write_json(path, artifact)
        self.store.event(stage="transcribe", call_id=cid, system=self.model_id, model=self.model_id, status="success",
                         fingerprint=fingerprint, audio_seconds=meta["duration_seconds"],
                         wall_seconds=time.monotonic() - start, external_cost_inr=cost)
        return artifact
