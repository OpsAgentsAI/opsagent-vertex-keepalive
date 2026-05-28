# opsagent-vertex-keepalive

One-shot Vertex AI slot probe scheduled for **2026-06-16 06:00 UTC** (09:00 IDT), the day after Google's 2026-06-15 per-model access cutover.

## Architecture

- **Cloud Run Job:** `vertex-slot-probe-i8pjzk8v` in `opsagent-prod/me-west1`
- **Cloud Scheduler:** `vertex-slot-probe-i8pjzk8v` (cron `0 6 16 6 *`, Asia/Jerusalem; fires once on 2026-06-16, job self-pauses after first run)
- **SA:** `cli-gateway-sa@opsagent-prod` (already has `aiplatform.user` on prod + `roles/owner` on staging + `cloudscheduler.admin` on prod)
- **Secrets:** `trello-api-key:latest` -> `TRELLO_KEY`, `trello-token:latest` -> `TRELLO_TOKEN`

## What it does

1. Probes 6 (project, model, location) slots — prod + staging x gemini-2.5-flash, gemini-2.5-flash-lite, gemini-3-flash-preview.
2. Pass criterion: HTTP 200 AND `usageMetadata.promptTokenCount >= 1`.
3. Posts a green/red report as a Trello comment on card `i8pJZk8v`.
4. On any failure: comments on triage parent `FpDfWEMH` + files a P0 card on `opsagent-devops` To Do list.
5. Self-pauses the Cloud Scheduler job so it doesn't fire again.

## Triggered by

Trello card: <https://trello.com/c/i8pJZk8v>
Triage parent: <https://trello.com/c/FpDfWEMH>

## Manual smoke test

```sh
gcloud run jobs execute vertex-slot-probe-i8pjzk8v \
  --region=me-west1 --project=opsagent-prod --wait
```
