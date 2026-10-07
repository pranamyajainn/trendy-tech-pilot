"""Buyers versus non-buyers (proposal item 02) and hot/warm/cold groups for open leads (item 03), without a
fitted model or an added-up score: the scope excludes model training, and the owner's statistical-methods note
advises cohort rates with intervals instead of a weighted score.

Design, fixed before results were examined (6 Oct 2026):
- Outcome: CRM "Is Converted = Yes" on every call (customers cohort) versus no Yes flag (open_sample cohort, a
  random 150 of the 1,448 such leads with a call of 3+ minutes). Buyers are held to the same 3-minute rule. The
  design samples non-buyers, so conversion rates scale them back to their population; odds ratios need no scaling.
- As-of snapshot: purchase dates are unavailable, so a lead's pre-purchase calls are its sales and follow-up
  calls before its first enrollment/payment, learner-support or administrative call (the payment conversation is
  the purchase itself, so it is a boundary, not a predictor; "says they have paid" is likewise excluded). Signals and objections come only from these calls; stable profile
  facts (experience, background) may come from any call.
- Factors: plain yes/no facts about a lead (below). For each: leads with and without it, conversion rate with
  and without, the difference in percentage points, the odds ratio with a 95% interval, Fisher's exact p-value
  and a Benjamini-Hochberg adjustment across all factors. "Clear" means adjusted p below 0.05.
- Groups: five operational groups. A group is Hot when its 95% interval lies above the overall rate, Cold when it
  lies below, Warm otherwise; groups with under 15 leads give "insufficient evidence". Groups are graded on leads
  first called January-May and checked on leads first called June-September (lead- and time-separated).
"""

import math
from collections import Counter

from scipy.stats import false_discovery_control, fisher_exact

from .artifacts import current_extraction
from .profile import lead_calls
from .storage import read_json, write_json

# TrendyTech's own exclusions (client call, 5 Oct 2026): not target customers even if they would buy.
EXCLUSIONS = {"fresher": ("yes", "Fresher"), "background": ("non_it", "Non-IT background"),
              "career_gap": ("over_1_year", "Career gap over a year")}


def exclusions(profile):
    return [label for field, (value, label) in EXCLUSIONS.items() if profile["profile"][field] == value]


def conversion_rate(b, o, sample_size, population):
    """Estimated share of a group who bought: buyers / (buyers + non-buyers scaled to their population), with a
    two-sided log-odds (Woolf) interval. Both counts are samples of what could have happened, so both add noise; an
    interval from the non-buyer sample alone came out 3-5 times too narrow (method review, 7 Oct 2026). Half a lead
    is added to each count when either is zero."""
    if not b + o:
        return None, None
    w = population / sample_size
    bb, oo = (b + .5, o + .5) if min(b, o) == 0 else (b, o)
    centre, se = math.log(bb / (w * oo)), math.sqrt(1 / bb + 1 / oo)
    expit = lambda z: 1 / (1 + math.exp(-z))
    return b / (b + w * o), (expit(centre - 1.96 * se), expit(centre + 1.96 * se))


PRE = {"sales", "brief_followup"}
POST = {"enrollment_or_payment", "learner_support", "administrative"}
DEVELOPMENT_BEFORE = "2026-06-01"
MIN_GROUP = 15
OPEN_POPULATION, OPEN_SAMPLE = 1448, 150

SIGNALS = {"Said they would pay or register": {"payment_intent"},
           "Agreed a next call or step": {"followup_agreed"}, "Asked about the fee": {"price_question"},
           "Asked for a demo or sample": {"demo_requested"}, "Said they need it soon": {"urgency"},
           "Said not interested or not a fit": {"low_interest", "not_a_fit", "do_not_contact"},
           "Said they have no time": {"no_time"}}
OBJECTIONS = {"Raised price": {"price"}, "Raised the course format (live or recorded, schedule)": {"format"},
              "Raised course fit or content": {"course_fit"}, "Raised timing or availability": {"timing", "time"},
              "Raised job outcomes": {"career_outcomes"}, "Raised prerequisites": {"prerequisites"},
              "Needs someone else's approval": {"decision_maker"}, "Raised trust": {"trust"}}
DECLINE = SIGNALS["Said not interested or not a fit"]


