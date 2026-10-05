from itertools import pairwise

import pytest

from trendytech_pilot import cohort
from trendytech_pilot.ensemble import tripwire
from trendytech_pilot.labelled import heard_by_whisper, timed_segments
from trendytech_pilot.remote_asr import parse_turns, piece_starts, stitch
from trendytech_pilot.storage import Store, write_json


def test_long_calls_are_cut_into_overlapping_pieces_that_reach_the_end():
    assert piece_starts(120) == [0] and piece_starts(600) == [0]
    assert piece_starts(1200) == [0, 570, 1140]  # the last piece covers 1140-1740 s, past the end
    for duration in (601, 1171, 1800, 3605):
        starts = piece_starts(duration)
        assert starts[-1] + 600 >= duration and all(b - a == 570 for a, b in pairwise(starts))


def test_labelled_lines_become_turns_and_unlabelled_lines_continue_them():
    turns = parse_turns("**Agent:** Hello, calling from the institute.\nstill the agent\nCustomer: Yes, tell me.\n\n")
    assert turns == [{"role": "agent", "text": "Hello, calling from the institute. still the agent"},
                     {"role": "prospect", "text": "Yes, tell me."}]


def test_stitching_drops_words_the_next_piece_repeats():
    first = [{"role": "agent", "text": "the fee is seventy thousand and we also have an EMI option for you"}]
    second = [{"role": "agent", "text": "we also have an EMI option for you"},
              {"role": "prospect", "text": "okay send me the details"}]
    turns, flags, joins = stitch([first, second])
    text = " ".join(t["text"] for t in turns)
    assert text.count("EMI option") == 1 and text.endswith("okay send me the details") and not flags and joins == [14]


def test_stray_single_word_matches_do_not_cut_away_new_speech():
    tail = "we discussed the fee and the batch dates and the EMI plan for next month okay"
    first = [{"role": "agent", "text": tail}]
    second = [{"role": "agent", "text": "the batch dates and the EMI plan for next month"},
              {"role": "prospect", "text": "I will check with my manager and then call you back okay"}]
    turns, _, _ = stitch([first, second])
    assert turns[-1]["text"] == "I will check with my manager and then call you back okay"


def test_a_join_without_a_clear_overlap_is_flagged_not_guessed():
    turns, flags, _ = stitch([[{"role": "agent", "text": "one two three"}], [{"role": "prospect", "text": "four five"}]])
    assert [t["text"] for t in turns] == ["one two three", "four five"] and flags == ["join_1_unmatched"]


def test_turns_take_their_time_from_the_whisper_passage_that_heard_them():
    whisper = [{"start": 0.0, "text": "Hello, calling about the course."},
               {"start": 12.5, "text": "I have five years of experience."}]
    turns = [{"role": "agent", "text": "Hello calling about the course"},
             {"role": "prospect", "text": "I have five years of experience"},
             {"role": "prospect", "text": "zzz qqq"}]  # not heard by Whisper: keeps the previous time
    segments = timed_segments(turns, whisper)
    assert [(s["start"], s["time_from_whisper"]) for s in segments] == [(0.0, True), (12.5, True), (12.5, False)]
    assert segments[0]["end"] == 12.5 and segments[-1]["end"] is None


def test_tripwire_holds_claims_whisper_did_not_hear():
    heard = "i have five years of experience at a bank"
    assert heard_by_whisper("I have five years", set(heard.split()))
    merged = {"facts": [{"field": "experience", "value": "five years", "evidence": {"segment_id": 2, "quote": "I have five years"}},
                        {"field": "company", "value": "Sytech", "evidence": {"segment_id": 3, "quote": "working at Sytech now"}}],
              "signals": [], "objections": [], "pitches": []}
    review, tiers = [], __import__("collections").Counter(verified=2)
    tripwire(merged, review, tiers, heard)
    assert [f["value"] for f in merged["facts"]] == ["five years"]
    assert review[0]["tier"] == "unconfirmed" and review[0]["item"]["value"] == "Sytech"
    assert tiers["verified"] == 2 and tiers["of_which_unconfirmed"] == 1


def call(lead, n, flag):
    return {"call_id": f"C{lead}{n}", "lead_number": lead, "created_on": f"2026-05-0{n}T10:00:00",
            "crm_conversion_flag": flag, "duration_seconds": 60, "recording_url": "https://example.invalid/x"}


