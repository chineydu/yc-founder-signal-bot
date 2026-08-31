import os
import re
import sqlite3
import time
import logging
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("yc-signal")

DB_PATH = os.getenv("DB_PATH", "state.db")
POLL_HOURS = float(os.getenv("POLL_HOURS", "8"))
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL_ID", "")
POND_WEBHOOK_URL = os.getenv("POND_WEBHOOK_URL", "")
POND_ACCESS_KEY = os.getenv("POND_ACCESS_KEY", "")
SOCIAL_SEARCH_ENABLED = os.getenv("SOCIAL_SEARCH_ENABLED", "true").lower() == "true"
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

YC_DIRECTORY = "https://www.ycombinator.com/companies"
YC_SPEEDRUN = "https://www.ycombinator.com/companies?tags=Speedrun"

app = Flask(__name__)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, source TEXT, url TEXT, company TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, ran_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS pond_runs (run_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL)")
    conn.commit()
    return conn


def seen(key):
    conn = db()
    row = conn.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone()
    conn.close()
    return row is not None


def mark(item):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?,?,?)", (item["key"], item["source"], item.get("url", ""), item.get("company", ""), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def slack_alert(item):
    if not SLACK_TOKEN or not SLACK_CHANNEL:
        log.warning("Slack is not configured; would alert: %s", item)
        return False
    r = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json; charset=utf-8"},
        json={"channel": SLACK_CHANNEL, "text": item["text"]},
        timeout=20,
    )
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data}")
    return True


def pond_event(item):
    if not POND_WEBHOOK_URL:
        return
    try:
        requests.post(POND_WEBHOOK_URL, json={"event": "yc_signal", "signal": item}, timeout=15).raise_for_status()
    except Exception as exc:
        log.warning("Pond webhook failed: %s", exc)


def emit(item):
    if seen(item["key"]):
        return
    slack_alert(item)
    pond_event(item)
    mark(item)


def get_html(url):
    r = requests.get(url, headers={"User-Agent": "yc-founder-signal/1.0"}, timeout=30)
    r.raise_for_status()
    return r.text


def extract_yc_companies(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=re.compile(r"^/companies/")):
        href = a.get("href", "")
        slug = href.rstrip("/").split("/")[-1]
        name = a.get_text(" ", strip=True)
        if not slug or not name or slug in {"companies", "new"} or len(name) > 120:
            continue
        out.append((slug, name, "https://www.ycombinator.com" + href))
    return list(dict((x[0], x) for x in out).values())


def yc_company_names():
    try:
        return {name.casefold() for _, name, _ in extract_yc_companies(get_html(YC_DIRECTORY))}
    except Exception:
        log.exception("Could not refresh YC directory for social reconciliation")
        return set()


def poll_yc_directory():
    html = get_html(YC_DIRECTORY)
    for slug, name, url in extract_yc_companies(html):
        emit({
            "key": f"yc:{slug}", "source": "YC Directory", "company": name, "url": url,
            "text": f"*NEW YC COMPANY*\n\nCompany: {name}\nSource: YC Directory\nStatus: ✅ Confirmed by YC\nYC Profile: {url}"
        })


def poll_speedrun():
    html = get_html(YC_SPEEDRUN)
    for slug, name, url in extract_yc_companies(html):
        emit({
            "key": f"speedrun:{slug}", "source": "YC Speedrun", "company": name, "url": url,
            "text": f"*NEW YC SPEEDRUN COMPANY*\n\nCompany: {name}\nSource: YC Speedrun\nStatus: ✅ Confirmed by YC\nYC Profile: {url}"
        })


def social_queries():
    common = '("got into YC" OR "accepted into YC" OR "YC S26" OR "Y Combinator" OR "backed by Y Combinator" OR "Speedrun batch")'
    return {
        "X": [f'site:x.com {common}', f'site:twitter.com {common}'],
        "LinkedIn": [f'site:linkedin.com/posts {common}', f'site:linkedin.com/feed/update {common}', f'site:linkedin.com/company {common}'],
    }


