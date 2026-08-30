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
from flask import Flask

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("yc-signal")

DB_PATH = os.getenv("DB_PATH", "state.db")
POLL_HOURS = float(os.getenv("POLL_HOURS", "8"))
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL_ID", "")
POND_WEBHOOK_URL = os.getenv("POND_WEBHOOK_URL", "")
SOCIAL_SEARCH_ENABLED = os.getenv("SOCIAL_SEARCH_ENABLED", "true").lower() == "true"
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

YC_DIRECTORY = "https://www.ycombinator.com/companies"
YC_SPEEDRUN = "https://www.ycombinator.com/companies?tags=Speedrun"

app = Flask(__name__)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, source TEXT, url TEXT, company TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, ran_at TEXT)")
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
    for fn in (poll_yc_directory, poll_speedrun):
        try:
            fn()
        except Exception:
            log.exception("Source failed: %s", fn.__name__)
    if SOCIAL_SEARCH_ENABLED:
        yc_names = yc_company_names()
        try:
            poll_x_api(yc_names)
        except Exception:
            log.exception("Source failed: X API")
        try:
            poll_social_search("X", social_queries()["X"], yc_names)
        except Exception:
            log.exception("Source failed: X indexed discovery")
        try:
            poll_social_search("LinkedIn", social_queries()["LinkedIn"], yc_names)
        except Exception:
            log.exception("Source failed: LinkedIn indexed discovery")
    conn = db()
    conn.execute("INSERT INTO runs(ran_at) VALUES(?)", (datetime.now(timezone.utc).isoformat(),))
    conn.commit()
    conn.close()
    log.info("Monitoring run complete")


@app.get("/")
def health():
    return {"ok": True, "service": "yc-founder-signal", "poll_hours": POLL_HOURS}


@app.get("/health")
def health2():
    return {"ok": True}


if __name__ == "__main__":
    while True:
        run_once()
        time.sleep(max(300, int(POLL_HOURS * 3600)))
