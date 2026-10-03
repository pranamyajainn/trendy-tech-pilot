import json

import pytest

from trendytech_pilot.referenced import expand_references


def fixture():
    transcript = {"segments": [{"id": 0, "text": "Please unlock my existing lessons."}]}
    data = {"conversation_type": "learner_support", "purpose_evidence": 0,
            "summary": "Learner requests access.", "facts": [], "signals": [], "pitches": [],
            "objections": [], "next_action": "Check access.", "uncertainties": []}
    return data, transcript


def test_reference_copies_exact_source():
    data, transcript = fixture()
    parsed = expand_references(json.dumps(data), transcript)
    assert parsed.purpose_evidence.quote == transcript["segments"][0]["text"]


@pytest.mark.parametrize("bad", [1, True, "0", -1])
def test_unknown_or_noninteger_reference_rejected(bad):
    data, transcript = fixture()
    data["purpose_evidence"] = bad
    with pytest.raises(ValueError, match="existing integer"):
        expand_references(json.dumps(data), transcript)


def test_support_cannot_pass_with_sales_signal():
    data, transcript = fixture()
    data["signals"] = [{"kind": "payment_intent", "description": "Wants lessons", "evidence": 0}]
    with pytest.raises(ValueError, match="Support/administration"):
        expand_references(json.dumps(data), transcript)


def test_support_preserves_explicit_contact_suppression():
    data, transcript = fixture()
    transcript["segments"].append({"id": 1, "text": "Please stop calling me."})
    data["signals"] = [{"kind": "do_not_contact", "description": "Requests no calls", "evidence": 1}]
    parsed = expand_references(json.dumps(data), transcript)
    assert parsed.signals[0].kind == "do_not_contact"


@pytest.mark.parametrize("quote", ["No", "A complete long segment. " * 100])
def test_complete_nonempty_segment_evidence_is_retained(quote):
    data, transcript = fixture()
    transcript["segments"][0]["text"] = quote
    parsed = expand_references(json.dumps(data), transcript)
    assert parsed.purpose_evidence.quote == quote


def test_blank_segment_cannot_be_evidence():
    data, transcript = fixture()
    transcript["segments"][0]["text"] = "   "
    with pytest.raises(ValueError, match="cannot be blank"):
        expand_references(json.dumps(data), transcript)
