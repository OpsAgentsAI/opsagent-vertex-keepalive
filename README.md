# opsagent-vertex-keepalive

Quarterly Vertex AI Foundation Model Predictions keepalive — keeps 4 dormant `(project, model, location)` slots warm inside Google's 90-day per-model activity window. Triggered by Cloud Scheduler every 60 days. Cost < $0.01/yr.

Triage parent: [Trello FpDfWEMH](https://trello.com/c/FpDfWEMH). Implementation card: [Trello CFfrq6hA](https://trello.com/c/CFfrq6hA).

## What it does

For each `(project, model, location)` triple in [`slots.json`](./slots.json), POST a minimal `generateContent` call (`maxOutputTokens: 1`) to Vertex. HTTP 200 keeps the per-model 90-day activity counter alive; non-200 (or a `requests` exception) is collected, the loop continues, and the job exits non-zero at the end if any slot failed — tripping a Cloud Monitoring alert + a Trello comment back to [FpDfWEMH](https://trello.com/c/FpDfWEMH).

## Dormant slots (kept warm by this job)

| Project | Model | Location |
|---|---|---|
| opsagent-prod | gemini-3-flash-preview | global |
| opsagent-staging | gemini-2.5-flash | us-central1 |
| opsagent-staging | gemini-2.5-flash-lite | us-central1 |
| opsagent-staging | gemini-3-flash-preview | global |

The two `opsagent-prod / gemini-2.5-*` slots stay warm on organic chat-API traffic and are intentionally NOT pinged here.

## Gotcha — `gemini-3-flash-preview` is global-only

That model is served ONLY at `location=global` (us-central1 / us-east5 return 404). For `global`, host is bare `aiplatform.googleapis.com` — no region prefix. Encoded in [`keepalive.py`](./keepalive.py).

## Infra (one-time bootstrap, not in this repo)

### Pre-flight verification

Before running the bootstrap, confirm the opsagent-prod WIF pool's `github` provider maps `attribute.repository`. Without that attribute mapping, the per-repo principalSet binding below matches zero tokens and the GHA deploy will fail at the auth step:

```bash
gcloud iam workload-identity-pools providers describe github \
  --workload-identity-pool=github --project=opsagent-prod --location=global \
  --format='value(attributeMapping)'
# Expect output to include: attribute.repository=assertion.repository
```

### Service account + roles

```bash
gcloud iam service-accounts create vertex-keepalive-sa \
  --project=opsagent-prod --display-name="Vertex keepalive cron"

# aiplatform.user on every project we keep alive
for P in opsagent-prod opsagent-staging; do
  gcloud projects add-iam-policy-binding "$P" \
    --member="serviceAccount:vertex-keepalive-sa@opsagent-prod.iam.gserviceaccount.com" \
    --role="roles/aiplatform.user"
done

# run.invoker so Cloud Scheduler can POST to the Cloud Run Job execute endpoint
gcloud projects add-iam-policy-binding opsagent-prod \
  --member="serviceAccount:vertex-keepalive-sa@opsagent-prod.iam.gserviceaccount.com" \
  --role="roles/run.invoker"

# WIF binding so this repo's GHA can impersonate the SA without a JSON key
gcloud iam service-accounts add-iam-policy-binding \
  vertex-keepalive-sa@opsagent-prod.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/523955774086/locations/global/workloadIdentityPools/github/attribute.repository/OpsAgentsAI/opsagent-vertex-keepalive"
```

## Cloud Scheduler trigger (one-time, after first deploy)

```bash
gcloud scheduler jobs create http vertex-keepalive-trigger \
  --project=opsagent-prod --location=me-west1 \
  --schedule="0 3 1 */2 *" --time-zone="Asia/Jerusalem" \
  --uri="https://run.googleapis.com/v2/projects/opsagent-prod/locations/me-west1/jobs/vertex-keepalive:run" \
  --http-method=POST \
  --oauth-service-account-email=vertex-keepalive-sa@opsagent-prod.iam.gserviceaccount.com
```

`0 3 1 */2 *` = 03:00 on the 1st of every other month → ~60-day cadence (mid-window inside the 90-day per-model expiry).

The v2 Cloud Run API path (`run.googleapis.com/v2/projects/.../locations/.../jobs/...:run`) is the current canonical surface; the deprecated v1 `namespaces`-based path still works today but is not the documented surface.

## Add a new project to the rotation

1. Append the new tuple to [`slots.json`](./slots.json) via PR.
2. Grant `vertex-keepalive-sa@opsagent-prod` the `roles/aiplatform.user` role on the new project.
3. Merge — next scheduled run picks it up.

## Local dry-run

```bash
pip install google-auth==2.38.0 requests==2.32.3
gcloud auth application-default login
SLOTS_FILE=slots.json python keepalive.py
```

## Owner

[`/gcloud-devops-expert`](https://trello.com/c/CFfrq6hA). Code review goes through [`/opsagents-cto`](https://trello.com/c/CFfrq6hA). Merge gate per rule #22: green CI on the exact head SHA before deploy.
