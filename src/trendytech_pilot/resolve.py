"""Audio-grounded resolution of transcript disagreements, and the consensus transcript built from it.

The resolver chooses between existing hypotheses for disputed stretches only; agreed text is never sent for
rewriting. Candidates are shown as A/B in a per-span random order to avoid position bias, and a chosen
candidate's text is copied by this code rather than retyped by the model.
"""

import base64
import json
import os
import time
from collections import Counter
from typing import Literal

import httpx

from .budget import Budget
from .consensus import align_to_segments, disputed_spans, segment_speakers, transcript_agreement
from .remote import post_with_busy_retries, provider_schema
from .remote_asr import URL, opus_audio
from .schema import StrictModel
from .storage import digest, read_json, write_json


class SpanDecision(StrictModel):
    span_id: int
    choice: Literal["A", "B", "combined", "neither"]
    text: str
    unclear: bool


class SpeakerRole(StrictModel):
    label: str
    role: Literal["agent", "prospect", "other", "unknown"]


class Resolution(StrictModel):
    speakers: list[SpeakerRole]
    spans: list[SpanDecision]


INSTRUCTIONS = """You are checking two automatic transcripts of a recorded phone call between an agent of an IT
training company (TrendyTech) and a prospect or enrolled learner. Speech is Indian English with some Hindi.
For each listed span, listen to the audio around the given time (times are approximate, within a few seconds)
and decide which candidate matches what was actually said.
- Answer "A" or "B" when that candidate is right or closer. Prefer one of them.
- Answer "combined" only when a correct transcript needs words from both, and "neither" only when both are
  clearly wrong and you can hear the words clearly. Only then write the text yourself.
- Your text must be verbatim: keep fillers, repetitions and false starts; do not paraphrase, fix grammar,
  translate, or reformat numbers. Write [unclear] for words you cannot make out and set unclear to true.
  Never add words you cannot hear. If there is no speech in the span, choose "neither" with empty text.
- Copy the chosen candidate's text into "text" as well, unchanged.
For each speaker label, say whether it is the company agent, the prospect or learner, another person (for
example a voicemail system or a third participant), or unknown, using the sample lines.
Return JSON only."""
RESOLVER = {"model": "gemini-3.1-pro-preview", "version": "resolver-v1", "threshold": 0.9, "thinking": "low",
            "instructions_sha256": digest(INSTRUCTIONS), "audio": "ogg/opus 24 kbps mono"}
SCHEMA = provider_schema(Resolution.model_json_schema())


