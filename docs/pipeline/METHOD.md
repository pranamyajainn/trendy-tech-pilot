# Worklist method: how open leads are graded, and why

Rule version `worklist-rule-v2`. Version 1 was adopted on 7 October 2026 after a method review. Version 2, the same
day, follows an external review: it adds the unusable-recording category; the rest of the rule is unchanged. The figures behind every statement
here are private:
- the review document is in `data/deliverables/internal/`;
- the research, the reviews and the scripts that reproduce the figures are in `data/research/`.

This file holds the reasoning only. It contains no client data.

## The rule (first match wins)

| # | Category | When | Evidence shown beside it |
|---|---|---|---|
| 1 | Check status | The lead's own calls show a bought paid programme, or a promised payment date passed with no conversation since | None: it is a CRM correction, not a sales call |
| 2 | Recording unusable: confirm status | The recordings have no usable audio, so nothing is known | None |
| 3 | Not reached | No live conversation ever took place (voicemail, screening, no answer) | None |
| 4 | Outside target | The lead's own words match TrendyTech's exclusion: fresher, non-IT background, or a career gap over a year | Past-lead rate |
| 5 | Cold: declined | Declined; wants something TrendyTech does not sell; or a condition TrendyTech cannot meet | Past-lead rate, or "too few past cases" |
| 6 | Hot | A commitment, or a conditional commitment TrendyTech can meet, at a live conversation within 45 days of the export end, with no refusal since | "Commitment stated" and a timing proxy, but no rate (see below) |
| 7 | Dormant: reconfirm | No live conversation for more than 90 days | None: stale information is not negative information |
| 8 | Warm | Everything else, including commitments gone stale (marked "re-open") | Past-lead rate |

Within each category, the most recent conversation comes first. Every day count is measured to the data cutoff (the
last recorded call in the export), not to today. Each file says so, for example "Based on recordings available
through 29 Sep 2026. Confirm current lead status before acting."

## How the worklist reads

The columns, in order:
- **Lead**, **Priority**, **Reason**. These two lead columns stay frozen while scrolling.
- **Next action**, **What to say**.
- **Evidence:** the decisive statement first, as call date and time, minute into the recording, and exact words.
- **Historical context.**
- Last conversation, buying intent, profile, open objections, SWOT.
- **Validation:** "Recording validation pending" until a person has played and confirmed the claims.

The coding check enforces three rules:
- next actions name a trigger ("after the manager approves the price"), never a relative time;
- discounts, past offers and trials are always conditional on current approval;
- dormant leads are messaged to reconfirm interest before any call. `worklist.categorise` is the code; the table in
`tests/test_worklist.py` pins every branch.

## Historical context and past-lead evidence

The same rule is read on closed past leads: the customer cohort against a seeded random sample of non-buyers.
Only calls before the purchase boundary are used, and only conversations of 3+ minutes. Each category shows:
- its customers and sampled non-buyers;
- the weighted conversion rate (each sampled non-buyer stands for population ÷ sample non-buyers);
- a two-sided log-odds interval (`patterns.conversion_rate`).

Three rules govern what is shown:
- **Hot shows no rate.** For most past customers, "I'll pay" was said on the call where they paid, so the rate is
  inflated by construction.
- **Thin categories show no rate.** Fewer than 10 customers or 5 sampled non-buyers is too few for a reliable number.
- **Dormant shows no rate.** It cannot be measured on closed leads.

**Lead rows carry no rates.** Group rates are broad estimates, not a lead's chance of buying, so they appear only in
the report's grading table, labelled as group estimates with ranges. A lead row says in words what the history
supports, or that no dependable comparison exists.

The 45- and 90-day thresholds come from a timing proxy: days from a customer's last sales call to their first
post-purchase call. That is not the payment date, so the thresholds are provisional until payment dates are
available.

## Why this method

Nine scoring methods were compared on the same past leads, using only what was said before purchase. They ranged
from a points scorecard and a fit × intent grid to logistic regression, trees, a random forest and a lookalike
method. Every method was tested with repeated cross-validation and on later leads.

- **The comparison did not show a reliable improvement from any of them** over the single signal "the lead said they
  will pay". The trained models also fall outside the contract, which rules out model training and requires
  traceable cohort evidence.
- **The chosen rule, tested the same way, ranked past leads at least as well as any compared method.** Its order was
  set with knowledge of the data, so that figure is optimistic. The holdout and Phase 1 give the clean test.
- **Most profile traits did not show reliable predictive value in this analysis.** Data role versus another IT role,
  asking the fee and booking a callback are examples. That does not prove they are useless: the full archive (about
  ten times the non-buyers) can test them and their combinations again.
- **TrendyTech's own exclusion is supported by the data,** and is stable across both halves of the year.

## Three traps the method avoids

1. **Knowing more looks like buying.**
   - About half of customers have no recorded sales conversation.
   - Most customer profile facts were learnt after payment.

   So only pre-purchase facts count, and "not asked" is never "no".
2. **The buyer's last call is the paying call.** Closing language decides who to call today. It is never presented
   as a trait of buyers.
3. **Fields that record the outcome.** The CRM owner field changes after purchase, and the salesperson is a
   confounder. Both are banned as inputs.

## What must not be claimed
- **No accuracy and no probability of closure for a lead.** Rates describe groups of past leads.
- **No "validated".** That waits for the human checks (`docs/qa-protocol.md`) and a later CRM comparison.
- **No "the cost gate passed".** Costs are measured from usage records at published rates; invoices are not
  reconciled.
- **Every category is dated** ("as of the last recorded call") and must be confirmed in LeadSquared before calling.

## Changing the method
1. Change `RULE` in `worklist.py`, with a new version name.
2. Update this file and the tests.
3. Run `pilot worklist freeze --force --reason "..."`. The earlier freezes are kept as history, and the holdout guard
   keeps using the first freeze time.

A rule changed after seeing holdout results is a new development round, not a result.
