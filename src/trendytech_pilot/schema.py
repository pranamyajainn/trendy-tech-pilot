"""Claims require literal transcript support; generated recommendations are separate."""

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    segment_id: int = Field(ge=0)
    quote: str = Field(min_length=1, max_length=10000)

    @field_validator("quote")
    @classmethod
    def nonblank_quote(cls, value):
        if not value.strip():
            raise ValueError("Evidence quote cannot be blank")
        return value


class Fact(StrictModel):
    field: Literal["location", "current_role", "company", "experience", "current_ctc", "target_role",
                   "technology_interest", "course", "goal", "timeline", "budget", "availability"]
    value: str = Field(min_length=1, max_length=400)
    evidence: Evidence


class Signal(StrictModel):
    kind: Literal["goal", "urgency", "price_question", "payment_intent", "payment_claim", "followup_agreed",
                  "demo_requested", "low_interest", "no_time", "not_a_fit", "do_not_contact", "other"]
    description: str = Field(max_length=500)
    evidence: Evidence


class Objection(StrictModel):
    category: Literal["price", "time", "trust", "course_fit", "prerequisites", "career_outcomes",
                      "format", "timing", "decision_maker", "other"]
    concern: str = Field(min_length=1, max_length=500)
    evidence: Evidence
    response: str | None = Field(default=None, max_length=700)
    response_evidence: Evidence | None = None
    resolution: Literal["resolved", "partly_addressed", "unresolved", "unclear"] = "unclear"
    resolution_evidence: Evidence | None = None


class Pitch(StrictModel):
    topic: str = Field(max_length=400)
    evidence: Evidence
    prospect_response: str | None = Field(default=None, max_length=400)
    response_evidence: Evidence | None = None


class CallExtraction(StrictModel):
    conversation_type: Literal["sales", "enrollment_or_payment", "learner_support", "administrative",
                               "brief_followup", "unusable", "unclear"] = "unclear"
    purpose_evidence: Evidence | None = None
    summary: str = Field(max_length=900)
    facts: list[Fact] = Field(default_factory=list, max_length=20)
    signals: list[Signal] = Field(default_factory=list, max_length=15)
    objections: list[Objection] = Field(default_factory=list, max_length=12)
    pitches: list[Pitch] = Field(default_factory=list, max_length=12)
    next_action: str = Field(max_length=700)
    uncertainties: list[str] = Field(default_factory=list, max_length=10)


# Evidence acceptance rules are part of the extraction identity and method freeze.
EVIDENCE_RULES_VERSION = "evidence-v3"
FILLERS = {"uh", "uhh", "um", "umm", "hmm", "ah", "er", "erm"}


def normalise(text):
    # Sentence punctuation and spoken fillers carry no evidence; decimal points, hyphens and apostrophes do.
    text = re.sub(r"[,!?;:\"“”()]|\.(?!\d)", " ", text.casefold())
    return " ".join(word for word in text.split() if word not in FILLERS)


def quote_supported(quote, segment_id, texts, order):
    """The quote lies in the cited segment or runs from it into an adjacent one, because ASR splits
    sentences mid-way. A quote found only in a neighbour is a wrong citation and fails."""
    quote = normalise(quote)
    if segment_id not in texts or not quote:
        return False
    cited = normalise(texts[segment_id])
    if quote in cited:
        return True
    position = order.index(segment_id)
    for neighbour in order[max(position - 1, 0):position] + order[position + 1:position + 2]:
        other = normalise(texts[neighbour])
        joined = f"{other} {cited}" if neighbour < segment_id else f"{cited} {other}"
        if quote in joined and quote not in other:
            return True
    return False


def validate_evidence(extraction, transcript):
    """Reject invented/mislocated quotes. Support does not prove semantic correctness."""
    texts = {s["id"]: s["text"] for s in transcript["segments"]}
    order = [s["id"] for s in transcript["segments"]]
    errors = []

    def check(evidence, location):
        if evidence is None:
            return
        if not quote_supported(evidence["quote"], evidence["segment_id"], texts, order):
            errors.append(location)

    data = extraction.model_dump() if isinstance(extraction, CallExtraction) else extraction
    if data.get("conversation_type") in ("learner_support", "administrative") and (
            any(s["kind"] != "do_not_contact" for s in data.get("signals", []))
            or data.get("objections") or data.get("pitches")):
        # Service calls must not become buying signals; contact suppression always survives.
        errors.append("service_call_has_sales_content")
    check(data.get("purpose_evidence"), "purpose_evidence")
    if data.get("conversation_type", "unclear") not in ("unclear", "unusable") and not data.get("purpose_evidence"):
        errors.append("purpose_missing_evidence")
    for collection in ["facts", "signals", "objections", "pitches"]:
        for index, item in enumerate(data.get(collection, [])):
            for key in ["evidence", "response_evidence", "resolution_evidence"]:
                check(item.get(key), f"{collection}[{index}].{key}")
            if collection == "objections":
                if item.get("response") and not item.get("response_evidence"):
                    errors.append(f"{collection}[{index}].response_missing_evidence")
                if item.get("resolution") == "resolved" and not item.get("resolution_evidence"):
                    errors.append(f"{collection}[{index}].resolution_missing_evidence")
            if collection == "pitches" and item.get("prospect_response") and not item.get("response_evidence"):
                errors.append(f"{collection}[{index}].response_missing_evidence")
    return errors
