---
name: potential-position
description: 第二阶段岗位择优 (stage-2 shortlist)：依据最新硬条件和职业画像复核已有候选，保留可能申请的对象并评级，核实岗位直达入口、截止日期与名额限制，维护或交付飞书求职工作台，记录本人报告的投递状态。用于第二轮精筛、评级、表格维护与交付；不做海量搜索（交给 jobradar），不执行投递。
---

# PotentialPosition — stage 2: grade, verify, deliver

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

## Mode (declared with `preflight`; the script enforces it)

| Mode | Use when the user asks to… | Allowed | Never |
|---|---|---|---|
| `review` | screen/grade/verify named candidates | hard gate, grade, verify pages, update or create candidate records | search beyond the named candidates |
| `maintain` | adjust columns, fields, names, wording ("只维护表格") | only the record fields (`--fields`) and view columns (`--columns --allow-views`) the user named | search, re-grade, add candidates, move other columns |
| `deliver` | push verified results to the workbench | delta writes by JOB ID; column moves only for `--columns` with `--allow-views` | overwrite manual fields or the user's column order |
| `track` | record that they applied / got an interview | create or update `application_tracking` rows (投递状态, 投递时间, 下次安排, 安排事项, 状态依据) with `--evidence user_report:<quote>` or `receipt:<path>` | inferring status from rows or prepared materials |

A request that mixes modes is done as separate runs in order, or you ask which comes first.
Stage-1 mass search belongs to `jobradar`.

## Host tools

Read [hosts.md](../jobradar/references/hosts.md) for your host's search, browser and ask-user
tools. List what you actually have before verifying pages.

## Procedure

`ROOT` = the user's JobRadar instance directory (see jobradar's Procedure), `P` = `python3 <this skill's scripts/>/position.py --root $ROOT --run <run>`.
The run is the batch folder name under `outputs/` (existing batch for updates, new
`YYYY-MM-DD_主题` for a new one).

**Before review mode: the hard conditions and profile.** Read the latest hard-condition and
career-profile documents directly from `$ROOT/Profile/` (invariant 2). Write the grading criteria you need into
the run's working notes in your own words; never copy profile text into Feishu or `_support/`.

1. `$P preflight --mode <mode> --host <codex|opencode|claude-code|deepseek|other> [--fields a,b] [--columns c,d --allow-views] [--evidence …]`
   While another run holds the lock it queues (`--wait`, default 90 s) and names the holder.
   To wait longer, follow "Waiting for the shared lock" in jobradar `references/hosts.md`;
   do not ask the user to release a lock whose holder is still working. Only the user may
   approve `$P release --user-approved`, and only for an abandoned holder. On the live workbench it also refuses when the lark-cli user
   login is not ready; ask the user to run `lark-cli auth login` themselves (in Claude Code:
   `! lark-cli auth login`), then retry. `radar.py doctor`
   shows the same `feishu_auth` status earlier.
2. `$P snapshot --label start` after **every** preflight (closeout diffs the table against it;
   `apply` and `closeout` refuse a start snapshot older than the current preflight). A new
   session, day or mode is best a new run name, so one run's receipt covers one piece of work. Then
   `$P validate --snapshot <path>`. Existing findings are reported, not silently fixed; fix only
   what the mode and request cover.
