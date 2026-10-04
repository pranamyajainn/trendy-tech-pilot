import pytest

from trendytech_pilot import client
from trendytech_pilot.client import Reference, export_client, journeys, render_evidence
from trendytech_pilot.quality import validation_status, wilson_interval
from trendytech_pilot.storage import Store, write_csv, write_json


def call(lead, n, flag=None):
    return {"call_id": f"C{lead}{n}", "lead_number": lead, "lead_alias": f"L{lead}", "created_on": f"2026-05-0{n}T10:00:00",
            "crm_conversion_flag": flag, "current_owner": "Owner", "recording_url": f"https://example.invalid/{lead}{n}"}


def artifact(conversation_type, signals=()):
    return {"extraction": {"conversation_type": conversation_type, "summary": "s", "next_action": "n", "facts": [],
                           "signals": list(signals), "objections": [], "pitches": []}}


@pytest.fixture
def fake_extractions(monkeypatch):
    table = {}
    monkeypatch.setattr(client, "current_extraction", lambda store, cid: table.get(cid))
    return table


def test_action_category_rules_come_from_the_journey_not_the_model(tmp_path, fake_extractions):
    calls = [call("1", 1), call("1", 2), call("2", 1, "Yes"), call("3", 1), call("3", 2), call("4", 1)]
    fake_extractions.update({"C11": artifact("sales"), "C12": artifact("unusable"), "C21": artifact("sales"),
                             "C31": artifact("unusable"), "C32": artifact("unusable"),
                             "C41": artifact("sales", [{"kind": "do_not_contact"}])})
    leads = journeys(Store(tmp_path), calls)
    assert leads["1"]["forced_category"] is None and leads["1"]["unanswered_after_last_live"] == 1
    assert leads["1"]["last_live"]["call"]["call_id"] == "C11"
    assert leads["2"]["forced_category"] == "Confirm enrollment"
    assert leads["3"]["forced_category"] == "Review contact details or contact preferences" and leads["3"]["last_live"] is None
    assert leads["4"]["forced_category"] == "Review contact details or contact preferences"


def test_evidence_must_come_from_the_leads_own_calls(tmp_path, monkeypatch, fake_extractions):
    fake_extractions["C11"] = artifact("sales")
    monkeypatch.setattr(client, "current_source", lambda store, cid: {"segments": [{"id": 4, "start": 75.2, "text": "I will pay"}]})
    journey = journeys(Store(tmp_path), [call("1", 1)])["1"]
    [rendered] = render_evidence(Store(tmp_path), journey, [Reference(call_id="C11", segment_id=4)])
    assert (rendered["timestamp"], rendered["quote"], rendered["date"]) == ("1:15", "I will pay", "2026-05-01")
    with pytest.raises(ValueError, match="not in this lead"):
        render_evidence(Store(tmp_path), journey, [Reference(call_id="C99", segment_id=4)])


def test_client_sheets_put_actionable_leads_first_and_stay_drafts_until_validated(tmp_path):
    store = Store(tmp_path)
    for alias, category in [("L1", "Confirm enrollment"), ("L2", "Resolve a purchase condition")]:
        write_json(store.path("review", "lead-actions", alias + ".json"), {
            "lead_alias": alias, "lead_number": alias[1:], "owner": "Owner", "last_live_conversation": "2026-05-01",
            "evidence": [{"date": "2026-05-01", "timestamp": "1:15", "quote": "I will pay"}],
            "action": {"action_category": category, "goal_and_context": "", "latest_position": "p",
                       "recommended_next_action": "a", "suggested_wording": "w", "timing_status_check": "t"}})
    result = export_client(store)
    from trendytech_pilot.storage import read_json
    rows = read_json(store.path("exports", "client", "lead_actions.json"))
    assert [r["action_category"] for r in rows] == ["Resolve a purchase condition", "Confirm enrollment"]
    assert result["review_draft"] is True and "1 May 2026 at 1:15" in rows[0]["supporting_evidence"]


