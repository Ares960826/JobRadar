# Host portability (shared by jobradar and potential-position)

One source package: `system/skill/` in the JobRadar code directory. A user's instance exposes it
to hosts through project links inside the instance: `.agents/skills/` (Codex, OpenCode) and
`.claude/skills/` (Claude Code), each pointing at `system/skill/<name>`. Never add links or
copies inside another host's home directory; the instance links are the supported install.

The skills never choose a model or provider. Different models follow prose differently, so
discipline lives in the scripts (`radar.py`, `position.py`): if a command refuses, fix the input
or ask the user — never work around it with raw writes.

## Capability map (re-check with the host's `--help` when versions change)

| Host | Search / page read | Browser | Ask the user | Subagents | Headless run |
|---|---|---|---|---|---|
| Codex | built-in web search tool (`web.run`) | CUA, or the dedicated CDP browser via `tools/browser.py` | `request_user_input`, or stop and ask in text | host-dependent; not required | `codex exec -C <dir>` |
| OpenCode | `websearch` / `webfetch` (verify with one query before relying on it) | `tools/browser.py` via bash | `question` tool | `task` tool | `opencode run --dir <dir>` |
| Claude Code | `WebSearch` / `WebFetch` (no JS rendering) | Chrome MCP if connected (the user's own Chrome, one instance shared by all workers: follow the browser hygiene rules in jobradar SKILL.md), else `tools/browser.py` | `AskUserQuestion` | `Agent` tool | `claude -p` |
| DeepSeek TUI | not verified | `tools/browser.py` via bash | not verified | not verified | `deepseek exec` (not verified) |

OpenCode and Claude Code load the instance's skill links without extra steps. For Codex, use
the engine your account's backend accepts for the configured model; an older standalone CLI
may be rejected.

## Waiting for the shared lock

Stage 1 and stage 2 share one lock (`system/state/active-run.json`), so two windows
working at once take turns. `radar.py init` and `position.py preflight` queue for it by
themselves: `--wait` defaults to 90 s and prints who holds the lock. If that is not enough,
wait longer yourself instead of asking the user to release anything:

- Claude Code: rerun with `--wait 3000` as a background command (`run_in_background`); you are
  notified when it gets the lock or gives up.
- Codex, OpenCode, DeepSeek TUI: rerun with a longer `--wait` and a command timeout at least
  60 s above it (the preflight login and file checks add time), repeating while the holder is
  still working. Check your host's maximum command timeout first; it is not recorded here.

Only `init` and `preflight` queue; other commands of a run that does not hold the lock fail at
once. A stage-1 operation lock (`stage1-operation.lock`) whose recorded process no longer runs is
reported as left by a killed process instead of being waited on.

Ask the user only when the holder looks abandoned (its window is closed, or it has held the lock
for hours without progress). Only the user may approve `release --user-approved`.

Rules that apply on every host:
- Before searching, name the search/page-read tools you actually see in this session. If a
  tool errors or is absent, say so and skip that route; do not substitute guesses.
- Never claim a capability you did not observe (e.g. "no MCP available") without checking.
- Search results are discovery only; open the page and record dated evidence.
- Subagents are optional. If you use them, give each the same invariants and mode; the
  parent still runs the gates and closeout.

## Shared runtimes

- Python 3.10+ standard library: all gates, IDs, rules, Feishu reads, workbook export.
- Feishu: the user's existing `lark-cli` login (`--as user`). `lark-cli` configuration is
  machine-wide, so every host uses the same profile. No identity change or credential export;
  do not switch or remove profiles unless the user asks. The instance's
  `system/config/feishu-workbench.json` names the base and tables; never write to any other base.
- Personal-file protection (e.g. a cv-privacy hook) is the user's own host setup, not part of
  JobRadar. If the user has one, never point it at an interpreter that lacks its packages:
  a guard that cannot start may deny every tool call.
- Workbook export (optional): `scripts/workbook.py`. `radar.py doctor` and the export receipt
  record `host` and the engine version. Host id comes from `JOBRADAR_HOST`, else host
  environment markers.
- Dedicated browser: profile in `~/.local/state/jobradar/browser` (host-neutral).
  `tools/browser.py status` reports whether it exists. A new profile needs a human login.
  Port conflicts fail without killing another browser.
- Jev credentials: `~/.config/jobradar/jev.env` (read only, never echo).
- Browser-engine state: `~/.local/state/jobradar/harness`.

## Verifying a new host

1. In the instance directory, start a fresh session and confirm both skills appear in the host's
   skill list (or its `/skills` equivalent).
2. Run `radar.py doctor` and `position.py modes`.
3. Run the offline behaviour matrix: `system/tests/hosts/run_host_evals.py --host <name>`.
Report discovery, command execution and live search separately; passing the first two does
not prove the third. Local gates are shared implementation tests, not proof that each host can
execute every tool. Re-run failed host cases from a fresh user-started session; do not change
provider/model or disable the sandbox to make them pass.

`radar.py doctor` checks the reviewed hard-condition hash, the workbook engine and the Feishu
login; it does not test search, API credentials, network access or sandbox permissions.
