#!/usr/bin/env python3
"""Vertex AI Foundation Model Predictions keepalive — ping 4 dormant slots
quarterly to stay inside Google's 90-day per-model activity window.

Usage (Cloud Run Job): set env SLOTS_FILE=/app/slots.json, run.
Local dry-run: `SLOTS_FILE=slots.json python keepalive.py`.

Exit codes:
    0  All slots returned 2xx (carbon-neutral keepalive)
    1  One or more slots failed (alerting trigger)

See card https://trello.com/c/CFfrq6hA — parent triage https://trello.com/c/FpDfWEMH
"""

from __future__ import annotations

import json
import os
import sys

import requests
from google.auth import default
from google.auth.transport.requests import Request


def main() -> int:
    slots_path = os.environ.get("SLOTS_FILE", "slots.json")
    with open(slots_path, encoding="utf-8") as fh:
        slots = json.load(fh)

    creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    failures: list[tuple[dict, str]] = []

    for slot in slots:
        tag = f"{slot['project']} / {slot['model']} @ {slot['location']}"
        host = (
            "aiplatform.googleapis.com"
            if slot["location"] == "global"
            else f"{slot['location']}-aiplatform.googleapis.com"
        )
        url = (
            f"https://{host}/v1/projects/{slot['project']}/locations/"
            f"{slot['location']}/publishers/google/models/{slot['model']}:generateContent"
        )
        try:
            if not creds.valid:
                creds.refresh(Request())
            res = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {creds.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
                    "generationConfig": {"maxOutputTokens": 1},
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            print(f"{tag} -> EXC {type(exc).__name__}: {exc}")
            failures.append((slot, f"exception: {type(exc).__name__}: {exc}"))
            continue
        print(f"{tag} -> {res.status_code}")
        if res.status_code != 200:
            failures.append((slot, f"HTTP {res.status_code}: {res.text[:500]}"))

    if failures:
        print(f"\nFAILURES ({len(failures)} of {len(slots)}):", file=sys.stderr)
        for slot, detail in failures:
            print(f"  {slot} -> {detail}", file=sys.stderr)
        return 1
    print(f"\nAll {len(slots)} slots OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