3. Do the mode's work using the decision rules below. Express every record or column change
   as a delta JSON:
   `{"update":[{"job_id":"JOB-000191","fields":{…}}], "create":[{"fields":{…}}], "views":[{"view":"vew…","visible_fields":[…]}]}`
   (`"table"` defaults to `candidates`; postdoctoral mentors, unit posts and fellowships go in `postdoc_mentors`, the
   merged postdoc table (same fields and rules; the old `postdocs` table is retired); other tables: `institutions`, `application_tracking`,
   `quotas`, `group_comparison`, addressed by `record_id`). 机会编号 never changes. A record
   that came from stage 1 keeps its stage-1 ID: `$P draft --from-run <stage-1 run> --ids JOB-…
   --out stage2/draft.json` (relative paths land in this run's `_support/`) prefills company, role, city, link and source facts. Fill
   its `_todo` items, resolve any `_possible_duplicates`, then delete every `_` key; apply refuses
   unknown keys. Draft refuses records eliminated in stage 1. A
   candidate found only in stage 2 gets its ID from `$P allocate --url <url> --key <record-key>
   --company <company> --title <role>`. If that returns `possible_duplicates`, update the
   existing ID instead. Formula fields are never written.
4. `$P apply --delta delta.json` — the only way to write records or view columns. It checks the
   delta against the mode, simulates it (refusing new validation errors), writes, confirms the
   values stuck, and guards manual fields, IDs, column order, deletions and grades. If it
   refuses, change the delta or ask the user; never bypass it with `lark-cli` write commands:
   closeout diffs the final table against the start snapshot and flags unexplained writes. Schema work `apply` cannot express (new field, field type change,
   formula, new view) needs the user's explicit request; take a snapshot before and after and
   run `validate` on both.
5. `$P closeout` — writes `_support/closeout-stage2.json`; quote it. A failure keeps the lock
   and must be reported to the user as "not complete".

## Decision rules (review mode)

1. **Hard conditions first.** Use the latest hard-condition document (read directly from `Profile/`). A
   record with clear evidence of violating a strict condition (e.g. region) becomes
   `候选状态=硬条件淘汰` with `淘汰原因`, no grade, and moves to the audit view. Unknown
   conditions stay 待核; they neither pass nor eliminate.
2. **Keep, don't cull.** Everything without a hard conflict that the user might apply to stays;
   weak fit is a low grade, not elimination. Label roles, recruitment programmes, organizations
   and LEAD direction leads separately; a LEAD never masquerades as a confirmed JOB. Every LEAD still shows a clickable verified announcement or official-entry link in 岗位直达链接, labelled by nature (official or reprint, batch notice, not a single job); see delivery.md.
3. **Grade by fit only.** Duties, evidence of ability, career development and stated
   preferences. S very strong, A strong, B worth applying, C supplementary, D low priority.
   No quota on S; no single label (上海, 博士, AI, 总行直属) earns a grade; research experience
   is not automatically production-engineering ability. Deadlines and urgency never enter the
   grade or its reason. Mark provisional grades when evidence is thin. A record you cannot grade
   yet says `未评级` in 评级依据状态.
4. **Quota before time.** Read [quota.md](references/quota.md). When a recruiting system limits
   institutions/roles, verify this cohort's official rules and compare the whole quota group;
   the user allocates. Urgency never consumes scarce slots. Mainland SOE groups and banks often
   cap the total across regions and role types; a small cap (e.g. 1) means present the group's
   best-fit role first and the rest as alternatives. Start from the stage-1 `quota_note`;
   a 限额处理 still reading 未核 blocks 可供本人决策 unless the user accepts it. Each 限额组 written on a candidate
   also gets one rule row in the `quotas` table in the same run; validate warns (`quota_table`)
   when a group has no rule row.
   **Check application status before any deadline or urgency claim.**
   Before reporting a deadline, urgency or "still open" for any record — in every mode,
   including page re-verification — read `application_tracking` and the record's `quotas` row.
   An applied job is reported as applied, never as urgent. A job whose quota group is full
   (by the user's report or a receipt) goes in a separate "名额已满，不再新增" group with its
   deadline as information only, never in an urgent list or call to action. A group that may still
   have room is reported with the slots used and left, and the user allocates.
5. **Ordering is separate.** Default S→A→B→C→D, then urgency (特别紧急 split by days left:
   0 = today, 1, 2, 3; then 4–7 一般紧急, 8+ 常规; past and unknown dates flagged separately), reference deadline, JOB ID. Native
   `TODAY()` formulas compute urgency; do not re-grade over time. Keep date confidence and the
   site's time zone; an unknown date is not urgent. A low-grade job near its deadline never
   displaces a higher-grade one, and quota-unresolved jobs are shown separately rather than
   slipping in through the urgency view.
6. **Postdocs need preparation time.** The user's window is `postdoc_prep_days` (instance `system/config/preferences.json`; `radar.py doctor` and `preflight` report it, 0 = no window). Postdoctoral mentors, unit posts and
   fellowships live in the merged `postdoc_mentors` table (类型 says which),
   whose deadline formula counts days left before that window: fewer days shows `准备期不足N天，按已过期处理` (code 8) and is
   reported like an expired date, never as urgent; its status and grade stay. A recurring one
   also goes on the watchlist (rule 7). Move a postdoc found in `candidates` with a delta
   `{"move": [{"job_id": "JOB-…", "from": "candidates", "to": "postdoc_mentors"}]}` (review or deliver
   mode): apply copies it, confirms the copy, then deletes the source row; link fields must be
   empty first, and the IDs stay unique across both tables.
   **Enterprise postdoc stations are ordinary jobs:** they belong in
   `candidates` under rules 1–5 (normal urgency, no preparation window, no mentor table); move one found
   in `postdoc_mentors` with `"from": "postdoc_mentors", "to": "candidates"`. See jobradar `record-schema.md`
   for which hosts count as enterprise.
   **Mentor-centred:** the `postdoc_mentors` table (博士后导师) holds one
   row per host supervisor, sorted by 证据层级 (1 posting + homepage, 2 homepage, 3 posting,
   4 potential = LEAD); see jobradar `record-schema.md`. One table holds all of it and every row is graded S–D by fit only
   (rule 3; thin evidence = provisional grade, and the evidence tier never sets the grade). For a
   unit post without a named PI the 导师｜课题组 cell holds the unit or company. 资助途径 lists
   only routes the mentor can host by evidence (same institution, programme rule); each 基金项目 row
   states 合作导师要求 from the official page (须先联系合作导师 / 学校提名 / 不需要 / 待核).
7. **Recurring programmes go on the local watchlist, not into oblivion.**
   When a fellowship or annual programme's current window has passed or the user says it is
   too late, add it to `system/state/watchlist.json` with `radar.py watch --add`
   (fields in jobradar `record-schema.md`). Do not eliminate it or change its status or grade;
   prepend `已挂起待下轮：见本地长期关注清单 <id>；` to its 核查缺口 so the table shows it is held.
   `preflight` lists `watch_due` entries; report them to the user before the mode's work.
8. **Identity.** A related sub-role, a different cohort or a programme page never silently
   replaces the original job; record it as a separate candidate or as a verification gap.
   The same role under two IDs (stage-1 `possible_duplicates`, validate `possible_duplicate`)
   is resolved by updating the older ID and not creating the newer one; never delete a record.
9. **Employment-type labels: judge the evidence, then decide who decides.** A label such as
   "岗位性质：合同工" can mean an ordinary labour contract, a special status (postdoc station,
   doctoral or talent track) or a role without formal status. Rule:
   - **Confirmed**: official text for that role or announcement establishes an ordinary contract
     with no formal status (劳务派遣, 外包, 不纳入编制/非正式职工, fixed-term project hire, or
     equivalent). Eliminate directly: `硬条件淘汰`, 淘汰原因 quotes the text and URL. The label
     by itself, an inference from silence, or a repost never counts as confirmation.
   - **Not confirmed**: never eliminate or down-grade on the label. Write
     `待本人判断：岗位性质标注为“<原文>”（<URL>）` in 核查缺口, with any 劳务派遣 / 外包 / 编制
     wording found in the announcement and whether the role is a postdoc, a doctoral or
     talent-programme track, or an ordinary graduate hire. In SOEs a contract label often
     reflects a special status such as a postdoctoral station contract, and doctoral hires are
     rarely denied formal status without reason, so never present the label as a likely
     violation. The record cannot be `可供本人决策` until the user resolves it
     (`本人已接受：…`) or eliminates it themselves.
   - Record which official pages were checked either way.

## Verification loop (done in stage 2, not deferred to after the user chooses)

For every candidate: this cohort's duties, city, eligibility, deadline, direct application link
and application limits. Statuses: 待核线索 → 公开信息已核，关键条件待确认 → 可供本人决策, or
硬条件淘汰. `可供本人决策` requires no open 核查缺口, a verified direct link and a resolved quota
group, unless the user explicitly accepts a named unknown — write it as `本人已接受：<item>` in
核查缺口 or 限额处理. `validate` enforces this. An empty
decision view is acceptable; never lower the bar to fill it. 本人行动 holds only the user's
choices and preparation; unfinished agent work goes to 核查缺口. For undisclosed items (pay,
contract type, hours) record which official sources were checked and what stays unknown.

Page checks: match employer, role name, location, cohort and deadline on the real page, official
or employer-linked ATS first. Never click an apply/submit button to test a link. Record the
concrete obstacle when a page only offers a company list, email, login path or conflicting cohort.
Network and proxy limits are in [network.md](references/network.md).

## Delivery

Read [delivery.md](references/delivery.md) before touching the workbench or producing a local
table. Only job and process summaries go to Feishu; no identity data, résumé text or attachments.
Tailored materials start only after the user picks a JOB ID, and stop at `ready_for_human`.
`scripts/presentation.py` provides the link-cell, urgency and ordering helpers. Ordering rule: applied jobs (per `application_tracking`) always sort last in candidate views, whatever their grade; only the table order changes, never the record data.

## Report template

- Mode, run, host, tools used.
- Counts: active / eliminated / graded by level / decision-ready; records changed.
- Remaining unknowns and verification gaps per JOB ID; network or page obstacles.
- Deadlines and urgency only after the application-status and quota check (decision rule 4):
  applied and quota-full jobs are listed separately and never as urgent.
- `validate` findings you did not fix (and why), `closeout` receipt path and `passed`.
  A write succeeding does not mean links work; a passing format check does not mean the
  grading is right.
