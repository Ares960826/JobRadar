# Collection adapters and acceptance

## Primary path

After/alongside official discovery, query structured collections before general
platforms. The Feishu adapter and scope rules are in [feishu-source.md](feishu-source.md).

Use host search to discover university, institute, public-body and employer
recruitment pages; open the actual source, extract dated evidence, and ingest JSON.
Search results alone are discovery evidence. Record a failed or blocked page in
`_support/sources/` instead of inventing a vacancy or declaring that the organization is closed.

Public ATS config example:

```json
{
  "name": "openai-ashby", "adapter": "ashby", "board": "openai",
  "company": "OpenAI", "region": "international", "limit": 8,
  "title_terms": ["Research Engineer", "Research Scientist"],
  "official_evidence_url": "https://openai.com/careers/"
}
```

The official evidence URL must actually link to the configured board. The core
cannot establish that relationship automatically. `greenhouse` uses the same
configuration with its public board slug. Each invocation fetches one board response
(25-second timeout, 20 MB cap), then selects at most `limit` matching records. Title
terms are retrieval scope, not suitability scores. Save raw response, hash and counts;
report truncation. Cross-board and all-institution coverage are not implied.

## Supplemental tools

None of these ships with JobRadar or has an adapter in the core; use one only if the user has it
installed, through `ingest` like any agent-extracted record.

- Recruitment-platform collectors (e.g. BossHunter, jobfindsme for BOSS直聘 / 51job): run them only
  in the dedicated browser profile (`system/tools/browser.py`), read/search/detail operations only.
  Results reflect the logged-in account's personalization; say so in the receipt.
- JobSpy: optional public-market discovery; existing tests do not guarantee all
  countries/sites. Validate actual returned locations and full vacancy evidence.
- Moka: distinguish employer-authorized integration APIs from public recruitment
  pages. Do not assume enterprise API credentials are public job-search access.
- offer-skill: pending accessible installation package and configuration. Do not
  treat its earlier blocked download as an operational integration.
- Brave/JSearch/USAJOBS/Jooble/Adzuna are optional, not required by this Skill.

No adapter may upload user credentials/profile into receipts. Report sample size,
logged-in versus public mode, source bias, blocked sources and missing sectors.
Repeated collection requires an explicit scope/budget; no background schedule exists.
