# Input and evidence contract

Ingest JSON: `{"records": [...]}`. Fields below are strings except the numeric source_tier:

| Field | Meaning |
|---|---|
| company, title, location | Official employer, role and location; use 未知 if absent |
| region | mainland / international / unassigned |
| kind | role / program / organization |
| official_url | Verified official HTTP(S) URL; required for tier 1, optional otherwise. An ATS page reached through the employer's own portal link (e.g. xiaoyuan.zhaopin.com from the operator's campus site) is official: put it here, not in source_url, and name the link chain in `evidence` |
| source_url | Required source URL when official_url is absent |
| source_tier | 1 official / 2 structured collection / 3 other; defaults to 1 |
| record_key | Stable source ID; distinct role key for a shared announcement |
| source_name | Meaningful source label |
| evidence | Short source-backed observation; not personal suitability claims |
| checked_at | ISO-8601 observation time with timezone; for mainland portals write `+08:00` (the host machine may run in another zone) |
| availability | listed / rolling / closed / unknown |

Optional: `salary_note`, `eligibility_note`, `deadline_note`, `quota_note`, `description`,
`official_evidence_url`, `evidence_file`, `discovery_context` (strings); `facts` (object);
`conflicts` (array of strings, one per disagreement); `observations` (array of objects).
`ingest` rejects the whole batch for a wrong type, so give subagent workers these types verbatim.
`deadline_note` keeps the site's own wording and time zone; if the site gives only a date, say
"仅日期" instead of inventing a time.
`quota_note` is the employer's or group's application-count limit for this cohort: total roles per applicant, per institution, per region or per role type, whether
cities share a slot, and the official page that says so. Large mainland SOEs and banks often let
one applicant apply across regions and role types but cap the total; a cap of 1 means only the
best-fit role should get it, so this decides stage-2 priority. Check the cohort's general
announcement and FAQ when you first record an employer. If no limit is published, write
`未核到：` plus the pages checked; never guess a number. The key is the exact `company` string,
so keep one spelling per employer; a group-wide cap goes on each subsidiary's records. A note
from an earlier batch may be quoted with its date. Re-ingest keeps an existing note, and stage-2
`draft` fills 限额处理 from any checked record of the same employer. One record per live
(not eliminated) mainland employer in the run must carry it: `ingest` lists unchecked employers as `quota_unchecked`, and search
`closeout` refuses until each has one.
**Postdoc preparation window.** A postdoctoral role or fellowship takes time to prepare: the
user's minimum is `postdoc_prep_days` (instance `system/config/preferences.json`; `radar.py doctor` and `preflight` report it, 0 = no window). Still collect one with fewer days between observation and its official
deadline (programmes recur), but mark it expired: set `facts.closed=true` with `closed_evidence`
quoting the deadline, the observation date and "博士后准备期不足N天"; `screen` then keeps it out of
the candidates, and a recurring one goes on the watchlist below. N days or more counts as open.
Ordinary jobs keep their real deadline.

**Enterprise postdoc stations are ordinary jobs.** A postdoc post at
an enterprise station (企业博士后科研工作站: SOE groups, banks, enterprise-owned research
institutes, private companies) is recorded and screened like any other job: real deadline,
`quota_note` per employer, no preparation window, no mentor-centred search. It goes to
`candidates` in stage 2. Universities, CAS, public institutes and new-type R&D labs (e.g. 之江实验室)
stay postdocs, as do university-hosted corporate joint labs.

**Postdoc search is mentor-centred.** Nearly every postdoc route,
funded fellowships included, is filed through a host institution with a named host supervisor
(合作导师, Project Mentor, host professor), so the mentor is the unit of search, not the posting.
- Order: institution (user priority) → departments in the user's directions → faculty list →
  per mentor: recruitment evidence, then the funding routes they could host. A posting found
  first is tied to its mentor with evidence, or recorded with `导师未定位` in its gaps.
- Evidence tiers: 1 formal posting + homepage statement, 2 homepage
  statement only, 3 formal posting only, 4 potential mentor (strong fit, no recruitment
  evidence). Give the statement's date or `undated`; a statement from two or more years ago
  counts as tier 4. A tier says what was found, so name the source not yet checked in the gaps.
- Records: one per mentor at one institution, `kind: role`, `record_key`
  `<institution-slug>-pi-<name-slug>-postdoc-<year>`. Tiers 1–3 get a JOB ID; tier 4 is a LEAD
  (stage 2). A mentor who already holds a JOB ID (an earlier posting record) keeps it; never
  re-ingest them under a new key.
- Merged table: the separate `postdocs` table is retired. Every
  postdoc record lives in `postdoc_mentors`, field 类型: 导师 (one row per mentor), 单位岗位
  (a unit or company posts a research post and names no PI; the 导师｜课题组 cell holds the unit
  or company), 基金项目 (fellowship or programme call), 机构入口, 方向线索. All types are graded
  S–D by fit only and keep the preparation-window rule.
