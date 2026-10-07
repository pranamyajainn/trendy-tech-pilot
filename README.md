# TrendyTech sales call extraction pilot

Implementation of the approximately 300-call, 50-lead extraction pilot in proposal SAI-Q-2026-013.
The deliverable is a three-sheet client workbook (open-lead worklist, lead journeys, call data), a short
report and an internal gate note on cost and accuracy. There is no dashboard.
**Start with [docs/pipeline/README.md](docs/pipeline/README.md)**: the pipeline end to end, the runbook for the pilot
and Phases 1-2, the grading method and the lead coding guide. Read [the pilot scope](docs/pilot-scope.md) before
running a batch.

**Method v2 (October 2026).** Local extraction models failed development review, and a single hosted model was
replaced by a cross-checked method:

1. Three independent transcripts: local Whisper large-v3-turbo, Gemini 3.8 Flash (verbatim) and Sarvam
   Saaras v4 (Indian English, with speaker diarization).
2. Word-level alignment and a 2-of-3 vote per segment. Only segments where all three disagree go to an
   audio-grounded Gemini 3.5 Flash resolver, which chooses among the candidates; code copies the chosen text.
3. Gemini 3.8 Flash extracts each call from the consensus transcript with speaker roles.
4. Gemini 3.5 Flash checks every extracted claim against the transcript. Overstated claims are lowered,
   unsupported ones are held in a review queue and never reach the client sheets.

Model agreement is not proof of accuracy. Client outputs stay labelled "Review draft" until the human
validation pack in [the QA protocol](docs/qa-protocol.md) is signed. Method history and development findings:
[the evaluation record](docs/local-evaluation.md).

## Data flow

Client Excel → audit → frozen sample and lead-level development/holdout split → recording downloads →
three transcripts → consensus and speaker roles → extraction → claim verification → lead coding → frozen
worklist rule → client sheets, report and gate note → human validation. Later groups of leads (the customer
cohort, the non-buyer sample, and in Phase 1 the rest of the archive) use one labelled transcript per call
(`pilot cohort`, `--cohort`).

The source workbook, recordings, transcripts, model responses, review files, contact details and outputs live
under ignored `data/`. This is a public code repository: never force-add client artifacts.

## Run on Apple silicon

Python 3.11+, FFmpeg/ffprobe and enough disk space for the recordings and Whisper weights are required.
Hosted stages need `GEMINI_API_KEY` and `SARVAM_API_KEY` in the ignored `.env`, and run only with
`PILOT_ALLOW_REMOTE=1`. Wrap long runs in `caffeinate -is` on mains power with the lid open.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[local,test,qa]'
cp .env.example .env
export PILOT_EXTRACTOR=verified PILOT_ALLOW_REMOTE=1 HF_HUB_OFFLINE=1
.venv/bin/pilot audit /absolute/path/to/client-export.xlsx
.venv/bin/pilot select
.venv/bin/pilot download --split all
.venv/bin/pilot transcribe            # local Whisper
.venv/bin/pilot cross-transcribe      # Gemini
.venv/bin/pilot sarvam-transcribe     # Sarvam batch jobs of 20 calls
.venv/bin/pilot resolve
.venv/bin/pilot extract
.venv/bin/pilot status
```

Review development outputs before freezing the method. Keep held-out calls untouched until then.

```sh
.venv/bin/pilot freeze
.venv/bin/pilot transcribe --split holdout   # then the same five stages with --split holdout
.venv/bin/pilot export
.venv/bin/pilot qa                    # holdout review sheets and the held-out accuracy sample
```

The worklist, client files and gate note are built as in [the runbook](docs/pipeline/RUNBOOK.md).

Every stage resumes existing artifacts. Each artifact carries a fingerprint of its inputs and method settings,
so a changed prompt, model or setting makes earlier outputs stale instead of mixing methods. Invalid generations
get one corrective retry. Commands exit nonzero when any call fails, while preserving successful work. Audio
downloads are restricted to the approved HTTPS recording host and validated with ffprobe. Holdout inference
requires a frozen method; `freeze --supersede` and `--retire` keep the previous freeze in history.

## Paid API budget

All Gemini and Sarvam requests share one file-locked INR budget (`budget.py`). Each request reserves its
worst-case cost first and settles to provider-reported usage; definite 4xx rejections are settled as unbilled,
and requests with unknown billing keep their reservation. Sarvam reports no usage, so its cost is the
published rate times audio duration. `PILOT_API_CAP_INR` can lower the ceiling, never raise it.

Provider-safe request schemas omit keywords Gemini rejects; the full schema is still validated locally.
Busy (429/503) responses are retried under the same reservation, and daily-quota 429s fail fast.

## Outputs

- `data/exports/client/`: the only source for client deliverables (`pilot worklist export`): worklist rows,
  lead journeys, calls and meta, with evidence as date and time into the recording.
- `data/deliverables/TrendyTech Pilot Workbook.xlsx`: client workbook (`scripts/build_client_workbook.mjs`).
- `data/deliverables/TrendyTech Pilot Report.docx`: short client report (`scripts/build_report.py`).
- `data/deliverables/internal/`: the gate note (`scripts/build_gate_note.py`) and internal workbook.
  The internal workbook (`scripts/build_internal_workbook.mjs`, from `pilot export`) holds per-stage costs,
  coverage and the review queue. Neither is for the client.
- `data/exports/`: call-level extractions, evidence, findings and the review queue.
- `data/qa/worklist-review.csv` and `audit-claims-sample.csv`: the human validation pack.
- `data/ledger.jsonl`: every attempt with runtime, tokens and spend.

Blank CRM conversion flags stay unknown; Yes flags are CRM-reported, not verified. No conversion probability
is inferred for a lead: categories carry counts of similar past leads. Worklist rows describe the last recorded
conversation and must be confirmed in LeadSquared before outreach.

The 300/50 ratio requires a purposive sample enriched for repeated calls. Findings describe this sample, not
the entire archive. Timestamps preserve source values with timezone unconfirmed.

## Cost and accuracy gate

The final proposal (2 Oct 2026) moves to Phase 1 if the pilot shows the remaining archive fits Phase 1's
INR 35,000 API cost (about INR 1.19 per audio minute) and held-out accuracy checks out. The
internal workbook reports external API spend per audio minute separately from unmeasured labour, hardware
and setup costs. Unknown costs and an unsigned validation pack cannot be read as a passed gate.

## Verification

```sh
.venv/bin/pytest -q
.venv/bin/ruff check src tests scripts/*.py
```

Tests use synthetic data and cover sampling, lead-disjoint holdouts, fabricated quotes, unsupported
resolutions, the 2-of-3 vote, resolver candidate handling, claim verification tiers, budget settlement,
the worklist rule and coding checks, client-text hygiene and spreadsheet injection. The GitHub Actions configuration is saved
as `docs/ci-workflow.example.yml`; it is not active because the connected credential cannot write workflows.

Run `python scripts/check_public_tree.py` after staging and before every public push. It checks indexed
blobs, not just the working files. Never publish client recordings, source files, transcripts or deliverables.

## References

- [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) and
  [large-v3-turbo weights](https://huggingface.co/mlx-community/whisper-large-v3-turbo)
- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Sarvam speech-to-text batch API](https://docs.sarvam.ai)
