import pytest

from trendytech_pilot import patterns


def x(kind, signals=(), objections=()):
    return {"extraction": {"conversation_type": kind, "signals": [{"kind": s} for s in signals],
                           "objections": [{"category": c, "resolution": "unresolved"} for c in objections]}}


@pytest.fixture
def calls(monkeypatch):
    table = {}
    monkeypatch.setattr(patterns, "current_extraction", lambda store, cid: table[cid])
    return table


def test_the_payment_conversation_and_everything_after_it_are_not_used_as_predictors(calls):
    calls.update({"a": x("sales", ["price_question"]), "b": x("enrollment_or_payment", ["payment_intent"]),
                  "c": x("sales", ["low_interest"])})
    pre = patterns.snapshot(None, [{"call_id": "a"}, {"call_id": "b"}, {"call_id": "c"}])
    assert [s["kind"] for p in pre for s in p["signals"]] == ["price_question"]


def test_groups_follow_their_order_of_precedence(calls):
    calls.update({"a": x("sales", ["payment_intent"]), "b": x("sales", ["followup_agreed", "low_interest"])})
    row, pre = patterns.facts(None, [{"call_id": "a"}, {"call_id": "b"}], None)
    assert row["Said they would pay or register"] and patterns.group_of(row, pre, excluded=False) == "Declined"
    assert patterns.group_of(row, pre, excluded=True) == "Not target"
    row, pre = patterns.facts(None, [{"call_id": "a"}], None)
    assert patterns.group_of(row, pre, excluded=False) == "Committed"
    assert patterns.group_of({}, [], excluded=False) is None


def record(buyer, flag, group="Engaged"):
    return {"buyer": buyer, "facts": {"Factor": flag}, "group": group}


def test_a_factor_is_clear_only_when_it_survives_the_multiple_comparison_adjustment():
    strong = [record(True, True)] * 60 + [record(True, False)] * 40 + [record(False, True)] * 10 + [record(False, False)] * 90
    [row] = patterns.factor_table(strong)
    assert row["odds_ratio"] > 5 and row["clear"] and row["difference_pp"] > 0
    weak = [record(True, True)] * 3 + [record(True, False)] * 3 + [record(False, True)] * 2 + [record(False, False)] * 2
    assert not patterns.factor_table(weak)[0]["clear"]


def test_small_groups_report_insufficient_evidence_and_exclusions_are_never_graded():
    records = ([record(True, True, "Committed")] * 5 + [record(True, True, "Engaged")] * 30
               + [record(False, True, "Engaged")] * 30 + [record(False, True, "Not target")] * 40)
    graded = patterns.grade(records)
    assert graded["Committed"]["category"] == "Insufficient evidence"
    assert graded["Not target"]["category"] == "Not target" and graded["_overall"]["leads"] == 65


def test_client_exclusions_are_named():
    profile = {"profile": {"fresher": "yes", "background": "it", "career_gap": "over_1_year"}}
    assert patterns.exclusions(profile) == ["Fresher", "Career gap over a year"]


def test_conversion_rate_scales_the_non_buyer_sample_to_its_population():
    rate, (low, high) = patterns.conversion_rate(b=100, o=10, sample_size=100, population=1000)
    assert rate == pytest.approx(100 / (100 + 10 * 10)) and low < rate < high
