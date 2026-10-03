import json

import httpx
import pytest

from trendytech_pilot import resolve
from trendytech_pilot.resolve import (
    GeminiResolver,
    Resolution,
    build_consensus,
    candidate_order,
    disputed_spans,
    vote,
)
from trendytech_pilot.storage import Store, read_json, write_json

SEGMENTS = [{"id": i, "text": t, "start": i * 2.0, "end": i * 2.0 + 2}
            for i, t in enumerate(["hello there", "two clubs", "data bricks lab", "fine thanks", "see you"])]
GEMINI = {"fingerprint": "gemini", "text": "hello there two clouds databricks lab five thanks see you monday"}
SARVAM = {"fingerprint": "sarvam", "text": "hello there two clouds data bricks lab fine thanks see you tuesday",
          "speakers": ["sarvam:0", "sarvam:1"],
          "turns": [{"speaker": "sarvam:0", "text": "hello there two clouds data bricks lab"},
                    {"speaker": "sarvam:1", "text": "fine thanks see you tuesday"}]}


def letter(call_id, span_id, system):
    return "ABC"[candidate_order(call_id, span_id).index(system)]


def test_majority_decides_where_two_systems_agree_and_only_three_way_splits_are_disputed():
    rows = {r["id"]: r["outcome"] for r in vote(SEGMENTS, GEMINI["text"], SARVAM["text"], 0.9)}
    # 0: all agree. 1: Gemini and Sarvam outvote Whisper. 2: formatting only. 3: Whisper and Sarvam agree.
    # 4: three different endings ("see you", "monday", "tuesday").
    assert rows == {0: "agreed", 1: "majority_gemini_sarvam", 2: "agreed", 3: "majority_whisper", 4: "disputed"}


def test_consensus_uses_majority_text_and_copies_the_chosen_candidate():
    rows = vote(SEGMENTS, GEMINI["text"], SARVAM["text"], 0.9)
    spans = disputed_spans(SEGMENTS, rows)
    assert [s["segment_ids"] for s in spans] == [[4]]
    resolution = Resolution(speakers=[{"label": "sarvam:0", "role": "agent"}, {"label": "sarvam:1", "role": "prospect"}],
                            spans=[{"span_id": 0, "choice": letter("Ctest", 0, "whisper"), "text": "retyped",
                                    "unclear": False}])
    out, dropped = build_consensus("Ctest", SEGMENTS, rows, spans, resolution, {0: "sarvam:0", 1: "sarvam:0", 4: "sarvam:1"})
    assert [(s["id"], s["source"], s["text"]) for s in out] == [
        (0, "agreed", "hello there"), (1, "majority_gemini_sarvam", "two clouds"), (2, "agreed", "data bricks lab"),
        (3, "majority_whisper", "fine thanks"), (4, "resolved_whisper", "see you")]
    assert out[0]["role"] == "agent" and out[4]["role"] == "prospect" and dropped == []


def test_short_text_must_match_exactly_while_long_text_tolerates_formatting():
    assert not resolve.similar("fine thanks", "five thanks", 0.9)
    assert resolve.similar("Data bricks lab", "databricks lab", 0.9)
    assert resolve.similar("we will share the curriculum on whatsapp today", "we will share the curriculam on whatsapp today", 0.9)


def test_candidate_order_is_a_stable_shuffle_of_all_three_systems():
    orders = {tuple(candidate_order("Ctest", span)) for span in range(12)}
    assert all(sorted(o) == ["gemini", "sarvam", "whisper"] for o in orders) and len(orders) > 1
    assert candidate_order("Ctest", 3) == candidate_order("Ctest", 3)


def resolver(tmp_path, monkeypatch, decisions):
    monkeypatch.setenv("PILOT_ALLOW_REMOTE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(resolve, "opus_audio", lambda path: b"synthetic-opus")
    store = Store(tmp_path)
    write_json(store.path("audio", "Ctest.json"), {"file": "synthetic.wav", "duration_seconds": 10})
    body = {"speakers": [{"label": "sarvam:0", "role": "agent"}], "spans": decisions}

    def respond(request):
        return httpx.Response(200, json={"usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 80,
                                                           "thoughtsTokenCount": 300},
                                         "candidates": [{"finishReason": "STOP", "content": {"parts": [
                                             {"text": "reasoning", "thought": True}, {"text": json.dumps(body)}]}}]})
    return GeminiResolver(store, httpx.Client(transport=httpx.MockTransport(respond))), store


def test_resolver_writes_consensus_and_settles_reported_usage(tmp_path, monkeypatch):
    decision = {"span_id": 0, "choice": letter("Ctest", 0, "sarvam"), "text": "", "unclear": False}
    res, store = resolver(tmp_path, monkeypatch, [decision])
    artifact = res.resolve({"call_id": "Ctest"}, {"fingerprint": "whisper", "segments": SEGMENTS}, GEMINI, SARVAM)
    assert artifact["decisions"]["resolved_sarvam"] == 1 and artifact["segment_outcomes"]["disputed"] == 1
    assert artifact["segments"][-1]["text"] == "see you tuesday"
    [entry] = read_json(store.path("api-budget.json"))["requests"].values()
    assert entry["status"] == "usage_reported" and entry["inr"] == pytest.approx(res.cost(900, 380))
    assert artifact["model"] == "gemini-3.1-pro-preview"  # Disputed audio goes to Pro.


def test_resolver_rejects_an_incomplete_decision_list(tmp_path, monkeypatch):
    res, store = resolver(tmp_path, monkeypatch, [])
    with pytest.raises(ValueError, match="every disputed span"):
        res.resolve({"call_id": "Ctest"}, {"fingerprint": "whisper", "segments": SEGMENTS}, GEMINI, SARVAM)
    assert [e["status"] for e in store.events()] == ["failed"]
    assert not store.path("asr", "consensus", "Ctest.json").exists()


def test_roles_without_disputes_are_mapped_by_the_cheaper_model(tmp_path, monkeypatch):
    res, store = resolver(tmp_path, monkeypatch, [])
    agreeing = {**SARVAM, "text": "hello there two clubs data bricks lab fine thanks see you"}
    artifact = res.resolve({"call_id": "Ctest"}, {"fingerprint": "whisper", "segments": SEGMENTS},
                           {**GEMINI, "text": agreeing["text"]}, agreeing)
    assert artifact["disputed_spans"] == 0 and artifact["model"] == "gemini-3.8-flash"
    [entry] = read_json(store.path("api-budget.json"))["requests"].values()
    assert entry["inr"] == pytest.approx(res.cost(900, 380, "gemini-3.8-flash"))
