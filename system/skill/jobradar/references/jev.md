# Optional Jev test adapter

Experimental and off by default. Filling the key alone is not authorization; the user's
concrete test task is. The normal JobRadar pipeline remains unchanged. Jev answers are
experimental evidence, never automatic elimination or final selection.

## Official TypeSafe skill

Use the official `typesafe-ai` skill for TypeSafe work if the host has it installed;
do not install it into or modify another host's configuration.
Source: https://github.com/typesafe-ai/skills/tree/main/skills/typesafe-ai
Read current API, chosen primitive and nearest cookbook before integration changes.
The direct HTTP endpoint and pinned `jev-1.13.0` were rechecked against official
API/models docs. No key migration or transport replacement is needed.

For evidence checks, follow the official citation-check pattern: deterministic
source/quote matching first, then independent Choice questions with supported,
contradicted and insufficient-evidence outcomes. Batch independent questions over
the same bounded state. Preserve raw answers, usage, latency and Agent review.
Do not treat the cookbook's example confidence threshold as measured local accuracy.

The user fills `~/.config/jobradar/jev.env`, mode 0600, outside the instance.
The only required entry for the default route is `TYPESAFE_API_KEY`.
Never cat/read/print the credential file into Agent context; the client reads it
internally. Never source the file as shell code or pass keys in argv. Use doctor to
report only key status. Do not change host model/provider configuration.

Configuration: `system/config/jev.json`. TypeSafe direct is the default,
using pinned `jev-1.13.0`. An optional OpenRouter transport exists but has not been
live-tested; selecting it requires its own key and model ID in this JobRadar config.
Never automatically fall back to another provider or install hooks.

## Entry points

Resolve SKILL to this package. Node 22.15+ required.

```sh
node "$SKILL/scripts/jev.mjs" doctor
node "$SKILL/scripts/jev.mjs" decide --input request.json
# Only after the user gives the test task:
node "$SKILL/scripts/jev.mjs" decide --input request.json --run YYYY-MM-DD_任务名称 --execute
node "$SKILL/scripts/jev.mjs" browse --input browser-task.json --run YYYY-MM-DD_网页任务 --execute
```

Without `--execute`, decide/browse validate local input only and do not read keys or
make network calls. Each executed run requires a new dated name and writes input
and receipts only to `system/validation/<run>/`. Existing runs are not
overwritten. No test rows are inserted into formal outputs.

Decision input is `{state, questions}` using the official Choice/Noul/Score shape.
Question IDs do not convey meaning to Jev: put exact conditions in instructions.
For first-stage evidence use explicit unknown/ambiguous options. Preserve selected
original evidence; do arithmetic and date ordering in code. The adapter validates
option IDs, probability distributions and response types; it does not prove the
model's semantic accuracy. Transport errors are reported without provider bodies,
keys or automatic retries. Limits are 10 model calls, 24 questions/request and
100 KB/request in the default test configuration.

## Browser test contract

The browser engine is selectable; the TypeSafe Jev decision layer is unchanged.

**Default engine `ultrafast` (2026-09-21):** browser-use/jev-ultrafast, vendored at
`system/runtime/jev-ultrafast/` (uv venv, ruff + 31 offline tests pass).
`scripts/jev_ultrafast_browser.py` runs the loop and re-applies the policy that
jev-ultrafast itself does not ship: https origin allowlist, approved-control
allowlist (kind+name+role), denied-action regex, readback, popup/origin handoff,
no mutation retry. It attaches to the dedicated Chrome via `BU_CDP_URL`
(default `http://127.0.0.1:19222`), pins `BH_HOME` to a host-neutral path
(`~/.local/state/jobradar/harness`; a deep instance path can be too long for
the daemon socket) and sets `BH_TELEMETRY=0`. It needs `TYPESAFE_API_KEY`; `TYPE_TEXT` additionally needs
`TEXT_MODEL_API_KEY` (not required for read-only navigation).

**Rollback engine `node` (retired 2026-09-21):** the earlier pinned
@tontoko/jev-browser 0.5.1 runtime remains declared at
`system/runtime/jev/` (package.json + lockfile + tarball); `scripts/jev-browser.mjs` and
`--engine node` need `npm ci` in that directory before use.

Neither engine copies browser profiles or registers a global MCP. Both require
explicit `--execute` and a new dated run name; both write only to
`system/validation/<run>/`. When needed after test authorization, use
`system/tools/browser.py start`; login remains a human operation.
Live execution of the browser engines is not validated.

A browser task has:

- `url`, `allowed_origins`: HTTPS entrypoint and explicit exact origins.
- `controls`: observed exact `{kind, name, role}` controls allowed for this task.
  Supported kinds are click, fill, select, check, uncheck and hover. Application,
  upload, send, payment, deletion and login controls are rejected.
- `steps`: at most 8 `{instruction, values?}` micro-steps; values are caller-supplied
  exact strings. There is no automatic text generator or unrestricted autopilot.
- `allow_scroll`: optional boolean.
- `expected_text`: optional list of strings for literal final-page readback.

Prepare controls by reading the public recruitment page through available host
browser tools. Do not invent control names or use the model to authorize more
controls. Browser action confidence is provisionally gated at 0.8; this is an
execution policy, not a measured job-classification accuracy.

Enter, uploads, application submissions and dialog acceptance are not exposed.
Non-GET/HEAD/OPTIONS browser requests and out-of-scope frame navigation are blocked.
Sites requiring POST searches or popup details may need a scoped adapter change;
stop and diagnose rather than relaxing restrictions automatically. A popup causes
handoff. Failed or uncertain actions are never replayed automatically.

Use only public job text and necessary page evidence in the initial experiment.
No résumé, account profile or private application state may be bundled into a
model request. Literal readback proves only the named visible facts; inspect the
final snapshot independently before claiming the requested outcome.

## Verification status

A small curated pilot (September 2026: five real `jev-1.13.0` requests, 58 semantic questions,
one bounded recruitment-site click) kept every unknown employment/working-time fact unknown, but
the model also linked a role to a parallel programme that Agent review had to override, and two
of eight evidence-boundary answers changed only after clearer wording. It is not an accuracy
benchmark or evidence of end-to-end speedup. OpenRouter and unrestricted browser autonomy remain
untested. Keep host-based discovery, bounded Jev classification, deterministic checks and Agent
review separate.
