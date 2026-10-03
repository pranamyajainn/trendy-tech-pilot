"""Second extraction model from a different family (OpenAI gpt-oss on Groq), with the same budget controls."""

import json
import os

import httpx

from .budget import Budget
from .remote import COST_BASIS, post_with_busy_retries
from .schema import CallExtraction
from .storage import digest

ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"
# Groq strict mode needs every property required and additionalProperties false; optional fields become nullable.
# Length and count limits are left to local pydantic validation, as for Gemini.
DROPPED_KEYS = {"title", "default", "maxItems", "maxLength", "minLength", "minimum"}


def strict_schema(schema):
    defs = schema.get("$defs", {})

    def clean(node, optional=False):
        if isinstance(node, list):
            return [clean(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return clean(defs[node["$ref"].rsplit("/", 1)[-1]], optional)
        out = {k: clean(v) for k, v in node.items() if k not in DROPPED_KEYS | {"$defs", "properties", "required"}}
        if "properties" in node:
            required = set(node.get("required", []))
            out["properties"] = {name: clean(prop, name not in required) for name, prop in node["properties"].items()}
            out["required"] = list(node["properties"])
            out["additionalProperties"] = False
        if optional and "anyOf" not in out and out.get("type") not in (None, "array"):
            out = {"anyOf": [out, {"type": "null"}]}
        return out

    return clean(schema)


RESPONSE_SCHEMA = strict_schema(CallExtraction.model_json_schema())
GROQ_GENERATION = {"endpoint": ENDPOINT, "reasoning_effort": "medium", "max_completion_tokens": 8192,
                   "response_schema_sha256": digest(RESPONSE_SCHEMA)}


def drop_nulls(value):
    """Strict mode returns null for omitted optional fields; pydantic defaults apply when the key is absent."""
    if isinstance(value, dict):
        return {k: drop_nulls(v) for k, v in value.items() if v is not None or k.endswith("evidence") or k == "response"
                or k == "prospect_response"}
    if isinstance(value, list):
        return [drop_nulls(v) for v in value]
    return value


class GroqExtractor:
    # Published Groq rates observed 3 Oct 2026: USD 0.15 input / 0.60 output per 1M tokens.
    # https://console.groq.com/docs/model/openai/gpt-oss-120b. Free-tier usage is recorded at these rates too.
    model_id = "openai/gpt-oss-120b"
    input_usd_per_million = .15
    output_usd_per_million = .60
    generation = GROQ_GENERATION
    busy_retry_delays = (5, 15, 45)

    def __init__(self, store, client=None):
        if os.getenv("PILOT_ALLOW_REMOTE") != "1":
            raise ValueError("Remote extraction is disabled. Explicit local opt-in is required.")
        self.key = os.getenv("GROQ_API_KEY")
        if not self.key:
            raise ValueError("Set GROQ_API_KEY in the ignored local .env; never in chat or Git.")
        self.store, self.budget = store, Budget(store)
        self.client = client or httpx.Client(timeout=300)
        self.fx_with_buffer = 125.0

    def cost(self, input_tokens, output_tokens):
        return (input_tokens * self.input_usd_per_million + output_tokens * self.output_usd_per_million) / 1e6 * self.fx_with_buffer

    def generate(self, system, user, max_tokens=None, schema=RESPONSE_SCHEMA, schema_name="call_extraction"):
        max_tokens = max_tokens or self.generation["max_completion_tokens"]
        payload = {"model": self.model_id, "messages": [{"role": "system", "content": system},
                                                        {"role": "user", "content": user}],
                   "reasoning_effort": self.generation["reasoning_effort"], "max_completion_tokens": max_tokens,
                   "response_format": {"type": "json_schema",
                                       "json_schema": {"name": schema_name, "strict": True, "schema": schema}}}
        input_bound = len((system + user + json.dumps(schema)).encode()) + 1024
        with self.budget.reserve(self.cost(input_bound, max_tokens), purpose="extract:" + self.model_id) as reservation:
            response, retries = post_with_busy_retries(self.client, self.generation["endpoint"], reservation,
                                                       self.busy_retry_delays, json=payload,
                                                       headers={"Authorization": "Bearer " + self.key})
            response.raise_for_status()
            result = response.json()
            usage = result.get("usage", {})
            n_in, n_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
            if any(type(n) is not int or n < 0 for n in (n_in, n_out)):
                raise RuntimeError("Provider omitted usage; conservative budget reservation retained")
            actual = self.cost(n_in, n_out)
            reservation.settle(actual, input_tokens=n_in, output_tokens=n_out, busy_retries=retries)
            self.store.event(stage="remote_usage", request_id=reservation.request_id, model=self.model_id,
                             external_cost_inr=actual, input_tokens=n_in, output_tokens=n_out, busy_retries=retries,
                             cost_basis=COST_BASIS.replace("INR 125/USD", "INR 125/USD (Groq)"))
            choice = result["choices"][0]
            if choice.get("finish_reason") not in ("stop", None):
                raise ValueError("Remote output incomplete; token usage has been recorded")
            content = json.dumps(drop_nulls(json.loads(choice["message"]["content"])))
            return content, {"input_tokens": n_in, "output_tokens": n_out, "provider_cost_estimate_inr": actual}