def pre_purchase(store, calls):
    """(call, extraction) for the lead's pre-purchase sales conversations, in date order."""
    pre = []
    for call in calls:
        x = current_extraction(store, call["call_id"])["extraction"]
        if x["conversation_type"] in POST:
            break
        if x["conversation_type"] in PRE:
            pre.append((call, x))
    return pre


def snapshot(store, calls):
    return [x for _, x in pre_purchase(store, calls)]


def facts(store, calls, profile):
    """The lead's yes/no factors; None where the underlying information is unknown."""
    pre = snapshot(store, calls)
    p = profile["profile"] if profile else None
    kinds = {s["kind"] for x in pre for s in x["signals"]}
    categories = {o["category"] for x in pre for o in x["objections"]}
    row = {}
    for label, wanted in SIGNALS.items():
        row[label] = bool(kinds & wanted) if pre else None
    for label, wanted in OBJECTIONS.items():
        row[label] = bool(categories & wanted) if pre else None
    row["A concern was left unresolved"] = (any(o["resolution"] != "resolved" for x in pre for o in x["objections"])
                                            if pre else None)
    row["Two or more sales conversations"] = len(pre) >= 2 if pre else None
    if p:
        row["Matches a TrendyTech exclusion"] = bool(exclusions(profile))
        row["IT background"] = {"it": True, "non_it": False}.get(p["background"])
        row["Switching into data from another role"] = (p["motivation"] == "switch_into_data_field"
                                                        if p["motivation"] != "unknown" else None)
        row["Already works in a data role"] = (p["current_role_group"] in ("data_engineering", "data_analytics_bi",
                                                                           "data_science_ml")
                                               if p["current_role_group"] != "unknown" else None)
        years = p["experience_years"]
        row["5+ years' experience"] = years >= 5 if years is not None else None
        row["Under 2 years' experience"] = years < 2 if years is not None else None
        row["Not working at present"] = {"not_working": True, "employed": False}.get(p["employment"])
        row["Based outside India"] = {"abroad": True, "india": False}.get(p["location_group"])
    return row, pre


def group_of(row, pre, excluded):
    """Five operational groups, in order of precedence."""
    if excluded:
        return "Not target"
    if not pre:
        return None  # no pre-purchase conversation: cannot be grouped
    last = {s["kind"] for s in pre[-1]["signals"]}
    if last & DECLINE:
        return "Declined"
    if row["Said they would pay or register"]:
        return "Committed"
    if row["Agreed a next call or step"] or row["Asked for a demo or sample"]:
        return "Engaged"
    return "Other contacted"


def leads(store):
    """One record per eligible lead in both cohorts."""
    out = []
    for name, buyer in (("customers", True), ("open_sample", False)):
        for lead, calls in lead_calls(store, name).items():
            if max(c["duration_seconds"] for c in calls) < 180:
                continue
            path = store.path("review", "profiles", lead + ".json")
            profile = read_json(path) if path.exists() else None
            row, pre = facts(store, calls, profile)
            excluded = bool(row.get("Matches a TrendyTech exclusion"))
            out.append({"lead": lead, "buyer": buyer, "profiled": profile is not None, "facts": row,
                        "group": group_of(row, pre, excluded), "first_call": calls[0]["created_on"][:10]})
    return out


def rate(buyers, non_buyers):
    return conversion_rate(buyers, non_buyers, OPEN_SAMPLE, OPEN_POPULATION)


def factor_table(records):
    rows = []
    for label in records[0]["facts"]:
        known = [r for r in records if r["facts"].get(label) is not None]
        a = sum(r["buyer"] and r["facts"][label] for r in known)        # buyers with
        b = sum(not r["buyer"] and r["facts"][label] for r in known)    # non-buyers with
        c = sum(r["buyer"] and not r["facts"][label] for r in known)    # buyers without
        d = sum(not r["buyer"] and not r["facts"][label] for r in known)
        if min(a + b, c + d) == 0:
            continue
        with_rate, with_interval = rate(a, b)
        without_rate, _ = rate(c, d)
        log_or = math.log(((a + .5) * (d + .5)) / ((b + .5) * (c + .5)))
        se = math.sqrt(1 / (a + .5) + 1 / (b + .5) + 1 / (c + .5) + 1 / (d + .5))
        rows.append({"factor": label, "buyers_with": a, "non_buyers_with": b, "buyers_without": c,
                     "non_buyers_without": d, "buyer_share": a / (a + c), "non_buyer_share": b / (b + d),
                     "rate_with": with_rate, "rate_with_interval": with_interval, "rate_without": without_rate,
                     "difference_pp": 100 * (with_rate - without_rate), "odds_ratio": math.exp(log_or),
                     "odds_ratio_interval": (math.exp(log_or - 1.96 * se), math.exp(log_or + 1.96 * se)),
                     "p": fisher_exact([[a, b], [c, d]]).pvalue})
    if rows:
        for row, q in zip(rows, false_discovery_control([r["p"] for r in rows], method="bh"), strict=True):
            row["p_adjusted"], row["clear"] = float(q), bool(q < .05)
    return sorted(rows, key=lambda r: r["p"])


