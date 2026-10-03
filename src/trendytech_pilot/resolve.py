"""Three-system transcript consensus: majority agreement first, then audio-grounded resolution of the rest.

Whisper (local), Gemini Transcribe and Sarvam Saaras come from different model families. Where two agree, the
majority text is used without another model call. Only where all three disagree does Gemini Pro listen to the
call and choose a candidate; candidates are shown in a per-span random order and the chosen text is copied by
this code rather than retyped by the model.
"""

import base64
import json
import os
import time
from collections import Counter
from difflib import SequenceMatcher
from typing import Literal

import httpx

from .budget import Budget
from .consensus import align_to_segments, compact, segment_speakers, transcript_agreement
from .remote import post_with_busy_retries, provider_schema
from .remote_asr import GEMINI_ASR, URL, opus_audio
from .remote_sarvam import SARVAM_ASR
from .schema import StrictModel
from .storage import digest, read_json, write_json

SYSTEMS = ("whisper", "gemini", "sarvam")


class SpanDecision(StrictModel):
    span_id: int
    choice: Literal["A", "B", "C", "combined", "neither"]
    text: str
    unclear: bool


class SpeakerRole(StrictModel):
    label: str
    role: Literal["agent", "prospect", "other", "unknown"]


class Resolution(StrictModel):
    speakers: list[SpeakerRole]
    spans: list[SpanDecision]


INSTRUCTIONS = """You are checking three automatic transcripts (A, B, C) of a recorded phone call between an agent
of an IT training company (TrendyTech) and a prospect or enrolled learner. Speech is Indian English with some
Hindi. Each listed span is a stretch where the three transcripts disagree. Listen to the audio around the given
time (times are approximate, within a few seconds) and decide which candidate matches what was actually said.
- Answer "A", "B" or "C" for the candidate that is right or closest. Prefer one of them.
- Answer "combined" only when a correct transcript needs words from more than one candidate, and "neither"
  only when all are clearly wrong and you can hear the words clearly. Only then write the text yourself.
- Your text must be verbatim: keep fillers, repetitions and false starts; do not paraphrase, fix grammar,
  translate, or reformat numbers. Write [unclear] for words you cannot make out and set unclear to true.
  Never add words you cannot hear. If there is no speech in the span, choose "neither" with empty text.
- Copy the chosen candidate's text into "text" as well, unchanged.
For each speaker label, say whether it is the company agent, the prospect or learner, another person (for
example a voicemail system or a third participant), or unknown, using the sample lines.
Return JSON only."""
# Gemini 3.5 Flash judges: a different model from Gemini 3.8 Flash, which wrote one of the candidates. Gemini Pro
# was used first but is capped at 250 requests per day on this account (3 Oct 2026).
RESOLVER = {"model": "gemini-3.5-flash", "roles_model": "gemini-3.5-flash", "version": "resolver-v5",
            "threshold": 0.9,
            "thinking": "low", "instructions_sha256": digest(INSTRUCTIONS), "audio": "ogg/opus 24 kbps mono",
            "systems": {"gemini": GEMINI_ASR["version"], "sarvam": SARVAM_ASR["version"]}}
SCHEMA = provider_schema(Resolution.model_json_schema())


