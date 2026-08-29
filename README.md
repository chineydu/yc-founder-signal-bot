# YC Founder Signal Bot

A single-workspace Slack monitoring worker for YC and YC Speedrun company signals.

## What it monitors

- YC company directory: source of truth for confirmed YC companies.
- YC Speedrun directory: separately tagged confirmed Speedrun signals.
- Social discovery: Google News RSS queries for public X/LinkedIn-indexed founder announcements mentioning YC/Speedrun. These are explicitly treated as **lead signals**, not proof of YC membership.
- Persistent SQLite state prevents repeat alerts.
- Slack bot posts incremental alerts to a configured channel.
- Optional Pond webhook forwards monitoring events for agent/health infrastructure.

## Important limitation

Direct X and LinkedIn API access is not included. The social discovery layer uses public Google News RSS indexing of posts/pages. For stronger direct-platform coverage, set up approved X/LinkedIn API or an approved search provider and add an adapter under `poll_social_search()` without changing the alert/state interfaces.

## Render setup

Create a **Background Worker** from this repository.

- Language: Python 3
- Branch: `main`
- Build command: `pip install -r requirements.txt`
- Start command: `python app.py`
- Compute: smallest plan suitable for your account

Environment variables:

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_CHANNEL_ID=C0BT5KFM8H5
POLL_HOURS=8
GOOGLE_NEWS_ENABLED=true
# Optional:
POND_WEBHOOK_URL=https://...
```

Do not commit secrets to GitHub.

### State

For production, attach a persistent disk and set:

```text
DB_PATH=/var/data/state.db
```

This keeps the seen-company state across deploys/restarts.

## Local run

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

## Alert types

**Early signal**

`EARLY YC SIGNAL — Founder Announced Before YC`

The alert states that the social signal is not yet confirmed by the YC Directory. This avoids waiting for an official YC social post.

**Confirmed YC**

`NEW YC COMPANY` / `NEW YC SPEEDRUN COMPANY`

The YC directory is treated as the confirmation source.

## Future platform adapters

The worker separates discovery, normalization, deduplication, and Slack delivery. Add adapters for X API, LinkedIn API, or additional platforms by emitting the same item shape (`key`, `source`, `company`, `url`, `text`) and passing it to `emit()`.

## Pond

Set `POND_WEBHOOK_URL` to the webhook/ingestion endpoint supplied by the Pond agent integration. Keep Pond credentials in Render secrets. The code forwards normalized signal events to Pond when configured.