def infer_company(text):
    patterns = [
        r'([A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4})\s*\(YC\s*[SP]\d{2}\)',
        r'(?:building|founded|founder of|co-founded|cofounder of|launching|launched)\s+([A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4})',
        r'(?:startup|company)\s+(?:called|named)\s+([A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4})',
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip(" .,!?:;\"")
    return "Unknown — see original post"


def is_early_yc_signal(text):
    hay = text.casefold()
    yc_terms = ("got into yc", "accepted into yc", "yc s26", "y combinator", "backed by y combinator", "speedrun")
    announcement_terms = ("got into", "accepted", "backed", "joined yc", "join yc", "yc s26", "speedrun", "selected")
    return any(k in hay for k in yc_terms) and any(k in hay for k in announcement_terms)


def emit_social(platform, link, text, author="Unknown founder", company=None):
    if not is_early_yc_signal(text):
        return
    company = company or infer_company(text)
    emit({
        "key": f"social:{platform}:{link}", "source": platform, "company": company, "url": link,
        "text": (
            "*EARLY YC SIGNAL — Founder Announced Before YC*\n\n"
            f"Company: {company}\nFounder: {author}\nBatch/program: YC / Speedrun (from post)\n"
            f"Source: {platform}\nStatus: ⚡ Founder/social announcement detected; not yet confirmed by YC Directory\n\n"
            f"Original post: {link}\n\nPost text: {text[:900]}"
        ),
    })


def social_feed(query):
    url = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=en-US&gl=US&ceid=US:en"
    return feedparser.parse(url)


def poll_social_search(platform, queries, yc_names):
    """Fallback discovery layer for public X/LinkedIn pages indexed by Google News."""
    for query in queries:
        feed = social_feed(query)
        for entry in feed.entries[:30]:
            link = entry.get("link", "")
            title = entry.get("title", "")
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
            author = entry.get("author", "") or "Unknown founder"
            text = f"{title} {summary}"
            if platform == "X" and not ("x.com/" in link or "twitter.com/" in link):
                continue
            if platform == "LinkedIn" and "linkedin.com" not in link:
                continue
            company = infer_company(text)
            if company.casefold() in yc_names:
                continue
            emit_social(platform, link, text, author, company)


def x_api_query():
    return '("got into YC" OR "accepted into YC" OR "YC S26" OR "Y Combinator" OR "backed by Y Combinator" OR "Speedrun") -is:retweet lang:en'


def poll_x_api(yc_names):
    """Direct X API detection. Falls back to indexed search when no token is configured."""
    if not X_BEARER_TOKEN:
        log.info("X_BEARER_TOKEN not configured; using indexed X discovery")
        return False
    params = {
        "query": x_api_query(),
        "max_results": 100,
        "tweet.fields": "created_at,author_id",
        "expansions": "author_id",
        "user.fields": "username,name",
    }
    r = requests.get(
        "https://api.x.com/2/tweets/search/recent",
        headers={"Authorization": f"Bearer {X_BEARER_TOKEN}"},
        params=params,
        timeout=30,
    )
    if r.status_code in (401, 403):
        log.error("X API authentication/access failed (%s): %s", r.status_code, r.text[:500])
        return False
    if r.status_code == 402:
        log.error("X API billing/access restriction (402): %s", r.text[:500])
        return False
    if r.status_code == 429:
        log.warning("X API rate limit reached; indexed discovery will still run")
        return False
    r.raise_for_status()
    payload = r.json()
    users = {u["id"]: u for u in payload.get("includes", {}).get("users", [])}
    for post in payload.get("data", []):
        author = users.get(post.get("author_id"), {})
        username = author.get("username", "")
        author_label = f"@{username}" if username else author.get("name", "Unknown founder")
        link = f"https://x.com/{username}/status/{post['id']}" if username else f"https://x.com/i/web/status/{post['id']}"
        text = post.get("text", "")
        company = infer_company(text)
        if company.casefold() in yc_names:
            continue
        emit_social("X", link, text, author_label, company)
    return True


def run_once():
    log.info("Starting monitoring run")
    failures = []
    for fn in (poll_yc_directory, poll_speedrun):
        try:
            fn()
        except Exception:
            failures.append(fn.__name__)
            log.exception("Source failed: %s", fn.__name__)
    if SOCIAL_SEARCH_ENABLED:
        yc_names = yc_company_names()
        try:
            poll_x_api(yc_names)
        except Exception:
            failures.append("X API")
            log.exception("Source failed: X API")
        try:
            poll_social_search("X", social_queries()["X"], yc_names)
        except Exception:
            failures.append("X indexed discovery")
            log.exception("Source failed: X indexed discovery")
        try:
            poll_social_search("LinkedIn", social_queries()["LinkedIn"], yc_names)
        except Exception:
            failures.append("LinkedIn indexed discovery")
            log.exception("Source failed: LinkedIn indexed discovery")
    conn = db()
    conn.execute("INSERT INTO runs(ran_at) VALUES(?)", (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    conn.close()
    log.info("Monitoring run complete")
    return failures


def pond_authorized():
    if not POND_ACCESS_KEY:
        return False
    auth = request.headers.get("Authorization", "")
    return auth == f"Bearer {POND_ACCESS_KEY}"


def pond_protocol_ok():
    return request.headers.get("X-Agent-Protocol-Version") == "1.0"


def pond_error(code, message, status=400):
    return jsonify({"error": {"code": code, "message": message}}), status


def pond_manifest():
    return {
        "protocol": "marketplace-agent",
        "protocol_version": "1.0",
        "agent_version": "2026.08.31.1",
        "metadata": {
            "name": "YC Founder Signal Monitor",
            "short_description": "Monitors YC, Speedrun, and public founder announcements and sends qualified signals to Slack.",
            "description": "Finds newly confirmed YC companies and early founder announcements before YC directory confirmation. Direct X API access is optional; when unavailable, indexed public discovery remains available.",
        },
        "actions": [
            {
                "id": "scan_yc_signals",
                "name": "Scan YC founder signals",
                "description": "Run the YC Founder Signal Monitor now and report the monitoring result, including any source limitations.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "include_social": {
                            "type": "boolean",
                            "description": "Whether to include public X and LinkedIn indexed founder-announcement discovery.",
                        }
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            }
        ],
        "capabilities": {
            "sync": True,
            "streaming": False,
            "async_tasks": False,
            "cancellation": False,
            "attachments": False,
            "feedback": False,
        },
        "input_modes": ["text/plain"],
        "output_modes": ["text/markdown"],
        "limits": {
            "max_request_bytes": 1048576,
            "max_attachment_bytes": 0,
            "max_run_seconds": 120,
        },
    }


@app.get("/")
def health():
    return {"ok": True, "service": "yc-founder-signal", "pond_protocol": "1.0", "poll_hours": POLL_HOURS}


@app.get("/health")
def health2():
    return {"ok": True}


@app.get("/manifest")
def manifest():
    return jsonify(pond_manifest())


@app.post("/runs")
def pond_run():
    if not pond_authorized():
        return pond_error("unauthorized", "Missing or incorrect Pond Access Key.", 401)
    if not pond_protocol_ok():
        return pond_error("invalid_request", "X-Agent-Protocol-Version must be exactly 1.0.", 400)

    payload = request.get_json(silent=True) or {}
    run_id = payload.get("run_id")
    if not run_id:
        return pond_error("invalid_request", "run_id is required.", 400)
    if payload.get("action_id") != "scan_yc_signals":
        return pond_error("unsupported_operation", "action_id must be scan_yc_signals.", 400)
    execution = payload.get("execution") or {}
    if execution.get("accepted_output_modes") and "text/markdown" not in execution["accepted_output_modes"]:
        return pond_error("unsupported_media_type", "This Agent returns text/markdown.", 400)
    deadline_ms = execution.get("deadline_ms")
    if deadline_ms is not None and deadline_ms > 120000:
        return pond_error("invalid_request", "deadline_ms exceeds the Agent limit.", 400)

    import hashlib
    import json
    request_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    conn = db()
    existing = conn.execute("SELECT request_hash, response_json FROM pond_runs WHERE run_id=?", (run_id,)).fetchone()
    if existing:
        if existing[0] != request_hash:
            conn.close()
            return pond_error("idempotency_conflict", "run_id was already used with a different request.", 409)
        response = json.loads(existing[1])
        conn.close()
        return jsonify(response), 200

    params = payload.get("parameters") or {}
    if "include_social" in params:
        SOCIAL_SEARCH_ENABLED_LOCAL = params["include_social"]
    else:
        SOCIAL_SEARCH_ENABLED_LOCAL = SOCIAL_SEARCH_ENABLED

    original_social = globals()["SOCIAL_SEARCH_ENABLED"]
    globals()["SOCIAL_SEARCH_ENABLED"] = bool(SOCIAL_SEARCH_ENABLED_LOCAL)
    try:
        failures = run_once()
    finally:
        globals()["SOCIAL_SEARCH_ENABLED"] = original_social

    if failures:
        result_text = (
            "YC Founder Signal Monitor completed with limitations.\n\n"
            f"Sources with errors: {', '.join(failures)}.\n"
            "The workflow continues running and reports source failures rather than treating them as a successful source result."
        )
    else:
        result_text = "YC Founder Signal Monitor completed successfully. YC and enabled social discovery sources were checked and qualified signals were sent through the configured alert pipeline."

    response = {
        "run_id": run_id,
        "status": "completed",
        "output": [{"type": "text", "text": result_text}],
        "usage": {"unit_of_measurement": "result", "quantity": 1},
    }
    conn = db()
    conn.execute(
        "INSERT INTO pond_runs(run_id, request_hash, response_json, created_at) VALUES(?,?,?,?)",
        (run_id, request_hash, json.dumps(response), datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return jsonify(response), 200


if __name__ == "__main__":
    # Render web services provide PORT; GitHub Actions/local worker mode does not.
    port = int(os.getenv("PORT", "0"))
    if port:
        app.run(host="0.0.0.0", port=port)
    else:
        while True:
            run_once()
            time.sleep(max(300, int(POLL_HOURS * 3600)))
