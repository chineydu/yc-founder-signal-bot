# YC Founder Signal Bot

A Slack monitoring agent for YC and YC Speedrun company signals, with a Pond Protocol V1 interface.

## What it monitors

- YC company directory: source of truth for confirmed YC companies.
- YC Speedrun directory: separately tagged confirmed Speedrun signals.
- Social discovery: Google News RSS queries for public X/LinkedIn-indexed founder announcements mentioning YC/Speedrun. These are explicitly treated as **lead signals**, not proof of YC membership.
- Optional direct X API discovery when `X_BEARER_TOKEN` has the required X API access.
- Persistent Postgres state prevents repeat alerts across monitoring runs and restarts.
- Slack bot posts incremental alerts to a configured channel.
- Pond Protocol V1 exposes `GET /manifest` and `POST /runs` so Pond can invoke the agent.

## Important X API limitation

A valid X bearer token does not bypass X API plan/billing restrictions. If X returns HTTP 402, the agent logs the access limitation and continues with the other discovery layers. No X API billing is required for the repository to run its YC directory and indexed public discovery paths.

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

Both services use the same Render Postgres database through `DATABASE_URL`, so deduplication survives restarts and separate cron executions.

The web service runs `python server.py`; the monitoring cron runs `python worker.py`.

Required environment variables/secrets:

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C...
POND_ACCESS_KEY=<the same key entered in Pond>
X_BEARER_TOKEN=<optional X bearer token>
POLL_HOURS=8
SOCIAL_SEARCH_ENABLED=true
DATABASE_URL=<Render Postgres internal connection string>
```

`POND_WEBHOOK_URL` is optional and is only needed if you also want to forward normalized events to another Pond webhook/ingestion service.

Do not commit secrets to GitHub.

## Pond setup

After the Render Web Service is deployed and healthy:

1. Copy the public Render service URL, for example `https://your-agent.onrender.com`.
2. Open Pond's **List Your Agent** page.
3. Enter that HTTPS URL in **Agent URL**. Do not enter the GitHub repository URL.
4. Continue to the next step.
5. Configure the same `POND_ACCESS_KEY` value in Pond that you configured in Render.
6. Let Pond fetch `/manifest` and validate the agent.
7. Test the `Scan YC founder signals` action.

The GitHub repository only supplies the source code. Pond must reach the deployed web service over HTTPS at runtime.

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

**Confirmed YC**

`NEW YC COMPANY` / `NEW YC SPEEDRUN COMPANY`

The YC directory is treated as the confirmation source.

## Future platform adapters

The worker separates discovery, normalization, deduplication, and Slack delivery. Add adapters for X API, LinkedIn API, or additional platforms by emitting the same item shape (`key`, `source`, `company`, `url`, `text`) and passing it to `emit()`.
