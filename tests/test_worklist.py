"""Worklist rule and coding checks, on synthetic leads only."""
import pytest

from trendytech_pilot import worklist as wl
from trendytech_pilot.storage import Store, write_json

EV = {"call_id": "C1", "date": "2026-09-01", "segment_id": 3, "start_seconds": 62.0, "quote": "I will pay this week"}
SEGMENTS = {"C1": [{"id": 3, "start": 62.0, "text": "Okay. I will pay this week, send the link.", "role": "prospect"}],
            "C2": [{"id": 0, "start": 0.0, "text": "The number you dialled is busy.", "role": "other"}]}


def coding(**over):
    item = {"display": "Not asked", "evidence": None}
    base = {"schema": "lead-coding-v1", "lead": "100001", "coder": "test", "calls_read": ["C1", "C2"],
            "status_check": {"needed": False, "reason": None, "evidence": None},
            "profile": {"experience": item, "role": item, "ctc": item, "location": item},
            "learner": {"value": "self", "evidence": None},
            "target_check": {"value": "not_asked", "reason": None, "evidence": None},
            "stance": {"value": "commitment", "detail": "Will pay this week", "condition": None,
                       "condition_can_be_met": None, "condition_reason": None, "evidence": EV},
            "refused_after_commitment": False, "last_live_conversation": {"date": "2026-09-01", "call_id": "C1"},
            "attempts_since_last_live": 1, "buying_intent": "Ready to pay this week.", "their_next_step": None,
            "objections": [], "swot": {"strength": "s", "weakness": "w", "opportunity": "o", "threat": "t"},
            "next_action": "Counsellor sends the payment link once the lead confirms the batch.",
            "what_to_say": {"opener": "o", "question": "q", "ask": "a"}}
    for key, value in over.items():
        base[key] = value
    return base


def stance(value, **extra):
    return {"value": value, "detail": f"{value} detail", "condition": None, "condition_can_be_met": None,
            "condition_reason": None, "evidence": EV, **extra}


AS_OF = "2026-09-29"


@pytest.mark.parametrize("over, expected", [
    ({}, "Hot"),
    ({"status_check": {"needed": True, "reason": "Calls show an enrolled learner", "evidence": EV}}, "Check status"),
    ({"stance": stance("none"), "last_live_conversation": None}, "Not reached"),
    ({"target_check": {"value": "outside_target", "reason": "Non-IT background", "evidence": EV}}, "Outside target"),
    ({"stance": stance("declined")}, "Cold: declined"),
    ({"stance": stance("product_not_sold")}, "Cold: declined"),
    ({"stance": stance("conditional_commitment", condition="live classes", condition_can_be_met="no")}, "Cold: declined"),
    ({"stance": stance("conditional_commitment", condition="price approval", condition_can_be_met="yes")}, "Hot"),
    ({"stance": stance("conditional_commitment", condition="family decision", condition_can_be_met="unclear")}, "Warm"),
    ({"refused_after_commitment": True}, "Warm"),
    ({"last_live_conversation": {"date": "2026-07-01", "call_id": "C1"}}, "Warm"),  # 90 days: stale commitment
    ({"last_live_conversation": {"date": "2026-06-01", "call_id": "C1"}}, "Dormant: reconfirm"),
    ({"stance": stance("open_deferral")}, "Warm"),
    ({"stance": stance("recording_unusable"), "last_live_conversation": None}, "Recording unusable: confirm status"),
])
def test_rule_order_is_first_match_wins(over, expected):
    assert wl.categorise(coding(**over), AS_OF)[0] == expected


def test_status_check_outranks_a_commitment_and_reasons_are_visible():
    category, reason = wl.categorise(coding(status_check={"needed": True, "reason": "Enrolled", "evidence": EV}), AS_OF)
    assert (category, reason) == ("Check status", "Enrolled")
    assert "Re-open" in wl.categorise(coding(last_live_conversation={"date": "2026-07-15", "call_id": "C1"}), AS_OF)[1]


@pytest.fixture
def fake_lead(monkeypatch):
    calls = [{"call_id": "C1", "created_on": "2026-09-01T10:00:00", "lead_name": "Testname Person",
              "salesperson": "Agentname", "current_owner": "Agentname"},
             {"call_id": "C2", "created_on": "2026-09-10T10:00:00", "lead_name": "Testname Person",
              "salesperson": "Agentname", "current_owner": "Agentname"}]
    monkeypatch.setattr(wl, "calls_of", lambda store, lead: calls)
    monkeypatch.setattr(wl, "current_source", lambda store, cid: {"segments": SEGMENTS[cid]})


