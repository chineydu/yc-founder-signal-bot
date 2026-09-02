"""One monitoring cycle for a Render Cron Job.

Render runs this file every 8 hours. The normal monitor checks YC Directory,
Speedrun, X, and the legacy indexed social path; the dedicated LinkedIn adapter
then performs the stronger public-indexed LinkedIn discovery pass and routes
signals through the main app's persistent dedupe/Slack pipeline.
"""

import logging
import os

import app
from linkedin_monitor import run_linkedin_monitor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("yc-worker")


if __name__ == "__main__":
    log.info("YC monitoring worker starting")
    log.info("Database state: %s", "Postgres" if os.getenv("DATABASE_URL") else "SQLite fallback")

    failures = app.run_once()

    if os.getenv("SOCIAL_SEARCH_ENABLED", "true").lower() == "true":
        try:
            yc_names = app.yc_company_names()
            linkedin_count = run_linkedin_monitor(yc_names, emit_callback=app.emit)
            log.info("Dedicated LinkedIn indexed discovery completed: %s new candidate(s)", linkedin_count)
        except Exception:
            failures.append("LinkedIn dedicated indexed discovery")
            log.exception("Source failed: dedicated LinkedIn indexed discovery")

    if failures:
        raise SystemExit(f"Monitoring completed with source failures: {', '.join(failures)}")

    conn = app.db()
    try:
        total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        total_seen = conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        log.info("Worker health: runs=%s seen=%s", total_runs, total_seen)
    finally:
        conn.close()

    log.info("YC monitoring worker finished successfully")