def clock(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def a_is_whisper(call_id, span_id):
    return int(digest([call_id, span_id, "order"])[0], 16) < 8


def resolver_prompt(call_id, spans, turns):
    samples = {}
    for turn in turns:
        samples.setdefault(turn["speaker"], [])
        if len(samples[turn["speaker"]]) < 3:
            samples[turn["speaker"]].append(turn["text"][:140])
    listed = []
    for span in spans:
        whisper, gemini = span["base_text"], span["other_text"]
        a, b = (whisper, gemini) if a_is_whisper(call_id, span["span_id"]) else (gemini, whisper)
        listed.append({"span_id": span["span_id"], "time": f"{clock(span['start'])}-{clock(span['end'] + 1)}", "A": a, "B": b})
    return (INSTRUCTIONS + "\n\nSpeaker labels with sample lines:\n" + json.dumps(samples, ensure_ascii=False)
            + "\n\nSpans:\n" + "\n".join(json.dumps(s, ensure_ascii=False) for s in listed))


def build_consensus(call_id, segments, rows, spans, resolution, speaker_by_segment):
    """Agreed segments keep the Whisper text; each disputed span becomes one segment carrying the decision."""
    decisions = {d.span_id: d for d in resolution.spans}
    roles = {s.label: s.role for s in resolution.speakers}
    span_of = {sid: span for span in spans for sid in span["segment_ids"]}
    agreement = {r["id"]: r["char_agreement"] for r in rows}
    out, dropped = [], []
    for segment in segments:
        span = span_of.get(segment["id"])
        if span is None:
            label = speaker_by_segment.get(segment["id"])
            out.append({"id": segment["id"], "start": segment["start"], "end": segment["end"], "text": segment["text"],
                        "source": "agreed", "unclear": False, "agreement": agreement[segment["id"]],
                        "speaker_label": label, "role": roles.get(label, "unknown")})
            continue
        if segment["id"] != span["segment_ids"][0]:
            continue
        decision = decisions[span["span_id"]]
        whisper_first = a_is_whisper(call_id, span["span_id"])
        if decision.choice in ("A", "B"):
            chose_whisper = (decision.choice == "A") == whisper_first
            text, source = (span["base_text"], "chose_whisper") if chose_whisper else (span["other_text"], "chose_gemini")
        else:
            text, source = decision.text, decision.choice
        labels = Counter(speaker_by_segment[sid] for sid in span["segment_ids"] if speaker_by_segment.get(sid))
        label = labels.most_common(1)[0][0] if labels else None
        if not text.strip():
            dropped.append(span["segment_ids"])
            continue
        out.append({"id": span["segment_ids"][0], "start": span["start"], "end": span["end"], "text": text.strip(),
                    "source": source, "unclear": decision.unclear or "[unclear]" in text,
                    "agreement": min(agreement[sid] for sid in span["segment_ids"]), "merged_ids": span["segment_ids"],
                    "speaker_label": label, "role": roles.get(label, "unknown")})
    return out, dropped


class GeminiResolver:
    # Published rates observed 3 Oct 2026 (prompts up to 200k tokens): input incl. audio USD 2.00, output incl.
    # thinking USD 12.00 per 1M tokens. https://ai.google.dev/gemini-api/docs/pricing
    model_id = RESOLVER["model"]
    input_usd_per_million, output_usd_per_million = 2.0, 12.0
    max_output_tokens = 16384
    busy_retry_delays = (5, 15, 45)

    def __init__(self, store, client=None):
        if os.getenv("PILOT_ALLOW_REMOTE") != "1":
            raise ValueError("Remote resolution is disabled. Explicit local opt-in is required.")
        self.key = os.getenv("GEMINI_API_KEY")
        if not self.key:
            raise ValueError("Set GEMINI_API_KEY in the ignored local .env; never in chat or Git.")
        self.store, self.budget = store, Budget(store)
        self.client = client or httpx.Client(timeout=600)
        self.fx_with_buffer = 125.0

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.input_usd_per_million + output_tokens * self.output_usd_per_million) / 1e6 * self.fx_with_buffer

    def resolve(self, call, whisper, gemini, force=False):
        cid = call["call_id"]
        fingerprint = digest([whisper["fingerprint"], gemini["fingerprint"], RESOLVER])
        path = self.store.path("asr", "consensus", cid + ".json")
        if path.exists() and not force and read_json(path)["fingerprint"] == fingerprint:
            return read_json(path)
        segments = whisper["segments"]
        rows = align_to_segments(segments, gemini["text"])
        spans = disputed_spans(segments, rows, RESOLVER["threshold"])
        speaker_by_segment = segment_speakers(segments, gemini["turns"])
        start, cost, usage = time.monotonic(), 0.0, {}
        labels = sorted({t["speaker"] for t in gemini["turns"]} - {None})
        if spans or labels:
            prompt = resolver_prompt(cid, spans, gemini["turns"])
            meta = read_json(self.store.path("audio", cid + ".json"))
            # Speaker roles alone can be judged from sample lines; audio is needed only for disputed spans.
            audio = [{"inlineData": {"mimeType": "audio/ogg", "data": base64.b64encode(opus_audio(meta["file"])).decode()}}]
            body = {"contents": [{"role": "user", "parts": (audio if spans else []) + [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA,
                                         "thinkingConfig": {"thinkingLevel": RESOLVER["thinking"]},
                                         "maxOutputTokens": self.max_output_tokens}}
            audio_tokens = meta["duration_seconds"] * 32 if spans else 0
            reserve = self.cost(audio_tokens + len(prompt.encode()) + 1024, self.max_output_tokens)
            try:
                with self.budget.reserve(reserve, call_id=cid, purpose="resolve:" + self.model_id) as reservation:
                    response, retries = post_with_busy_retries(
                        self.client, URL.format(model=self.model_id), reservation, self.busy_retry_delays,
                        headers={"x-goog-api-key": self.key}, json=body)
                    response.raise_for_status()
                    result = response.json()
                    meta_usage = result.get("usageMetadata", {})
                    n_in = meta_usage.get("promptTokenCount")
                    n_out = meta_usage.get("candidatesTokenCount", 0) + meta_usage.get("thoughtsTokenCount", 0)
                    if type(n_in) is not int or n_in < 0 or type(n_out) is not int or n_out < 0:
                        raise RuntimeError("Provider omitted usage; conservative budget reservation retained")
                    cost, usage = self.cost(n_in, n_out), {"input_tokens": n_in, "output_tokens": n_out}
                    reservation.settle(cost, call_id=cid, busy_retries=retries, **usage)
                    candidate = result["candidates"][0]
                    if candidate.get("finishReason") not in ("STOP", None):
                        raise ValueError("Resolver output incomplete; usage has been recorded")
                    text = "".join(p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought"))
                    resolution = Resolution.model_validate(json.loads(text))
                    if sorted(d.span_id for d in resolution.spans) != [s["span_id"] for s in spans]:
                        raise ValueError("Resolver must decide every disputed span exactly once")
            except BaseException as exc:
                self.store.event(stage="resolve", call_id=cid, model=self.model_id, error_type=type(exc).__name__,
                                 status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                                 wall_seconds=time.monotonic() - start, external_cost_inr=cost)
                raise
        else:
            resolution = Resolution(speakers=[], spans=[])
        consensus, dropped = build_consensus(cid, segments, rows, spans, resolution, speaker_by_segment)
        artifact = {"call_id": cid, "fingerprint": fingerprint, "resolver": RESOLVER,
                    "whisper_fingerprint": whisper["fingerprint"], "gemini_fingerprint": gemini["fingerprint"],
                    "word_agreement": transcript_agreement(segments, gemini["text"]),
                    "disputed_spans": len(spans), "decisions": dict(Counter(s["source"] for s in consensus)),
                    "speaker_roles": {s.label: s.role for s in resolution.speakers}, "dropped_segments": dropped,
                    "segments": consensus, **usage}
        write_json(path, artifact)
        self.store.event(stage="resolve", call_id=cid, model=self.model_id, status="success", fingerprint=fingerprint,
                         disputed_spans=len(spans), wall_seconds=time.monotonic() - start, external_cost_inr=cost, **usage)
        return artifact
