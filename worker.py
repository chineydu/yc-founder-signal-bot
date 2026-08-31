"""One monitoring cycle for a Render Cron Job.

Render runs this file every 8 hours. State is stored in Render Postgres when
DATABASE_URL is configured, so the same company/post is not alerted again.
"""

import logging

from app import run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

if __name__ == "__main__":
    failures = run_once()
    if failures:
        raise SystemExit(f"Monitoring completed with source failures: {', '.join(failures)}")
