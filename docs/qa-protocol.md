# Pilot quality protocol

The proposal requests held-out transcription and extraction accuracy, but does not specify a numerical
acceptance threshold. Do not invent client acceptance or claim that exact-quote matching proves accuracy.

## Development

1. Inspect recordings from the development leads only. Include short follow-ups, long discovery calls,
   background noise and learner-support calls. Verify the actual speech against the transcript.
2. Review extracted field meaning and speaker attribution, not just whether the quote exists.
3. Confirm that support calls, quoted course fees, module week numbers, and agent questions do not become
   purchase intent, prospect budget, enrollment deadlines, or prospect profile facts.
4. Adjust only on development calls. Record failures and the final model/prompt revision. Freeze before
   opening held-out transcripts. The 10 held-out leads share no lead identifiers with development.

## Held-out evaluation

1. Run the frozen method on the held-out leads without changing it based on their results.
2. Use `pilot qa` to prepare local reference worksheets. A reviewer listens to each entire recording and
   writes reference text/fields independently. The reference sheet deliberately hides the ASR hypothesis.
   Include speech missing from ASR. Set `reviewed_silence=yes` only for a full call with no intelligible speech.
3. Add reviewer name and review timestamp. Leave genuinely unreviewed rows blank.
4. Rerun `pilot qa` to measure word error rate and field correctness on signed-off rows, always with review
   counts/denominators. WER normalizes case and whitespace, retaining punctuation. All-silence review sets
   report insertion counts and leave WER unavailable because the reference word denominator is zero.
   Report missing review coverage. Review omitted facts as well as extracted facts.
5. Check the exact source quote, what it means, and which speaker said it. No diarization is performed by
   the current mono-ASR route. Ambiguous role attribution requires correction or an unknown value.
6. Separate generic call-quality evidence coverage from approved script adherence (unavailable).

Until this is complete, deliverables must say accuracy is not yet independently established. The client
must not be told that the commercial accuracy gate has passed.

Both model weight revisions and the prompt are frozen. Stale artifacts are excluded from reports and QA.
Review rows belong to a specific artifact fingerprint; superseded reviews are archived separately.
An unresolved concern is retained from its source call until a cross-call review confirms it was resolved.

## Current calibration findings

On the first development call, the initial Qwen2.5 7B and Qwen3 8B candidates failed schema/evidence or
semantic checks. Errors included fabricated/mislocated quotes and treating learner module access as
purchase intent. These outputs were quarantined, not accepted into the worklist. This is evidence against
promoting those configurations; it is not an estimated error rate for the whole pilot.

The local Whisper route is running; speed results alone do not establish its transcription accuracy.
An optional paid extraction fallback is disabled unless the owner opts in and configures a local key.
