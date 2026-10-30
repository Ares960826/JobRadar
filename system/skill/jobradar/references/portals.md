# Portal notes (mainland SOE and bank campus sites)

Dated observations from real runs. A note tells you where to look and what worked. It is not
evidence for a record. Re-check the page itself, and update or date a note when a portal
changes. Times are Asia/Shanghai (CST).

| Employer / portal | Observed | Note |
|---|---|---|
| 中国移动 job.10086.cn | 2026-10-06 | JS app; renders in a browser. The AI zone listed 295 roles. Each unit has its own deadline, so record each one rather than a group date. Reposts gave a wrong city and deadline for 九天; the official page wins. |
| 中国联通 campus site → xiaoyuan.zhaopin.com | 2026-10-06 | About 2,800 roles. Its filters did not respond to automated clicks, so only the head of the list was read. Detail pages reached from the official site are employer-linked ATS: put them in `official_url`. No deadline was shown. |
| 中国电信 | 2026-10-06 | Showed "岗位陆续更新上线中" with no role list. 天翼AI and TeleAI roles were known only from reposts (tier 3). |
| 国家能源集团 zhaopin.chnenergy.com.cn | 2026-10-06 | The unit filter ignores clicks. Instead, set the page's own search form fields (`workUnit` = unit name, `pagenum`) and submit that form (it was `document.forms[3]`). Read the results in a separate call. 社招 and 统招 notices share the portal: check the notice type and cohort, because an `annc/showfagg?...&kinds=3` link was a 2026 社招 notice. Role pages show "岗位性质". "合同工" there is quoted for the user's own stage-2 judgment, not eliminated (record-schema.md). |
| 中国石油 | 2026-10-06 | Daily maintenance 00:00–06:00 CST; retry after 06:00. |
| 中国烟草 | 2026-10-06 | Not reachable from this machine; only reposts. |
| 中国石化, 华电电科院, 交通银行 | 2026-10-06/07 | Job details open in a new tab. Read the `href` and navigate the same tab. |
| 中国工商银行 job.icbc.com.cn | 2026-10-07 | Hash routes: `#/main/school/postDetail/<id>` and `announDetail/<id>` are stable direct links. Some roles have no separate page. |
| 中国农业银行 career.abchina.com.cn | 2026-10-07 | Hash routes: `#/PositionDetails/:<id>` and `#/NoticeDetails/<id>` are stable. |
| 中国建设银行 job2.ccb.com | 2026-10-07 | `job_detail.html?planId=…&planPost=…` gives per-role links. Announcements are `announcement.html?annoId=…`, with department tables in attachments. |
| 中国银行 campus.chinahr.com/pages/2027-boc | 2026-10-07 | No per-role URLs. Filter by institution and write the click path in 定位说明. |
| 交通银行 job.bankcomm.com | 2026-10-07 | `#/school/recruitmentInfo/?positionId=<id>` per role. No 2027 general announcement was found on the official site. |
| 中国广电, 南方电网 | 2026-10-06 | No 2027 announcement found. Re-check before reporting "none". |
