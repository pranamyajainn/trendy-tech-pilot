import json

import httpx
import pytest

from trendytech_pilot.extract import parse_json_response
from trendytech_pilot.remote_groq import RESPONSE_SCHEMA, GroqExtractor, drop_nulls, strict_schema


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def test_strict_schema_requires_every_property_and_forbids_extras():
    objects = [n for n in walk(RESPONSE_SCHEMA) if "properties" in n]
    assert objects and all(set(n["required"]) == set(n["properties"]) and n["additionalProperties"] is False
                           for n in objects)
    assert not any("$ref" in n or "maxLength" in n for n in walk(RESPONSE_SCHEMA))
    # An optional enum with a default becomes nullable instead of disappearing.
    assert {"type": "null"} in RESPONSE_SCHEMA["properties"]["conversation_type"]["anyOf"]


def test_strict_schema_keeps_already_nullable_fields_single_level():
    schema = strict_schema({"type": "object", "properties": {"x": {"anyOf": [{"type": "string"}, {"type": "null"}]}}})
    assert schema["properties"]["x"] == {"anyOf": [{"type": "string"}, {"type": "null"}]}


def test_nulls_for_defaulted_fields_fall_back_to_model_defaults():
    raw = {"conversation_type": None, "purpose_evidence": None, "summary": "", "next_action": "", "facts": [],
           "signals": [], "pitches": [], "uncertainties": [],
           "objections": [{"category": "price", "concern": "Too costly", "evidence": {"segment_id": 0, "quote": "costly"},
                           "response": None, "response_evidence": None, "resolution": None, "resolution_evidence": None}]}
    parsed = parse_json_response(json.dumps(drop_nulls(raw)))
    assert parsed.conversation_type == "unclear" and parsed.objections[0].resolution == "unclear"


def test_groq_usage_is_budgeted(tmp_path, monkeypatch):
    from trendytech_pilot.storage import Store, read_json
    monkeypatch.setenv("PILOT_ALLOW_REMOTE", "1")
    monkeypatch.setenv("GROQ_API_KEY", "synthetic-test-key")
    body = {"usage": {"prompt_tokens": 3000, "completion_tokens": 500},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"summary": "", "next_action": ""})}}]}
    extractor = GroqExtractor(Store(tmp_path), httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body))))
    text, _usage = extractor.generate("system", "transcript")
    assert json.loads(text) == {"summary": "", "next_action": ""}
    [entry] = read_json(extractor.budget.path)["requests"].values()
    assert entry["status"] == "usage_reported" and entry["inr"] == pytest.approx(extractor.cost(3000, 500))