def test_customer_cohort_takes_fully_converted_leads_outside_the_pilot_and_stays_fixed(tmp_path):
    store = Store(tmp_path)
    write_json(store.path("calls.json"), [call("1", 1, "Yes"), call("1", 2, "Yes"), call("2", 1, "Yes"),
                                          call("3", 1, "Yes"), call("3", 2, None), call("4", 1, None)])
    write_json(store.path("selection.json"), {"calls": [call("2", 1, "Yes")]})
    write_json(store.path("source.json"), {"sha256": "s"})
    result = cohort.build(store)
    assert (result["leads"], result["calls"]) == (1, 2)  # lead 2 is in the pilot, 3 has mixed flags, 4 is open
    with pytest.raises(ValueError, match="Freeze the method"):
        cohort.check_frozen(store, "customers", whole_cohort=True)
    cohort.check_frozen(store, "customers", whole_cohort=False)  # named-call validation runs are allowed
    cohort.freeze(store, "customers")
    cohort.check_frozen(store, "customers", whole_cohort=True)
    write_json(store.path("calls.json"), [call("1", 1, "Yes")])
    with pytest.raises(ValueError, match="already fixed"):
        cohort.build(store)


def test_restitching_from_cached_pieces_logs_no_new_spend(tmp_path, monkeypatch):
    import httpx

    from trendytech_pilot import remote_asr
    from trendytech_pilot.storage import read_json
    monkeypatch.setenv("PILOT_ALLOW_REMOTE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(remote_asr, "wav_piece", lambda path, start, seconds: b"synthetic-wav")
    store = Store(tmp_path)
    write_json(store.path("audio", "Ctest.json"), {"sha256": "synthetic", "file": "x.wav", "duration_seconds": 90})
    body = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "Agent: Hello.\nCustomer: Yes."}]}}],
            "usageMetadata": {"promptTokenCount": 3000, "candidatesTokenCount": 20}}
    requests = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: requests.append(r) or httpx.Response(200, json=body)))
    asr = remote_asr.GeminiLabelledTranscriber(store, client)
    first = asr.transcribe({"call_id": "Ctest"})
    assert [t["role"] for t in first["turns"]] == ["agent", "prospect"]
    asr.transcribe({"call_id": "Ctest"}, force=False)
    store.path("asr", "gemini-labelled", "Ctest.json").unlink()  # e.g. a stitching change: pieces are reused
    asr.transcribe({"call_id": "Ctest"})
    spend = [e["external_cost_inr"] for e in store.events() if e["status"] == "success"]
    assert len(requests) == 1 and spend[0] > 0 and spend[1] == 0
    assert read_json(store.path("api-budget.json"))["committed_inr"] == pytest.approx(spend[0])


def test_open_sample_takes_leads_with_a_real_conversation_and_no_purchase(tmp_path, monkeypatch):
    monkeypatch.setattr(cohort, "OPEN_SAMPLE_SIZE", 1)
    store = Store(tmp_path)
    long_open = [{**call("5", 1, None), "duration_seconds": 300}, call("5", 2, None)]
    write_json(store.path("calls.json"), [call("1", 1, "Yes"), call("3", 1, "Yes"), call("3", 2, None), call("4", 1, None),
                                          *long_open])
    write_json(store.path("selection.json"), {"calls": []})
    write_json(store.path("source.json"), {"sha256": "s"})
    result = cohort.build(store, "open_sample")
    assert (result["leads"], result["calls"]) == (1, 2)  # 1 bought, 3 has a Yes, 4 only a 1-minute call
    assert {c["lead_alias"] for c in cohort.calls_of(store, "open_sample")} == {"N001"}


def test_a_finished_answer_with_no_text_is_recorded_as_no_speech(tmp_path, monkeypatch):
    import httpx

    from trendytech_pilot import remote_asr
    monkeypatch.setenv("PILOT_ALLOW_REMOTE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(remote_asr, "wav_piece", lambda path, start, seconds: b"synthetic-wav")
    store = Store(tmp_path)
    write_json(store.path("audio", "Cquiet.json"), {"sha256": "synthetic", "file": "x.wav", "duration_seconds": 50})
    body = {"candidates": [{"finishReason": "STOP", "content": {}}],
            "usageMetadata": {"promptTokenCount": 1400, "thoughtsTokenCount": 400}}
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    artifact = remote_asr.GeminiLabelledTranscriber(store, client).transcribe({"call_id": "Cquiet"})
    assert artifact["turns"] == [] and artifact["flags"] == ["no_speech_transcribed"]