def test_a_clean_coding_passes(tmp_path, fake_lead):
    assert wl.check_coding(Store(tmp_path), "100001", coding()) == []


@pytest.mark.parametrize("over, problem", [
    ({"calls_read": ["C1"]}, "calls_read"),
    ({"stance": stance("commitment", evidence={**EV, "quote": "I will pay next month"})}, "not verbatim"),
    ({"stance": stance("commitment", evidence={**EV, "segment_id": 9})}, "segment 9 not in call"),
    ({"stance": stance("commitment", evidence={**EV, "call_id": "C9"})}, "not this lead's"),
    ({"next_action": "Ask him to pay today."}, "gendered pronoun"),
    ({"next_action": "Agentname sends the link."}, "personal name"),
    ({"buying_intent": "The model thinks they will buy."}, "internal word"),
    ({"next_action": "Send link to a@b.com today."}, "contact detail"),
    ({"stance": stance("conditional_commitment")}, "needs condition"),
    ({"stance": stance("none")}, "but a last live conversation"),
    ({"next_action": "Call the lead this week about the fee."}, "relative timing"),
    ({"next_action": "Offer the webinar discount on the next call."}, "conditional on current approval"),
    ({"what_to_say": {"opener": "o", "question": "q", "ask": "Shall I hold the early-bird price?"}}, "conditional"),
])
def test_bad_codings_are_rejected(tmp_path, fake_lead, over, problem):
    problems = wl.check_coding(Store(tmp_path), "100001", coding(**over))
    assert any(problem in p for p in problems), problems


def test_selection_is_stored_once_and_reproducible(tmp_path, monkeypatch):
    store = Store(tmp_path)
    pool = {str(n): [] for n in range(100, 200)}
    monkeypatch.setattr(wl, "lead_calls", lambda store, group: pool if group == "open_sample" else {})
    first = wl.select(store)
    assert len(first["drawn"]) == wl.EXTRA_LEADS and first["drawn"] == sorted(first["drawn"])
    pool.clear()  # a later run must reuse the stored list, not redraw from changed data
    assert wl.select(store)["drawn"] == first["drawn"]
    pool.update({str(n): [] for n in range(100, 200)})
    assert wl.select(store, force=True)["drawn"] == first["drawn"]  # same seed, same pool, same draw