def grade(records):
    """{group: {...}} with conversion rate, interval and Hot/Warm/Cold against the overall rate of these leads."""
    grouped = [r for r in records if r["group"] and r["group"] != "Not target"]
    base, base_interval = rate(sum(r["buyer"] for r in grouped), sum(not r["buyer"] for r in grouped))
    out = {"_overall": {"leads": len(grouped), "rate": base, "interval": base_interval}}
    for name in ("Committed", "Engaged", "Other contacted", "Declined", "Not target"):
        members = [r for r in records if r["group"] == name]
        b, o = sum(r["buyer"] for r in members), sum(not r["buyer"] for r in members)
        value, interval = rate(b, o)
        if name == "Not target":
            category = "Not target"
        elif b + o < MIN_GROUP or interval is None:
            category = "Insufficient evidence"
        else:
            category = "Hot" if interval[0] > base else "Cold" if interval[1] < base else "Warm"
        out[name] = {"buyers": b, "non_buyers": o, "rate": value, "interval": interval, "category": category}
    return out


def analyse(store):
    records = leads(store)
    development = [r for r in records if r["first_call"] < DEVELOPMENT_BEFORE]
    later = [r for r in records if r["first_call"] >= DEVELOPMENT_BEFORE]
    graded, check = grade(development), grade(later)
    categories = {g: v["category"] for g, v in graded.items() if not g.startswith("_")}
    later_buyers = [r for r in later if r["buyer"] and r["group"] not in (None, "Not target")]
    hot_groups = [g for g, c in categories.items() if c == "Hot"]
    result = {"design": __doc__, "leads": {"buyers": sum(r["buyer"] for r in records),
                                           "non_buyers": sum(not r["buyer"] for r in records),
                                           "buyers_profiled": sum(r["buyer"] and r["profiled"] for r in records),
                                           "without_pre_purchase_conversation": Counter(
                                               "buyer" if r["buyer"] else "non_buyer" for r in records if r["group"] is None)},
              "factors": factor_table(records), "groups_development": graded, "groups_later_check": check,
              "categories": categories,
              "later_check": {"buyers_in_hot_groups": sum(r["group"] in hot_groups for r in later_buyers),
                              "buyers_grouped": len(later_buyers)}}
    result["pilot_leads"] = pilot_view(store, records, graded)
    result["who_buys"] = who_buys(store)
    write_json(store.path("review", "patterns.json"), result)
    return result


REASONS = {"Committed": "Said they would pay or register",
           "Engaged": "Agreed a next call or asked for a demo, but did not commit to paying",
           "Other contacted": "Had a sales conversation without a commitment, decline or agreed next step",
           "Declined": "Said they were not interested in their latest sales conversation"}