- Fellowships, national or school programmes (博新, MSCA) and unit-level calls
  become 基金项目 rows. For each, record from the official page whether a host must be secured
  before applying and what the host provides (support letter, joint proposal, nomination);
  unknown stays unknown.
- Contacting a mentor is the user's act (invariant 1): an agent may draft a message only after
  the user names the mentor's ID, and never sends it.

`recurring_note` quotes official evidence that a programme or cohort
recurs (annual call, numbered batches, "每年两批"), with the page. A PhD job search spans
years: a closed fellowship can be applied for next cycle, even from another postdoc post, so
a closed but recurring record is never just dropped. Stage-1 `screen` still eliminates this
closed cohort's batch row (its window is over); the programme lives on in the local watchlist `system/state/watchlist.json`,
which every agent sees at start (`doctor`, `init`, stage-2 `preflight` list `watch_due`).
Feishu is the user's table, not where agents look for reminders, so the reminder is local.
Add or update an entry with `radar.py --root "$ROOT" watch --add entry.json` (list with
`watch`); the same `id` updates it, job IDs accumulate, nothing is deleted:

| key | value |
|---|---|
| id | lowercase slug, stable across cycles (e.g. `tsinghua-shuimu-scholar`) |
| name, official_url | programme name; official page that shows the cycle |
| cycle | the recurrence as officially stated, with year and source |
| next_window | `官方：` an announced date, `估计：` an estimate plus its basis, or `未知：` |
| recheck_on | `YYYY-MM-DD` to look for the next announcement (before the estimated window) |
| job_ids | permanent IDs of past and current cohorts |
| eligibility_window | limits that time can break (age, years since PhD, once per year), quoted |
| open_questions | unverified points that decide whether the plan works, e.g. whether people already in another postdoc post may apply |
| status | `watching`; `closed` only on the user's word |

Never invent a date: an estimate says it is one and why. A new cycle's announcement is a new
cohort with its own JOB ID, `父记录` pointing to the earlier one in stage 2, added to `job_ids`.
Keep raw facts in their original currency, period, scope and timezone. Do not infer
net savings from gross pay. Do not infer a closed job merely from an inaccessible URL.

A role page label such as "岗位性质：合同工" is not elimination evidence: its meaning depends on
the employer and announcement, and the user judges it in stage 2.
Quote it in `eligibility_note`; never set `forbidden_identity` from that label alone. Set it
only when official text for that role or announcement confirms an ordinary contract with no
formal status (e.g. 劳务派遣, 外包, 不纳入编制/非正式职工, fixed-term project hire), quoting that
text in `forbidden_identity_evidence`. `ingest` refuses `forbidden_identity=true` whose evidence names a
contract label without such wording; stage-2 `validate` applies the same check to 淘汰原因.

`facts` passes to `system/skill/jobradar/scripts/initial_filter.py` with the user's `hard_rules`
(instance `system/config/preferences.json`). Elimination facts for every user: `closed`,
`all_locations_excluded`, `forbidden_identity`, `ineligible`, plus `annual_salary_cny`,
`benefits_deficient` and `net_savings_below_floor` when the matching rule section exists. Any
other flag (a location such as `beijing_only`, `sustained_intensity`, `frequent_travel`) counts
only if `hard_rules` names it as a `fact`; read the rules before setting flags.
True values require the corresponding `<field>_evidence`. Salary and benefit rules
must follow that module's schema and source conventions. If a condition cannot be
checked without protected profile access, leave it unknown. Never set a Boolean
from keyword similarity or use a preferred qualification as mandatory ineligibility.

Identity is canonical official URL (or source URL) + record_key, shared across Skill runs. This
deduplicates repeated source records, not semantic duplicates across different URLs. `ingest`
therefore lists `possible_duplicates` in its receipt: new IDs whose employer and role loosely
match a record in any batch's `records.json`. It is a warning, not a merge; check each pair and,
when it is the same role, update the older ID in stage 2 instead of creating a second record.
IDs are reserved in `system/state/skill-ids.json`, always above the highest ID found in any
batch's `records.json`, so a lost registry never reissues an ID.

Workbook source columns are generated. 用户决定 and 我的备注 are editable and survive
refresh by ID. No second-round ranking or automatic Feishu synchronization occurs.


### Conditional exceptions

Each conditional rule in `hard_rules` names its exception flag: `exceptional_opportunity` for
overseas savings shortfalls, the rule's `exception` for `conditional_rejections` and for each
`location_floors` entry (e.g. `beijing_salary_exception` in the example instance). Set it to
true / false / omitted with its `_evidence` field. An explicit
false with supporting evidence enables the default rejection; missing assessment is
retained for verification, never interpreted as false. The underlying violation also
requires its own evidence. No exception overrides the mainland `absolute_floor_cny`,
excluded locations or forbidden employment identity. Do not perform career scoring to
fill these flags during bulk search; use explicit available evidence or leave unknown.
