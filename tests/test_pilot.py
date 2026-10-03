import json

import pytest

from trendytech_pilot.cli import freeze_method, selected_calls, verify_freeze
from trendytech_pilot.extract import (
    LOCAL_GENERATION,
    evidence_error_message,
    extraction_fingerprint,
    parse_json_response,
)
from trendytech_pilot.ingest import duration_seconds, outcome_flag, select_sample
from trendytech_pilot.quality import word_error_rate
from trendytech_pilot.remote import GeminiExtractor
from trendytech_pilot.reporting import cost_summary, priority_for
from trendytech_pilot.schema import CallExtraction, validate_evidence
from trendytech_pilot.storage import Store, safe_cell, write_json


def call(lead="1", number=0, flag=None):
    return {"call_id": f"C{lead}-{number}", "lead_number": lead, "created_on": f"2026-01-{number+1:02d}T10:00:00",
            "duration_seconds": 120, "crm_conversion_flag": flag, "salesperson": "Synthetic salesperson"}


def analysis(kinds):
    return {"extraction": {"signals": [{"kind": kind} for kind in kinds]}}


def test_sample_preserves_whole_groups_and_holdout_never_splits_a_lead():
    calls = [call(str(lead), i) for lead in range(20) for i in range(2 if lead < 10 else 3)]
    selected = select_sample(calls, "fixed", {2: 5, 3: 5})
    assert selected == select_sample(list(reversed(calls)), "fixed", {2: 5, 3: 5})
    assert len(selected) == 25
    leads = {c["lead_number"] for c in selected}
    assert len(leads) == 10
    for lead in leads:
        assert len([c for c in selected if c["lead_number"] == lead]) == len([c for c in calls if c["lead_number"] == lead])
        assert len({c["split"] for c in selected if c["lead_number"] == lead}) == 1
    assert len({c["lead_number"] for c in selected if c["split"] == "holdout"}) == 2


def test_infeasible_sample_does_not_silently_drop_leads():
    with pytest.raises(ValueError, match="Need"):
        select_sample([call()], quotas={6: 50})


def test_blank_and_mixed_conversion_flags_never_mean_a_loss():
    assert outcome_flag([call()]) == "Outcome unknown"
    assert outcome_flag([call(flag="Yes"), call(number=1)]) == "Mixed CRM flags; unresolved"


@pytest.mark.parametrize("text", ["0h:60m:1s", "00:05:12", "bad", None])
def test_malformed_durations_are_rejected(text):
    with pytest.raises(ValueError):
        duration_seconds(text)


def test_duration_hour_component():
    assert duration_seconds("1h:2m:3s") == 3723


def test_exact_evidence_is_required_and_resolution_needs_acceptance():
    transcript = {"segments": [{"id": 0, "text": "The price is too high for me."}]}
    ex = CallExtraction(summary="Price concern", next_action="Discuss affordability", objections=[{
        "category": "price", "concern": "Price", "evidence": {"segment_id": 0, "quote": "price is too high"},
        "resolution": "resolved",
    }])
    assert validate_evidence(ex, transcript) == ["objections[0].resolution_missing_evidence"]
    ex.objections[0].resolution = "unresolved"
    assert validate_evidence(ex, transcript) == []
    ex.objections[0].evidence.quote = "I will pay today"
    assert validate_evidence(ex, transcript) == ["objections[0].evidence"]


def fact_errors(transcript, segment_id, quote):
    ex = CallExtraction(summary="", next_action="", facts=[{"field": "budget", "value": "synthetic",
                        "evidence": {"segment_id": segment_id, "quote": quote}}])
    return validate_evidence(ex, transcript)


def test_quote_may_run_into_an_adjacent_segment_but_not_live_only_in_one():
    transcript = {"segments": [{"id": 0, "text": "Let's come to"}, {"id": 1, "text": "the fee structure directly."},
                               {"id": 3, "text": "It is 4.5 lakh."}]}
    assert fact_errors(transcript, 0, "Let's come to the fee structure") == []
    assert fact_errors(transcript, 1, "come to the fee structure") == []
    assert fact_errors(transcript, 3, "directly. It is 4.5 lakh") == []  # neighbour by order, despite the id gap
    assert fact_errors(transcript, 0, "the fee structure directly") == ["facts[0].evidence"]  # wrong citation
    assert fact_errors(transcript, 3, "It is 45 lakh") == ["facts[0].evidence"]  # a decimal point is content


def test_fillers_and_punctuation_are_ignored_but_corrected_words_are_not():
    transcript = {"segments": [{"id": 0, "text": "like uh data engineer, but uh mostly on-prem. I think 38 may works"}]}
    assert fact_errors(transcript, 0, "data engineer but mostly on-prem") == []
    assert fact_errors(transcript, 0, "I think 30th May works") == ["facts[0].evidence"]
    assert fact_errors(transcript, 0, "uh") == ["facts[0].evidence"]  # a filler alone supports nothing


def test_evidence_retry_message_shows_the_cited_segment_text():
    transcript = {"segments": [{"id": 3, "text": "i think 38 may should be suitable"}]}
    ex = CallExtraction(summary="", next_action="", facts=[{"field": "timeline", "value": "30 May",
                        "evidence": {"segment_id": 3, "quote": "30th May should be suitable"}}])
    message = evidence_error_message(ex.model_dump(), transcript, validate_evidence(ex, transcript))
    assert "facts[0].evidence cites segment 3" in message and "i think 38 may should be suitable" in message