def test_freeze_refuses_once_a_holdout_lead_is_coded(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(wl, "holdout_leads", lambda store: {"900001"})
    write_json(wl.coding_path(store, "900001"), coding(lead="900001"))
    with pytest.raises(ValueError, match="Holdout leads were coded before the freeze"):
        wl.freeze(store)


def test_rule_drift_after_a_freeze_is_refused(tmp_path, monkeypatch):
    store = Store(tmp_path)
    write_json(store.path("review", "worklist-freeze.json"), {"rule_hash": "old"})
    with pytest.raises(ValueError, match="differs from the frozen rule"):
        wl.frozen(store)


def test_summaries_lose_names_including_spoken_ones(tmp_path, monkeypatch):
    calls = [{"call_id": "C1", "lead_name": "Ovani Trel", "salesperson": "Asha", "current_owner": "Asha"}]
    monkeypatch.setattr(wl, "name_words", lambda store: ({"asha", "liro"}, {"ovani", "trel", "pemra"}))
    clean = wl.redactor(Store(tmp_path), calls)
    assert clean("Asha called Ovani; Liro will follow up.") == "the counsellor called the lead; the counsellor will follow up."
    assert clean("Their batch coordinator, Tara, will call.") == "Their batch coordinator, will call."
    assert clean("Pitched to prospect Arjunan, an engineer.") == "Pitched to lead, an engineer."
    assert clean("Pemra referred the lead to the Ultimate course.") == "another person referred the lead to the Ultimate course."


class FakeModel:
    model_id = "fake-model"

    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def generate(self, system, user, max_tokens=None, schema=None, schema_name=None):
        self.calls.append(user)
        return __import__("json").dumps(self.answers.pop(0)), {"input_tokens": 1, "output_tokens": 1}


def test_model_coding_is_validated_retried_and_never_overwrites_a_reviewed_file(tmp_path, fake_lead, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(wl, "show", lambda store, lead: "transcript")
    bad = coding(next_action="Ask him to pay.")
    model = FakeModel([bad, coding()])
    written = wl.code_lead(store, "100001", model)
    assert written["coder"].startswith("model:fake-model:") and len(model.calls) == 2
    assert "failed these checks" in model.calls[1]
    write_json(wl.coding_path(store, "100002"), coding(lead="100002", coder="assistant-review"))
    untouched = wl.code_lead(store, "100002", FakeModel([]), force=True)
    assert untouched["coder"] == "assistant-review"
    with pytest.raises(ValueError, match="failed the checks twice"):
        wl.code_lead(store, "100003", FakeModel([bad, bad]))


def test_generated_summaries_never_guess_gender():
    assert wl.neutral("The agent introduced herself. She explained onboarding.") == \
        "The agent introduced themselves. They explained onboarding."
    assert wl.neutral("Asked the agent to call her back; her sister completed BCA.") == \
        "Asked the agent to call them back; their sister completed BCA."
    assert wl.neutral("He was busy, so his manager called him.") == "They were busy, so their manager called them."


def test_summaries_use_the_sheet_terms():
    assert wl.wording("The agent called the prospect; the lead the lead agreed.") == \
        "The counsellor called the lead; the lead agreed."
    assert wl.wording("Agentic GenAI course") == "Agentic GenAI course"



def test_offers_pass_only_when_conditional_on_current_approval(tmp_path, fake_lead):
    ok = coding(next_action="If the manager approves the discount, send the revised fee and call the lead.")
    assert wl.check_coding(Store(tmp_path), "100001", ok) == []


CALL_TIMES = {"C1": "2026-09-01T14:02:00"}


def test_rows_lead_with_action_and_decisive_evidence_and_carry_no_group_rates():
    excluded = coding(target_check={"value": "outside_target", "reason": "Second-year student",
                                    "evidence": {**EV, "quote": "I will pay this week"}})
    row = wl.worklist_row(wl.LeadCoding.model_validate(excluded), "Outside target", "Second-year student", AS_OF, CALL_TIMES)
    assert list(row)[:6] == ["lead", "priority", "reason", "next_action", "what_to_say", "evidence"]
    assert row["evidence"].startswith("Why: call of 1 Sep 2026 14:02, minute 01:02:")
    assert row["last_conversation"] == "1 Sep 2026 (28 days before cutoff)"
    assert row["validation"] == "Recording validation pending"
    for text in wl.HISTORICAL_CONTEXT.values():
        assert not __import__("re").search(r"\d", text)  # group estimates stay in the report, not on lead rows


def test_dormant_rows_message_before_calling():
    old = wl.LeadCoding.model_validate(coding(stance=stance("open_deferral"),
                                              last_live_conversation={"date": "2026-05-01", "call_id": "C1"}))
    category, reason = wl.categorise(old, AS_OF)
    row = wl.worklist_row(old, category, reason, AS_OF, CALL_TIMES)
    assert category == "Dormant: reconfirm" and row["next_action"].startswith("Message the lead to reconfirm interest")


def test_a_new_rule_version_keeps_history_and_the_first_freeze_time(tmp_path, monkeypatch):
    store = Store(tmp_path)
    monkeypatch.setattr(wl, "holdout_leads", lambda store: set())
    for name, value in (("export_date", "2026-09-29"), ("past_lead_evidence", {}), ("buyer_timing", {}),
                        ("select", {"drawn": []})):
        monkeypatch.setattr(wl, name, lambda store, v=value: v)
    first = wl.freeze(store)
    with pytest.raises(ValueError, match="needs --force and --reason"):
        wl.freeze(store, force=True)
    second = wl.freeze(store, force=True, reason="separate unusable recordings")
    assert second["first_frozen_at"] == first["frozen_at"]
    assert second["history"][0]["superseded_reason"] == "separate unusable recordings"



def test_saying_trendytech_does_not_offer_something_is_not_an_offer(tmp_path, fake_lead):
    fine = coding(next_action="TrendyTech does not offer live GenAI classes; send the recorded option details once asked.")
    assert wl.check_coding(Store(tmp_path), "100001", fine) == []
