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
GOOGLE_NEWS_ENABLED = os.getenv("GOOGLE_NEWS_ENABLED", "true").lower() == "true"

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
    conn = db(); row = conn.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone(); conn.close(); return row is not None


def mark(item):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?,?,?)", (item["key"], item["source"], item.get("url", ""), item.get("company", ""), datetime.now(timezone.utc).isoformat()))
    conn.commit(); conn.close()


def slack_alert(item):
    if not SLACK_TOKEN or not SLACK_CHANNEL:
        log.warning("Slack is not configured; would alert: %s", item)
        return False
    text = item["text"]
    r = requests.post("https://slack.com/api/chat.postMessage", headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json; charset=utf-8"}, json={"channel": SLACK_CHANNEL, "text": text}, timeout=20)
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
    # YC pages expose company links in rendered HTML and JSON-like data. We intentionally
    # use conservative extraction so a page layout change cannot create thousands of alerts.
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=re.compile(r"^/companies/")):
        href = a.get("href", "")
        slug = href.rstrip("/").split("/")[-1]
        name = a.get_text(" ", strip=True)
        if not slug or not name or slug in {"companies", "new"}:
            continue
        if len(name) > 120:
            continue
        out.append((slug, name, "https://www.ycombinator.com" + href))
    # de-dupe while preserving order
    return list(dict((x[0], x) for x in out).values())


def poll_yc_directory():
    html = get_html(YC_DIRECTORY)
    for slug, name, url in extract_yc_companies(html):
        item = {
            "key": f"yc:{slug}", "source": "YC Directory", "company": name, "url": url,
            "text": f"*NEW YC COMPANY*\n\nCompany: {name}\nSource: YC Directory\nStatus: ✅ Confirmed by YC\nYC Profile: {url}"
        }
        emit(item)


def poll_speedrun():
    html = get_html(YC_SPEEDRUN)
    for slug, name, url in extract_yc_companies(html):
        item = {
            "key": f"speedrun:{slug}", "source": "YC Speedrun", "company": name, "url": url,
            "text": f"*NEW YC SPEEDRUN COMPANY*\n\nCompany: {name}\nSource: YC Speedrun\nStatus: ✅ Confirmed by YC\nYC Profile: {url}"
        }
        emit(item)


def google_news_queries():
    return [
        '"got into YC" startup founder',
        '"accepted into YC" founder startup',
        '"YC S26" founder startup',
        '"Y Combinator" "S26" founder',
        '"Speedrun" "Y Combinator" founder',
        '"backed by Y Combinator" startup founder',
    ]


def poll_social_search():
    if not GOOGLE_NEWS_ENABLED:
        return
    # Google News RSS is a low-friction discovery layer. It is deliberately treated as a
    # lead source, not as proof of YC membership. Official YC directory confirmation is
    # reconciled separately; pre-directory social signals are labeled EARLY YC SIGNAL.
    for q in google_news_queries():
        url = "https://news.google.com/rss/search?q=" + quote_plus(q) + "&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        for entry in feed.entries[:20]:
            link = entry.get("link", "")
            title = entry.get("title", "")
            summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
            hay = (title + " " + summary).lower()
            if not any(x in hay for x in ["yc", "y combinator", "speedrun"]):
                continue
            if not any(x in hay for x in ["got into", "accepted", "backed", "selected", "join yc", "joined yc"]):
                continue
            item = {
                "key": f"social:{link}", "source": "Social discovery (X/LinkedIn lead)", "url": link,
                "text": f"*EARLY YC SIGNAL — Founder Announced Before YC*\n\nSource: Social discovery (X/LinkedIn lead)\nStatus: ⚡ Founder/social announcement detected; not yet confirmed by YC Directory\n\nHeadline: {title}\nLink: {link}\n\nNote: This is a lead signal. YC Directory confirmation is monitored separately."
            }
            emit(item)


def run_once():
    log.info("Starting monitoring run")
    test_item = {
        "key": f"test:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "source": "Test",
        "text": "✅ YC Founder Signal Monitor — Slack connection test successful."
    }
    slack_alert(test_item)
    for fn in (poll_yc_directory, poll_speedrun, poll_social_search):
        try:
            fn()
        except Exception:
            log.exception("Source failed: %s", fn.__name__)
    conn = db(); conn.execute("INSERT INTO runs(ran_at) VALUES(?)", (datetime.now(timezone.utc).isoformat(),)); conn.commit(); conn.close()
    log.info("Monitoring run complete")


@app.get("/")
def health():
    return {"ok": True, "service": "yc-founder-signal", "poll_hours": POLL_HOURS}


@app.get("/health")
def health2():
    return {"ok": True}


if __name__ == "__main__":
    # Keep the process alive on Render. SQLite persists in the mounted disk when DB_PATH
    # points at a persistent-disk path; for a simple deployment it will also work locally.
    while True:
        run_once()
        time.sleep(max(300, int(POLL_HOURS * 3600)))
