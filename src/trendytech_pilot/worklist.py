"""Open-lead worklist (proposal item 03), decided by a frozen, evidence-ranked rule rather than a fitted model.

Pipeline (each step deterministic from stored inputs; docs/pipeline/ explains the method and the runbook):
1. select   Draw the extra open leads once, by a seeded hash order, and store the list.
2. show     Print every call of a lead with segment ids and timestamps, for whoever codes it.
3. (coding) One file per lead, data/review/worklist/<lead>.json, schema lead-coding-v1 (docs/pipeline/CODING-GUIDE.md).
            Coding records what was said, with verbatim evidence; it never sets the category.
4. check    Validate every coded file: schema, complete reading, verbatim quotes, privacy and wording.
5. freeze   Fix the rule, the export date and the past-lead evidence, with a hash, before any holdout lead is coded.
6. export   Categorise each lead by the frozen rule and write the client sheets (worklist, journeys, calls).

The past-lead evidence uses the customer cohort against the random non-buyer sample, both read only up to the
purchase boundary (patterns.pre_purchase). Open leads are coded from all of their calls, so a lead whose own calls
show an enrolment is caught by the status check, not hidden by a boundary.
"""

import datetime as dt
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .artifacts import current_extraction, current_source
from .patterns import DECLINE, OPEN_POPULATION, OPEN_SAMPLE, conversion_rate, pre_purchase
from .profile import lead_calls
from .storage import digest, read_json, write_json

SCHEMA_VERSION = "lead-coding-v1"
SELECTION_SEED = "worklist-pilot-v1"
EXTRA_LEADS = 31  # 19 open pilot leads + 31 drawn from the non-buyer sample = 50, as the owner chose (7 Oct 2026).
# The rule, fixed by the method review of 7 Oct 2026 (decision record in docs/pipeline/METHOD.md). Changing any
# value is a new rule version and needs a new freeze.
RULE = {"version": "worklist-rule-v1", "hot_within_days": 45, "dormant_after_days": 90, "min_conversation_seconds": 180,
        "order": ["Check status", "Not reached", "Outside target", "Cold: declined", "Hot", "Dormant: reconfirm", "Warm"]}
CATEGORY_ORDER = {c: i for i, c in enumerate(["Hot", "Warm", "Check status", "Dormant: reconfirm", "Cold: declined",
                                              "Outside target", "Not reached"])}

# ---------- coding schema ----------
class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    call_id: str
    date: str
    segment_id: int
    start_seconds: float
    quote: str


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display: str
    evidence: Evidence | None


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experience: Item
    role: Item
    ctc: Item
    location: Item


class Learner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Literal["self", "someone_else", "unclear"]
    evidence: Evidence | None


class TargetCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Literal["in_target", "outside_target", "not_asked"]
    reason: str | None
    evidence: Evidence | None


class StatusCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    needed: bool
    reason: str | None
    evidence: Evidence | None


class Stance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: Literal["commitment", "conditional_commitment", "dated_deferral", "open_deferral", "open", "declined",
                   "product_not_sold", "none"]
    detail: str
    condition: str | None
    condition_can_be_met: Literal["yes", "no", "unclear"] | None
    condition_reason: str | None
    evidence: Evidence | None


