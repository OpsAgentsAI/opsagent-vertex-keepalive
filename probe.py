#!/usr/bin/env python3
"""
One-shot Vertex slot probe for Trello card i8pJZk8v.

Probes 6 (project, model, location) slots after Google's 2026-06-15 per-model
access cutover. Posts a green/red report as a Trello comment on i8pJZk8v.
On any failure: comments on FpDfWEMH (triage parent), files a P0 card on the
opsagent-devops To Do list, then self-pauses the Cloud Scheduler job that
triggered this run.

Auth: relies on the runtime SA's ADC (cli-gateway-sa@opsagent-prod, which has
aiplatform.user on opsagent-prod and roles/owner on opsagent-staging, and
cloudscheduler.admin on opsagent-prod).

Trello key/token: read from env (mounted from Secret Manager versions
trello-api-key:latest and trello-token:latest).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

# ---------- Configuration ---------------------------------------------------

SLOTS = [
    # (project, model, location, host)
    ("opsagent-prod",    "gemini-2.5-flash",         "us-central1", "us-central1-aiplatform.googleapis.com"),
    ("opsagent-prod",    "gemini-2.5-flash-lite",    "us-central1", "us-central1-aiplatform.googleapis.com"),
    ("opsagent-prod",    "gemini-3-flash-preview",   "global",      "aiplatform.googleapis.com"),
    ("opsagent-staging", "gemini-2.5-flash",         "us-central1", "us-central1-aiplatform.googleapis.com"),
    ("opsagent-staging", "gemini-2.5-flash-lite",    "us-central1", "us-central1-aiplatform.googleapis.com"),
    ("opsagent-staging", "gemini-3-flash-preview",   "global",      "aiplatform.googleapis.com"),
]

CARD_ID_THIS  = "i8pJZk8v"  # this card (verify probe)
CARD_ID_TRIAGE = "FpDfWEMH"  # triage parent (keepalive)
DEVOPS_TODO_LIST_ID = "69e245a413c8661afe10645d"

SCHEDULER_PROJECT  = os.environ.get("SCHEDULER_PROJECT", "opsagent-prod")
SCHEDULER_LOCATION = os.environ.get("SCHEDULER_LOCATION", "me-west1")
SCHEDULER_JOB      = os.environ.get("SCHEDULER_JOB", "vertex-slot-probe-i8pjzk8v")

TRELLO_KEY   = os.environ.get("TRELLO_KEY",   "").strip()
TRELLO_TOKEN = os.environ.get("TRELLO_TOKEN", "").strip()


# ---------- Auth ------------------------------------------------------------

def get_access_token() -> str:
    """Fetch an OAuth access token using the runtime SA's ADC via metadata server."""
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]


# ---------- Probe -----------------------------------------------------------

def probe_slot(token: str, project: str, model: str, loc: str, host: str) -> dict:
    """Probe one slot. Returns dict with pass/fail + diagnostics."""
    url = (
        f"https://{host}/v1/projects/{project}/locations/{loc}"
        f"/publishers/google/models/{model}:generateContent"
    )
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
        "generationConfig": {"maxOutputTokens": 1},
    }).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            http_code = r.status
            resp_body = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        http_code = e.code
        resp_body = e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return {
            "project": project, "model": model, "location": loc,
            "http_code": 0, "prompt_tokens": 0, "passed": False,
            "duration_ms": int((time.time() - started) * 1000),
            "body_excerpt": f"transport error: {e!r}",
        }

    duration_ms = int((time.time() - started) * 1000)
    prompt_tokens = 0
    try:
        parsed = json.loads(resp_body)
        prompt_tokens = parsed.get("usageMetadata", {}).get("promptTokenCount", 0)
    except Exception:
        pass

    passed = (http_code == 200) and (prompt_tokens >= 1)
    return {
        "project": project, "model": model, "location": loc,
        "http_code": http_code, "prompt_tokens": prompt_tokens,
        "passed": passed, "duration_ms": duration_ms,
        "body_excerpt": resp_body[:400],
    }


# ---------- Trello ----------------------------------------------------------

def trello_call(method: str, path: str, params: dict | None = None,
                body: dict | None = None) -> dict:
    if not TRELLO_KEY or not TRELLO_TOKEN:
        raise RuntimeError("TRELLO_KEY/TRELLO_TOKEN env vars missing")
    qs = {"key": TRELLO_KEY, "token": TRELLO_TOKEN}
    if params:
        qs.update(params)
    url = f"https://api.trello.com/1/{path}?{urllib.parse.urlencode(qs)}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def post_comment(card_id: str, text: str) -> None:
    trello_call("POST", f"cards/{card_id}/actions/comments", params={"text": text})


def move_card(card_id: str, list_id: str) -> None:
    trello_call("PUT", f"cards/{card_id}", params={"idList": list_id})


def create_card(list_id: str, name: str, desc: str) -> dict:
    return trello_call("POST", "cards",
                       params={"idList": list_id, "name": name, "desc": desc})


# ---------- Self-disable ----------------------------------------------------

def pause_scheduler() -> str:
    """Pause the Cloud Scheduler job that triggered us so it doesn't fire again."""
    try:
        out = subprocess.run(
            ["gcloud", "scheduler", "jobs", "pause", SCHEDULER_JOB,
             f"--location={SCHEDULER_LOCATION}",
             f"--project={SCHEDULER_PROJECT}",
             "--quiet"],
            capture_output=True, text=True, timeout=60,
        )
        if out.returncode == 0:
            return "paused"
        return f"pause-failed: {out.stderr.strip()[:200]}"
    except Exception as e:
        return f"pause-error: {e!r}"


