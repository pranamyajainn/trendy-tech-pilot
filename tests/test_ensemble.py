import pytest

from trendytech_pilot.ensemble import ClaimVerdict, claims_of, merge
from trendytech_pilot.storage import Store, digest, read_json, write_json


def ev(segment_id, quote):
    return {"segment_id": segment_id, "quote": quote}


def extraction(conversation_type="sales", facts=(), signals=(), objections=(), pitches=()):
    return {"conversation_type": conversation_type, "purpose_evidence": ev(0, "course"), "summary": "s",
            "next_action": "n", "uncertainties": [], "facts": list(facts), "signals": list(signals),
            "objections": list(objections), "pitches": list(pitches)}


ROLE = {"field": "current_role", "value": "data engineer", "evidence": ev(3, "data engineer")}
PRICE = {"category": "price", "concern": "Too costly", "evidence": ev(10, "too costly"), "response": "EMI available",
         "response_evidence": ev(11, "EMI"), "resolution": "resolved", "resolution_evidence": ev(12, "okay")}
INTENT = {"kind": "payment_intent", "description": "Will pay today", "evidence": ev(20, "pay today")}


def verdicts(*kinds, **purpose):
    return {i: ClaimVerdict(claim_id=i, verdict=k, reason="r", **(purpose if i == 0 else {})) for i, k in enumerate(kinds)}


def test_claims_carry_their_cited_segments():
    claims = claims_of(extraction(facts=[ROLE], objections=[PRICE]))
    assert [c["collection"] for c in claims] == ["conversation_type", "facts", "objections"]
    assert claims[2]["segments"] == [10, 11, 12] and "Resolution: resolved" in claims[2]["statement"]


def test_supported_claims_enter_findings_and_unsupported_ones_go_to_review():
    x = extraction(facts=[ROLE], signals=[INTENT])
    merged, review, tiers = merge(x, claims_of(x), verdicts("supported", "supported", "unsupported"))
    assert merged["facts"] == [ROLE] and merged["signals"] == []
    assert tiers == {"verified": 1, "needs_review": 1} and review[0]["collection"] == "signals"


def test_overstated_resolution_is_lowered_rather_than_dropped():
    x = extraction(objections=[PRICE])
    v = {0: ClaimVerdict(claim_id=0, verdict="supported", reason="r"),
         1: ClaimVerdict(claim_id=1, verdict="overstated", reason="answered, not accepted",
                         corrected_resolution="partly_addressed")}
    merged, review, tiers = merge(x, claims_of(x), v)
    assert merged["objections"][0]["resolution"] == "partly_addressed"
    assert merged["objections"][0]["resolution_evidence"] is None and tiers == {"verified_corrected": 1} and review == []


def test_overstated_signal_goes_to_review():
    x = extraction(signals=[INTENT])
    merged, review, _ = merge(x, claims_of(x), verdicts("supported", "overstated"))
    assert merged["signals"] == [] and review[0]["verdict"] == "overstated"


def test_verifier_corrects_call_type_only_when_a_purpose_quote_exists():
    x = extraction("brief_followup")
    merged, review, _ = merge(x, claims_of(x), verdicts("unsupported", corrected_conversation_type="sales"))
    assert merged["conversation_type"] == "sales" and review[0]["tier"] == "corrected"
    y = {**extraction("unusable"), "purpose_evidence": None}
    merged, review, _ = merge(y, claims_of(y), verdicts("unsupported", corrected_conversation_type="sales"))
    assert merged["conversation_type"] == "unusable" and review[0]["tier"] == "needs_review"


def test_service_calls_keep_profile_facts_but_not_sales_content():
    x = extraction("learner_support", facts=[ROLE], pitches=[{"topic": "Live sessions", "evidence": ev(5, "live"),
                                                             "prospect_response": None, "response_evidence": None}])
    merged, review, tiers = merge(x, claims_of(x), verdicts("supported", "supported", "supported"))
    assert merged["facts"] == [ROLE] and merged["pitches"] == []
    assert tiers == {"verified": 1, "excluded_service_call": 1} and review[0]["tier"] == "excluded_service_call"


def test_consensus_is_current_only_when_every_link_is(tmp_path, monkeypatch):
    from trendytech_pilot import artifacts
    from trendytech_pilot.remote_asr import GEMINI_ASR
    from trendytech_pilot.remote_sarvam import SARVAM_ASR
    from trendytech_pilot.resolve import RESOLVER
    store = Store(tmp_path)
    whisper = {"fingerprint": "whisper", "flags": [], "duration_seconds": 60, "segments": []}
    monkeypatch.setattr(artifacts, "current_transcript", lambda store, cid: whisper)
    write_json(store.path("audio", "Ctest.json"), {"sha256": "audio"})
    gemini_fp, sarvam_fp = digest(["audio", GEMINI_ASR]), digest(["audio", SARVAM_ASR])
    write_json(store.path("asr", "gemini", "Ctest.json"), {"fingerprint": gemini_fp})
    write_json(store.path("asr", "sarvam", "Ctest.json"), {"fingerprint": sarvam_fp})
    write_json(store.path("asr", "consensus", "Ctest.json"), {"fingerprint": digest(["whisper", gemini_fp, sarvam_fp, RESOLVER]),
                                                              "segments": [{"id": 0, "text": "hi"}]})
    assert artifacts.current_consensus(store, "Ctest")["duration_seconds"] == 60
    monkeypatch.setitem(RESOLVER, "threshold", 0.5)  # Any resolver setting change makes the consensus stale.
    assert artifacts.current_consensus(store, "Ctest") is None


def test_superseding_a_frozen_method_keeps_the_old_freeze_with_its_reason(tmp_path):
    from trendytech_pilot.cli import freeze_method
    store = Store(tmp_path)
    write_json(store.path("selection.json"), {"sha256": "synthetic", "calls": []})
    write_json(store.path("method-freeze.json"), {"prompt_version": "extraction-v8", "llm_model": "old"})
    with pytest.raises(ValueError, match="supersede"):
        freeze_method(store, "asr", "llm")
    freeze_method(store, "asr", "llm", supersede="Multi-model cross-check")
    [archived] = store.path("method-freeze-history").glob("*.json")
    assert read_json(archived)["reason"] == "Multi-model cross-check"
    assert read_json(archived)["method"]["prompt_version"] == "extraction-v8"
    assert read_json(store.path("method-freeze.json"))["llm_model"] == "llm"


def test_retiring_a_freeze_archives_it_and_blocks_holdout_until_a_new_freeze(tmp_path):
    from trendytech_pilot.cli import retire_freeze, verify_freeze
    store = Store(tmp_path)
    write_json(store.path("method-freeze.json"), {"prompt_version": "extraction-v8"})
    assert retire_freeze(store, "Replaced by multi-model method")["retired"] == "extraction-v8"
    assert not store.path("method-freeze.json").exists()
    [archived] = store.path("method-freeze-history").glob("*.json")
    assert read_json(archived)["reason"] == "Replaced by multi-model method"
    with pytest.raises(ValueError, match="Freeze the method"):
        verify_freeze(store, "asr", "llm")
