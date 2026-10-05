"""Hosted speech-to-text as an independent second transcript. It never replaces the Whisper transcript in place.

Gemini 3.8 Flash transcribes from audio with a verbatim instruction. The dedicated gemini-3.5-transcribe model was
used first, but this project's quota for it is 100 requests per day (3 Oct 2026), too few for 300 calls; its
outputs for 96 development calls are kept under data/superseded/.
"""

import base64
import json
import os
import re
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


# Method v3 transcript (5 Oct 2026): one Gemini pass gives the words and the speaker turns. Development test on 16
# calls (data/experiments/gemini-only-asr): claims from it were judged correct as often as method v2's, at about a
# third of the cost. Set up as the Gemini app is used: original audio, plain text, default thinking. Asking for
# turns inside a JSON schema made the model repeat sentences; plain labelled lines did not.
LABELLED_INSTRUCTIONS = """Transcribe this phone call word for word. It is between an agent of an IT training
company (TrendyTech) and a prospect or learner, in Indian English with some Hindi.
Start each turn on a new line with "Agent:" or "Customer:". Write exactly what is said, once, in order, including
repetitions. Write Hindi in Latin script as spoken and numbers exactly as said. Write [unclear] for words you cannot
make out, and never add words that were not spoken. Output only the transcript."""
# Long audio drifts, loops or drops passages, so calls are sent in 10-minute pieces that overlap by 30 seconds.
LABELLED_ASR = {"model": "gemini-3.8-flash", "version": "gemini-labelled-v1", "audio": "original audio as 16-bit PCM WAV",
                "thinking": "default", "chunk_seconds": 600, "overlap_seconds": 30,
                "instructions_sha256": digest(LABELLED_INSTRUCTIONS)}
# How pieces are joined, kept apart from LABELLED_ASR so a joining fix re-uses the paid pieces. v2: a cut needs a
# run of 3+ matching words; in v1 single stray matches ("okay") cut a join too late and dropped speech.
STITCH = {"version": "stitch-v2", "min_total_match": 6, "min_run": 3}
TURN = re.compile(r"\s*\**(Agent|Customer)\**\s*:\s*\**\s*(.*)")


def wav_piece(path, start, seconds):
    """A lossless PCM WAV cut of the original recording (its own sample rate and channel layout kept)."""
    return subprocess.run(["ffmpeg", "-v", "error", "-ss", str(start), "-t", str(seconds), "-i", str(path),
                           "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", "-"],
                          capture_output=True, check=True, timeout=300).stdout


def piece_starts(duration):
    size, step = LABELLED_ASR["chunk_seconds"], LABELLED_ASR["chunk_seconds"] - LABELLED_ASR["overlap_seconds"]
    starts = [0]
    while starts[-1] + size < duration:
        starts.append(starts[-1] + step)
    return starts


def parse_turns(text):
    """Labelled lines become turns; an unlabelled line continues the previous turn."""
    turns = []
    for line in text.splitlines():
        match = TURN.match(line)
        if match:
            turns.append({"role": "agent" if match[1] == "Agent" else "prospect", "text": match[2].strip()})
        elif line.strip() and turns:
            turns[-1]["text"] += " " + line.strip()
    return [t for t in turns if t["text"]]


def stitch(pieces):
    """Join overlapping pieces: words the next piece repeats from the end of the previous one are dropped, up to
    the end of the last run of 3+ matching words. A join with no clear repeated stretch is kept whole and flagged
    instead of guessed. Also returns the word position where each later piece starts."""
    from difflib import SequenceMatcher

    from .schema import normalise

    tokens, flags, joins = [], [], []  # tokens: (piece, turn index within piece, role, word)
    for p, turns in enumerate(pieces):
        new = [(p, i, t["role"], w) for i, t in enumerate(turns) for w in t["text"].split()]
        if tokens and new:
            tail, head = tokens[-150:], new[:200]
            a, b = [normalise(t[3]) for t in tail], [normalise(t[3]) for t in head]
            blocks = [m for m in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks() if m.size]
            runs = [m for m in blocks if m.size >= STITCH["min_run"]]
            if runs and sum(m.size for m in blocks) >= STITCH["min_total_match"]:
                new = new[runs[-1].b + runs[-1].size:]
            else:
                flags.append(f"join_{p}_unmatched")
            joins.append(len(tokens))
        tokens += new
    turns = []
    for p, i, role, word in tokens:
        if turns and turns[-1]["key"] == (p, i):
            turns[-1]["text"] += " " + word
        else:
            turns.append({"key": (p, i), "role": role, "text": word})
    return [{"role": t["role"], "text": t["text"]} for t in turns], flags, joins


