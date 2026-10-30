---
name: jobradar
description: 第一阶段岗位雷达 (stage-1 job radar)：搜索大陆与国际官方招聘和结构化岗位合集，交叉核查来源，执行有证据的第一轮硬条件淘劣，记录标准化的初筛结果（本地工作表可选）。用于岗位搜索、初筛和批次更新；不做评级排序或飞书交付（交给 potential-position），不投递。
---

# JobRadar — stage 1: search and hard elimination

## Invariants (identical in both JobRadar skills; they override everything below)

1. A human submits every application, even when the user says "go ahead" or "我同意". Stop before any submit, send, upload, recruiter message, "next step" or declaration that could file one; login, captcha and declarations belong to the user.
2. The instance's `Profile/` (hard conditions, career profile, self-description) holds only what the user chose to share with the agent: read it directly. Résumés, identity documents and other personal files are read only through the user's own privacy tooling or with their explicit permission for that file. Profile text never goes to Feishu, and career-profile aspirations are never résumé facts.
3. Permanent IDs (`JOB-000001`, or `LEAD-YYYYMMDD-NNN` for direction leads) are the only record identity. Never renumber, delete rows to eliminate, or overwrite 用户决定 / 本人备注 / 父记录.
4. Stay inside the project, `~/.config/jobradar/` and `~/.local/state/jobradar/`. Never write to another host's home (`~/.codex`, `~/.config/opencode`, `~/.claude`, `~/.deepseek`) or read its session logs.
5. No global installs (`npm -g`, `brew`, `pip` outside a project venv), global proxy/VPN, hooks or schedules without the user's explicit approval in this session.
6. Do only what the user asked in this turn. Name your mode before acting; anything outside it needs the user's go-ahead.
7. Application or interview status comes only from the user's own report or a submission receipt, never from table rows or finished materials.
8. Unknown stays unknown. Never invent a URL, deadline, quota or eligibility, and never claim a tool or capability you have not observed in this session.
9. Temporary files go in the run's `_support/` or `system/validation/<run>/`.
10. Finish with the stage's `closeout` command and quote its receipt. Without a passing receipt, report "not complete" and list what failed.

## Mode

Say which mode you are in before the first action:

| Mode | Does | Never |
|---|---|---|
| `search` | bounded collection and ingest for a named scope | grading, ranking, Feishu writes |
| `screen` | evidence-based hard elimination; unknowns stay | fit scores, second-stage ranking |
| `diagnostic` / `maintenance` | explicitly requested checks / repairs under validation | candidate collection or grading |
| `export` | optional: refresh the batch's local workbook by JOB ID, only when the user wants one | discarding manual columns |

Ranking, shortlists, Feishu delivery, link/deadline verification for chosen candidates →
`potential-position`. Say so and stop; do not stretch stage 1.

## Host tools

Read [hosts.md](references/hosts.md), find your host's row, and list the search and
page-read tools you actually have before searching. A tool that errors is a gap to report.

## Procedure

`ROOT` = the user's JobRadar instance directory (holds `Profile/`, `system/config/`, `system/state/`,
`outputs/`; layout in `templates/instance/` of the code directory), `S` = this skill's `scripts/`
directory (resolve the skill link).

1. `python3 "$S/radar.py" --root "$ROOT" doctor`. Its `watch_due` (also in the `init` receipt)
   lists recurring programmes whose re-check date has come; tell the user first.
2. Pick a **new** run name `YYYY-MM-DD_主题` (never reuse an example or an existing batch
   unless the user named it). Write the plan to
   `outputs/<run>/_support/plan.input.json` (never to `plan.json`, which init owns) with `scope`, `instances`
   (`mainland` / `international`), `max_records` and, when the user gave one, `priority`
   (ordered list, e.g. `["银行", "运营商", "其余集团"]`; collect in that order), then
   `radar.py --root "$ROOT" --run <run> init --plan <that file>`.
   If the user changes scope or priority mid-run, write the new plan to another `_support/` file
   and run `amend --plan <new.json> --reason "<user's words>"` (search runs only; diagnostic and
   maintenance plans are fixed at init); it keeps the lease, logs
   `_support/amendments.json` and refuses a budget below the records already held. Raise
   `max_records` this way rather than splitting a batch; only one stage-1 run holds the lease.