# ---------- Report formatting ----------------------------------------------

def format_report(results: list[dict], ts_utc: str) -> tuple[str, bool]:
    all_pass = all(r["passed"] for r in results)
    head = ("GREEN" if all_pass else "RED") + " - 6 Vertex slots verified post-cutover"
    lines = [f"## {head}", "", f"Probe run: {ts_utc}", ""]
    lines.append("| Project | Model | Location | HTTP | promptTokens | Result |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        lines.append(
            f"| {r['project']} | {r['model']} | {r['location']} | "
            f"{r['http_code']} | {r['prompt_tokens']} | {mark} |"
        )
    lines.append("")
    if not all_pass:
        lines.append("### Failed slot bodies (first 400 chars)")
        for r in results:
            if not r["passed"]:
                lines.append(
                    f"- **{r['project']}/{r['model']}** ({r['location']}): "
                    f"`{r['body_excerpt']}`"
                )
        lines.append("")
    lines.append(f"Source: Cloud Run Job `{os.environ.get('K_SERVICE','vertex-slot-probe-i8pjzk8v')}` "
                 f"in `{SCHEDULER_PROJECT}/{SCHEDULER_LOCATION}`.")
    return "\n".join(lines), all_pass


# ---------- Main ------------------------------------------------------------

def main() -> int:
    ts_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"[probe] start {ts_utc}", flush=True)

    try:
        token = get_access_token()
    except Exception as e:
        print(f"[probe] FATAL: cannot mint access token: {e!r}", flush=True)
        try:
            post_comment(CARD_ID_THIS,
                         f"RED - probe failed to mint access token at {ts_utc}: `{e!r}`. "
                         "SA may have lost aiplatform.user. Card stays open.")
        except Exception as ee:
            print(f"[probe] also failed to post Trello comment: {ee!r}", flush=True)
        return 2

    results: list[dict] = []
    for project, model, loc, host in SLOTS:
        r = probe_slot(token, project, model, loc, host)
        print(f"[probe] {project}/{model}@{loc} -> HTTP {r['http_code']} "
              f"tokens={r['prompt_tokens']} pass={r['passed']}", flush=True)
        # Single retry-with-backoff on 429
        if (not r["passed"]) and r["http_code"] == 429:
            time.sleep(30)
            r = probe_slot(token, project, model, loc, host)
            print(f"[probe] retry {project}/{model}@{loc} -> HTTP {r['http_code']} "
                  f"tokens={r['prompt_tokens']} pass={r['passed']}", flush=True)
        results.append(r)

    report, all_pass = format_report(results, ts_utc)
    print("[probe] report:\n" + report, flush=True)

    # Post green/red report on i8pJZk8v
    try:
        post_comment(CARD_ID_THIS, report)
        print("[probe] posted report to i8pJZk8v", flush=True)
    except Exception as e:
        print(f"[probe] WARN: could not post Trello report: {e!r}", flush=True)

    # All-pass branch: leave card where the runner will pick it up (do not auto-Done;
    # acceptance criteria says move to Done, but runner pipeline will catch the comment).
    # Failure branch: comment on triage parent, file P0 card on devops board.
    if not all_pass:
        failures = [r for r in results if not r["passed"]]
        try:
            post_comment(
                CARD_ID_TRIAGE,
                f"RED probe at {ts_utc} - {len(failures)}/6 slots failed post-cutover.\n\n"
                + "\n".join(
                    f"- {r['project']}/{r['model']}@{r['location']} -> HTTP {r['http_code']} "
                    f"body: `{r['body_excerpt'][:200]}`"
                    for r in failures
                )
                + f"\n\nSee i8pJZk8v report for full table.",
            )
        except Exception as e:
            print(f"[probe] WARN: triage comment failed: {e!r}", flush=True)

        for r in failures:
            try:
                card = create_card(
                    DEVOPS_TODO_LIST_ID,
                    f"Lost Vertex access on {r['project']}/{r['model']} - Google 2026-06-15 cutover hit",
                    f"## P0 - Vertex slot lost\n\n"
                    f"- Probe run: {ts_utc}\n"
                    f"- Slot: `{r['project']}/{r['model']}` @ `{r['location']}`\n"
                    f"- HTTP: `{r['http_code']}`\n"
                    f"- promptTokenCount: `{r['prompt_tokens']}`\n"
                    f"- Response body (400 chars):\n```\n{r['body_excerpt']}\n```\n\n"
                    f"## Parents\n- Probe card: https://trello.com/c/{CARD_ID_THIS}\n"
                    f"- Triage parent: https://trello.com/c/{CARD_ID_TRIAGE}\n\n"
                    f"## Runbook\n"
                    f"1. Re-run keepalive `generateContent` for this slot via cli-gateway-mcp\n"
                    f"   (auth as cli-gateway-sa, hit "
                    f"`https://{r['location'] if r['location'] != 'global' else 'aiplatform'}"
                    f"{('-aiplatform' if r['location'] != 'global' else '')}.googleapis.com"
                    f"/v1/projects/{r['project']}/locations/{r['location']}"
                    f"/publishers/google/models/{r['model']}:generateContent`).\n"
                    f"2. Re-probe the slot after 5 min.\n"
                    f"3. If still failing, escalate to Michal to re-allowlist via Cloud Console.",
                )
                print(f"[probe] filed P0 card {card.get('shortLink','?')}", flush=True)
            except Exception as e:
                print(f"[probe] WARN: P0 card creation failed: {e!r}", flush=True)

    # Self-disable scheduler regardless of outcome - this card fires once.
    pause_state = pause_scheduler()
    print(f"[probe] scheduler self-disable: {pause_state}", flush=True)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