class LastLive(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str
    call_id: str


class Objection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: Literal["price", "payment method", "format", "timing", "course fit", "placement", "prerequisites",
                   "family or approval", "trust", "other"]
    detail: str
    status: Literal["open", "resolved"]
    evidence: Evidence | None


class Swot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strength: str
    weakness: str
    opportunity: str
    threat: str


class WhatToSay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opener: str
    question: str
    ask: str


class LeadCoding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_: Literal["lead-coding-v1"] = Field(alias="schema")
    lead: str
    coder: str
    calls_read: list[str]
    status_check: StatusCheck
    profile: Profile
    learner: Learner
    target_check: TargetCheck
    stance: Stance
    refused_after_commitment: bool
    last_live_conversation: LastLive | None
    attempts_since_last_live: int
    buying_intent: str
    their_next_step: str | None
    objections: list[Objection]
    swot: Swot
    next_action: str
    what_to_say: WhatToSay


# ---------- 1. selection ----------
def open_pilot_leads(store):
    """Pilot leads with no CRM conversion flag on any call, in lead order."""
    return sorted(lead for lead, calls in lead_calls(store, "pilot").items()
                  if all(c["crm_conversion_flag"] != "Yes" for c in calls))


def select(store, force=False):
    """The worklist leads: every open pilot lead plus EXTRA_LEADS drawn from the non-buyer sample by seeded hash
    order. Stored once; later runs reuse the stored list so the worklist cannot drift."""
    path = store.path("review", "worklist-selection.json")
    if path.exists() and not force:
        return read_json(path)
    pool = sorted(lead_calls(store, "open_sample"))
    drawn = sorted(pool, key=lambda lead: digest([SELECTION_SEED, lead]))[:EXTRA_LEADS]
    selection = {"seed": SELECTION_SEED, "pool": "open_sample", "pool_size": len(pool), "pool_hash": digest(pool),
                 "pilot_open": open_pilot_leads(store), "drawn": sorted(drawn),
                 "created_at": dt.datetime.now(dt.UTC).isoformat()}
    write_json(path, selection)
    return selection


def worklist_leads(store):
    s = select(store)
    return s["pilot_open"] + s["drawn"]


def calls_of(store, lead):
    for group in ("pilot", "open_sample", "customers"):
        calls = lead_calls(store, group).get(lead)
        if calls:
            return sorted(calls, key=lambda c: (c["created_on"], c["call_id"]))
    raise ValueError(f"Lead {lead} is not in any cohort")


def holdout_leads(store):
    return {str(c["lead_number"]) for c in read_json(store.path("selection.json"))["calls"] if c["split"] == "holdout"}


# ---------- 2. show ----------
def stamp(seconds):
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def show(store, lead):
    """Every call of one lead, as a coder reads it."""
    if lead in holdout_leads(store) and not store.path("review", "worklist-freeze.json").exists():
        raise ValueError("Holdout leads may be read only after `pilot worklist freeze`")
    lines = []
    for call in calls_of(store, lead):
        x = current_extraction(store, call["call_id"])["extraction"]
        lines.append(f"\n=== call {call['call_id']} | {call['created_on']} | {int(call['duration_seconds'])}s | "
                     f"{x['conversation_type']}")
        for seg in current_source(store, call["call_id"])["segments"]:
            role = {"agent": "COUNSELLOR", "prospect": "LEAD"}.get(seg.get("role"), "SPEAKER")
            lines.append(f"[{seg['id']} | {stamp(seg['start'])}] {role}: {seg['text']}")
    return "\n".join(lines)


# ---------- 4. check ----------
CLIENT_FIELDS = ("buying_intent", "their_next_step", "next_action")
BANNED = re.compile(r"\b(model|AI|gemini|extraction|signal|segment|cohort|h[eo]ld[- ]?out|development|fingerprint|transcript)\b",
                    re.IGNORECASE)
PRONOUNS = re.compile(r"\b(he|she|him|her|his|hers|himself|herself)\b", re.IGNORECASE)
CONTACT = re.compile(r"https?://|www\.|@[a-z0-9-]+\.|\b\d{10}\b|\+91", re.IGNORECASE)


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", text.lower())).strip()


def client_texts(coding):
    texts = {k: getattr(coding, k) for k in CLIENT_FIELDS}
    texts.update({f"objections[{i}].detail": o.detail for i, o in enumerate(coding.objections)})
    texts.update({f"swot.{k}": v for k, v in coding.swot.model_dump().items()})
    texts.update({f"what_to_say.{k}": v for k, v in coding.what_to_say.model_dump().items()})
    texts.update({"stance.detail": coding.stance.detail, "stance.condition": coding.stance.condition})
    return {k: v for k, v in texts.items() if v}


def evidences(coding):
    found = [("status_check", coding.status_check.evidence), ("learner", coding.learner.evidence),
             ("target_check", coding.target_check.evidence), ("stance", coding.stance.evidence)]
    found += [(f"profile.{k}", getattr(coding.profile, k).evidence) for k in Profile.model_fields]
    found += [(f"objections[{i}]", o.evidence) for i, o in enumerate(coding.objections)]
    return [(where, e) for where, e in found if e is not None]


def check_coding(store, lead, data):
    """Problems with one coded file; an empty list means it may be used."""
    try:
        coding = LeadCoding.model_validate(data)
    except Exception as exc:  # noqa: BLE001 -- report the schema error as a problem, not a crash
        return [f"schema: {exc}"]
    problems = []
    calls = calls_of(store, lead)
    by_id = {c["call_id"]: c for c in calls}
    if coding.lead != lead:
        problems.append(f"lead field {coding.lead} != file lead {lead}")
    if set(coding.calls_read) != set(by_id):
        problems.append(f"calls_read must list all {len(by_id)} calls of the lead")
    for where, ev in evidences(coding):
        call = by_id.get(ev.call_id)
        if call is None:
            problems.append(f"{where}: call {ev.call_id} is not this lead's"); continue
        if ev.date != call["created_on"][:10]:
            problems.append(f"{where}: date {ev.date} != call date {call['created_on'][:10]}")
        seg = next((s for s in current_source(store, ev.call_id)["segments"] if s["id"] == ev.segment_id), None)
        if seg is None:
            problems.append(f"{where}: segment {ev.segment_id} not in call"); continue
        if abs(seg["start"] - ev.start_seconds) > 2:
            problems.append(f"{where}: start {ev.start_seconds} != segment start {seg['start']}")
        if not norm(ev.quote) or norm(ev.quote) not in norm(seg["text"]):
            problems.append(f"{where}: quote is not verbatim from segment {ev.segment_id}")
        if len(ev.quote.split()) > 15:
            problems.append(f"{where}: quote longer than 15 words")
    names = {w.lower() for c in calls for field in ("lead_name", "salesperson", "current_owner")
             for w in re.findall(r"[A-Za-z]{3,}", c.get(field) or "")} - {"trendytech"}
    for where, text in client_texts(coding).items():
        for rx, what in ((BANNED, "internal word"), (PRONOUNS, "gendered pronoun"), (CONTACT, "contact detail")):
            if m := rx.search(text):
                problems.append(f"{where}: {what} {m.group()!r}")
        if hit := names & set(re.findall(r"[a-z]{3,}", text.lower())):
            problems.append(f"{where}: personal name {sorted(hit)}")
    for k, v in coding.swot.model_dump().items():
        if len(v.split()) > 25:
            problems.append(f"swot.{k}: over 25 words")
    if len(coding.next_action.split()) > 50:
        problems.append("next_action: over 50 words")
    s = coding.stance
    if s.value == "conditional_commitment" and not (s.condition and s.condition_can_be_met and s.condition_reason):
        problems.append("stance: a conditional commitment needs condition, condition_can_be_met and condition_reason")
    if s.value == "none" and coding.last_live_conversation is not None:
        problems.append("stance none (never reached) but a last live conversation is given")
    if s.value != "none" and coding.last_live_conversation is None and s.value != "declined":
        problems.append("a stance other than none needs the last live conversation")
    if coding.last_live_conversation:
        live = by_id.get(coding.last_live_conversation.call_id)
        if live is None or live["created_on"][:10] != coding.last_live_conversation.date:
            problems.append("last_live_conversation does not match one of the lead's calls")
    if coding.target_check.value == "outside_target" and not (coding.target_check.reason and coding.target_check.evidence):
        problems.append("target_check: outside_target needs a reason and evidence")
    if coding.status_check.needed and not (coding.status_check.reason and coding.status_check.evidence):
        problems.append("status_check: needed requires a reason and evidence")
    return problems


def coding_path(store, lead):
    return store.path("review", "worklist", f"{lead}.json")


def check_all(store, leads=None):
    """{lead: problems} for every worklist lead; a missing file is a problem."""
    out = {}
    for lead in leads or worklist_leads(store):
        path = coding_path(store, lead)
        out[lead] = check_coding(store, lead, read_json(path)) if path.exists() else ["not coded yet"]
    return out


# ---------- 5. rule and evidence ----------
def export_date(store):
    """The day the CRM export ends: the last recorded call across every cohort."""
    return max(c["created_on"][:10] for group in ("pilot", "customers", "open_sample")
               for calls in lead_calls(store, group).values() for c in calls)


def categorise(coding, as_of, rule=RULE):
    """(category, reason) for one coded lead under the frozen rule. First matching statement wins."""
    c = LeadCoding.model_validate(coding) if isinstance(coding, dict) else coding
    s = c.stance
    days = (dt.date.fromisoformat(as_of) - dt.date.fromisoformat(c.last_live_conversation.date)).days \
        if c.last_live_conversation else None
    since = f"{days} days before the data ends" if days is not None else ""
    if c.status_check.needed:
        return "Check status", c.status_check.reason
    if s.value == "none":
        n = c.attempts_since_last_live
        return "Not reached", f"No conversation in {n} recorded call{'s' if n != 1 else ''}"
    if c.target_check.value == "outside_target":
        return "Outside target", c.target_check.reason
    if s.value in ("declined", "product_not_sold"):
        return "Cold: declined", s.detail
    if s.value == "conditional_commitment" and s.condition_can_be_met == "no":
        return "Cold: declined", f"Condition TrendyTech cannot meet: {s.condition}"
    committed = s.value == "commitment" or (s.value == "conditional_commitment" and s.condition_can_be_met == "yes")
    if committed and not c.refused_after_commitment and days is not None and days <= rule["hot_within_days"]:
        return "Hot", s.detail + (f" (condition: {s.condition})" if s.condition else "") + f"; {since}"
    if days is not None and days > rule["dormant_after_days"]:
        return "Dormant: reconfirm", f"Last conversation {since}; information is stale"
    if committed:
        return "Warm", f"Re-open: commitment from {since}"
    return "Warm", s.detail


def fit(store, lead, pre):
    """Outside target / in target / unknown from the lead profile, counting a value only if a pre-purchase call is
    among its cited evidence (most customer profile facts were learnt after payment)."""
    path = store.path("review", "profiles", f"{lead}.json")
    if not path.exists():
        return "unknown"
    p = read_json(path)
    ids = {c["call_id"] for c, _ in pre}

    def known(field, ev):
        v = p["profile"].get(field)
        if v in (None, "", "unknown", "not_mentioned"):
            return None
        return v if any(e["call_id"] in ids for e in (p.get("evidence") or {}).get(ev, [])) else None
    if (known("background", "background") == "non_it" or known("fresher", "fresher") == "yes"
            or known("career_gap", "career_gap") == "over_1_year" or known("employment", "employment") == "student"
            or known("current_role_group", "current_role") in ("non_it", "student")):
        return "outside"
    return "in_or_unknown"


def past_lead_evidence(store, rule=RULE):
    """For each category the rule can give a closed past lead: customers and sampled non-buyers in it, and the
    weighted conversion rate with its interval. Read at each past lead's latest pre-purchase conversation, so the
    Hot figure is inflated by the paying call and is never shown as a rate."""
    counts = {}
    for group, buyer in (("customers", True), ("open_sample", False)):
        for lead, calls in lead_calls(store, group).items():
            pre = pre_purchase(store, calls)
            convs = [x for c, x in pre if c["duration_seconds"] >= rule["min_conversation_seconds"]]
            if not convs:
                continue
            kinds = {g["kind"] for g in convs[-1]["signals"]}
            category = ("Outside target" if fit(store, lead, pre) == "outside" else
                        "Hot" if "payment_intent" in kinds and not kinds & DECLINE else
                        "Cold: declined" if kinds & DECLINE else "Warm")
            b, n = counts.get(category, (0, 0))
            counts[category] = (b + buyer, n + (not buyer))
    out = {}
    for category, (b, n) in sorted(counts.items()):
        rate, bounds = conversion_rate(b, n, OPEN_SAMPLE, OPEN_POPULATION)
        out[category] = {"customers": b, "non_buyers_sampled": n, "rate": rate, "low": bounds[0], "high": bounds[1]}
    return out


def freeze(store, force=False):
    """Fix the rule, export date and evidence. Refused once any holdout lead has been coded, so the holdout is
    scored only by a rule that never saw it."""
    path = store.path("review", "worklist-freeze.json")
    if path.exists() and not force:
        raise ValueError("The worklist rule is already frozen; a new rule version needs --force and a recorded reason")
    coded_holdout = [lead for lead in holdout_leads(store) if coding_path(store, lead).exists()]
    if coded_holdout:
        raise ValueError(f"Holdout leads were coded before the freeze: {coded_holdout}")
    record = {"rule": RULE, "rule_hash": digest(RULE), "as_of": export_date(store),
              "evidence": past_lead_evidence(store), "timing": buyer_timing(store), "selection_hash": digest(select(store)),
              "frozen_at": dt.datetime.now(dt.UTC).isoformat()}
    write_json(path, record)
    return record


def frozen(store):
    path = store.path("review", "worklist-freeze.json")
    if not path.exists():
        raise ValueError("Run `pilot worklist freeze` first")
    record = read_json(path)
    if record["rule_hash"] != digest(RULE):
        raise ValueError("The rule in code differs from the frozen rule; refreeze with a new rule version")
    return record


# ---------- 6. export ----------
TYPE_LABEL = {"sales": "Sales call", "brief_followup": "Follow-up", "enrollment_or_payment": "Enrolment or payment",
              "learner_support": "Learner support", "administrative": "Administrative", "unusable": "No conversation",
              "unclear": "Unclear"}
RESOLUTION_LABEL = {"resolved": "resolved", "partly_addressed": "partly addressed", "unresolved": "not resolved",
                    "not_addressed": "not addressed"}
SCOPE_NOTE = ("As of the last recorded call in the export. Confirm each lead's current status in LeadSquared before "
              "calling.")
REVIEW_COLUMNS = ["lead", "category", "claim", "date", "timestamp", "quote", "verdict", "note", "reviewer", "reviewed_at"]


def human(day):
    d = dt.date.fromisoformat(day[:10])
    return f"{d.day} {d.strftime('%b')} {d.year}"


def cite(ev):
    return f"{human(ev.date)} at {stamp(ev.start_seconds)}: “{ev.quote}”" if ev else ""


_NAMES = {}


def name_words(store):
    """Person-name words from every CRM row: (staff, leads). Words that also occur in lower case in the call
    summaries are ordinary words ("mark", "will") and are left alone."""
    if store.root not in _NAMES:
        staff, leads, corpus = set(), set(), set()
        for group in ("pilot", "customers", "open_sample"):
            for calls in lead_calls(store, group).values():
                for c in calls:
                    staff |= set(re.findall(r"[A-Za-z]{3,}", f"{c.get('salesperson') or ''} {c.get('current_owner') or ''}"))
                    leads |= set(re.findall(r"[A-Za-z]{3,}", c.get("lead_name") or ""))
                    x = current_extraction(store, c["call_id"])["extraction"]
                    corpus |= set(re.findall(r"\b[a-z]{3,}\b", f"{x['summary']} {x['next_action'] or ''}"))
        common = corpus | {"trendytech", "trendy", "tech", "data", "sumit"}
        _NAMES[store.root] = ({w.lower() for w in staff} - common, {w.lower() for w in leads} - common)
    return _NAMES[store.root]


def redactor(store, calls):
    """Replace person names in generated call summaries: this lead's own name words become "the lead", staff
    names "the counsellor", any other CRM name "another person"."""
    staff, lead_names = name_words(store)
    own = {w.lower() for c in calls for w in re.findall(r"[A-Za-z]{3,}", c.get("lead_name") or "")}

    def clean(text):
        def swap(m):
            w = m.group().lower()
            if w in own:
                return "the lead"
            if w in staff:
                return "the counsellor"
            return "another person" if w in lead_names else m.group()
        text = ROLE_NAME.sub(lambda m: m.group(1), text)
        text = neutral(re.sub(r"\b[A-Za-z]{3,}\b", swap, text))
        return wording(text)
    return clean


# Generated summaries sometimes guess a person's gender; nothing shown to the client should. Object "her" (before a
# preposition, adverb or punctuation) becomes "them"; possessive "her" becomes "their".
PRONOUN_SWAPS = [(r"\b(she|he) (is|was|has)\b", lambda m: "they " + {"is": "are", "was": "were", "has": "have"}[m.group(2)]),
                 (r"\b(herself|himself)\b", "themselves"), (r"\b(she|he)\b", "they"), (r"\bhers\b", "theirs"),
                 (r"\b(her|him)(?=\s+(?:back|to|in|on|at|about|that|again|later|for|with|after|when)\b|[.,;:!?)]|$)", "them"),
                 (r"\bhim\b", "them"), (r"\b(her|his)\b", "their")]


# The sheets say counsellor and lead; generated summaries say agent and prospect.
TERMS = [(r"\bagents\b", "counsellors"), (r"\bagent\b", "counsellor"), (r"\bprospects\b", "leads"),
         (r"\bprospect\b", "lead"), (r"\b(the (?:lead|counsellor))(?:\s+\1\b)+", r"\1")]


def wording(text):
    for pattern, repl in TERMS:
        text = re.sub(pattern, lambda m, r=repl: (m.expand(r) if m.group()[0].islower() else m.expand(r).capitalize()),
                      text, flags=re.IGNORECASE)
    return text


def neutral(text):
    for pattern, repl in PRONOUN_SWAPS:
        text = re.sub(pattern, lambda m, r=repl: (r(m) if callable(r) else r) if m.group()[0].islower()
                      else (r(m) if callable(r) else r).capitalize(), text, flags=re.IGNORECASE)
    return text


# A capitalised word right after a role word and before a pause or a verb is a spoken name the CRM does not hold
# ("their batch coordinator, Tara, who", "prospect Ovani, an engineer"). Product words never take that position.
ROLE_NAME = re.compile(r"\b((?:prospect|lead|learner|student|caller|agent|counsell?or|coordinator|advisor|mentor|manager"
                       r"|executive|colleague|representative|trainer|friend|brother|sister|father|mother|husband|wife))"
                       r",?\s+[A-Z][a-z]{2,}(?=[,.;:)]|\s+(?:from|who|to|is|was|will|and|said|asked|called|at|an|a)\b)")


def evidence_text(category, evidence, timing):
    if category == "Hot":
        return (f"Commitment stated. Past customers' first post-purchase call came a median {timing['median_days']} "
                f"days after their last sales call; {timing['within_45_share']:.0%} within 45 days.")
    if category == "Dormant: reconfirm":
        return "No past rate: the information is stale, not negative."
    e = evidence.get(category)
    if not e:
        return ""
    if e["customers"] < 10 or e["non_buyers_sampled"] < 5:
        return (f"Too few similar past leads for a reliable rate ({e['customers']} customers, "
                f"{e['non_buyers_sampled']} sampled non-buyers).")
    return (f"{e['rate'] * 10:.1f} in 10 similar past leads bought (range {e['low'] * 10:.1f}–{e['high'] * 10:.1f}); "
            f"based on {e['customers']} customers and {e['non_buyers_sampled']} sampled non-buyers")


def buyer_timing(store):
    """Days from each customer's last pre-purchase call to their first post-purchase call: a proxy for payment
    timing, which the export does not record."""
    from .patterns import POST
    lags = []
    for calls in lead_calls(store, "customers").values():
        pre, post = pre_purchase(store, calls), None
        for c in calls:
            if current_extraction(store, c["call_id"])["extraction"]["conversation_type"] in POST:
                post = c; break
        if pre and post:
            lags.append((dt.date.fromisoformat(post["created_on"][:10]) - dt.date.fromisoformat(pre[-1][0]["created_on"][:10])).days)
    lags.sort()
    return {"customers": len(lags), "median_days": lags[len(lags) // 2],
            "within_45_share": sum(x <= 45 for x in lags) / len(lags), "within_90_share": sum(x <= 90 for x in lags) / len(lags)}


def worklist_row(coding, category, reason, evidence, timing, as_of):
    c = coding
    open_obj = [o for o in c.objections if o.status == "open"]
    intent = c.buying_intent
    if c.stance.condition:
        met = {"yes": "a decision TrendyTech controls", "no": "TrendyTech does not offer this", "unclear": "depends on the lead"}
        intent += f" Condition: {c.stance.condition} ({met.get(c.stance.condition_can_be_met, 'unclear')})."
    last = (f"{human(c.last_live_conversation.date)} "
            f"({(dt.date.fromisoformat(as_of) - dt.date.fromisoformat(c.last_live_conversation.date)).days} days)"
            if c.last_live_conversation else "Never reached")
    proof = [cite(c.stance.evidence)] + [cite(o.evidence) for o in open_obj[:2]] + (
        [cite(c.status_check.evidence)] if c.status_check.needed else [])
    return {"lead": c.lead, "category": category, "why": reason, "past_leads_like_this": evidence_text(category, evidence, timing),
            "last_conversation": last, "buying_intent": intent,
            "experience": c.profile.experience.display, "role": c.profile.role.display, "ctc": c.profile.ctc.display,
            "location": c.profile.location.display,
            "open_objections": "\n".join(f"{o.topic.capitalize()}: {o.detail}" for o in open_obj) or "None open",
            "swot": "\n".join(f"{k[0].upper()}: {v}" for k, v in c.swot.model_dump().items()),
            "next_action": c.next_action,
            "what_to_say": f"Open: {c.what_to_say.opener}\nAsk: {c.what_to_say.question}\nClose: {c.what_to_say.ask}",
            "proof": "\n".join(p for p in dict.fromkeys(proof) if p)}


def journey_rows(store, leads):
    from .client import INTERNAL_TEXT
    journeys, call_rows = [], []
    for lead in leads:
        calls = calls_of(store, lead)
        clean = redactor(store, calls)
        flags = {c["crm_conversion_flag"] == "Yes" for c in calls}
        status = "Open lead" if flags == {False} else "Customer (CRM)" if flags == {True} else "Unclear: mixed CRM flags"
        steps = []
        for c in calls:
            x = current_extraction(store, c["call_id"])["extraction"]
            minutes = max(1, round(c["duration_seconds"] / 60)) if c["duration_seconds"] >= 30 else 0
            length = f"{minutes} min" if minutes else "under 1 min"
            steps.append(f"{human(c['created_on'])} · {TYPE_LABEL[x['conversation_type']]} · {length}: {clean(x['summary'])}")
            objections = "\n".join(f"{o['category'].replace('_', ' ').capitalize()} ({RESOLUTION_LABEL.get(o['resolution'], o['resolution'])}): "
                                   f"{clean(o['concern'])}" for o in x["objections"])
            call_rows.append({"lead": lead, "date": human(c["created_on"]), "type": TYPE_LABEL[x["conversation_type"]],
                              "length": length, "summary": clean(x["summary"]), "objections": objections or "None",
                              "next_step": clean(x["next_action"] or "")})
        journeys.append({"lead": lead, "status": status, "calls": len(calls), "first_call": human(calls[0]["created_on"]),
                         "last_call": human(calls[-1]["created_on"]), "journey": "\n".join(steps)})
    for row in journeys + call_rows:
        for key, value in row.items():
            if m := INTERNAL_TEXT.search(str(value)) or PRONOUNS.search(str(value)):
                raise ValueError(f"Lead {row['lead']} {key} contains {m.group()!r}; fix the cleaning rules")
    return journeys, call_rows


def review_sheet(store, rows, codings):
    """One line per claim behind every Hot, Warm and Check-status row, for a person to play and sign. Kept
    (with any signatures) across re-exports; new claims are appended."""
    import csv
    path = store.path("qa", "worklist-review.csv")
    existing = {}
    if path.exists():
        with path.open(encoding="utf-8-sig") as handle:
            existing = {(r["lead"], r["claim"], r["quote"]): r for r in csv.DictReader(handle)}
    out = []
    for row in rows:
        if row["category"] not in ("Hot", "Warm", "Check status"):
            continue
        c = codings[row["lead"]]
        claims = [("stance: " + c.stance.detail, c.stance.evidence)] + [
            (f"objection {o.topic}: {o.detail}", o.evidence) for o in c.objections if o.status == "open"]
        if c.status_check.needed:
            claims.append(("status: " + (c.status_check.reason or ""), c.status_check.evidence))
        for claim, ev in claims:
            if ev is None:
                continue
            key = (row["lead"], claim, ev.quote)
            out.append(existing.get(key) or {"lead": row["lead"], "category": row["category"], "claim": claim,
                                             "date": ev.date, "timestamp": stamp(ev.start_seconds), "quote": ev.quote,
                                             "verdict": "", "note": "", "reviewer": "", "reviewed_at": ""})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, REVIEW_COLUMNS)
        writer.writeheader()
        writer.writerows(out)
    signed = [r for r in out if r["reviewer"] and r["reviewed_at"] and r["verdict"]]
    return {"claims": len(out), "signed": len(signed), "complete": bool(out) and len(signed) == len(out)}


def export(store):
    """Categorise every worklist lead by the frozen rule and write the client sheets and an internal record."""
    from .quality import validation_status
    record = frozen(store)
    leads = worklist_leads(store)
    problems = {lead: p for lead, p in check_all(store, leads).items() if p}
    if problems:
        raise ValueError(f"{len(problems)} coded leads fail `pilot worklist check`: {sorted(problems)}")
    hold = holdout_leads(store)
    frozen_at = dt.datetime.fromisoformat(record["frozen_at"]).timestamp()
    early = [lead for lead in leads if lead in hold and coding_path(store, lead).stat().st_mtime < frozen_at]
    if early:
        raise ValueError(f"Holdout leads coded before the freeze: {early}")
    timing = record.get("timing") or buyer_timing(store)
    codings = {lead: LeadCoding.model_validate(read_json(coding_path(store, lead))) for lead in leads}
    rows, internal = [], []
    for lead in leads:
        category, reason = categorise(codings[lead], record["as_of"])
        rows.append(worklist_row(codings[lead], category, reason, record["evidence"], timing, record["as_of"]))
        internal.append({"lead": lead, "category": category, "reason": reason, "coder": codings[lead].coder,
                         "holdout": lead in hold, "source": "pilot" if lead in select(store)["pilot_open"] else "non-buyer sample",
                         "coding_hash": digest(read_json(coding_path(store, lead)))})
    days = lambda r: int(re.search(r"\((\d+) days\)", r["last_conversation"]).group(1)) if "days)" in r["last_conversation"] else 10**6
    rows.sort(key=lambda r: (CATEGORY_ORDER[r["category"]], days(r), r["lead"]))
    pilot = sorted(lead_calls(store, "pilot"))
    journeys, calls = journey_rows(store, sorted(set(pilot) | set(leads)))
    review = review_sheet(store, rows, codings)
    # Capitalised words that never occur in lower case anywhere in the client text: possible names for a person
    # to scan before anything is shared (spoken names the CRM does not hold cannot all be caught by rule).
    text = " ".join(str(v) for r in rows + journeys + calls for v in r.values())
    lower = set(re.findall(r"\b[a-z]{3,}\b", text))
    calendar = {w.lower() for w in list(__import__("calendar").month_name)[1:] + list(__import__("calendar").month_abbr)[1:]
                + list(__import__("calendar").day_name) + ["Sept"]}
    possible_names = sorted({w for w in re.findall(r"\b[A-Z][a-z]{2,}\b", text)
                             if w.lower() not in lower and w.lower() not in calendar})
    audit = validation_status(store)
    audit_done = audit["complete"]
    counts = {c: sum(r["category"] == c for r in rows) for c in CATEGORY_ORDER}
    meta = {"scope_note": SCOPE_NOTE, "as_of": record["as_of"], "review_draft": not (review["complete"] and audit_done),
            "categories": counts, "leads": len(rows), "journeys": len(journeys), "calls": len(calls),
            "evidence": record["evidence"], "timing": timing, "rule": record["rule"], "validation": audit,
            "hand_check": review}
    for name, value in (("meta", meta), ("worklist", rows), ("journeys", journeys), ("calls", calls)):
        write_json(store.path("exports", "client", f"{name}.json"), value)
    write_json(store.path("exports", "internal", "worklist.json"),
               {"rule_hash": record["rule_hash"], "frozen_at": record["frozen_at"], "as_of": record["as_of"],
                "leads": internal, "hand_check": review, "audit": audit, "possible_names_to_scan": possible_names})
    return {"leads": len(rows), "categories": counts, "journeys": len(journeys), "calls": len(calls),
            "hand_check": review, "review_draft": meta["review_draft"]}


# ---------- 3b. coding by the hosted model (Phase 1 scale) ----------
GUIDE_PATH = __import__("pathlib").Path(__file__).resolve().parents[2] / "docs" / "pipeline" / "CODING-GUIDE.md"
CODING_PROMPT_VERSION = "lead-coding-prompt-v1"


def code_lead(store, lead, model, force=False):
    """Code one lead with the hosted model from the same guide people use, through the same validator. Never
    overwrites a file coded by a person or a reviewing assistant. Not yet validated for client use: Phase 1 first
    measures its agreement with the pilot's reviewed codings (docs/pipeline/RUNBOOK.md)."""
    import json
    import time

    from .remote import provider_schema
    path = coding_path(store, lead)
    if path.exists():
        existing = read_json(path)
        if not force or not str(existing.get("coder", "")).startswith("model:"):
            return existing
    guide = GUIDE_PATH.read_text()
    coder = f"model:{model.model_id}:{CODING_PROMPT_VERSION}:{digest(guide)[:8]}"
    system = (guide + "\n\nAnswer with one JSON object that follows the schema exactly. Use \"coder\": "
              f"\"{coder}\" and copy every quote character for character from the cited segment.")
    user, feedback, start = f"Lead {lead}. Every call:\n" + show(store, lead), "", time.monotonic()
    schema = provider_schema(LeadCoding.model_json_schema(by_alias=True))
    for attempt in range(2):
        raw, usage = model.generate(system, user + feedback, max_tokens=6000, schema=schema, schema_name="lead_coding")
        data = json.loads(raw)
        data["coder"], data["lead"] = coder, lead
        problems = check_coding(store, lead, data)
        if not problems:
            write_json(path, data)
            store.event(stage="worklist-code", lead=lead, status="success", wall_seconds=time.monotonic() - start,
                        external_cost_inr=0, **usage)
            return data
        feedback = "\nYour previous answer failed these checks: " + "; ".join(problems)[:1500] + ". Answer again."
    store.event(stage="worklist-code", lead=lead, status="failed", wall_seconds=time.monotonic() - start,
                external_cost_inr=0)
    raise ValueError(f"Lead {lead} coding failed the checks twice: {problems[:3]}")
