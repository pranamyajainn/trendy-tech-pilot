# Pilot quality protocol

The proposal requests held-out transcription and extraction accuracy, but does not specify a numerical
acceptance threshold. Do not invent client acceptance or claim that model agreement or exact-quote matching
proves accuracy.

## What the method checks automatically

- Three independent transcripts (Whisper, Gemini, Sarvam) vote per segment; only three-way disagreements go
  to an audio-grounded resolver. Agreement between systems reduces transcription errors but is not ground truth.
- Every quote must come from the cited consensus segment (or its boundary neighbour), with fillers ignored.
- A second model checks every extracted claim against the transcript. Overstated claims are lowered;
  unsupported claims are held in the review queue and never reach the client sheets.
- The verifier was calibrated on development calls with deliberately planted false claims
  (`data/qa/verifier-calibration.json`). That shows it is not rubber-stamping; it does not measure how many real
  errors remain.

## Development

1. Review development outputs only: transcripts, extracted meaning, speaker roles and the review queue.
2. Confirm that support calls, quoted course fees, module week numbers and agent questions do not become
   purchase intent, prospect budget, enrollment deadlines or prospect profile facts.
3. Adjust only on development calls. Record the review in `data/qa/` and freeze before opening held-out content.
   The 10 held-out leads share no lead identifiers with development.

## Human validation (required before the "Review draft" label is removed)

Two reviewer sheets. A reviewer opens each recording at the time shown and signs every row with `reviewer` and
`reviewed_at`. Unsigned rows are not counted.

1. `data/qa/worklist-review.csv` (written by `pilot worklist export`): every claim behind a Hot, Warm or
   Check-status worklist row. Set `verdict` to Confirmed, Corrected or Removed. Fix a wrong claim in the lead's
   coded file (`data/review/worklist/<lead>.json`), then run `pilot worklist check` and `export` again.
2. `data/qa/audit-claims-sample.csv` (written by `pilot qa`): a seeded random sample of up to 100 extracted
   claims from the held-out calls, which were never used to tune the method. Set `correct` to yes or no and note
   the problem.

Validation is complete when both sheets are fully signed; `pilot worklist export` then drops the REVIEW DRAFT
label. The
client report then states audit precision with its Wilson 95% interval and denominator. This measures whether
extracted claims are correct, not how much information the method missed; note omissions in `note`.

The older full-call transcription reference sheets (`pilot qa`, word error rate) remain available for a
deeper transcription audit but are not required for the validation statement.

Until validation is complete, deliverables say accuracy is not yet independently established, and the client
must not be told that the commercial accuracy gate has passed.

Stale artifacts are excluded from reports and QA. Review rows belong to a specific artifact fingerprint;
superseded reviews are archived separately. An unresolved concern is retained from its source call until a
later call shows it was resolved.
