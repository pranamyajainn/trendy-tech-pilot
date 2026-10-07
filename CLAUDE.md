# TrendyTech call-extraction pilot

Python CLI that turns a client CRM call export and recordings into evidence-linked, model-verified call
extractions and an open-lead worklist graded by a frozen rule, with a three-sheet client workbook (worklist, lead
journeys, calls), a two-page client report and an internal gate note, for a ~300-call / 50-lead paid pilot.
START WITH docs/pipeline/README.md: the pipeline end to end, runbook, method and lead coding guide. The final proposal SAI-Q-2026-013 (2 Oct 2026, private under data/source/)
is the authority; read it before changing deliverables. Scope: docs/pilot-scope.md. QA: docs/qa-protocol.md.
Method history: docs/local-evaluation.md. PROJECT_HANDOFF.md is an untracked 3 Oct 2026 snapshot; verify it.

## Hard rules
- PUBLIC repo. Everything under data/ is private customer data. Never git-add data/, audio, xlsx, docx, pdf,
  logs or .env. Never put transcript text, names, phones, emails or recording URLs in commits, docs, tests or
  fixtures. Tests use synthetic data only.
- Never print .env or a key. Check key readiness as a boolean only. Never pass keys as command arguments.
- Never hand-edit, delete or reset data/source.json, data/selection.json, data/ledger.jsonl,
  data/api-budget.json or data/method-freeze.json, and never point PILOT_DATA_DIR at a fresh directory to
  get around a guard or the API budget.
- Holdout (10 leads / 62 calls) stays untouched until `pilot freeze`. Never tune on holdout output.
- Experiments write only to data/experiments/. Never copy experiment output into data/extractions/.
- Paid APIs: Gemini and Sarvam only, one shared ceiling MAX_CAP_INR in budget.py (INR 4,000; owner decisions
  are recorded there). Raising it needs an explicit owner decision. Provider credit is not budget approval.
- Client outputs (exports/client, the client workbook and report) never show costs, model names, API usage,
  fingerprints, dev/holdout labels or internal field names. Those belong in the internal workbook only.
- Never claim accuracy, conversion prediction or a passed cost gate. Model agreement and quote matching are
  not correctness; only the human validation pack (docs/qa-protocol.md) can lift the "Review draft" label.
- No dashboards, servers, databases, schedulers or trained scoring models: out of pilot scope.

## Commands
- State:    .venv/bin/pilot status
- Tests:    .venv/bin/pytest -q
- Lint:     .venv/bin/ruff check src tests scripts/*.py
- Remote:   export PILOT_EXTRACTOR=verified PILOT_ALLOW_REMOTE=1 HF_HUB_OFFLINE=1 for every stage below
            (.env defaults stay local/off). Keep the same env for freeze and holdout runs.
- Pipeline: .venv/bin/pilot {download|transcribe|cross-transcribe|sarvam-transcribe|resolve|extract}
            (--split development|holdout|all; --calls ID,ID; --limit N takes the first N; --force)
            then pilot freeze [--supersede REASON|--retire REASON], export, qa (holdout sheets + accuracy sample).
- Worklist: pilot worklist {select|show LEAD|code LEADS|check [LEAD]|freeze|export} (docs/pipeline/RUNBOOK.md).
            Coded leads live in data/review/worklist/<lead>.json (docs/pipeline/CODING-GUIDE.md); every file must
            pass `check`; holdout leads are coded only after `freeze`; play-and-sign sheet data/qa/worklist-review.csv.
- Phase 1:  pilot cohort archive (every lead not yet processed), then the cohort stages with --cohort archive.
- Long runs: wrap in `caffeinate -is`, on mains power with the lid open; a sleeping Mac stalls requests.
- Outputs:  NODE=~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node
            $NODE scripts/build_client_workbook.mjs; $NODE scripts/build_internal_workbook.mjs (internal, from export)
            .venv/bin/python scripts/build_report.py; .venv/bin/python scripts/build_gate_note.py (internal)
- Before any push: stage intended files only, run .venv/bin/python scripts/check_public_tree.py,
  git diff --cached --check, and read the staged content.

## Architecture (src/trendytech_pilot/)
Transcripts: audio.py (download, local Whisper large-v3-turbo) + remote_asr.py (Gemini 3.8 Flash verbatim)
+ remote_sarvam.py (Sarvam Saaras v4 batch, diarized) → consensus.py (word alignment) → resolve.py (2-of-3
vote; only three-way disputes go to an audio-grounded Gemini 3.5 Flash resolver, which also maps Sarvam
speakers to agent/prospect). Extraction: extract.py (prompt, retries) + remote.py (GeminiExtractor,
provider-safe schema, busy retries) → ensemble.py (Gemini 3.5 Flash verifies every claim against the
transcript; unsupported claims go to the review queue) → schema.py (validate_evidence). budget.py: one
file-locked INR budget for all paid calls. artifacts.py: the single definition of "current" (fingerprints,
freeze). reporting.py: internal exports and review queue. client.py: client-text guard, dates and the held-out
accuracy sample (validation_pack). quality.py: holdout QA and validation status. patterns.py: buyer vs non-buyer
comparison and the two-sided conversion-rate interval. worklist.py: worklist selection, coding schema and checks,
the frozen rule (categorise), past-lead evidence, freeze and client export. cohort.py: customers, open_sample and
archive groups. cli.py: commands. The pre-7-Oct client sheets (lead priorities, groups, who buys) are retired.
scripts/build_pilot_folder.py: browsable private copy (data/phase0-pilot). Experimental modules (referenced.py,
experiments.py, calibrate scripts), lead-action drafting and swot.py were removed on 7 Oct 2026 (git history).

## Conventions
- Python >=3.11, ruff line-length 110. Match the existing dense style; comments explain why.
- All JSON writes go through storage.write_json. Every paid or inference attempt (success, failed,
  interrupted) gets a Store.event with wall_seconds and external_cost_inr, and a Budget reservation.
- Any method identity dict (GEMINI_ASR, SARVAM_ASR, RESOLVER, VERIFIED_GENERATION, PROMPT_VERSION,
  EVIDENCE_RULES_VERSION) is part of a fingerprint: changing it makes stored outputs stale and needs a refreeze.
- CLI batches isolate per-call failures and exit nonzero; never turn failures into empty successes.
- Unknown is not zero or negative: blank CRM flags, missing extractions and unmeasured costs stay null.

## Provider limits found (3 Oct 2026)
- gemini-3.5-transcribe: 100 requests/day; gemini-3.1-pro (shared with gemini-pro-latest): 250/day. Neither
  is used now. A 429 containing "PerDay" fails fast; 4xx rejections are settled as unbilled.
- Sarvam keyterms made the model insert product names into unclear audio; they are deliberately not sent.

## Environment caveats
- .venv's base interpreter and the workbook runtime (@oai/artifact-tool via the node_modules symlink) live in
  ~/.cache/codex-runtimes/. Do not delete that cache. soffice/pdftoppm for render checks are under
  ~/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/override/.
- Audio metadata stores absolute paths: do not move the project folder.
- ffmpeg/ffprobe come from Homebrew; model weights are in ~/.cache/huggingface/hub.
