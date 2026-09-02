"""Public-indexed LinkedIn discovery adapter for the YC Founder Signal Monitor."""
from __future__ import annotations

import hashlib
import html
import os
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote_plus, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

SEARCH_ENGINES = (
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
    "https://www.bing.com/news/search?q={query}&format=rss",
)
LINKEDIN_POST_QUERIES = (
    'site:linkedin.com/posts ("got into YC" OR "accepted into YC" OR "accepted by Y Combinator")',
    'site:linkedin.com/feed/update ("got into YC" OR "accepted into YC" OR "accepted by Y Combinator")',
    'site:linkedin.com/posts ("YC S26" OR "YC W26" OR "Y Combinator S26" OR "Y Combinator W26")',
    'site:linkedin.com/posts ("backed by Y Combinator" OR "joined YC" OR "joining YC")',
    'site:linkedin.com/posts ("Speedrun batch" OR "YC Speedrun" OR "accepted to Speedrun")',
)
YC_TERMS = (
    "got into yc", "accepted into yc", "accepted by y combinator", "accepted to y combinator",
    "yc s26", "yc w26", "y combinator s26", "y combinator w26", "backed by y combinator",
    "joined yc", "joining yc", "speedrun batch", "yc speedrun", "accepted to speedrun",
)
ANNOUNCEMENT_TERMS = ("got into", "accepted", "backed", "joined yc", "joining yc", "join yc", "selected", "speedrun batch", "accepted to speedrun")
LINKEDIN_POST_RE = re.compile(r'https?://(?:[a-z]{2,3}\.)?(?:www\.)?linkedin\.com/(?:posts/[^\s?#<>"\']+|feed/update/[^\s?#<>"\']+)', re.I)


def normalize_linkedin_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(html.unescape(url))
    if "linkedin.com" not in parsed.netloc.lower():
        return ""
    return f"https://www.linkedin.com{parsed.path.rstrip('/')}"


def _entry_text(entry) -> str:
    parts = []
    for value in (entry.get("title", ""), entry.get("summary", "")):
        if value:
            parts.append(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))
    for item in entry.get("content", []):
        value = item.get("value")
        if value:
            parts.append(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))
    return " ".join(parts).strip()


def _resolve_search_link(link: str) -> str:
    if not link:
        return ""
    if "linkedin.com" in urlparse(link).netloc.lower():
        return link
    try:
        response = requests.get(link, headers={"User-Agent": "Mozilla/5.0 (compatible; YCFounderSignal/1.0)"}, timeout=10, allow_redirects=True)
        return response.url or ""
    except requests.RequestException:
        return ""


def _canonical_url(entry) -> str:
    text = _entry_text(entry)
    for candidate in LINKEDIN_POST_RE.findall(html.unescape(text)):
        normalized = normalize_linkedin_url(candidate)
        if normalized:
            return normalized
    resolved = _resolve_search_link(entry.get("link", ""))
    match = LINKEDIN_POST_RE.search(resolved) if resolved else None
    return normalize_linkedin_url(match.group(0)) if match else ""


def is_early_yc_signal(text: str) -> bool:
    hay = text.casefold()
    return any(term in hay for term in YC_TERMS) and any(term in hay for term in ANNOUNCEMENT_TERMS)


def infer_batch(text: str) -> str:
    match = re.search(r'\b(?:YC|Y Combinator)\s*([SW])\s*(\d{2})\b', text, re.I)
    if match:
        return f"YC {match.group(1).upper()}{match.group(2)}"
    return "YC Speedrun" if "speedrun" in text.casefold() else "YC / Speedrun (from post)"


