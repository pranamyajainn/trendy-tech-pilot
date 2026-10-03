import json

import httpx
import pytest

from trendytech_pilot import resolve
from trendytech_pilot.consensus import align_to_segments, disputed_spans
from trendytech_pilot.resolve import GeminiResolver, Resolution, a_is_whisper, build_consensus
from trendytech_pilot.storage import Store, read_json, write_json

SEGMENTS = [{"id": i, "text": t, "start": i * 2.0, "end": i * 2.0 + 2}
            for i, t in enumerate(["hello there", "two clubs", "only on azure", "fine thanks"])]
GEMINI = {"fingerprint": "gemini", "text": "hello there two clouds only for azure fine thanks",
          "turns": [{"speaker": "spk:0", "text": "hello there two clouds only for azure"},
                    {"speaker": "spk:1", "text": "fine thanks"}]}


def choose(call_id, span_id, system):
    """The A/B letter that points at a given system for this span's randomised order."""
    return "A" if a_is_whisper(call_id, span_id) == (system == "whisper") else "B"


def test_consensus_copies_the_chosen_candidate_and_keeps_agreed_text():
    rows = align_to_segments(SEGMENTS, GEMINI["text"])
    spans = disputed_spans(SEGMENTS, rows)
    # The model's retyped text is ignored when it picks a candidate.
    resolution = Resolution(speakers=[{"label": "spk:0", "role": "agent"}, {"label": "spk:1", "role": "prospect"}],
                            spans=[{"span_id": 0, "choice": choose("Ctest", 0, "gemini"), "text": "retyped", "unclear": False}])
    out, dropped = build_consensus("Ctest", SEGMENTS, rows, spans, resolution, {0: "spk:0", 1: "spk:0", 2: "spk:0", 3: "spk:1"})
    assert [(s["id"], s["source"], s["text"], s["role"]) for s in out] == [
        (0, "agreed", "hello there", "agent"), (1, "chose_gemini", "two clouds only for azure", "agent"),
        (3, "agreed", "fine thanks", "prospect")]
    assert out[1]["merged_ids"] == [1, 2] and (out[1]["start"], out[1]["end"]) == (2.0, 6.0) and dropped == []


def test_a_span_with_no_speech_is_dropped_not_kept_as_empty_evidence():
    rows = align_to_segments(SEGMENTS, GEMINI["text"])
    spans = disputed_spans(SEGMENTS, rows)
    resolution = Resolution(speakers=[], spans=[{"span_id": 0, "choice": "neither", "text": " ", "unclear": False}])
    out, dropped = build_consensus("Ctest", SEGMENTS, rows, spans, resolution, {})
    assert [s["id"] for s in out] == [0, 3] and dropped == [[1, 2]]


def resolver(tmp_path, monkeypatch, decisions):
    monkeypatch.setenv("PILOT_ALLOW_REMOTE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(resolve, "opus_audio", lambda path: b"synthetic-opus")
    store = Store(tmp_path)
    write_json(store.path("audio", "Ctest.json"), {"file": "synthetic.wav", "duration_seconds": 8})
    body = {"speakers": [{"label": "spk:0", "role": "agent"}], "spans": decisions}

    def respond(request):
        return httpx.Response(200, json={"usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 80,
                                                           "thoughtsTokenCount": 300},
                                         "candidates": [{"finishReason": "STOP", "content": {"parts": [
                                             {"text": "reasoning", "thought": True}, {"text": json.dumps(body)}]}}]})
    return GeminiResolver(store, httpx.Client(transport=httpx.MockTransport(respond))), store


def test_resolver_writes_consensus_and_settles_reported_usage(tmp_path, monkeypatch):
    decision = {"span_id": 0, "choice": choose("Ctest", 0, "whisper"), "text": "two clubs only on azure", "unclear": False}
    res, store = resolver(tmp_path, monkeypatch, [decision])
    artifact = res.resolve({"call_id": "Ctest"}, {"fingerprint": "whisper", "segments": SEGMENTS}, GEMINI)
    assert artifact["decisions"] == {"agreed": 2, "chose_whisper": 1} and artifact["speaker_roles"] == {"spk:0": "agent"}
    [entry] = read_json(store.path("api-budget.json"))["requests"].values()
    assert entry["status"] == "usage_reported" and entry["inr"] == pytest.approx(res.cost(900, 380))
    assert store.path("asr", "consensus", "Ctest.json").exists()


def test_resolver_rejects_an_incomplete_decision_list(tmp_path, monkeypatch):
    res, store = resolver(tmp_path, monkeypatch, [])
    with pytest.raises(ValueError, match="every disputed span"):
        res.resolve({"call_id": "Ctest"}, {"fingerprint": "whisper", "segments": SEGMENTS}, GEMINI)
    assert [e["status"] for e in store.events()] == ["failed"]
    assert not store.path("asr", "consensus", "Ctest.json").exists()
