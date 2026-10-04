# Pilot implementation scope

Authority: client proposal SAI-Q-2026-013, 28 September 2026, pages 3–4.
The customer authorised starting the pilot on 1 October 2026 and initially chose local models.
On 3 October 2026 the customer confirmed that local, open-source or paid hosted models may be used.
Local extraction failed development review, so extraction moved to hosted models. The owner then asked for
the most accurate output rather than the cheapest: method v2 cross-checks three transcripts (local Whisper,
Gemini, Sarvam) and verifies every extracted claim with a second model, within an INR 4,000 API ceiling.
On 3 October 2026 the client asked for a two-sheet client workbook (Sales Insights, Lead Actions) with no
costs, model names or technical labels; costs, logs and QA moved to a separate internal workbook.
The signed proposal file, recordings and client workbook remain outside Git history.

## Commitments

- Build an extraction pipeline and process approximately 300 calls across approximately 50 lead journeys.
- Extract stated profile, goals, timing, pitches, responses, objections and handling with supporting transcript evidence.
- Deliver a structured workbook, a short findings report and a mini worklist with next-call guidance.
- Benchmark transcription and extraction quality with a held-out sample.
- Measure processing cost per audio minute, against the proposal's INR 0.60/minute scale-up ceiling.
- Preserve every available call for selected leads. An export-complete journey is not proof of a complete lifetime history.

## Current instructions and data limitations

The owner confirmed verified payment dates and approved scripts are unavailable and instructed us to proceed.
Blank conversion flags stay unknown. A Yes flag means CRM-reported conversion, not independently verified purchase.
The pilot does not estimate conversion probabilities, claim uplift, or attribute sales causally to a pitch.
Script adherence remains unavailable. Other call-quality dimensions use an explicitly provisional rubric.
All source rows are answered calls, so best-contact-time success rates cannot be estimated.
Recorded timestamps retain their source values; timezone is unconfirmed.
Worklist guidance is retrospective, as of the last available call. Current live status requires confirmation.

## Selection and evaluation

Select 50 leads, 300 calls, deterministically from the provided export. Enrich for multi-call journeys to meet
the contracted call/lead ratio; this is a purposive pilot sample, not a representative conversion cohort.
Keep 10 entire leads in a locked QA holdout before examining their content. Never tune on the holdout.
Record source hash, selection seed, sampling strata and model/prompt versions.
Automatic schema/evidence checks measure consistency, not real-world transcription or extraction accuracy.
Final accuracy needs independent reference transcripts and field judgements, kept separate from model output.
No numerical accuracy acceptance threshold was specified in the proposal. Proposed targets must be labelled
internal and cannot be claimed as client-agreed acceptance criteria.

## Cost boundaries

Track unique successful audio minutes once, every inference attempt and retry, wall time, model identifiers,
external API spend and optional local compute cost. Local API spend can be zero while labour, power and
hardware are nonzero or unmeasured. An unmeasured fully allocated rate cannot pass the commercial cost gate.
Show the external-spend rate separately from the fully allocated processing rate and quality gate.

## Excluded

Full archive processing, ML training, live voice agents, a dashboard, CRM integration, current-lead refresh
automation, calibrated lead probabilities, and controlled conversion-lift experiments are outside this pilot.

## Delivery checklist

- [x] 300 downloaded audio files validated with ffprobe, with source manifest and checksums
- [x] Method v2 developed and reviewed on the 238 development calls, then frozen (4 Oct 2026)
- [x] 300 consensus transcripts and verified extractions, including the 62 held-out calls (4 Oct 2026)
- [x] 7 sales findings and one next action per lead, each traceable to a call and time
- [x] Client workbook and report (review drafts), internal workbook with costs and review queue
- [ ] Human validation pack signed: every client statement and a 100-claim held-out audit sample
- [x] Actual runtime and API cost ledger; labour, hardware and setup costs recorded as unmeasured
