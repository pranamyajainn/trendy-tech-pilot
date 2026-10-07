# Runbook

All commands run from the repository root. `pilot` means `.venv/bin/pilot`.

```bash
NODE=~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
```

Paid stages need the remote environment from `CLAUDE.md`:
`export PILOT_EXTRACTOR=verified PILOT_ALLOW_REMOTE=1 HF_HUB_OFFLINE=1`. They are recorded against the shared API
ceiling in `budget.py`; raising that ceiling is the owner's decision. Wrap long runs in `caffeinate -is` on mains
power.

## A. Rebuild the pilot deliverables from stored data (free, deterministic)

```bash
pilot status                              # inventory; method must show frozen
pilot worklist check                      # every coded lead valid (holdout listed as "not coded" until step 3)
pilot worklist freeze                     # once only; refuses if a holdout lead is already coded
# ... code the 3 open holdout leads now (CODING-GUIDE.md), then a second reading, then:
pilot worklist check
pilot worklist export                     # categories, client sheets, internal record, data/qa/worklist-review.csv
$NODE scripts/build_client_workbook.mjs   # data/deliverables/TrendyTech Pilot Workbook.xlsx
.venv/bin/python scripts/build_report.py  # data/deliverables/TrendyTech Pilot Report.docx
.venv/bin/python scripts/build_gate_note.py   # data/deliverables/internal/Pilot Gate Note.docx (owner only)
```

Running `export` and the three builders again gives identical files, apart from the build timestamps inside the
Office files. Signatures already entered in `data/qa/worklist-review.csv` are kept.

### Human checks before sharing
- **`data/qa/worklist-review.csv`.** For each row, play the call at the timestamp. Set `verdict` (Confirmed,
  Corrected or Removed) and fill `reviewer` and `reviewed_at`. Fix any wrong coding file, then run `check` and
  `export` again.
- **`data/qa/audit-claims-sample.csv`.** The held-out claim sample: set `correct` (yes or no), `reviewer` and
  `reviewed_at`.
- **Possible names.** Scan the words under `possible_names_to_scan` in `data/exports/internal/worklist.json`.

When the first two are complete, `export` drops the REVIEW DRAFT label.

## B. Coding a lead by hand (intern or assistant)
1. `pilot worklist show <lead>` and read every call.
2. Write `data/review/worklist/<lead>.json` per `CODING-GUIDE.md`.
3. `pilot worklist check <lead>` until it prints OK.
4. A different person or assistant re-reads the calls first, then the file, and corrects it.

## C. Phase 1: the rest of the archive

About 5,500 calls (about 15,600 audio minutes) are not processed yet.

1. **Fix the group:**
   ```bash
   pilot cohort archive
   ```
   It holds every lead not in the pilot, `customers` or `open_sample`, as whole journeys.
2. **Validate the method on a few calls, then freeze it:**
   ```bash
   pilot gemini-transcribe --cohort archive --calls <5 ids>
   pilot extract --cohort archive --calls <same>
   pilot cohort archive --freeze
   ```
3. **Full run:**
   ```bash
   pilot download --cohort archive
   pilot gemini-transcribe --cohort archive
   pilot extract --cohort archive
   ```
   Failures are isolated per call and the command exits nonzero. Re-running retries only what is missing.
4. **Profiles:** `pilot profiles <group>`. The `profiles` command takes a group name; add `archive` to its choices
   in `cli.py` when needed.
5. **Recompute the past-lead evidence on the full data.** This is a new freeze, because the evidence changes: run
   `pilot worklist freeze --force` and record the reason.
6. **Code the open leads at scale with `pilot worklist code <leads>`.** First measure agreement with the pilot's
   reviewed codings:
   - code the same leads with the model into a scratch copy of `data/`;
   - compare category, stance and status check;
   - agree a threshold with the owner before trusting it.

   The worklist selection (`worklist.select`) covers the pilot. Phase 1 needs a selection of current open leads
   added the same way: a stored, seeded list.
7. **Rebuild the outputs as in section A.**

## D. Phase 2: daily calls
Each day's new calls go through `download → gemini-transcribe → extract` under the frozen method. Then the leads they
touch are re-coded (`worklist code`) and the worklist is re-exported. Once a month:
- recompute the evidence (a new freeze, with the reason recorded);
- compare last month's categories with who actually bought, from the LeadSquared feed;
- have a person check a sample of about 30 coded leads.

## E. If something stops halfway
Every stage is safe to re-run. Stored results are reused, and only missing or stale items are processed. Check
`data/ledger.jsonl` for what ran and what it cost. Never hand-edit or delete `source.json`, `selection.json`,
`ledger.jsonl`, `api-budget.json`, `method-freeze.json` or the cohort files.
