"""Optional paid Gemini fallback. Disabled unless explicitly configured locally."""

import json
import os
import time

import httpx

from .budget import Budget, BudgetExceeded  # noqa: F401 -- BudgetExceeded is part of this module's interface
from .schema import CallExtraction
from .storage import digest

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
# The OpenAI-compatible endpoint rejected schemas containing these keywords with HTTP 400 (tested 2026-10-03).
# Every limit is still enforced locally: parse_json_response validates responses with the full pydantic model.
UNSUPPORTED_SCHEMA_KEYS = {"title", "default", "additionalProperties", "maxItems", "maxLength", "minLength", "minimum"}
# Capacity and rate-limit rejections. They are retried under the request's existing reservation.
BUSY_STATUSES = {429, 503}
COST_BASIS = "Provider tokens at published rates with INR 125/USD budget factor; invoice not reconciled"


def provider_schema(schema):
    """Inline $defs and drop keywords the provider rejects. Property names are never filtered."""
    defs = schema.get("$defs", {})

    def clean(node):
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return clean(defs[node["$ref"].rsplit("/", 1)[-1]])
        return {key: {name: clean(prop) for name, prop in value.items()} if key == "properties" else clean(value)
                for key, value in node.items() if key not in UNSUPPORTED_SCHEMA_KEYS | {"$defs"}}

    return clean(schema)


def post_with_busy_retries(client, url, reservation, delays, **request):
    """Retry capacity rejections under one reservation rather than stacking a new one per attempt."""
    for retries, delay in enumerate((*delays, None)):
        response = client.post(url, **request)
        if response.status_code not in BUSY_STATUSES or delay is None:
            return response, retries
        reservation.note(busy_retries=retries + 1)
        time.sleep(delay)


RESPONSE_SCHEMA = provider_schema(CallExtraction.model_json_schema())
# Part of the extraction identity and method freeze (extract.generation_config): any change makes old outputs
# stale. Google recommends the default temperature for Gemini 3 reasoning models.
# https://ai.google.dev/gemini-api/docs/gemini-3#temperature
GEMINI_GENERATION = {"endpoint": ENDPOINT, "temperature": 1.0, "reasoning_effort": "low", "max_completion_tokens": 2200,
                     "response_schema_sha256": digest(RESPONSE_SCHEMA)}


class GeminiExtractor:
    # Published standard rates checked 2026-10-01, valid through 2026-12-31.
    # https://ai.google.dev/gemini-api/docs/pricing
    model_id = "gemini-3.8-flash"
    input_usd_per_million = .75
    output_usd_per_million = 3.75
    generation = GEMINI_GENERATION
    busy_retry_delays = (5, 15, 45)

    def __init__(self, store, client=None):
        if os.getenv("PILOT_ALLOW_REMOTE") != "1":
            raise ValueError("Remote extraction is disabled. Explicit local opt-in is required.")
        self.key = os.getenv("GEMINI_API_KEY")
        if not self.key:
            raise ValueError("Set GEMINI_API_KEY in the ignored local .env; never in chat or Git.")
        self.store = store
        self.client = client or httpx.Client(timeout=180)
        self.budget = Budget(store)
        self.budget_path = self.budget.path
        self.fx_with_buffer = 125.0  # Budget assumption: INR 100/USD plus 25% tax/FX buffer; not a spot quote.

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.input_usd_per_million + output_tokens * self.output_usd_per_million) / 1e6 * self.fx_with_buffer

    def generate(self, system, user, max_tokens=None, schema=RESPONSE_SCHEMA, schema_name="call_extraction"):
        max_tokens = max_tokens or self.generation["max_completion_tokens"]
        input_bound = len((system + user + json.dumps(schema)).encode()) + 1024
        payload = {
            "model": self.model_id, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.generation["temperature"], "reasoning_effort": self.generation["reasoning_effort"],
            "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "strict": True,
                                                                        "schema": schema}},
        }
        with self.budget.reserve(self.cost(input_bound, max_tokens)) as reservation:
            response, retries = post_with_busy_retries(self.client, self.generation["endpoint"], reservation,
                                                       self.busy_retry_delays, json=payload,
                                                       headers={"Authorization": "Bearer " + self.key})
            response.raise_for_status()
            result = response.json()
            usage = result.get("usage", {})
            if "prompt_tokens" not in usage or "completion_tokens" not in usage:
                raise RuntimeError("Provider omitted usage; conservative budget reservation retained")
            n_in, n_out = usage["prompt_tokens"], usage["completion_tokens"]
            if any(type(n) is not int or n < 0 for n in (n_in, n_out)):
                raise ValueError("Provider usage must be nonnegative integers; reservation retained")
            actual_estimate = self.cost(n_in, n_out)
            reservation.settle(actual_estimate, input_tokens=n_in, output_tokens=n_out, busy_retries=retries)
            self.store.event(stage="remote_usage", request_id=reservation.request_id, model=self.model_id,
                             external_cost_inr=actual_estimate, input_tokens=n_in, output_tokens=n_out,
                             busy_retries=retries, cost_basis=COST_BASIS)
            choice = result["choices"][0]
            if choice.get("finish_reason") not in ("stop", None):
                raise ValueError("Remote output incomplete; token usage has been recorded")
            return choice["message"]["content"], {"input_tokens": n_in, "output_tokens": n_out,
                                                   "provider_cost_estimate_inr": actual_estimate}
