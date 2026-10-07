# Lead coding guide (schema `lead-coding-v1`)

One coded file per open lead, written to `data/review/worklist/<lead>.json`. A coder reads **every recorded call** of
one lead and fills the schema below. A coder can be:
- a person (an intern);
- an assistant working from this guide;
- in Phase 1, the hosted model stage, once it agrees with human-checked files.

**Code, not the coder, decides the category.** The coder records only what was said, with evidence.
`pilot worklist check` validates every file and rejects anything that breaks a rule below.

Examples in this guide are synthetic. Never copy real customer text into the repository.

## How to read a lead

From the project root:

```bash
.venv/bin/pilot worklist show <lead>
```

This prints every call of the lead, in date order, with:
- the call id, date, duration and call type;
- each segment as `[segment id | mm:ss] SPEAKER: text`.

Speaker labels are automatic and sometimes swapped: judge who is speaking from what is said.

A **live conversation** is a call where the lead actually discussed the course. These are **not** live
conversations: voicemail, call screening, "call me later", unusable audio, a wrong person. An **attempt** is any
call after the last live conversation that was not itself a live conversation.

## Rules

1. **Lead-voiced only.**
   - A commitment, objection or fact counts only if the LEAD said it, or explicitly agreed ("yes", "right") when
     the counsellor said it.
   - Silence is not agreement.
   - A date the counsellor proposes counts only if the lead repeats or accepts it.
2. **Cite everything.** Every claim carries an `evidence` object:
   - `call_id`;
   - `date` (YYYY-MM-DD);
   - `segment_id`;
   - `start_seconds`;
   - `quote`: copied **exactly** from that segment, at most 15 words.
3. **Unknown is not "no".** If something was never discussed, set `display` to `"Not asked"` and `evidence` to
   `null`.
4. **Privacy.** Never write the name of the lead, the counsellor or anyone else, a phone number, an email or a URL,
   in any field.
   - A company or city the lead states is allowed.
   - Write "the counsellor" for the agent and "the lead" for the prospect.
   - Use no gendered pronouns.
5. **No internal words** in client-facing fields (`buying_intent`, `their_next_step`, `objections[].detail`, `swot`,
   `next_action`, `what_to_say`). The banned words are: model, AI (write the course as "GenAI"), Gemini, extraction,
   signal, segment, cohort, holdout, development, fingerprint, transcript.
6. **Specific, not generic.** Every action names this lead's actual situation: amounts, products, dates, what was
   promised.
   - Bad: "Follow up to confirm interest."
   - Good: "Manager to approve one final price for the two-course bundle (lead asked 90k, offer was 99k), then call
     this week."

## Stance (where the lead stands at the LATEST live conversation)

Use the strongest value that applies. A later refusal overrides an earlier commitment; record that with
`refused_after_commitment: true`.

| Value | Meaning |
|---|---|
| `commitment` | The lead said they will pay or enrol ("I'll pay tomorrow", "send the link, I'm registering"). |
| `conditional_commitment` | The lead will enrol if a specific condition is met. **Always fill `condition`, `condition_can_be_met` and `condition_reason`.** |
| `dated_deferral` | Not now, with a date or event ("after my appraisal in March"). |
| `open_deferral` | "I'll think and get back", with no date. |
| `open` | Interested and asking questions, no deferral or commitment. |
| `declined` | Clearly not interested, chose another option, or denied making the enquiry. |
| `product_not_sold` | Wants something TrendyTech does not sell in that form, after the counsellor clarified. |
| `none` | No live conversation ever took place. |

**Can the condition be met?** Answer for things TrendyTech controls or offers:

| Answer | When | Examples |
|---|---|---|
| `yes` | TrendyTech controls or offers it | A price approval, an EMI or split-payment route, sending information, a batch date |
| `no` | TrendyTech does not offer it | Live classes on a recorded-only course, a job guarantee, a product it does not sell |
| `unclear` | It depends on something outside the call | A family decision, salary credit |

## Status check

Set `status_check.needed: true`, with `reason` and `evidence`, when:
- the lead's own calls show they already bought a **paid** TrendyTech programme: enrolment, payment, a batch, or
  learner support for a paid course; or
- the lead promised a payment date that has passed, with no live conversation since.

A learner of a **free** course (for example a free SQL course) has not bought anything. That is not a status
check. Mention it in `buying_intent` or `swot` instead: it is useful context for the counsellor.

## Target check (TrendyTech's own exclusion)

| Value | When |
|---|---|
| `outside_target` | The lead's own words show they are a fresher with no work experience, from a non-IT background, or have a career gap of more than one year. Give `reason` and `evidence`. |
| `in_target` | The lead's own words show none of these. |
| `not_asked` | Otherwise. |

## Schema

```json
{
  "schema": "lead-coding-v1",
  "lead": "100001",
  "coder": "who coded it, e.g. 'intern:initials' or 'assistant-review'",
  "calls_read": ["C0000000000000001", "C0000000000000002"],
  "status_check": {"needed": false, "reason": null, "evidence": null},
  "profile": {
    "experience": {"display": "6 years total, 2 in data", "evidence": {}},
    "role": {"display": "Data analyst (SQL, reporting)", "evidence": {}},
    "ctc": {"display": "Not asked", "evidence": null},
    "location": {"display": "Pune", "evidence": {}}
  },
  "learner": {"value": "self | someone_else | unclear", "evidence": null},
  "target_check": {"value": "in_target | outside_target | not_asked", "reason": null, "evidence": null},
  "stance": {
    "value": "one of the stance values above",
    "detail": "plain words, e.g. 'Will enrol this week if the 2-course price is approved'",
    "condition": "e.g. 'price approval to 90k' or null",
    "condition_can_be_met": "yes | no | unclear | null",
    "condition_reason": "why, or null",
    "evidence": {}
  },
  "refused_after_commitment": false,
  "last_live_conversation": {"date": "2026-09-05", "call_id": "C0000000000000002"},
  "attempts_since_last_live": 0,
  "buying_intent": "one plain sentence: how close they are and what they are waiting for",
  "their_next_step": "what the LEAD said they would do next, or null",
  "objections": [
    {"topic": "price | payment method | format | timing | course fit | placement | prerequisites | family or approval | trust | other",
     "detail": "specific: amounts, products, what was offered",
     "status": "open | resolved",
     "evidence": {}}
  ],
  "swot": {"strength": "max 20 words", "weakness": "max 20 words", "opportunity": "max 20 words", "threat": "max 20 words"},
  "next_action": "who does what, by when, and any approval needed first (max 50 words)",
  "what_to_say": {"opener": "refers to the lead's last words", "question": "addresses the main open objection", "ask": "the concrete ask"}
}
```

An `evidence` object looks like this:

```json
{"call_id": "C0000000000000002", "date": "2026-09-05", "segment_id": 41, "start_seconds": 203.7, "quote": "let me think and get back"}
```

Use `{}` only in the example above; real files need full evidence or `null`.