class GeminiLabelledTranscriber(GeminiTranscriber):
    """Words and speaker turns from one model. Pieces are cached individually, so a retry after a failure does
    not pay again for the pieces that succeeded."""
    model_id = LABELLED_ASR["model"]
    max_output_tokens = 16384

    def transcribe(self, call, force=False):
        cid = call["call_id"]
        meta = read_json(self.store.path("audio", cid + ".json"))
        fingerprint = digest([meta["sha256"], LABELLED_ASR])  # identity of each paid piece
        artifact_fingerprint = digest([fingerprint, STITCH])
        path = self.store.path("asr", "gemini-labelled", cid + ".json")
        if path.exists() and not force and read_json(path)["fingerprint"] == artifact_fingerprint:
            return read_json(path)
        size = LABELLED_ASR["chunk_seconds"]
        starts = piece_starts(meta["duration_seconds"])
        start, pieces, paid_now = time.monotonic(), [], 0.0
        for offset in starts:
            piece, paid = self._piece(cid, meta, fingerprint, offset, size, force)
            pieces.append(piece)
            paid_now += paid
        turns, flags, joins = stitch([parse_turns(p["text"]) for p in pieces])
        if not turns:
            flags.append("no_speech_transcribed")
        artifact = {"call_id": cid, "fingerprint": artifact_fingerprint, "system": LABELLED_ASR, "stitch": STITCH,
                    "join_word_positions": joins, "audio_sha256": meta["sha256"],
                    "duration_seconds": meta["duration_seconds"], "pieces": [{k: p[k] for k in ("offset", "input_tokens",
                    "output_tokens", "inr")} for p in pieces], "turns": turns, "flags": flags}
        write_json(path, artifact)
        self.store.event(stage="transcribe", call_id=cid, system=LABELLED_ASR["version"], model=self.model_id,
                         status="success", fingerprint=fingerprint, audio_seconds=meta["duration_seconds"],
                         wall_seconds=time.monotonic() - start, external_cost_inr=paid_now)  # cached pieces cost nothing
        return artifact

    def _piece(self, cid, meta, fingerprint, offset, seconds, force):
        path = self.store.path("asr", "gemini-labelled-pieces", f"{cid}-{offset}.json")
        if path.exists() and not force and read_json(path)["fingerprint"] == fingerprint:
            return read_json(path), 0.0
        body = {"contents": [{"role": "user", "parts": [
                    {"inlineData": {"mimeType": "audio/wav",
                                    "data": base64.b64encode(wav_piece(meta["file"], offset, seconds)).decode()}},
                    {"text": LABELLED_INSTRUCTIONS}]}],
                "generationConfig": {"maxOutputTokens": self.max_output_tokens}}
        start, cost = time.monotonic(), 0.0
        try:
            reserve = self.cost(min(seconds, meta["duration_seconds"]) * 32 + 1000, self.max_output_tokens)
            with self.budget.reserve(reserve, call_id=cid, purpose="asr:" + LABELLED_ASR["version"], offset=offset) as res:
                response, retries = post_with_busy_retries(
                    self.client, URL.format(model=self.model_id), res, self.busy_retry_delays,
                    headers={"x-goog-api-key": self.key}, json=body)
                response.raise_for_status()
                result = response.json()
                usage = result.get("usageMetadata", {})
                n_in = usage.get("promptTokenCount")
                n_out = usage.get("candidatesTokenCount", 0) + usage.get("thoughtsTokenCount", 0)
                if type(n_in) is not int or n_in < 0 or type(n_out) is not int or n_out < 0:
                    raise RuntimeError("Provider omitted usage; conservative budget reservation retained")
                cost = self.cost(n_in, n_out)
                res.settle(cost, call_id=cid, offset=offset, busy_retries=retries, input_tokens=n_in, output_tokens=n_out)
            candidate = result["candidates"][0]
            if candidate.get("finishReason") not in ("STOP", None):
                raise ValueError("Transcription incomplete; a cut-off transcript is never accepted")
            # A finished answer with no parts is Gemini hearing no speech; the call is kept and flagged, not failed.
            parts = (candidate.get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        except BaseException as exc:
            self.store.event(stage="transcribe", call_id=cid, system=LABELLED_ASR["version"], model=self.model_id,
                             status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed", offset=offset,
                             error_type=type(exc).__name__, wall_seconds=time.monotonic() - start, external_cost_inr=cost)
            raise
        piece = {"fingerprint": fingerprint, "offset": offset, "text": text, "input_tokens": n_in,
                 "output_tokens": n_out, "inr": cost}
        write_json(path, piece)
        return piece, cost
