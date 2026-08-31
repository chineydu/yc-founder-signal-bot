"""One monitoring cycle for a Render Cron Job.

Render runs this file every 8 hours. State is stored in Render Postgres when
DATABASE_URL is configured, so the same company/post is not alerted again.
"""

import logging
import os

from app import db, run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("yc-worker")

if __name__ == "__main__":
    log.info("YC monitoring worker starting")
    log.info("Database state: %s", "Postgres" if os.getenv("DATABASE_URL") else "SQLite fallback")
    failures = run_once()
    if failures:
        raise SystemExit(f"Monitoring completed with source failures: {', '.join(failures)}")
    conn = db()
    try:
        total_runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        total_seen = conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]
        log.info("Worker health: runs=%s seen=%s", total_runs, total_seen)
    finally:
        conn.close()
    log.info("YC monitoring worker finished successfully")