def pilot_view(store, records, graded):
    """Category, group rate and reasons for each of the 50 pilot leads. Categories come from the January-May
    grading; the rate shown pools both periods for the most data."""
    from .client import human_date

    calls = read_json(store.path("selection.json"))["calls"]
    pooled = grade(records)
    clear = {f["factor"]: f for f in factor_table(records) if f["clear"]}
    out = []
    for lead, journey in lead_calls(store, "pilot").items():
        path = store.path("review", "profiles", lead + ".json")
        profile = read_json(path) if path.exists() else None
        row, pre = facts(store, journey, profile)
        excluded = exclusions(profile) if profile else []
        group = group_of(row, pre, bool(excluded))
        flags = {c["crm_conversion_flag"] for c in journey}
        crm = "customer" if flags == {"Yes"} else "mixed" if "Yes" in flags else "open"
        if group is None:
            category, reasons = "Not reached", ["No sales conversation in the recorded calls"]
        else:
            category = graded[group]["category"]
            reasons = ([f"Matches a TrendyTech exclusion: {', '.join(excluded)}"] if group == "Not target"
                       else [REASONS[group]])
        notes = [f"{name} ({'buyers more likely' if f['odds_ratio'] > 1 else 'buyers less likely'})"
                 for name, f in clear.items() if row.get(name) and name not in (
                     "Said they would pay or register", "Said not interested or not a fit", "Matches a TrendyTech exclusion")]
        concerns = sorted({o["category"].replace("_", " ") for x in pre for o in x["objections"] if o["resolution"] != "resolved"})
        stats = pooled.get(group) if group else None
        out.append({"lead_number": lead, "lead_alias": journey[0]["lead_alias"], "crm": crm, "group": group,
                    "category": category, "reasons": reasons, "other_factors": notes, "open_concerns": concerns,
                    "group_rate": stats["rate"] if stats else None, "group_interval": stats["interval"] if stats else None,
                    "group_leads": stats["buyers"] + stats["non_buyers"] if stats else None,
                    "last_sales_conversation": human_date(pre_purchase(store, journey)[-1][0]["created_on"]) if pre else None,
                    "calls": len(journey)})
    assert len({c["lead_number"] for c in calls}) == len(out)
    return out


PROFILE_LABELS = {
    "motivation": ("Main reason for the course", {
        "switch_into_data_field": "Switching into data from another role", "grow_in_current_data_role":
        "Growing in a current data role", "better_salary_or_job": "Better salary or job", "find_a_job": "Looking for a job",
        "skills_for_current_project": "Skills for a current project", "buying_for_team": "Buying for a team",
        "other": "Other reason"}),
    "current_role_group": ("Current role", {
        "software_developer": "Software developer", "testing_qa": "Testing / QA", "data_analytics_bi": "Data analyst / BI",
        "data_engineering": "Data engineer", "data_science_ml": "Data science / ML", "cloud_devops": "Cloud / DevOps",
        "database_admin": "Database administrator", "it_support_operations": "IT support / operations",
        "other_it": "Other IT role", "non_it": "Outside IT", "student": "Student"}),
    "background": ("Background", {"it": "IT", "non_it": "Outside IT"}),
    "employment": ("Working now", {"employed": "Working", "not_working": "Not working", "student": "Student"}),
    "location_group": ("Location", {"india": "India", "abroad": "Outside India"})}


def who_buys(store):
    """Shares of each profile value among buyers and non-buyers (eligible leads, known values only)."""
    rows = []
    groups = {"buyers": [], "non_buyers": []}
    for name, key in (("customers", "buyers"), ("open_sample", "non_buyers")):
        for lead, calls in lead_calls(store, name).items():
            path = store.path("review", "profiles", lead + ".json")
            if max(c["duration_seconds"] for c in calls) >= 180 and path.exists():
                groups[key].append(read_json(path)["profile"])
    bands = lambda y: None if y is None else ("Under 2 years" if y < 2 else "2-5 years" if y < 5 else
                                              "5-10 years" if y < 10 else "10+ years")
    fields = [(f, title, lambda p, f=f, labels=labels: labels.get(p[f])) for f, (title, labels) in PROFILE_LABELS.items()]
    fields.insert(2, ("experience", "Experience", lambda p: bands(p["experience_years"])))
    for _, title, value in fields:
        counts = {k: Counter(v for v in map(value, ps) if v) for k, ps in groups.items()}
        known = {k: sum(c.values()) for k, c in counts.items()}
        for v in sorted(set(counts["buyers"]) | set(counts["non_buyers"]), key=lambda v: -counts["buyers"][v]):
            rows.append({"trait": title, "value": v, "buyers": counts["buyers"][v], "buyers_known": known["buyers"],
                         "buyer_share": counts["buyers"][v] / known["buyers"] if known["buyers"] else None,
                         "non_buyers": counts["non_buyers"][v], "non_buyers_known": known["non_buyers"],
                         "non_buyer_share": counts["non_buyers"][v] / known["non_buyers"] if known["non_buyers"] else None})
    return {"profiled_buyers": len(groups["buyers"]), "profiled_non_buyers": len(groups["non_buyers"]), "rows": rows}
