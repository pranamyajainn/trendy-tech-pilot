import httpx
import pytest

from trendytech_pilot.remote import BudgetExceeded, GeminiExtractor
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
