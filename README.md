# YC Founder Signal Bot

A Slack monitoring agent for YC and YC Speedrun company signals, with a Pond Protocol V1 interface.

## What it monitors

- YC company directory: source of truth for confirmed YC companies.
- YC Speedrun directory: separately tagged confirmed Speedrun signals.
- X: optional direct X API discovery plus a public-indexed fallback when the X API is unavailable or billing-limited.
- LinkedIn: public-indexed discovery of LinkedIn posts/feed updates mentioning YC/Speedrun acceptance or founder announcements, plus company-page signals.
- Social discovery is explicitly treated as **lead evidence**, not proof of YC membership. The YC Directory remains the confirmation source.
- Persistent state prevents repeat alerts across monitoring runs.
- Slack bot posts incremental alerts to a configured channel.
- Pond Protocol V1 exposes `GET /manifest` and `POST /runs` so Pond can invoke the agent.

## Social-source limitation

The public X and LinkedIn adapters use search-engine indexes because direct global post-search APIs are restricted or require approved access. Indexed discovery is therefore best-effort and is not guaranteed to find every post. A valid X bearer token does not bypass X API plan/billing restrictions; if X returns HTTP 402, the monitor continues with the public-indexed X and LinkedIn paths.

The dedicated `linkedin_monitor.py` adapter searches separate LinkedIn post and company-page query families, normalizes canonical LinkedIn URLs, extracts batch/program hints, suppresses companies already present in the YC Directory, and routes first-seen results through the main `app.emit()` dedupe/Slack pipeline.

## Pond Protocol V1

The agent implements a synchronous Pond Protocol V1 action:

- `GET /manifest` — public agent discovery.
- `POST /runs` — authenticated synchronous execution of `scan_yc_signals`.
- `GET /tasks/{task_id}` is exposed only as a compatibility probe; the agent advertises `async_tasks: false`.

Configure the same `POND_ACCESS_KEY` on the deployed server and in Pond. Pond calls the public `/manifest` without authentication and sends the Access Key plus `X-Agent-Protocol-Version: 1.0` to `/runs`.

## Render deployment

The repository uses two Render services:

1. **Web Service** — keeps the Pond HTTPS endpoint available.
2. **Cron Job** — runs `worker.py` every 8 hours for persistent monitoring.

The Render worker runs the normal monitor and then the dedicated LinkedIn indexed adapter, so the LinkedIn pass is also active outside GitHub Actions.

Required environment variables/secrets:

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C...
POND_ACCESS_KEY=<the same key entered in Pond>
X_BEARER_TOKEN=<optional X bearer token>
POLL_HOURS=8
SOCIAL_SEARCH_ENABLED=true
DATABASE_URL=<Render Postgres/internal connection string if used by deployment>
```

Do not commit secrets to GitHub.

## GitHub Actions

The scheduled workflow runs every 6 hours and also supports a manual `workflow_dispatch` run. For a real monitoring run, leave **"Send a controlled demo alert to Slack instead of running monitoring" unchecked** (`test_slack=false`). The workflow runs the normal monitor and a dedicated LinkedIn indexed discovery pass, then saves state.

## Pond setup

After the Render Web Service is deployed and healthy:

1. Copy the public Render service URL, for example `https://your-agent.onrender.com`.
2. Open Pond's **List Your Agent** page.
3. Enter that HTTPS URL in **Agent URL**. Do not enter the GitHub repository URL.
4. Continue to the next step.
5. Configure the same `POND_ACCESS_KEY` value in Pond that you configured in Render.
6. Let Pond fetch `/manifest` and validate the agent.
7. Test the `Scan YC founder signals` action.

The GitHub repository supplies the source code. Pond must reach the deployed web service over HTTPS at runtime.

## Local run

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python app.py
```

For local Pond testing, set `PORT=8000` and expose the server through a secure public HTTPS tunnel. Never expose the Pond access key in source code.

## Alert types

**Early signal**

`EARLY YC SIGNAL — Founder Announced Before YC`

The alert states that the social signal is not yet confirmed by the YC Directory. This avoids waiting for an official YC social post.

**LinkedIn company-page signal**

`LINKEDIN YC SIGNAL — COMPANY PAGE`

Company-page discovery is clearly labelled as an indexed signal because public indexing does not reliably expose page creation time.

**Confirmed YC**

`NEW YC COMPANY` / `NEW YC SPEEDRUN COMPANY`

The YC directory is treated as the confirmation source.

## Future platform adapters

Discovery, normalization, deduplication, and Slack delivery are separated. Add another platform by emitting the same item shape (`key`, `source`, `company`, `url`, `text`) and routing it through `app.emit()`.
