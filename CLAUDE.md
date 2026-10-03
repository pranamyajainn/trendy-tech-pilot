# TrendyTech call-extraction pilot

Local Python CLI (Apple-silicon MLX for audio, Gemini for text extraction) that turns a client CRM call export
and recordings into evidence-linked extractions, lead-journey worklists, an Excel workbook and a Word report for
a ~300-call / 50-lead paid pilot (proposal SAI-Q-2026-013). Scope: docs/pilot-scope.md. QA: docs/qa-protocol.md.
History: PROJECT_HANDOFF.md (a 3 Oct 2026 snapshot written by a previous agent; verify claims against code/data).

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
- Paid API: Gemini only, project cap INR 500 enforced in remote.py. Raising it needs an explicit owner decision.
  Google account credit is not budget approval.
- No new model downloads without approval. Use HF_HUB_OFFLINE=1. Never run ASR and a large local LLM at once.
- Never claim accuracy, conversion prediction or a passed cost gate. Quote matching is not correctness.
- No dashboards, servers, databases, schedulers or trained scoring models: out of pilot scope.

## Commands
- State:    .venv/bin/pilot status
- Tests:    .venv/bin/pytest -q
- Lint:     .venv/bin/ruff check src tests scripts/*.py
- Pipeline: .venv/bin/pilot {audit <xlsx>|select|download|transcribe|extract|freeze|export|qa}
            (--split development|holdout|all; --limit N takes the first N, not a sample; --force)
- Gemini:   export PILOT_EXTRACTOR=gemini PILOT_ALLOW_REMOTE=1 HF_HUB_OFFLINE=1 before extract/freeze/holdout
            commands (the .env defaults stay local/off). Keep the same env for freeze and holdout runs.
- Trial:    PILOT_ALLOW_REMOTE=1 .venv/bin/python scripts/calibrate_remote.py CALL_ID ... (development only)
- Outputs:  .venv/bin/pilot export, then
            ~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node scripts/export_workbook.mjs
            .venv/bin/python scripts/build_report.py
- Before any push: stage intended files only, run .venv/bin/python scripts/check_public_tree.py,
  git diff --cached --check, and read the staged content.

## Architecture (src/trendytech_pilot/)
cli.py (commands, split, freeze) → ingest.py (audit, sampling) → audio.py (download, mlx-whisper)
→ extract.py (prompt, LocalExtractor, extract_call with retry/quarantine) + remote.py (GeminiExtractor,
budget reservation + lock, busy retries, provider-safe schema) → schema.py (pydantic + validate_evidence)
→ artifacts.py (freshness; the single definition of "current") → reporting.py (exports, worklist rules,
cost) / quality.py (holdout QA). storage.py: atomic write_json, safe_cell CSV escaping, Store.event ledger.
models.py: pinned local revisions. Experimental, not wired into the CLI: referenced.py, experiments.py,
scripts/calibrate_*.py.

## Conventions
- Python >=3.11, ruff line-length 110. Match the existing dense style; comments explain why.
- All JSON writes go through storage.write_json. Every download/inference attempt (success, failed,
  interrupted) gets a Store.event with wall_seconds and external_cost_inr.
- Extraction identity = transcript + model + revision + PROMPT_VERSION + SYSTEM + generation_config(model).
  Bump PROMPT_VERSION when SYSTEM changes and ASR_VERSION when ASR behaviour changes; register a new local
  model's immutable revision in models.py first.
- CLI batches isolate per-call failures and exit nonzero; never turn failures into empty successes.
- Unknown is not zero or negative: blank CRM flags, missing extractions and unmeasured costs stay null.

## Environment caveats
- .venv's base interpreter and the workbook runtime (@oai/artifact-tool via the node_modules symlink) live in
  ~/.cache/codex-runtimes/. Do not delete that cache.
- Audio metadata stores absolute paths: do not move the project folder.
- ffprobe comes from Homebrew; model weights are in ~/.cache/huggingface/hub (5 pinned snapshots).
