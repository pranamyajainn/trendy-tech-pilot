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

## Next decision

The owner has approved testing a hosted text extractor with an INR 500 processing cap; the local API key
is still pending. Keep audio transcription local. The prepared Gemini fallback reserves estimated cost before each request, retains
uncertain reservations, and limits the configured processing budget to INR 500 or less. It remains disabled.
Do not claim the full pilot is client-ready, a cost gate is passed, or conversion predictions are validated.

Generation settings were checked against the official
[Qwen3.5 model card](https://huggingface.co/Qwen/Qwen3.5-9B#best-practices).
The optional fallback's rates were rechecked against
[Google's pricing](https://ai.google.dev/gemini-api/docs/pricing) on 3 October 2026.
