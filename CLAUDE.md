# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

The rules, product invariants and test list above come from `AGENTS.md` (shared with Codex and
other hosts). Keep shared rules there; this file only adds orientation for Claude Code.

## Commands

No build step and no dependency manifest: the Python scripts use only the standard library;
only the optional Jev helpers (`jev*.mjs`) need Node. Tests are plain `unittest` files run directly (no pytest).

```bash
export PYTHONPYCACHEPREFIX="$TMPDIR/pyc"   # keep __pycache__ out of the repo
python3 system/skill/jobradar/scripts/test_radar.py                 # whole file
python3 system/skill/jobradar/scripts/test_radar.py RadarTests.test_ids_seeded_above_existing_batches_and_shared_between_runs   # one test
```

Baseline: the four suites in AGENTS.md all pass (37/44/12/2).
`system/tests/hosts/run_host_evals.py` launches real host CLIs and writes files, so do not run it.

## Architecture

Two agent skills, each a `SKILL.md` (agent procedure, in Chinese/English) plus a
deterministic Python core. The host agent does search, page reading and grading; the cores
never call an LLM and never submit anything. They enforce the workflow as gates and receipts.

- **Stage 1 `jobradar`** — `system/skill/jobradar/scripts/radar.py`, an argparse CLI:
  `doctor`, `watch`, `init --plan`, `amend`, `ingest`, `collect-ats`, `collect-feishu`,
  `screen`, `export`, `status`, `closeout`. A run is a batch folder
  `outputs/<YYYY-MM-DD_主题>/` (or `system/validation/<run>/` with `--area validation`)
  holding `岗位初筛工作表.xlsx`, `批次说明.md` and `_support/` (plan, records, evidence,
  receipts). `init` takes a shared lease (`system/state/active-run.json`); every other command
  except `status`/`closeout` requires it. Permanent `JOB-NNNNNN` IDs are allocated from
  `system/state/` and seeded above the highest ID in any batch's `records.json`. `initial_filter.py` is a generic
  evidence-first engine; thresholds and personal flags come from the instance's
  `system/config/preferences.json` → `hard_rules` (schema in the module docstring).
  `radar.rules()` refuses while the hard-condition document's SHA-256 differs from
  `hard_constraint_sha256`, and `screen` stores a fingerprint (document, rules, implementation)
  that `export`/`closeout` compare against the current one.
- **Stage 2 `potential-position`** — `system/skill/potential-position/scripts/position.py`
  loads `radar.py` as a module and adds a mode-gated Feishu workflow:
  `preflight --mode review|maintain|deliver|track` → `snapshot --label start` → `validate` →
  `apply --delta` (every Feishu write: checked against the mode, simulated, written, re-read,
  manual fields/IDs/column order guarded) → `closeout` (diffs remote state against the start
  snapshot, so writes that bypassed `apply` show up). Feishu access goes through `lark-cli`;
  tests use a fixture remote (`fixture_remote` / `--fixture`) instead.
- **Config**: code-level in `system/config/` (`jev.json`);
  user-level (`preferences.json`, `feishu-workbench.json`, optional `feishu-aggregate.json`) only in an instance, see
  `templates/instance/`. Skill reference docs in
  `system/skill/*/references/` (record schema, portals, hosts, Feishu, Jev, quotas).
- **Optional collectors**: Jev/TypeSafe browser helpers (`jev*.mjs`,
  `jev_ultrafast_browser.py`), Chrome/CDP (`system/tools/browser.py`).
- **Workbook** (optional; Feishu is the main workbench, and stage 2 reads
  `records.json`, not the xlsx): `scripts/workbook.py` writes and reads `岗位初筛工作表.xlsx` with zipfile + XML.
  Closeout only requires it to be current once a batch has exported one.
  Each export regenerates the sheet but keeps the user's row order and the manual columns
  (用户决定, 我的备注, including formulas) by JOB ID; it refuses rather than drop content it
  cannot carry over (other sheets, cells beyond column R, shared formulas).
- `docs/RADAR.md` and `docs/WORKFLOW.md` are the human-facing guides.

## Code directory vs. instance

This repository is the code. A user's data lives in a separate **instance** directory with the
layout of `templates/instance/` (`Profile/`, `system/config/`, `system/state/`, `outputs/`,
`system/validation/`); `--root` (default: cwd) points at it, and every `root / ...` path in
`radar.py`/`position.py` is instance data. Code-side paths come from `__file__`
(`parents[N]`, `system/runtime/`, `jev-client.mjs` → `system/config/jev.json`). Do not run the
scripts with `--root` at this repository; for a manual check copy `templates/instance` to a temp
dir. The example instance is also the rule fixture of `test_initial_filter.py`/`test_radar.py`,
so editing its `hard_rules` or `Profile/hard-conditions.md` means updating its
`hard_constraint_sha256` and the tests together.

`doctor` runs `lark-cli auth status` against the real Feishu login; run it with `lark-cli` off
`PATH` when developing.
