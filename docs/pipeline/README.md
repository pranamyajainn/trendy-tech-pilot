# Pipeline: from call recordings to the open-lead worklist

Start here if you are new to this project: a developer, an intern, or an assistant without earlier context. This
folder explains the whole system, how to run it, and how to reproduce every output exactly.

| File | What it is for |
|---|---|
| `README.md` (this file) | The pipeline end to end, the folder map, and what is deterministic |
| [`RUNBOOK.md`](RUNBOOK.md) | Exact commands: rebuild the pilot outputs, add new calls, run Phase 1 and Phase 2 |
| [`METHOD.md`](METHOD.md) | How leads are graded, why this method was chosen, and what must not be claimed |
| [`CODING-GUIDE.md`](CODING-GUIDE.md) | How one lead is coded (by a person, an assistant, or the hosted model) |

The project rules in `CLAUDE.md` at the repository root always apply. Read them first. In short:
- the repository is public;
- everything under `data/` is private customer data and is never committed;
- paid API use has one shared ceiling;
- client files never show costs, model names or internal labels.

Background reading:
- the proposal (`data/source/`, private) is the authority on what is delivered;
- `docs/pilot-scope.md` gives the commitments;
- `docs/qa-protocol.md` covers the human checks.

## The pipeline

```
client export (xlsx) ─► pilot audit/select ─► calls.json, selection.json (pilot), cohorts/*.json (later groups)
        │
        ▼
recordings ─► download ─► transcribe ─► extract (+ verify) ─► one structured record per call, with quotes
        │                                                         │
        │                         profiles (per lead) ◄───────────┤
        ▼                                                         ▼
worklist select ─► worklist show / code ─► worklist check ─► worklist freeze ─► worklist export
                       (one coded file per open lead)        (rule + evidence)   (client sheets + QA sheet)
        │
        ▼
build_client_workbook.mjs ─► client workbook       build_report.py ─► client report
build_gate_note.py ─► internal gate note (cost and accuracy)
```

| Stage | Command | Writes (all under `data/`) | Paid? |
|---|---|---|---|
| Validate export, pick pilot | `pilot audit <xlsx>`, `pilot select` | `calls.json`, `selection.json` | no |
| Fix a later group of leads | `pilot cohort customers\|open_sample\|archive` | `cohorts/<name>.json` (refuses to change once fixed) | no |
| Recordings | `pilot download` | `audio/` | no |
| Transcripts | `pilot gemini-transcribe --cohort <name>` (later groups); pilot used three systems | `asr/...` | yes |
| Call records | `pilot extract --cohort <name>` (extract, then verify every claim) | `extractions/` | yes |
| Lead profiles | `pilot profiles <group>` | `review/profiles/` | yes |
| Worklist leads | `pilot worklist select` | `review/worklist-selection.json` (stored once) | no |
| Read a lead | `pilot worklist show <lead>` | prints every call with segment ids and times | no |
| Code a lead | a person or assistant per `CODING-GUIDE.md`, or `pilot worklist code <leads>` | `review/worklist/<lead>.json` | model only |
| Validate coding | `pilot worklist check [lead]` | nothing; exits nonzero on any problem | no |
| Fix the rule | `pilot worklist freeze` | `review/worklist-freeze.json` (rule hash, export date, evidence) | no |
| Grade and export | `pilot worklist export` | `exports/client/{meta,worklist,journeys,calls}.json`, `exports/internal/worklist.json`, `qa/worklist-review.csv` | no |
| Client files | `$NODE scripts/build_client_workbook.mjs`; `.venv/bin/python scripts/build_report.py` | `deliverables/` | no |
| Owner's gate note | `.venv/bin/python scripts/build_gate_note.py` | `deliverables/internal/` | no |

## What is deterministic, and how

Every stage writes its result to a file and later stages read only those files. Running a stage again therefore
reproduces the same output, unless its inputs or its method changed, and changes are refused or recorded.

- **Selections are stored once.**
  - `selection.json`, `cohorts/*.json` and `review/worklist-selection.json` come from seeded hash orders.
  - A rebuild that would differ is refused.
  - The worklist draw is `sha256(seed, lead)` order over the non-buyer sample, with seed `worklist-pilot-v1`.
- **Paid stages are fingerprinted.** A call's transcript and record carry fingerprints of the method (models,
  prompts, settings). `artifacts.py` is the single definition of "current". A stored output is reused unless
  `--force` is given or its fingerprint no longer matches the code.
- **Method freezes.** The pilot method (`pilot freeze`), each cohort's method (`pilot cohort <name> --freeze`) and
  the worklist rule (`pilot worklist freeze`) are fixed with hashes. Code that drifts from a freeze is refused until
  it is refrozen with a recorded reason.
- **Coding is the only judgement step.** Its output is a stored file per lead. Once written, it is an input like any
  other: grading, evidence and exports are pure code over stored files. Two coders can disagree, so:
  - every file passes `worklist check`;
  - every lead gets a second independent reading;
  - every Hot, Warm and Check-status claim is played and signed by a person in `qa/worklist-review.csv`.
- **The holdout stays honest.**
  - The 10 holdout pilot leads cannot be shown or coded before `worklist freeze`.
  - The freeze refuses to run once any holdout lead is coded.
  - Export refuses holdout files written before the freeze.
- **Unknown is never "no".** Missing facts stay "Not asked". Categories that cannot be measured show no rate.

## Where things live

| Path | Contents | Public? |
|---|---|---|
| `src/trendytech_pilot/` | All pipeline code. `worklist.py` holds selection, coding schema, checks, rule, evidence and export | yes |
| `scripts/` | Client workbook, client report, gate note | yes |
| `tests/` | Synthetic-data tests (`.venv/bin/pytest -q`) | yes |
| `docs/pipeline/` | This documentation | yes |
| `data/` | Exports, audio, transcripts, call records, codings, QA sheets, deliverables | **no, never commit** |
| `data/phase0-pilot/` | Browsable copy: per lead, recordings (links), readable transcripts and analyses | no |
| `data/research/` | Parameter research, method council, review scripts and figures | no |

## Before anything is shared with the client
1. `pilot worklist check` passes on every lead.
2. Every Hot, Warm and Check-status claim in `data/qa/worklist-review.csv` is played and signed.
3. The held-out sample in `data/qa/audit-claims-sample.csv` is signed. Until steps 2 and 3 are done, the client
   files say REVIEW DRAFT.
4. The words listed under `possible_names_to_scan` in `data/exports/internal/worklist.json` are checked in the client
   files.
5. `.venv/bin/pytest -q`, `.venv/bin/ruff check src tests scripts/*.py` and
   `.venv/bin/python scripts/check_public_tree.py` pass before any commit.
