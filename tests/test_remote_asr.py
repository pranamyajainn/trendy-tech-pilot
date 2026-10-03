import httpx
import pytest

from trendytech_pilot import remote_asr
from trendytech_pilot.remote_asr import GeminiTranscriber
from trendytech_pilot.storage import Store, read_json, write_json

RESPONSE = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
    {"text": "Hello, calling about the course.", "audioTranscription": {"speakerLabel": "spk:0", "words": [
        {"word": "Hello,", "startOffset": "0.4s", "endOffset": "0.8s"},
        {"word": "course.", "startOffset": "2s", "endOffset": "2.5s"}]}},
    {"text": "Yes.", "audioTranscription": {"speakerLabel": "spk:1", "words": [
        {"word": "Yes.", "startOffset": "3s", "endOffset": "3.2s"}]}}]}}],
    "usageMetadata": {"promptTokenCount": 1500}}


def transcriber(tmp_path, monkeypatch, handler):
    monkeypatch.setenv("PILOT_ALLOW_REMOTE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(remote_asr, "opus_audio", lambda path: b"synthetic-opus")
    store = Store(tmp_path)
    write_json(store.path("audio", "Ctest.json"), {"sha256": "synthetic", "file": "synthetic.wav", "duration_seconds": 60})
    return GeminiTranscriber(store, httpx.Client(transport=httpx.MockTransport(handler))), store


def test_turns_speakers_and_published_minimum_rate_are_recorded(tmp_path, monkeypatch):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=RESPONSE)
    asr, store = transcriber(tmp_path, monkeypatch, respond)
    artifact = asr.transcribe({"call_id": "Ctest"})
    assert artifact["speakers"] == ["spk:0", "spk:1"] and artifact["text"] == "Hello, calling about the course. Yes."
    assert artifact["turns"][0]["start"] == 0.4 and artifact["turns"][1]["end"] == 3.2
    # 1,500 audio tokens cost less than the published per-minute rate, which therefore applies.
    [entry] = read_json(store.path("api-budget.json"))["requests"].values()
    assert entry["status"] == "usage_reported" and entry["inr"] == pytest.approx(0.005 * 125)
    [event] = store.events()
    assert event["stage"] == "transcribe" and event["external_cost_inr"] == pytest.approx(0.625)
    asr.transcribe({"call_id": "Ctest"})
    assert len(requests) == 1  # Same audio and settings: the stored transcript is reused.


def test_missing_usage_keeps_the_reservation_and_records_the_failure(tmp_path, monkeypatch):
    asr, store = transcriber(tmp_path, monkeypatch, lambda request: httpx.Response(200, json={"candidates": []}))
    with pytest.raises(RuntimeError, match="omitted usage"):
        asr.transcribe({"call_id": "Ctest"})
    [entry] = read_json(store.path("api-budget.json"))["requests"].values()
    assert entry["status"] == "uncertain_reserve_retained"
    assert [e["status"] for e in store.events()] == ["failed"]
    assert not store.path("asr", "gemini", "Ctest.json").exists()


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
