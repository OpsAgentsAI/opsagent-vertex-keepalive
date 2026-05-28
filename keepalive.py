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
    failures: list[tuple[dict, int, str]] = []

    for slot in slots:
        creds.refresh(Request())
        host = (
            "aiplatform.googleapis.com"
            if slot["location"] == "global"
            else f"{slot['location']}-aiplatform.googleapis.com"
        )
        url = (
            f"https://{host}/v1/projects/{slot['project']}/locations/"
            f"{slot['location']}/publishers/google/models/{slot['model']}:generateContent"
        )
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
        tag = f"{slot['project']} / {slot['model']} @ {slot['location']}"
        print(f"{tag} -> {res.status_code}")
        if res.status_code != 200:
            failures.append((slot, res.status_code, res.text[:500]))

    if failures:
        print(f"\nFAILURES ({len(failures)}):", file=sys.stderr)
        for slot, code, body in failures:
            print(f"  {slot} -> HTTP {code}: {body}", file=sys.stderr)
        return 1
    print("\nAll slots OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