def clock(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def similar(a, b, threshold):
    """Formatting-tolerant agreement. Short text must match exactly: one letter can be the whole meaning
    ("fine" or "five"), while in longer stretches small differences are mostly spacing and punctuation."""
    a, b = compact(a), compact(b)
    if max(len(a), len(b)) < 12:
        return a == b
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= threshold


def vote(segments, gemini_text, sarvam_text, threshold):
    """Per Whisper segment: the three systems' texts for the same speech and the majority outcome."""
    g_rows = {r["id"]: r for r in align_to_segments(segments, gemini_text)}
    s_rows = {r["id"]: r for r in align_to_segments(segments, sarvam_text)}
    rows = []
    for segment in segments:
        texts = {"whisper": segment["text"], "gemini": g_rows[segment["id"]]["other_text"],
                 "sarvam": s_rows[segment["id"]]["other_text"]}
        wg = similar(texts["whisper"], texts["gemini"], threshold)
        ws = similar(texts["whisper"], texts["sarvam"], threshold)
        if wg and ws:
            outcome = "agreed"
        elif wg or ws:
            outcome = "majority_whisper"
        elif similar(texts["gemini"], texts["sarvam"], threshold):
            outcome = "majority_gemini_sarvam"
        else:
            outcome = "disputed"
        rows.append({"id": segment["id"], "texts": texts, "outcome": outcome})
    return rows


def disputed_spans(segments, rows):
    """Consecutive disputed segments become one span, so the resolver hears complete stretches of speech."""
    by_id = {s["id"]: s for s in segments}
    spans, last = [], None
    for position, row in enumerate(rows):
        if row["outcome"] != "disputed":
            continue
        if last is None or position != last + 1:
            spans.append({"segment_ids": [], "texts": {system: [] for system in SYSTEMS}})
        spans[-1]["segment_ids"].append(row["id"])
        for system in SYSTEMS:
            spans[-1]["texts"][system].append(row["texts"][system])
        last = position
    for index, span in enumerate(spans):
        span.update(span_id=index, start=by_id[span["segment_ids"][0]]["start"],
                    end=by_id[span["segment_ids"][-1]]["end"],
                    texts={system: " ".join(t for t in texts if t) for system, texts in span["texts"].items()})
    return spans


def candidate_order(call_id, span_id):
    """A deterministic per-span shuffle of the three systems behind letters A, B and C."""
    return sorted(SYSTEMS, key=lambda system: digest([call_id, span_id, system, "order"]))


def resolver_prompt(call_id, spans, turns):
    samples = {}
    for turn in turns:
        samples.setdefault(turn["speaker"], [])
        if len(samples[turn["speaker"]]) < 3:
            samples[turn["speaker"]].append(turn["text"][:140])
    listed = []
    for span in spans:
        order = candidate_order(call_id, span["span_id"])
        listed.append({"span_id": span["span_id"], "time": f"{clock(span['start'])}-{clock(span['end'] + 1)}",
                       **{letter: span["texts"][system] for letter, system in zip("ABC", order)}})
    return (INSTRUCTIONS + "\n\nSpeaker labels with sample lines:\n" + json.dumps(samples, ensure_ascii=False)
            + "\n\nSpans:\n" + "\n".join(json.dumps(s, ensure_ascii=False) for s in listed))


def build_consensus(call_id, segments, rows, spans, resolution, speaker_by_segment):
    """Majority text where two systems agree; each disputed span becomes one segment carrying the decision."""
    decisions = {d.span_id: d for d in resolution.spans}
    roles = {s.label: s.role for s in resolution.speakers}
    span_of = {sid: span for span in spans for sid in span["segment_ids"]}
    by_id = {r["id"]: r for r in rows}
    out, dropped = [], []
    for segment in segments:
        span = span_of.get(segment["id"])
        label = speaker_by_segment.get(segment["id"])
        if span is None:
            row = by_id[segment["id"]]
            text = row["texts"]["gemini"] if row["outcome"] == "majority_gemini_sarvam" else segment["text"]
            if text.strip():
                out.append({"id": segment["id"], "start": segment["start"], "end": segment["end"], "text": text.strip(),
                            "source": row["outcome"], "unclear": False, "speaker_label": label,
                            "role": roles.get(label, "unknown")})
            continue
        if segment["id"] != span["segment_ids"][0]:
            continue
        decision = decisions[span["span_id"]]
        if decision.choice in ("A", "B", "C"):
            system = candidate_order(call_id, span["span_id"])["ABC".index(decision.choice)]
            text, source = span["texts"][system], "resolved_" + system
        else:
            text, source = decision.text, "resolved_" + decision.choice
        labels = Counter(speaker_by_segment[sid] for sid in span["segment_ids"] if speaker_by_segment.get(sid))
        label = labels.most_common(1)[0][0] if labels else None
        if not text.strip():
            dropped.append(span["segment_ids"])
            continue
        out.append({"id": span["segment_ids"][0], "start": span["start"], "end": span["end"], "text": text.strip(),
                    "source": source, "unclear": decision.unclear or "[unclear]" in text,
                    "merged_ids": span["segment_ids"], "speaker_label": label, "role": roles.get(label, "unknown")})
    return out, dropped


# Published rates observed 3 Oct 2026, USD per 1M input/output tokens (output incl. thinking).
# https://ai.google.dev/gemini-api/docs/pricing
RATES = {"gemini-3.1-pro-preview": (2.0, 12.0), "gemini-3.8-flash": (0.75, 3.75), "gemini-3.5-flash": (1.5, 9.0)}


class GeminiResolver:
    model_id = RESOLVER["model"]
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

    def cost(self, input_tokens, output_tokens, model=RESOLVER["model"]):
        rate_in, rate_out = RATES[model]
        return (input_tokens * rate_in + output_tokens * rate_out) / 1e6 * self.fx_with_buffer

    def resolve(self, call, whisper, gemini, sarvam, force=False):
        cid = call["call_id"]
        fingerprint = digest([whisper["fingerprint"], gemini["fingerprint"], sarvam["fingerprint"], RESOLVER])
        path = self.store.path("asr", "consensus", cid + ".json")
        if path.exists() and not force and read_json(path)["fingerprint"] == fingerprint:
            return read_json(path)
        segments = whisper["segments"]
        rows = vote(segments, gemini["text"], sarvam["text"], RESOLVER["threshold"])
        spans = disputed_spans(segments, rows)
        # Speaker labels come from Sarvam's diarization; Gemini transcribes without speaker turns.
        speaker_by_segment = segment_speakers(segments, sarvam["turns"])
        start, cost, usage = time.monotonic(), 0.0, {}
        if spans or sarvam["speakers"]:
            resolution, cost, usage = self._ask(cid, spans, sarvam["turns"], start)
        else:
            resolution = Resolution(speakers=[], spans=[])
        consensus, dropped = build_consensus(cid, segments, rows, spans, resolution, speaker_by_segment)
        artifact = {"call_id": cid, "fingerprint": fingerprint, "resolver": RESOLVER,
                    "whisper_fingerprint": whisper["fingerprint"], "gemini_fingerprint": gemini["fingerprint"],
                    "sarvam_fingerprint": sarvam["fingerprint"],
                    "word_agreement": {"whisper_gemini": transcript_agreement(segments, gemini["text"]),
                                       "whisper_sarvam": transcript_agreement(segments, sarvam["text"])},
                    "segment_outcomes": dict(Counter(r["outcome"] for r in rows)), "disputed_spans": len(spans),
                    "decisions": dict(Counter(s["source"] for s in consensus)),
                    "speaker_roles": {s.label: s.role for s in resolution.speakers}, "dropped_segments": dropped,
                    "segments": consensus, **usage}
        write_json(path, artifact)
        self.store.event(stage="resolve", call_id=cid, status="success", fingerprint=fingerprint,
                         disputed_spans=len(spans), wall_seconds=time.monotonic() - start, external_cost_inr=cost,
                         **{"model": self.model_id, **usage})
        return artifact

    def _ask(self, cid, spans, turns, start):
        """The judge listens to the audio when spans are disputed; without disputed spans only speaker roles are
        mapped, from sample lines, and no audio is sent. An incomplete or invalid answer gets one retry that
        names the problem."""
        model = RESOLVER["model"] if spans else RESOLVER["roles_model"]
        prompt, feedback, total, usage = resolver_prompt(cid, spans, turns), "", 0.0, {}
        meta = read_json(self.store.path("audio", cid + ".json"))
        audio = [{"inlineData": {"mimeType": "audio/ogg", "data": base64.b64encode(opus_audio(meta["file"])).decode()}}]
        try:
            for attempt in range(2):
                text, cost, usage = self._request(cid, model, (audio if spans else []), prompt + feedback,
                                                  meta["duration_seconds"] * 32 if spans else 0)
                total += cost
                try:
                    resolution = Resolution.model_validate(json.loads(text))
                    decided = sorted(d.span_id for d in resolution.spans)
                    if decided != [s["span_id"] for s in spans]:
                        raise ValueError(f"decided span ids {decided} but expected {[s['span_id'] for s in spans]}")
                    return resolution, total, usage
                except ValueError as exc:
                    if attempt:
                        raise ValueError(f"Resolver answer invalid twice: {str(exc)[:200]}") from None
                    feedback = (f"\n\nYour previous answer was invalid ({str(exc)[:300]}). Return exactly one decision "
                                "for every listed span_id, as valid JSON.")
        except BaseException as exc:
            self.store.event(stage="resolve", call_id=cid, model=model, error_type=type(exc).__name__,
                             status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                             wall_seconds=time.monotonic() - start, external_cost_inr=total)
            raise

    def _request(self, cid, model, audio_parts, prompt, audio_tokens):
        body = {"contents": [{"role": "user", "parts": audio_parts + [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": SCHEMA,
                                     "thinkingConfig": {"thinkingLevel": RESOLVER["thinking"]},
                                     "maxOutputTokens": self.max_output_tokens}}
        with self.budget.reserve(self.cost(audio_tokens + len(prompt.encode()) + 1024, self.max_output_tokens, model),
                                 call_id=cid, purpose="resolve:" + model) as reservation:
            response, retries = post_with_busy_retries(self.client, URL.format(model=model), reservation,
                                                       self.busy_retry_delays, headers={"x-goog-api-key": self.key},
                                                       json=body)
            response.raise_for_status()
            result = response.json()
            meta_usage = result.get("usageMetadata", {})
            n_in = meta_usage.get("promptTokenCount")
            n_out = meta_usage.get("candidatesTokenCount", 0) + meta_usage.get("thoughtsTokenCount", 0)
            if type(n_in) is not int or n_in < 0 or type(n_out) is not int or n_out < 0:
                raise RuntimeError("Provider omitted usage; conservative budget reservation retained")
            cost, usage = self.cost(n_in, n_out, model), {"input_tokens": n_in, "output_tokens": n_out, "model": model}
            reservation.settle(cost, call_id=cid, busy_retries=retries, **usage)
            candidate = result["candidates"][0]
            if candidate.get("finishReason") not in ("STOP", None):
                raise ValueError("Resolver output incomplete; usage has been recorded")
            return "".join(p.get("text", "") for p in candidate["content"]["parts"] if not p.get("thought")), cost, usage
