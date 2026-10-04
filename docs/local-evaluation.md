# Method evaluation record

This records how the method reached its current form (v2, frozen 4 October 2026). All reviews below used
the 238 development calls only; the 62 calls of the 10 held-out leads were never used for tuning.

## Local extraction (1–2 October 2026)

### What was tested

- Qwen2.5 7B, Qwen3 8B, Qwen3.5 9B and Qwen3.5 27B, quantized for the local Mac.
- The original full JSON schema with exact quotations and validation retries.
- A smaller segment-reference format: the model chooses a segment and code copies its text exactly.
- Qwen's recommended non-thinking sampling settings, with a fixed random seed.
- Reasoning enabled, both greedy and with recommended sampling, bounded at 6,500 generated tokens.
- Separate purpose, profile and discussion stages to reduce the work in each generation.

The scripts `calibrate_references.py` and `calibrate_staged.py` require development call IDs and write only
to ignored `data/experiments/`. They do not create production extractions, freeze a method, or contact an API.
Experimental outputs passing reference validation still require meaning and omission review.

### Findings

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

## Method v1: hosted extraction (Gemini), 3 October 2026

The owner approved hosted text extraction within an INR 500 cap and the client confirmed any model type may be
used. Audio transcription stayed local. The route uses `gemini-3.8-flash` through Google's OpenAI-compatible endpoint.

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

v1 was frozen after this transcript-only review, then retired (history in `data/method-freeze-history/`) when
the owner asked for cross-checked transcription and verification. Its outputs are kept under `data/superseded/`.

## Method v2: three transcripts and verified extraction, 3–4 October 2026

The owner raised the paid-API ceiling (now INR 4,000, recorded in `budget.py`) and asked for the most accurate
output rather than the cheapest.

- Transcription: the owner asked for transcription to be cross-checked rather than trusted from one system.
  Two independent hosted transcripts were added. Gemini's dedicated transcription model allowed only 100
  requests per day, so Gemini 3.8 Flash transcribes with a plain verbatim instruction; asking it for speaker
  turns made it repeat sentences. Sarvam Saaras v4 adds an Indian-English specialist with speaker diarization.
  Sarvam's keyterm list made it insert product names into unclear audio, so no keyterms are sent.
- Consensus: transcripts are aligned word by word and each Whisper segment takes the 2-of-3 majority. Short
  texts must match exactly ("fine" is not "five"). Across 11,891 development segments, all three agreed on 65%,
  two agreed on 24%, and 11% (1,002 spans) went to the resolver, which hears the audio and picks a candidate
  in shuffled order; code copies the chosen text, so the resolver cannot rewrite it.
- Speaker roles: the resolver maps Sarvam's speakers to agent or prospect, and extraction prompt
  `extraction-v9` uses those roles. Roles are not verified separately; the claim verifier rechecks attribution.
- Verification: a second model checks every claim against the transcript. Planted-error calibration caught
  30/30 false claims (wrong values, invented payments, relabelled resolutions, agent statements attributed to
  the prospect) and kept 33/35 genuine claims. On development, 391 claims were verified, 7 lowered or
  corrected and 15 held for review; 16 call types were corrected.
- Model choice: Gemini 3.1 Pro was the first resolver and verifier, but its 250 requests/day quota (shared with
  `gemini-pro-latest`) stopped the run. The owner chose to finish with Flash. Gemini 3.5 Flash judges and
  verifies, a different model from the 3.8 Flash extractor, so the checker does not grade its own output.
- Known limits: amounts said in shorthand ("120" for INR 1.2 lakh) are held for review rather than
  interpreted; occasional category slips remain; 7 speakers stayed unknown.

The development review is transcript-level and done by the builder, so it is not an independent accuracy
measurement. The human validation pack in [the QA protocol](qa-protocol.md) provides that. Do not claim the
commercial gate has passed or that conversion predictions are validated.

Generation settings were checked against the official
[Qwen3.5 model card](https://huggingface.co/Qwen/Qwen3.5-9B#best-practices).
Gemini rates were rechecked against [Google's pricing](https://ai.google.dev/gemini-api/docs/pricing) and Sarvam's
against its published pricing on 3 October 2026.