def test_service_call_cannot_carry_sales_content_but_keeps_contact_suppression():
    transcript = {"segments": [{"id": 0, "text": "Please unlock my lessons."}, {"id": 1, "text": "Stop calling me."}]}
    purpose = {"segment_id": 0, "quote": "unlock my lessons"}
    for kind, expected in [("payment_intent", ["service_call_has_sales_content"]), ("do_not_contact", [])]:
        ex = CallExtraction(conversation_type="administrative", purpose_evidence=purpose, summary="", next_action="",
                            signals=[{"kind": kind, "description": kind,
                                      "evidence": {"segment_id": 1, "quote": "Stop calling me."}}])
        assert validate_evidence(ex, transcript) == expected


def test_invented_segment_is_rejected():
    ex = CallExtraction(summary="", next_action="", facts=[{"field": "goal", "value": "Promotion",
        "evidence": {"segment_id": 99, "quote": "promotion"}}])
    assert validate_evidence(ex, {"segments": []})


def test_unrequested_conversion_probability_field_is_rejected():
    with pytest.raises(ValueError):
        parse_json_response(json.dumps({"summary": "", "next_action": "", "conversion_probability": .9}))


def test_stop_contact_overrides_positive_signals_and_crm_flag():
    calls = [call(flag="Yes")]
    assert priority_for(calls, {calls[0]["call_id"]: analysis(["do_not_contact", "payment_intent"])})[0] == "Do not contact"


def test_latest_call_controls_temperature_not_earlier_interest():
    calls = [call(), call(number=1)]
    data = {calls[0]["call_id"]: analysis(["payment_intent"]), calls[1]["call_id"]: analysis(["low_interest"])}
    assert priority_for(calls, data)[0] == "Cold signal"


def voicemail():
    return {"extraction": {"conversation_type": "unusable", "signals": []}}


def test_trailing_voicemail_does_not_hide_the_last_live_conversation():
    calls = [call(), call(number=1), call(number=2)]
    data = {calls[0]["call_id"]: analysis(["payment_intent"]), calls[1]["call_id"]: voicemail(),
            calls[2]["call_id"]: voicemail()}
    assert priority_for(calls, data)[0] == "Hot signal"


def test_journey_that_never_reached_a_person_is_labelled_separately():
    calls = [call(), call(number=1)]
    assert priority_for(calls, {c["call_id"]: voicemail() for c in calls})[0] == "No live conversation"


def test_incomplete_journey_does_not_get_hot_label():
    calls = [call(), call(number=1)]
    assert priority_for(calls, {calls[1]["call_id"]: analysis(["payment_intent"])})[0] == "Needs review"


def test_retry_cost_is_included_but_audio_denominator_not_duplicated(tmp_path, monkeypatch):
    monkeypatch.delenv("PILOT_LOCAL_COMPUTE_INR_PER_HOUR", raising=False)
    store = Store(tmp_path)
    c = call()
    write_json(store.path("transcripts", c["call_id"] + ".json"), {"duration_seconds": 120})
    store.event(call_id=c["call_id"], stage="extract", status="failed", wall_seconds=60, external_cost_inr=1)
    store.event(call_id=c["call_id"], stage="extract", status="success", wall_seconds=60, external_cost_inr=1)
    result = cost_summary(store, [c])
    assert result["unique_transcribed_audio_minutes"] == 2
    assert result["external_api_inr_per_audio_minute"] == 1
    assert result["processing_inr_per_audio_minute"] is None
    assert result["failed_attempts"] == 1
    assert result["cost_gate"].startswith("Not assessed")


def test_holdout_requires_frozen_unchanged_method(tmp_path):
    store = Store(tmp_path)
    write_json(store.path("selection.json"), {"sha256": "synthetic", "calls": []})
    with pytest.raises(ValueError, match="Freeze"):
        verify_freeze(store, "asr", "llm")
    freeze_method(store, "asr", "llm")
    verify_freeze(store, "asr", "llm")
    with pytest.raises(ValueError, match="already frozen"):
        verify_freeze(store, "new-asr", "llm")


@pytest.mark.parametrize("model, settings", [(GeminiExtractor.model_id, GeminiExtractor.generation),
                                             ("mlx-community/Qwen3.5-27B-4bit", LOCAL_GENERATION)])
def test_generation_settings_are_part_of_extraction_identity(monkeypatch, model, settings):
    before = extraction_fingerprint("transcript", model, "revision")
    monkeypatch.setitem(settings, "temperature", 0.5)
    assert extraction_fingerprint("transcript", model, "revision") != before


def test_freeze_rejects_changed_generation_settings(tmp_path, monkeypatch):
    store = Store(tmp_path)
    write_json(store.path("selection.json"), {"sha256": "synthetic", "calls": []})
    freeze_method(store, "asr", GeminiExtractor.model_id)
    verify_freeze(store, "asr", GeminiExtractor.model_id)
    monkeypatch.setitem(GeminiExtractor.generation, "temperature", 0.0)
    with pytest.raises(ValueError, match="already frozen"):
        verify_freeze(store, "asr", GeminiExtractor.model_id)


def test_unknown_reference_is_not_zero_wer():
    assert word_error_rate("", "transcript") is None


@pytest.mark.parametrize("value", ["=HYPERLINK(\"bad\")", "+SUM(A1:A2)", " @malicious", "-1+2"])
def test_csv_injection_is_escaped(value):
    assert safe_cell(value).startswith("'")


def test_development_default_does_not_include_holdout(tmp_path):
    store = Store(tmp_path)
    a = {**call(), "split": "development"}
    b = {**call("2"), "split": "holdout"}
    write_json(store.path("selection.json"), {"calls": [a, b]})
    assert selected_calls(store) == [a]
