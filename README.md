# TrendyTech sales call extraction pilot

Implementation of the approximately 300-call, 50-lead extraction pilot in proposal SAI-Q-2026-013.
The deliverable is a structured workbook, a findings report and a mini worklist. There is no new dashboard.
Read [the pilot scope](docs/pilot-scope.md) before running a batch.

**Current stage: holdout evaluation.** Local Qwen models (7B to 27B) failed development meaning review.
Text extraction moved to Gemini (`gemini-3.8-flash`, prompt `extraction-v8`, evidence rules `evidence-v3`), which
passed a transcript-level development review of all 238 development calls; the method was frozen on 3 October 2026.
Independent audio-level accuracy is measured only by the holdout review; see [the QA protocol](docs/qa-protocol.md)
and [the evaluation record](docs/local-evaluation.md). Exact-quote matching is not semantic accuracy.

## Data flow

Client Excel → audit → frozen sample and lead-level QA split → recording downloads → local Whisper
transcripts → Gemini structured extraction → quote checks → lead journeys and worklist → QA and cost report.

The source workbook, recordings, transcripts, model responses, quarantine files, contact details and outputs
live under ignored `data/`. This is a public code repository: never force-add client artifacts.

## Run on Apple silicon

Python 3.11+, FFmpeg/ffprobe and enough disk space for the recordings and local model weights are required.
The first execution downloads pretrained model weights. No model training is used. Paid API calls happen only
through the opt-in hosted extraction route below.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[local,test,qa]'
cp .env.example .env
.venv/bin/pilot audit /absolute/path/to/client-export.xlsx
.venv/bin/pilot select
.venv/bin/pilot download --split all
.venv/bin/pilot transcribe --limit 12
.venv/bin/pilot extract --limit 12
.venv/bin/pilot status
```

Review development outputs and audio before freezing the method. Keep held-out calls untouched until then.

```sh
.venv/bin/pilot transcribe
.venv/bin/pilot extract
.venv/bin/pilot freeze
.venv/bin/pilot transcribe --split holdout
.venv/bin/pilot extract --split holdout
.venv/bin/pilot export
.venv/bin/pilot qa
```

Every stage resumes existing artifacts. ASR caches include audio/model/immutable-weight-revision/version hashes; extraction caches
include transcript/model/prompt/generation-setting hashes. Invalid generations are quarantined and attempted at most twice.
Commands exit nonzero when any call fails, while preserving successful work. Audio downloads are restricted
to the approved HTTPS recording host and validated with ffprobe. Holdout inference requires a frozen method.
Complete cached model snapshots are loaded locally. Set `HF_HUB_OFFLINE=1` after downloading model weights
to prevent network metadata checks. Do not run ASR and large-model extraction simultaneously on a 24 GB Mac.

## Hosted text extraction (Gemini)

Local extraction models did not pass development review. The owner approved a Gemini text-extraction test
with an INR 500 total processing cap; audio transcription stays local. The route stays off unless
`PILOT_ALLOW_REMOTE=1` and a local `GEMINI_API_KEY` are set. Trial it on development calls first:

```sh
PILOT_ALLOW_REMOTE=1 .venv/bin/python scripts/calibrate_remote.py CALL_ID [CALL_ID ...]
```

Trials write only to ignored `data/experiments/`; they never become production extractions. The request schema
omits keywords Gemini's OpenAI-compatible endpoint rejects, while the full schema is still validated locally.
Busy (429/503) responses are retried under the same budget reservation. Generation settings are part of the
extraction fingerprint and the method freeze, so changing them makes earlier outputs stale.

## Outputs and interpretation

- `data/exports/worklist.csv`: one row per lead, as of its last exported call; suggested next steps and opportunity SWOT.
- `data/exports/calls.csv`: call-level extraction and provisional coverage of six call-quality dimensions
  (script adherence is always unavailable).
- `data/exports/findings.json`, `objection_summary.csv`, `signal_summary.csv`: sample-level counts with traceable examples.
- `data/exports/evidence.csv`: exact transcript quotations with call IDs and timestamps.
- `data/exports/overview.json`: coverage and explicitly bounded cost metrics.
- `data/qa/`: full-call independent references and field review templates. Empty unreviewed references never produce accuracy scores. Explicitly reviewed silence can count hallucinated insertions.
- `data/ledger.jsonl`: successful/failed attempts, runtimes, token counts and actual external spend.

Missing extraction is distinct from no objection. Blank CRM conversion flags remain unknown. Yes flags are
unverified; no conversion rate or probability is inferred. Worklist temperature is a transparent rule based
on the last available call, not a trained score. CRM-reported enrollment and do-not-contact signals override
sales-priority labels. All outputs require human review before sales use.
Historical concerns are retained per call: a later concern in the same category does not silently resolve
an earlier one. Cross-call resolution requires review. Superseded model/prompt artifacts are excluded.

The 300/50 ratio requires a purposive sample enriched for repeated calls. Findings describe this pilot sample,
not the entire archive. Journeys include every call in this export for each selected lead; complete lifetime
coverage is unconfirmed. Timestamps preserve source values with timezone unconfirmed. Mono audio is not
speaker-diarized; speaker attribution from conversation is explicitly unverified.

## Cost and accuracy gate

The proposal uses INR 0.60/audio-minute and satisfactory held-out accuracy as the scale-up gate.
API spend for local inference is zero, but that is not the full economic cost. Configure
`PILOT_LOCAL_COMPUTE_INR_PER_HOUR` only with a defensible hardware/electricity allocation. Engineering,
human QA, storage and setup costs must be recorded separately. Unknown costs and missing independent QA
cannot be interpreted as a passed commercial gate. Automatic quote matching is not semantic accuracy.

## Verification

```sh
.venv/bin/pytest -q
.venv/bin/ruff check src tests
```

Tests cover complete-journey sampling, reproducibility, lead-disjoint holdouts, unknown outcomes, fabricated
quotes, unsupported resolutions, chronology, contact suppression, retry accounting and spreadsheet injection.
The GitHub Actions configuration is saved as `docs/ci-workflow.example.yml`. It is not active: the connected
GitHub credential cannot write workflow files. Local lint and tests can run without that permission.

## Model references

- [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper)
- [Whisper large-v3-turbo weights](https://huggingface.co/mlx-community/whisper-large-v3-turbo)
- [MLX LM](https://github.com/ml-explore/mlx-lm)
- [Qwen3.5 27B quantized weights](https://huggingface.co/mlx-community/Qwen3.5-27B-4bit)

Model choice is provisional until the actual call quality checks pass. Model agreement is not a substitute
for listening to audio and reviewing the extracted meaning.

## Private workbook and report

After `pilot export`, `scripts/export_workbook.mjs` creates the Excel workbook using the Codex bundled
`@oai/artifact-tool` runtime. `scripts/build_report.py` uses bundled Python with python-docx for the review
document. Render and visually inspect both before delivery. These builders disclose incomplete extraction
and pending accuracy rather than presenting them as completed results. JSON/CSV exports do not need the
artifact runtime. Private deliverables belong under ignored `data/deliverables/`.

Run `python scripts/check_public_tree.py` after staging and before every public push. It checks indexed
blobs, not just the working files. Never publish client recordings, source files, transcripts or deliverables.
