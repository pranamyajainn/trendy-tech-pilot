import json

import httpx
import pytest

from trendytech_pilot.extract import parse_json_response
from trendytech_pilot.remote import UNSUPPORTED_SCHEMA_KEYS, BudgetExceeded, GeminiExtractor
from trendytech_pilot.storage import Store, read_json


def test_remote_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("PILOT_ALLOW_REMOTE", raising=False)
    with pytest.raises(ValueError, match="disabled"):
        GeminiExtractor(Store(tmp_path))


def enabled(tmp_path, monkeypatch, handler):
    monkeypatch.setenv("PILOT_ALLOW_REMOTE", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    return GeminiExtractor(Store(tmp_path), httpx.Client(transport=httpx.MockTransport(handler)))


def test_api_usage_counted_even_when_generated_text_is_invalid(tmp_path, monkeypatch):
    def respond(request):
        return httpx.Response(200, json={"usage": {"prompt_tokens": 200, "completion_tokens": 100},
                                        "choices": [{"finish_reason": "stop", "message": {"content": "invalid json"}}]})
    extractor = enabled(tmp_path, monkeypatch, respond)
    text, usage = extractor.generate("system", "transcript")
    assert text == "invalid json"
    budget = read_json(extractor.budget_path)
    assert budget["committed_inr"] == pytest.approx(extractor.cost(200, 100))
    assert usage["provider_cost_estimate_inr"] > 0


def test_uncertain_timeout_reserves_cost_and_respects_cap(tmp_path, monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("Synthetic timeout")
    extractor = enabled(tmp_path, monkeypatch, timeout)
    with pytest.raises(httpx.ReadTimeout):
        extractor.generate("system", "transcript")
    reserved = read_json(extractor.budget_path)["committed_inr"]
    assert reserved > 0
    extractor.cap = reserved
    with pytest.raises(BudgetExceeded):
        extractor.generate("system", "transcript")


@pytest.mark.parametrize("cap", ["nan", "inf", "-1", "0"])
def test_invalid_cap_cannot_disable_budget_check(tmp_path, monkeypatch, cap):
    monkeypatch.setenv("PILOT_API_CAP_INR", cap)
    with pytest.raises(ValueError, match="finite and positive"):
        enabled(tmp_path, monkeypatch, lambda request: pytest.fail("Must not make a request"))


@pytest.mark.parametrize("count", [-1, 1.5, True, "200"])
def test_invalid_provider_usage_retains_reserve(tmp_path, monkeypatch, count):
    def respond(request):
        return httpx.Response(200, json={"usage": {"prompt_tokens": count, "completion_tokens": 100}})
    extractor = enabled(tmp_path, monkeypatch, respond)
    with pytest.raises(ValueError, match="nonnegative integers"):
        extractor.generate("system", "transcript")
    budget = read_json(extractor.budget_path)
    assert budget["committed_inr"] > 0
    assert next(iter(budget["requests"].values()))["status"] == "uncertain_reserve_retained"


def ok_response(request=None):
    return httpx.Response(200, json={"usage": {"prompt_tokens": 200, "completion_tokens": 100},
                                     "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]})


def schema_keywords(node):
    """Schema keywords at every level, excluding property names."""
    if isinstance(node, list):
        return set().union(*(schema_keywords(item) for item in node))
    if not isinstance(node, dict):
        return set()
    found = set(node)
    for key, value in node.items():
        found |= set().union(*(schema_keywords(child) for child in (value.values() if key == "properties" else [value])))
    return found


def test_request_schema_omits_provider_rejected_keywords_but_keeps_structure(tmp_path, monkeypatch):
    sent = []

    def respond(request):
        sent.append(json.loads(request.content))
        return ok_response()
    enabled(tmp_path, monkeypatch, respond).generate("system", "transcript")
    schema = sent[0]["response_format"]["json_schema"]["schema"]
    assert not schema_keywords(schema) & (UNSUPPORTED_SCHEMA_KEYS | {"$ref", "$defs"})
    assert {"summary", "next_action"} <= set(schema["required"])
    assert "learner_support" in schema["properties"]["conversation_type"]["enum"]
    assert "budget" in schema["properties"]["facts"]["items"]["properties"]["field"]["enum"]


def test_limits_omitted_from_request_schema_are_still_enforced_locally():
    with pytest.raises(ValueError):
        parse_json_response(json.dumps({"summary": "x" * 901, "next_action": ""}))
    with pytest.raises(ValueError):
        parse_json_response(json.dumps({"summary": "", "next_action": "", "unexpected": 1}))


def test_busy_response_is_retried_under_one_reservation(tmp_path, monkeypatch):
    responses = [httpx.Response(503), ok_response()]
    sleeps = []
    monkeypatch.setattr("trendytech_pilot.remote.time.sleep", sleeps.append)
    extractor = enabled(tmp_path, monkeypatch, lambda request: responses.pop(0))
    text, _usage = extractor.generate("system", "transcript")
    budget = read_json(extractor.budget_path)
    [entry] = budget["requests"].values()
    assert text == "{}" and sleeps == [extractor.busy_retry_delays[0]]
    assert entry["status"] == "usage_reported" and entry["busy_retries"] == 1
    assert budget["committed_inr"] == pytest.approx(extractor.cost(200, 100))


def test_persistently_busy_provider_keeps_one_uncertain_reservation(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("trendytech_pilot.remote.time.sleep", lambda seconds: None)

    def busy(request):
        calls.append(request)
        return httpx.Response(503)
    extractor = enabled(tmp_path, monkeypatch, busy)
    with pytest.raises(httpx.HTTPStatusError):
        extractor.generate("system", "transcript")
    [entry] = read_json(extractor.budget_path)["requests"].values()
    assert len(calls) == len(extractor.busy_retry_delays) + 1
    assert entry["status"] == "uncertain_reserve_retained"
    assert entry["busy_retries"] == len(extractor.busy_retry_delays)


def test_invalid_request_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def reject(request):
        calls.append(request)
        return httpx.Response(400)
    extractor = enabled(tmp_path, monkeypatch, reject)
    with pytest.raises(httpx.HTTPStatusError):
        extractor.generate("system", "transcript")
    assert len(calls) == 1
