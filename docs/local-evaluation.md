# Local extraction evaluation — status as of 3 October 2026

The pipeline has all 300 recordings and 238 development transcripts. The remaining 62 calls belong to the
10 held-out leads. They are not used to tune extraction. No paid API requests have been made.

## What has been tested

- Qwen2.5 7B, Qwen3 8B, Qwen3.5 9B and Qwen3.5 27B, quantized for the local Mac.
- The original full JSON schema with exact quotations and validation retries.
- A smaller segment-reference format: the model chooses a segment and code copies its text exactly.
- Qwen's recommended non-thinking sampling settings, with a fixed random seed.
- Reasoning enabled, both greedy and with recommended sampling, bounded at 6,500 generated tokens.
- Separate purpose, profile and discussion stages to reduce the work in each generation.

The scripts `calibrate_references.py` and `calibrate_staged.py` require development call IDs and write only
to ignored `data/experiments/`. They do not create production extractions, freeze a method, or contact an API.
Experimental outputs passing reference validation still require meaning and omission review.

## Findings

Segment references remove invented quotations, but cannot make an unsupported interpretation correct.
The reviewed outputs still confused agent suggestions with prospect facts, missed explicit professional
experience, and sometimes treated a partial-payment arrangement as a discount. Support-call outputs also
confused job notice periods with enrollment timing. These are material sales-guidance errors.

The two completed reasoning trials exhausted their 6,500-token limits without a final extraction. They
took approximately 421 and 494 seconds. A further queued trial was stopped; its partial runtime was recorded
separately as an estimate. This is a limitation of these bounded configurations, not proof that the models
cannot ever solve the task. The staged trial produced two schema-valid outputs from three calls, but missed
clearly stated profile facts and misrepresented a proposed discount check. It was not promoted.

These deliberately selected development cases diagnose failure modes. Their pass counts are not an accuracy
estimate for the 300-call sample. Reference matching is not independent transcription or extraction QA.
Source text, call identifiers, raw outputs and detailed review notes remain private.

## Hosted extraction (Gemini), 3 October 2026

The owner approved hosted text extraction within an INR 500 cap and the client confirmed any model type may be
used. Audio transcription stays local. The route uses `gemini-3.8-flash` through Google's OpenAI-compatible endpoint.

- Integration: the endpoint rejected the strict pydantic schema (HTTP 400). A provider-safe schema is now sent;
  the full schema is still validated locally. Busy 429/503 responses are retried under one budget reservation.
- 20-call development trial: meaning was correct on the traps the local models failed (agent suggestions vs
  prospect facts, split payment vs discount, discount checks vs promises, quoted fee vs budget, notice period vs
  enrollment timing). All quote failures were genuine quotes crossing an ASR segment boundary or dropping a
  filler word; evidence rules `evidence-v2` accept those while still rejecting corrected words and wrong citations.
- Full development runs: 238/238 calls passed schema and evidence validation. Review led to prompt v7 (field
  definitions) and v8 (bare acknowledgements are not resolution; one record per concern) and `evidence-v3`
  (service calls cannot carry sales content). After v8, all resolved objections showed explicit acceptance.
- Stability: between two runs, call type agreed on 219/238 calls and sales vs non-sales on 230/238. Flips are
  borderline short calls.
- Cost: about INR 0.3 for a short call and INR 1.4 for a 20-minute call at the budgeting rates in `remote.py`.

The method was frozen after this transcript-only development review. It is not an independent accuracy
measurement; the holdout review provides that. Do not claim the commercial gate has passed or that conversion
predictions are validated.

Generation settings were checked against the official
[Qwen3.5 model card](https://huggingface.co/Qwen/Qwen3.5-9B#best-practices).
The Gemini route's rates were rechecked against
[Google's pricing](https://ai.google.dev/gemini-api/docs/pricing) on 3 October 2026.
