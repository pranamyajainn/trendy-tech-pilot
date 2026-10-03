import json

import httpx
import pytest

from trendytech_pilot import remote_asr
from trendytech_pilot.remote_asr import GeminiTranscriber
from trendytech_pilot.storage import Store, read_json, write_json

RESPONSE = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
    {"text": "thinking", "thought": True},
    {"text": json.dumps({"transcript": " Hello, calling about the course. Yes. "})}]}}],
    "usageMetadata": {"promptTokenCount": 2000, "candidatesTokenCount": 60, "thoughtsTokenCount": 40}}


def transcriber(tmp_path, monkeypatch, handler):
    monkeypatch.setenv("PILOT_ALLOW_REMOTE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(remote_asr, "opus_audio", lambda path: b"synthetic-opus")
    store = Store(tmp_path)
    write_json(store.path("audio", "Ctest.json"), {"sha256": "synthetic", "file": "synthetic.wav", "duration_seconds": 60})
    return GeminiTranscriber(store, httpx.Client(transport=httpx.MockTransport(handler))), store


def test_verbatim_transcript_and_token_cost_are_recorded(tmp_path, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=RESPONSE)
    asr, store = transcriber(tmp_path, monkeypatch, respond)
    artifact = asr.transcribe({"call_id": "Ctest"})
    assert artifact["text"] == "Hello, calling about the course. Yes." and "turns" not in artifact
    [entry] = read_json(store.path("api-budget.json"))["requests"].values()
    assert entry["status"] == "usage_reported" and entry["inr"] == pytest.approx(asr.cost(2000, 100))
    asr.transcribe({"call_id": "Ctest"})
    assert len(requests) == 1  # Same audio and settings: the stored transcript is reused.


def test_cut_off_transcript_is_rejected_after_recording_usage(tmp_path, monkeypatch):
    body = {**RESPONSE, "candidates": [{**RESPONSE["candidates"][0], "finishReason": "MAX_TOKENS"}]}
    asr, store = transcriber(tmp_path, monkeypatch, lambda request: httpx.Response(200, json=body))
    with pytest.raises(ValueError, match="cut-off"):
        asr.transcribe({"call_id": "Ctest"})
    [entry] = read_json(store.path("api-budget.json"))["requests"].values()
    assert entry["status"] == "usage_reported" and [e["status"] for e in store.events()] == ["failed"]
    assert not store.path("asr", "gemini", "Ctest.json").exists()


def test_missing_usage_keeps_the_reservation_and_records_the_failure(tmp_path, monkeypatch):
    asr, store = transcriber(tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"candidates": []}))
    with pytest.raises(RuntimeError, match="omitted usage"):
        asr.transcribe({"call_id": "Ctest"})
    [entry] = read_json(store.path("api-budget.json"))["requests"].values()
    assert entry["status"] == "uncertain_reserve_retained"


def test_remote_transcription_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PILOT_ALLOW_REMOTE", raising=False)
    with pytest.raises(ValueError, match="disabled"):
        GeminiTranscriber(Store(tmp_path))


def test_sarvam_output_becomes_speaker_turns_and_keyterms_are_not_sent():
    from trendytech_pilot.remote_sarvam import SARVAM_ASR, parse_output
    turns, text = parse_output({"transcript": "Hello. Yes.", "diarized_transcript": {"entries": [
        {"transcript": "Hello.", "start_time_seconds": 0.9, "end_time_seconds": 1.5, "speaker_id": "0"},
        {"transcript": " ", "start_time_seconds": 2, "end_time_seconds": 2.1, "speaker_id": "1"},
        {"transcript": "Yes.", "start_time_seconds": 3, "end_time_seconds": 3.4, "speaker_id": "1"}]}})
    assert text == "Hello. Yes." and [(t["speaker"], t["text"]) for t in turns] == [("sarvam:0", "Hello."), ("sarvam:1", "Yes.")]
    assert "keyterms" not in SARVAM_ASR["job_parameters"]
