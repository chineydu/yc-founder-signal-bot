"""LinkedIn discovery adapter for the YC Founder Signal Monitor.

This adapter intentionally uses public, indexed LinkedIn discovery for broad
founder/company discovery. LinkedIn's authenticated Posts API is author- or
organization-scoped and requires approved permissions, so it is an optional
enrichment path rather than a fake "global LinkedIn search" API.

The monitor:
- searches several LinkedIn-specific public queries;
- normalizes LinkedIn URLs and extracts a useful author/company hint;
- requires YC/Speedrun acceptance language;
- suppresses companies already present in the YC directory;
- emits first-seen alerts to Slack with a stable dedupe key.

It is safe to run independently from app.py and is used by the scheduled
GitHub Actions monitor.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

YC_TERMS = (
    "got into yc",
    "accepted into yc",
    "accepted by y combinator",
    "accepted to y combinator",
    "yc s26",
    "yc w26",
    "y combinator s26",
    "y combinator w26",
    "backed by y combinator",
    "speedrun batch",
    "yc speedrun",
)

ANNOUNCEMENT_TERMS = (
    "got into",
    "accepted",
    "backed",
    "joined yc",
    "join yc",
    "yc s26",
    "yc w26",
    "selected",
    "speedrun",
)

LINKEDIN_QUERIES = (
    'site:linkedin.com/posts ("got into YC" OR "accepted into YC" OR "YC S26" OR "Y Combinator")',
    'site:linkedin.com/feed/update ("got into YC" OR "accepted into YC" OR "YC S26" OR "Y Combinator")',
    'site:linkedin.com/company ("YC S26" OR "Y Combinator" OR "Speedrun")',
    'site:linkedin.com/posts ("backed by Y Combinator" OR "Speedrun batch" OR "YC Speedrun")',
    'site:linkedin.com/company ("Y Combinator" OR "Speedrun") "2026"',
)


def normalize_linkedin_url(url: str) -> str:
    """Return a stable LinkedIn URL without tracking parameters/fragments."""
    if not url:
        return ""
    parsed = urlparse(url)
    if "linkedin.com" not in parsed.netloc.lower():
        return url
    path = parsed.path.rstrip("/")
    return f"https://www.linkedin.com{path}"


def is_linkedin_url(url: str) -> bool:
    return "linkedin.com" in urlparse(url).netloc.lower()


def is_early_yc_signal(text: str) -> bool:
    hay = text.casefold()
    return any(term in hay for term in YC_TERMS) and any(
        term in hay for term in ANNOUNCEMENT_TERMS
    )


def infer_company(text: str) -> str:
    patterns = (
        r"([A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4})\s*\(YC\s*[SP]\d{2}\)",
        r"(?:building|founded|founder of|co-founded|cofounder of|launching|launched)\s+([A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4})",
        r"(?:startup|company)\s+(?:called|named)\s+([A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip(" .,!?:;\"")
    return "Unknown — see original post"


def infer_author(text: str, fallback: str = "Unknown founder") -> str:
    # Google News RSS commonly supplies the publisher/author separately; this
    # catches common title patterns when it does not.
    patterns = (
        r"^([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})\s+(?:announces|shares|says|joins)",
        r"by\s+([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return fallback or "Unknown founder"


def _rss(query: str):
    url = (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )
    return feedparser.parse(url)


def _seen_store(path: str, key: str) -> bool:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS linkedin_seen "
        "(key TEXT PRIMARY KEY, url TEXT, company TEXT, detected_at TEXT)"
    )
    row = conn.execute("SELECT 1 FROM linkedin_seen WHERE key=?", (key,)).fetchone()
    conn.close()
    return row is not None


def _mark_seen(path: str, key: str, url: str, company: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS linkedin_seen "
        "(key TEXT PRIMARY KEY, url TEXT, company TEXT, detected_at TEXT)"
    )
    conn.execute(
        "INSERT OR IGNORE INTO linkedin_seen VALUES (?, ?, ?, ?)",
        (key, url, company, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def _slack_alert(text: str) -> None:
    token = os.getenv("SLACK_BOT_TOKEN", "")
    channel = os.getenv("SLACK_CHANNEL_ID", "")
    if not token or not channel:
        raise RuntimeError("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID are required")
    response = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, "text": text},
        timeout=20,
    )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data}")


def run_linkedin_monitor(yc_company_names: set[str]) -> int:
    """Run one LinkedIn discovery cycle. Returns number of new signals."""
    state_path = os.getenv("DB_PATH", "state.db")
    emitted = 0
    for query in LINKEDIN_QUERIES:
        for entry in _rss(query).entries[:50]:
            link = normalize_linkedin_url(entry.get("link", ""))
            if not is_linkedin_url(link):
                continue

            title = entry.get("title", "")
            summary = BeautifulSoup(
                entry.get("summary", ""), "html.parser"
            ).get_text(" ", strip=True)
            text = f"{title} {summary}".strip()
            if not is_early_yc_signal(text):
                continue

            company = infer_company(text)
            if company.casefold() in {x.casefold() for x in yc_company_names}:
                # This is already confirmed by YC; it is not an early signal.
                continue

            author = infer_author(text, entry.get("author", "Unknown founder"))
            stable = hashlib.sha256(link.encode("utf-8")).hexdigest()[:32]
            if _seen_store(state_path, stable):
                continue

            alert = (
                "*EARLY YC SIGNAL — Founder Announced Before YC*\n\n"
                f"Company: {company}\n"
                f"Founder: {author}\n"
                "Batch/program: YC / Speedrun (from post)\n"
                "Source: LinkedIn\n"
                "Status: ⚡ Founder/social announcement detected; not yet confirmed by YC Directory\n\n"
                f"Original post: {link}\n\n"
                f"Post text: {text[:1200]}"
            )
            _slack_alert(alert)
            _mark_seen(state_path, stable, link, company)
            emitted += 1
    return emitted


if __name__ == "__main__":
    # Standalone smoke mode. The main monitor supplies the YC directory names
    # during normal scheduled execution.
    print(run_linkedin_monitor(set()), "new LinkedIn signals")
