"""Guarded browser adapter over browser-use/jev-ultrafast.

Replaces the Node @tontoko/jev-browser adapter (scripts/jev-browser.mjs) as the
browser-execution layer. The TypeSafe Jev decision layer is unchanged.

Same task contract as the Node adapter: url, allowed_origins, controls, steps,
expected_text, allow_scroll. Guardrails mirror jev-policy.mjs and are enforced
here, because jev-ultrafast itself ships no origin allowlist, no request-method
restriction and no submit/deny filter:

- origin allowlist (https, exact, <=12); abort if the page leaves it
- approved-control allowlist: the model may only choose a control that the human
  task already declared (kind + name + role)
- denied-action regex on every candidate and every chosen label
- no submit/login/upload/pay/delete path: those controls are never offered
- readback of expected_text; never retries a browser mutation
- human handoff on popup/dialog/out-of-scope/origin change

Host connection: attaches to the project's dedicated Chrome via BU_CDP_URL
(default http://127.0.0.1:19222). Telemetry and home dirs are pinned inside the
project runtime. Experimental: requires explicit authorization and credentials.

Usage:
  <runtime>/jev-ultrafast/.venv/bin/python jev_ultrafast_browser.py \
      --task task.json --out receipt.json [--dry-run]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

DENIED = re.compile(
    r"(提交|投递|发送|上传|支付|购买|删除|注册|登录|立即申请|申请职位|apply\s*now|submit|send|upload|pay\b|purchase|delete|sign\s*in)",
    re.I,
)
KINDS = {"click", "fill", "select", "check", "uncheck", "hover"}
MAX_CONTROLS = 40
MAX_ORIGINS = 12

RUNTIME = Path(__file__).resolve().parents[3] / "runtime" / "jev-ultrafast"
CREDENTIAL_FILE = Path.home() / ".config" / "jobradar" / "jev.env"


def fail(code, msg=None):
    raise SystemExit(json.dumps({"status": "stopped", "reason": code, "detail": msg}))


def validate_task(t, max_steps):
    if not isinstance(t, dict):
        fail("INVALID_BROWSER_TASK")
    origins = t.get("allowed_origins")
    if not isinstance(t.get("url"), str) or not isinstance(origins, list) or not origins or len(origins) > MAX_ORIGINS:
        fail("INVALID_BROWSER_TASK")
    for o in origins:
        if not isinstance(o, str) or not o.startswith("https://") or o != o.rstrip("/") or "@" in o:
            fail("INVALID_ORIGIN")
    url = t["url"]
    if not any(url.startswith(o + "/") or url == o for o in origins):
        fail("OUTSIDE_ALLOWED_ORIGINS")
    controls = t.get("controls")
    steps = t.get("steps")
    if not isinstance(controls, list) or len(controls) > MAX_CONTROLS:
        fail("INVALID_BROWSER_TASK")
    if not isinstance(steps, list) or not steps or len(steps) > max_steps:
        fail("INVALID_BROWSER_TASK")
    for c in controls:
        if not isinstance(c, dict) or c.get("kind") not in KINDS:
            fail("UNSAFE_CONTROL")
        if not isinstance(c.get("name"), str) or not c.get("name") or not isinstance(c.get("role"), str) or not c.get("role"):
            fail("UNSAFE_CONTROL")
        if DENIED.search(c["name"]):
            fail("UNSAFE_CONTROL")
    for s in steps:
        if not isinstance(s.get("instruction"), str) or not s["instruction"].strip():
            fail("INVALID_BROWSER_STEP")
    if t.get("expected_text") is not None and not isinstance(t["expected_text"], list):
        fail("INVALID_EXPECTATION")
    return t


def origin_of(url):
    m = re.match(r"^(https://[^/]+)", url or "")
    return m.group(1) if m else None


def approved(controls):
    return [(c["kind"], c["name"], c["role"]) for c in controls]


def filter_actions(actions, allow):
    out = []
    for a in actions:
        if a.get("kind") not in KINDS:
            continue
        label = (a.get("label") or "").split(" → ")[0]
        if DENIED.search(label):
            continue
        if any(a["kind"] == k and label == n and a.get("role") == r for (k, n, r) in allow):
            out.append(a)
    return out


def load_credentials():
    if not CREDENTIAL_FILE.is_file():
        fail("CREDENTIAL_FILE_MISSING")
    for line in CREDENTIAL_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def configure_host():
    os.environ.setdefault("BU_CDP_URL", "http://127.0.0.1:19222")
    # Short, host-neutral path: the daemon's AF_UNIX socket exceeds the ~104-byte
    # limit under the project's "Notes (D)" path. Kept out of any harness's private
    # config (~/.codex etc.); JobRadar state lives under ~/.local/state/jobradar.
    os.environ.setdefault("BH_HOME", str(Path.home() / ".local" / "state" / "jobradar" / "harness"))
    os.environ.setdefault("BH_TELEMETRY", "0")


def run(task, allow, max_steps, timeout_ms):
    from jev_ultrafast import model
    from jev_ultrafast.browser import Browser, StalePage

    receipt = {"status": "running", "steps": [], "final_submission_enabled": False, "engine": "jev-ultrafast"}
    browser = Browser(task["url"])
    history = []
    try:
        if origin_of(browser_evaluate(browser, "location.href")) not in task["allowed_origins"]:
            receipt["status"] = "needs_handoff"
            receipt["reason"] = "OUTSIDE_ALLOWED_ORIGINS"
            return receipt
        page = browser.observe(screenshot=False)
        for index, step in enumerate(task["steps"]):
            if origin_of(page.get("url")) not in task["allowed_origins"]:
                receipt["status"] = "needs_handoff"
                receipt["reason"] = "OUTSIDE_ALLOWED_ORIGINS"
                break
            guarded = dict(page, actions=filter_actions(page.get("actions", []), allow))
            try:
                decision = model.choose(guarded, step["instruction"], history)
            except StalePage:
                receipt["status"] = "needs_handoff"
                receipt["reason"] = "STALE_PAGE"
                break
            selected = decision["choice"]
            entry = {
                "step": index + 1,
                "choice": selected,
                "operation": decision.get("operation"),
                "confidence": decision.get("confidence"),
                "executed": False,
            }
            receipt["steps"].append(entry)
            if selected in {"DONE", "BLOCKED"}:
                entry["executed"] = False
                receipt["status"] = "readback_pending" if selected == "DONE" else "needs_handoff"
                break
            action = next((a for a in guarded["actions"] if a["id"] == selected), None)
            if action is None or DENIED.search((action.get("label") or "")):
                entry["outcome"] = "not_allowed"
                receipt["status"] = "needs_handoff"
                break
            entry["target"] = action.get("label")
            text = None
            if action["kind"] == "fill":
                context = model.field_context(step["instruction"], action, page, history)
                text, helper = model.field_text(context)
                entry["text_helper"] = helper["model"]
            try:
                browser.act(action, page, text=text)
                entry["executed"] = True
            except Exception as exc:  # no retry on mutation
                entry["outcome"] = "not_verified_no_retry"
                receipt["status"] = "stopped"
                receipt["reason"] = type(exc).__name__
                break
            history.append({"action": entry.get("target"), "kind": action["kind"], "text": text, "page_changed": None})
            page = browser.observe(screenshot=False)
        else:
            receipt["status"] = "readback_pending"
        body = browser_evaluate(browser, "document.body ? document.body.innerText : ''") or ""
        expected = task.get("expected_text") or []
        receipt["readback"] = {
            "url": browser_evaluate(browser, "location.href"),
            "expected_text": [{"text": s, "present": s in body} for s in expected],
        }
        if receipt["status"] == "readback_pending":
            receipt["status"] = (
                "readback_matched"
                if expected and all(x["present"] for x in receipt["readback"]["expected_text"])
                else "needs_independent_verification"
            )
        return receipt
    finally:
        browser.close()


def browser_evaluate(browser, expression):
    return browser.evaluate(expression)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", required=True)
    ap.add_argument("--out")
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--timeout-ms", type=int, default=90000)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    task = json.loads(Path(args.task).read_text())
    task = validate_task(task, args.max_steps)
    allow = approved(task["controls"])
    preview = {
        "status": "validated_not_executed",
        "engine": "jev-ultrafast",
        "allowed_origins": task["allowed_origins"],
        "controls": [{"kind": c["kind"], "name": c["name"], "role": c["role"]} for c in task["controls"]],
        "steps": len(task["steps"]),
        "denied_controls_dropped": [c for c in task["controls"] if DENIED.search(c["name"])],
        "network_requests": 0,
    }
    if args.dry_run:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return

    load_credentials()
    configure_host()
    receipt = run(task, allow, args.max_steps, args.timeout_ms)
    if args.out:
        Path(args.out).write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": receipt["status"], "reason": receipt.get("reason"), "receipt": args.out}))
    if receipt["status"] in {"stopped", "needs_handoff"}:
        sys.exit(1)


if __name__ == "__main__":
    main()
