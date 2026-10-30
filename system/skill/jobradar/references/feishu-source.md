# Second-priority curated source

Instance config (optional): `system/config/feishu-aggregate.json`; the template holds
placeholders and `collect-feishu` refuses until they are filled. This external collection
is distinct from the user's future second-round Feishu table. Only read it using
existing user login. Never put credentials or unnecessary full-table data in prompts.

The adapter calls `base +record-search` with view, stable field IDs, keyword and
limit; without a keyword it calls `+record-list`. It saves NDJSON and a manifest.
It never edits views or writes records. Cache is keyed by the full query, expires
after 15 minutes, and is bypassed with `--fresh`. Retrieval time is not a source
publication time or evidence that a job remains open.

Inspect manifest `columns` for current field names. Keep `record_id` as the stable
collection identity. Multi-select locations remain arrays until normalization.
Missing deadlines and “尽快投递” remain unknown. Recruitment categories grouped
in one row are normally a program, not multiple confirmed vacancies. Listed links
are candidate official entrances until independently verified.

`has_more=true` means truncated. Continue with `next_offset` only within the agreed
budget. Preserve query scope and `rev` across pages; a changed revision prevents
strict snapshot completeness. The built-in adapter reads one bounded page; use the
host's lark-base workflow for larger exports. Avoid casual `--filter-json`: it
overrides the view filter. Keyword search preserves the specified view scope.

Cross-check employer, role/program, locations, cohort and dates before merging.
An employer homepage proves an entrance, not role eligibility or availability.
Keep source-specific observations with tier, URL, row ID and timestamp. Record
`conflicts` when unresolved; no silent overwrite or automatic rejection.
Collection-only leads may use `source_tier: 2`, `source_url`, `record_key`, and an
empty `official_url`. They must not be displayed as officially verified.

For auth/ACL failures use existing user login recovery; do not change identity,
downgrade credential storage or alter sharing permissions.
