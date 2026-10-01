import json

import pytest

from trendytech_pilot.cli import freeze_method, selected_calls, verify_freeze
from trendytech_pilot.extract import parse_json_response
from trendytech_pilot.ingest import duration_seconds, outcome_flag, select_sample
from trendytech_pilot.quality import word_error_rate
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