def infer_company(text: str) -> str:
    patterns = (
        r'([A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4})\s*\(YC\s*[SW]\d{2}\)',
        r'(?:building|founded|founder of|co-founded|cofounder of|launching|launched)\s+([A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4})',
        r'(?:startup|company)\s+(?:called|named)\s+([A-Z][A-Za-z0-9&._-]*(?:\s+[A-Z][A-Za-z0-9&._-]*){0,4})',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip(' .,!?:;"')
    return "Unknown — see original post"


def infer_author(text: str, fallback: str = "Unknown founder") -> str:
    match = re.search(r'^([A-Z][A-Za-z.\'-]+(?:\s+[A-Z][A-Za-z.\'-]+){1,3})\s+(?:announces|shares|says|joins)', text)
    return match.group(1).strip() if match else fallback


def _search_feed(query: str):
    encoded = quote_plus(query)
    for template in SEARCH_ENGINES:
        try:
            feed = feedparser.parse(template.format(query=encoded))
        except Exception:
            continue
        if getattr(feed, "bozo", False) and not feed.entries:
            continue
        yield from feed.entries[:40]


def search_public_linkedin(queries=None, max_entries: int = 40):
    queries = tuple(queries or LINKEDIN_POST_QUERIES)
    seen_urls: set[str] = set()
    count = 0
    for query in queries:
        for entry in _search_feed(query):
            if count >= max_entries:
                return
            text = _entry_text(entry)
            if not is_early_yc_signal(text):
                continue
            link = _canonical_url(entry)
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)
            count += 1
            yield {"url": link, "text": text, "author": entry.get("author", "") or infer_author(text), "published": entry.get("published", ""), "kind": "post", "batch": infer_batch(text)}


def _local_seen(path: str, key: str) -> bool:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS linkedin_seen (key TEXT PRIMARY KEY, url TEXT, company TEXT, detected_at TEXT)")
    row = conn.execute("SELECT 1 FROM linkedin_seen WHERE key=?", (key,)).fetchone()
    conn.close()
    return row is not None


def _local_mark(path: str, key: str, url: str, company: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE IF NOT EXISTS linkedin_seen (key TEXT PRIMARY KEY, url TEXT, company TEXT, detected_at TEXT)")
    conn.execute("INSERT OR IGNORE INTO linkedin_seen VALUES (?, ?, ?, ?)", (key, url, company, datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()


def _slack_alert(text: str) -> None:
    token, channel = os.getenv("SLACK_BOT_TOKEN", ""), os.getenv("SLACK_CHANNEL_ID", "")
    if not token or not channel:
        raise RuntimeError("SLACK_BOT_TOKEN and SLACK_CHANNEL_ID are required")
    response = requests.post("https://slack.com/api/chat.postMessage", headers={"Authorization": f"Bearer {token}"}, json={"channel": channel, "text": text}, timeout=20)
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Slack error: {data}")


def _alert_text(candidate: dict, company: str) -> str:
    return ("*EARLY YC SIGNAL — Founder Announced Before YC*\n\n"
            f"Company: {company}\nFounder: {candidate['author']}\nBatch/program: {candidate['batch']}\n"
            "Source: LinkedIn (public indexed discovery)\n"
            "Status: ⚡ Founder/social announcement detected; not yet confirmed by YC Directory\n\n"
            f"Original post: {candidate['url']}\n\nPost text: {candidate['text'][:1200]}")


def run_linkedin_monitor(yc_company_names: set[str], emit_callback=None) -> int:
    state_path = os.getenv("DB_PATH", "state.db")
    emitted = 0
    yc_names = {name.casefold() for name in yc_company_names}
    for candidate in search_public_linkedin():
        company = infer_company(candidate["text"])
        if company.casefold() in yc_names:
            continue
        key = f"social:LinkedIn:{candidate['url']}"
        stable = hashlib.sha256(key.encode()).hexdigest()[:32]
        if _local_seen(state_path, stable):
            continue
        item = {"key": key, "source": "LinkedIn", "company": company, "url": candidate["url"], "text": _alert_text(candidate, company)}
        if emit_callback is not None:
            emit_callback(item)
        else:
            _slack_alert(item["text"])
        _local_mark(state_path, stable, candidate["url"], company)
        emitted += 1
    return emitted