def test_validation_needs_every_client_claim_signed_and_a_full_audit_sample(tmp_path):
    store = Store(tmp_path)
    write_csv(store.path("qa", "validate-client-claims.csv"),
              [{"item": "i", "verdict": "Confirmed", "reviewer": "R", "reviewed_at": "2026-10-04"}])
    write_csv(store.path("qa", "audit-claims-sample.csv"),
              [{"correct": "yes" if i < 92 else "no", "reviewer": "R", "reviewed_at": "2026-10-04"} for i in range(100)])
    status = validation_status(store)
    assert status["complete"] and status["audit_precision"] == 0.92
    assert status["audit_precision_95ci"] == pytest.approx(wilson_interval(92, 100))
    write_csv(store.path("qa", "audit-claims-sample.csv"), [{"correct": "", "reviewer": "", "reviewed_at": ""}])
    assert not validation_status(store)["complete"]


def test_wilson_interval_handles_perfect_scores_without_claiming_certainty():
    low, high = wilson_interval(50, 50)
    assert high == 1.0 and low < 0.95
    assert wilson_interval(0, 0) is None


def test_client_export_keeps_only_sheet_columns_and_refuses_internal_detail(tmp_path):
    store = Store(tmp_path)
    insight = {"finding": "f", "evidence_and_scale": "4 of 9 leads", "sales_implication": "i", "recommended_change": "c",
               "suggested_wording": "w", "how_to_assess": "a", "source": "Lead 1, 2026-05-01 at 1:15",
               "examples": [{"call_id": "C0123456789abcdef", "recording_url": "https://example.invalid/1"}]}
    write_json(store.path("review", "sales-insights.json"), {"insights": [insight]})
    export_client(store)
    from trendytech_pilot.storage import read_json
    [row] = read_json(store.path("exports", "client", "sales_insights.json"))
    assert list(row) == list(client.INSIGHT_COLUMNS)
    for leak in ["Checked by Gemini", "see C0123456789abcdef", "price_question", "held-out calls"]:
        write_json(store.path("review", "sales-insights.json"), {"insights": [{**insight, "sales_implication": leak}]})
        with pytest.raises(ValueError, match="internal detail"):
            export_client(store)


def test_enrolled_learners_are_routed_to_support_unless_a_sale_followed(tmp_path, fake_extractions):
    calls = [call("1", 1, "Yes"), call("1", 2, "Yes"), call("2", 1, "Yes"), call("2", 2, "Yes"), call("3", 1, "Yes")]
    fake_extractions.update({"C11": artifact("sales"), "C12": artifact("learner_support"),
                             "C21": artifact("learner_support"), "C22": artifact("sales"), "C31": artifact("sales")})
    leads = journeys(Store(tmp_path), calls)
    assert leads["1"]["forced_category"] == "Route to learner support"
    assert "learner support call on 2 May 2026" in leads["1"]["basis"]
    assert leads["2"]["forced_category"] == "Confirm enrollment"  # a sales conversation came after support
    assert leads["3"]["forced_category"] == "Confirm enrollment"


def test_drafted_actions_must_not_name_the_lead_or_agent(tmp_path, fake_extractions):
    fake_extractions["C11"] = artifact("sales")
    journey = journeys(Store(tmp_path), [{**call("1", 1), "lead_name": "Asha Verma", "salesperson": "Ravi K"}])["1"]
    text = {"goal_and_context": "", "latest_position": "Asha asked about fees.", "recommended_next_action": "a",
            "suggested_wording": "Hi, calling from TrendyTech", "timing_status_check": "t", "evidence": []}
    assert client.names_in(journey, client.LeadAction(action_category=client.CATEGORIES[0], **text)) == {"Asha"}
    clean = {**text, "latest_position": "The prospect asked about fees."}
    assert not client.names_in(journey, client.LeadAction(action_category=client.CATEGORIES[0], **clean))


def test_a_plain_decline_goes_to_contact_preferences_but_a_decline_with_a_concern_does_not(tmp_path, fake_extractions):
    fake_extractions.update({"C11": artifact("sales", [{"kind": "low_interest"}]),
                             "C21": {"extraction": {**artifact("sales", [{"kind": "low_interest"}])["extraction"],
                                                    "objections": [{"category": "format"}]}}})
    leads = journeys(Store(tmp_path), [call("1", 1), call("2", 1)])
    assert leads["1"]["forced_category"] == "Review contact details or contact preferences"
    assert "not interested" in leads["1"]["basis"] and leads["2"]["forced_category"] is None
