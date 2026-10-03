"""Optional paid Gemini fallback. Disabled unless explicitly configured locally."""

import json
import math
import os
import uuid

import httpx

from .schema import CallExtraction
from .storage import read_json, write_json


class BudgetExceeded(RuntimeError):
    pass


class GeminiExtractor:
    # Published standard rates checked 2026-10-01, valid through 2026-12-31.
    # https://ai.google.dev/gemini-api/docs/pricing
    model_id = "gemini-3.8-flash"
    input_usd_per_million = .75
    output_usd_per_million = 3.75

    def __init__(self, store, client=None):
        if os.getenv("PILOT_ALLOW_REMOTE") != "1":
            raise ValueError("Remote extraction is disabled. Explicit local opt-in is required.")
        self.key = os.getenv("GEMINI_API_KEY")
        if not self.key:
            raise ValueError("Set GEMINI_API_KEY in the ignored local .env; never in chat or Git.")
        self.store = store
        self.client = client or httpx.Client(timeout=180)
        configured_cap = float(os.getenv("PILOT_API_CAP_INR", "500"))
        if not math.isfinite(configured_cap) or configured_cap <= 0:
            raise ValueError("API cap must be finite and positive")
        self.cap = min(configured_cap, 500)
        self.fx_with_buffer = 125.0  # Budget assumption: INR 100/USD plus 25% tax/FX buffer; not a spot quote.
        self.budget_path = store.path("api-budget.json")

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.input_usd_per_million + output_tokens * self.output_usd_per_million) / 1e6 * self.fx_with_buffer

    def generate(self, system, user, max_tokens=2200):
        import fcntl

        # One remote request at a time, including across CLI processes. Uncertain requests retain reserves.
        with self.store.path("api-budget.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            schema = CallExtraction.model_json_schema()
            input_bound = len((system + user + json.dumps(schema)).encode()) + 1024
            reserve = self.cost(input_bound, max_tokens)
            budget = read_json(self.budget_path) if self.budget_path.exists() else {"committed_inr": 0, "requests": {}}
            if budget["committed_inr"] + reserve > self.cap:
                raise BudgetExceeded("The conservative API reserve would exceed the configured INR cap")
            request_id = str(uuid.uuid4())
            budget["committed_inr"] += reserve
            budget["requests"][request_id] = {"status": "reserved", "inr": reserve}
            write_json(self.budget_path, budget)
            payload = {
                "model": self.model_id, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                # Google recommends the default temperature for Gemini 3 reasoning models.
                # https://ai.google.dev/gemini-api/docs/gemini-3#temperature
                "temperature": 1.0, "reasoning_effort": "low", "max_completion_tokens": max_tokens,
                "response_format": {"type": "json_schema", "json_schema": {"name": "call_extraction", "strict": True, "schema": schema}},
            }
            try:
                response = self.client.post("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                                            headers={"Authorization": "Bearer " + self.key}, json=payload)
                response.raise_for_status()
                result = response.json()
                usage = result.get("usage", {})
                if "prompt_tokens" not in usage or "completion_tokens" not in usage:
                    raise RuntimeError("Provider omitted usage; conservative budget reservation retained")
                n_in, n_out = usage["prompt_tokens"], usage["completion_tokens"]
                if any(type(n) is not int or n < 0 for n in (n_in, n_out)):
                    raise ValueError("Provider usage must be nonnegative integers; reservation retained")
                actual_estimate = self.cost(n_in, n_out)
                budget["committed_inr"] += actual_estimate - reserve
                budget["requests"][request_id] = {"status": "usage_reported", "inr": actual_estimate,
                                                   "input_tokens": n_in, "output_tokens": n_out}
                write_json(self.budget_path, budget)
                self.store.event(stage="remote_usage", request_id=request_id, model=self.model_id,
                                 external_cost_inr=actual_estimate, input_tokens=n_in, output_tokens=n_out,
                                 cost_basis="Provider tokens at published rates with INR 125/USD budget factor; invoice not reconciled")
                choice = result["choices"][0]
                if choice.get("finish_reason") not in ("stop", None):
                    raise ValueError("Remote output incomplete; token usage has been recorded")
                return choice["message"]["content"], {"input_tokens": n_in, "output_tokens": n_out,
                                                       "provider_cost_estimate_inr": actual_estimate}
            except (httpx.HTTPError, KeyError, RuntimeError, ValueError):
                # Unknown provider billing is never silently counted as free.
                if budget["requests"][request_id]["status"] == "reserved":
                    budget["requests"][request_id]["status"] = "uncertain_reserve_retained"
                    write_json(self.budget_path, budget)
                raise