3. Collect in routing order (below). Public ATS: `collect-ats --config source.json`.
   Feishu campus collection: `collect-feishu --config "$ROOT/system/config/feishu-aggregate.json" --keyword <kw>` (optional: a missing or unfilled config means this source is skipped; say so in coverage).
   Agent-extracted records: write JSON per [record-schema.md](references/record-schema.md),
   then `ingest --input observations.json`. Every ingest appends likely duplicates to
   `_support/possible-duplicates.json`; name each pair in 批次说明 (same role under two IDs is
   linked in stage 2, never deleted). **Application-count limit:** when you first record a mainland
   employer, check its (or its group's) limit on how many roles one applicant may apply for
   this cohort and write it in `quota_note` (record-schema.md); closeout refuses a mainland
   employer without one. **Recurring programmes:** a fellowship or annual programme whose
   current window has closed is still recorded (`availability: closed`) with `recurring_note`
   quoting the official evidence that it recurs; ingest lists it as `watch_missing` until you
   add it with `radar.py watch --add <entry.json>` (record-schema.md), and closeout refuses
   before that. Ingest compares only against batch `records.json` files;
   records created only in the workbench are caught later by stage-2 `validate`/`allocate`.
4. `screen` — applies the user's `hard_rules` from `$ROOT/system/config/preferences.json` and
   refuses if the hard-condition document (`hard_constraint_source`) changed since they were
   reviewed. In an authorized maintenance task, compare the document (read directly from
   `Profile/`, invariant 2) with `hard_rules` (schema in `initial_filter.py`), update the rules,
   then set `hard_constraint_sha256`. Do not simply replace the hash to bypass the check.
5. `export` — optional local workbook (`岗位初筛工作表.xlsx`), only when the user asks for one;
   Feishu (stage 2) is the main workbench. Refuses on missing/duplicate IDs rather than dropping
   manual notes. Once a batch has a workbook, closeout requires it to be current.
6. `closeout` — writes `_support/closeout-stage1.json`; quote it in your report.

`init` acquires the same persistent project lease used by stage 2. If another run holds it,
`init` queues (`--wait`, default 90 s) and reports the holder; to wait longer, follow
"Waiting for the shared lock" in hosts.md rather than asking the user to release it. Ingest, screen,
collection and export are serialized for their full operation. `closeout` releases this
run's lease even on failure; use `init` with the same plan to resume. Do not remove another
run's lock. Search closeout requires current rules and a current screening receipt, plus a current
workbook if one was exported; an empty run cannot masquerade as a completed search.

For requested diagnostics/repairs, use `--area validation --run <dated-name>` on every
command. Plan: `mode` = `diagnostic` or `maintenance`, `scope`, `max_records` = 0,
`required_checks` = named checks; maintenance may declare exact `allowed_changes` paths.
Store `_support/checks.json` keyed by check name, each with `passed`, `evidence` (relative
file under this run), `sha256`. `closeout` verifies these files and rule readiness.
A diagnostic pass proves only those checks, not search coverage or all-host readiness.

## Search routing

1. **Official sources first:** employers, universities, institutes, government notices and
   ATS boards actually linked by the employer.
2. **Structured collections second:** the configured Feishu campus collection, before general
   platforms; read [feishu-source.md](references/feishu-source.md). Use it as an index for gaps
   in official pages; official pages resolve gaps in the collection.
3. **Other platforms and social leads:** complementary discovery only. Login history biases
   results; see [collection.md](references/collection.md) for optional adapters.

**Postdocs: search by mentor, not by posting.** Go institution →
department → faculty → recruitment evidence → funding routes; tiers, IDs and route records are
in [record-schema.md](references/record-schema.md).

**Rendering pages.** Most mainland SOE campus portals are JavaScript apps (job.10086.cn,
zhaopin.com subdomains, chng / chnenergy / china-cdt / spic portals). A plain page fetch returns
an empty shell. Use a rendering browser (hosts.md) for these before falling back to tier 3, and
record which portals still failed. Check [portals.md](references/portals.md) for known filters,
maintenance windows and workarounds, and add what you learn to it in maintenance mode.

**Browser hygiene** (the browser is the user's own, shared by every worker):
- One browser worker at a time; each opens its own tab and closes it when done.
- Never click links that open a new tab; read the `href` and navigate the same tab.
- Keep page scripts short (well under 45 s). A search that reloads the page will hang a script;
  set the form fields, submit, then read the result in a separate call.
- End every browser task with a tab inventory and close what you opened. Tabs outside the
  automation group cannot be closed by the agent; tell the user which ones remain.

**Time zones.** Mainland portals and deadlines use Asia/Shanghai; the host machine may not.
Record observation times with `+08:00`, and check portal maintenance windows in CST.

**Subagent workers** get the invariants, the mode, the record types from record-schema.md
(`conflicts` is an array, employer-linked ATS goes in `official_url`, `quota_note` per employer), the browser rules above,
and the company/title list of records already in this run and in earlier batches of the same
employers, so they skip known roles.

Priority is not proof. Match employer, actual role/program, location, graduation cohort and
dates before comparing. Keep both observations with URLs and timestamps when they disagree;
unresolved conflicts stay unknown and cannot eliminate a candidate. Never merge different
roles because the company matches, and never silently replace an official requirement with
collection metadata. A program or organization is not a confirmed vacancy. Query collections
read-only with projected fields and the built-in cache, not repeated full-table reads.

## Boundaries

- Mainland and international are coequal instances; report coverage separately.
- Historical social batches (`origin=user_social_excerpt`) are read-only and never mixed in.
- IDs come only from `system/state/skill-ids.json`; never assign or edit one by hand.
- Jev experiments only on the user's explicit task: read [jev.md](references/jev.md); results
  never enter formal candidate tables.

## Output layout

Each batch is `outputs/YYYY-MM-DD_主题/` with `批次说明.md`, `_support/` and, only if
exported, `岗位初筛工作表.xlsx`. Machine records, plans, evidence, receipts and backups go in
`_support/`. Test runs go in `system/validation/`, never `outputs/`. The workbook
freezes the header and columns through company and role; the two amber manual columns
(用户决定, 我的备注) survive refresh by JOB ID.

## Report template

- Mode and run name; tools used for search.
- Records by region and granularity (role / program / organization); retained vs eliminated.
- Unknowns, failures, truncation (`has_more`), blocked sources, coverage gaps.
- `closeout` receipt path and `passed`. Do not claim all sources, providers or institutions
  were covered.
