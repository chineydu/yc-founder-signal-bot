"""Public-indexed X discovery fallback.

This module is intentionally a fallback for the direct X API. It uses public
search-engine news RSS indexes and never requires an X credential. Coverage is
not comprehensive: posts/accounts that are not indexed will not be found.
"""

import html
import re
from urllib.parse import quote_plus, urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

SEARCH_ENGINES = (
    "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en",
    "https://www.bing.com/news/search?q={query}&format=rss",
)

X_URL_RE = re.compile(r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[^\s\"<>]+/status/\d+", re.I)
YC_PATTERNS = (
    re.compile(r"\bYC\s*S\d{2}\b", re.I),
    re.compile(r"\bY Combinator\b", re.I),
    re.compile(r"\bgot into YC\b", re.I),
    re.compile(r"\baccepted into YC\b", re.I),
    re.compile(r"\bbacked by Y Combinator\b", re.I),
    re.compile(r"\bSpeedrun\b", re.I),
)
ANNOUNCEMENT_PATTERNS = (
    re.compile(r"\bgot into\b", re.I),
    re.compile(r"\baccepted\b", re.I),
    re.compile(r"\bjoining\b", re.I),
    re.compile(r"\bjoined\b", re.I),
    re.compile(r"\bbacked\b", re.I),
    re.compile(r"\bselected\b", re.I),
)


def _x_url_from_text(text: str) -> str:
    match = X_URL_RE.search(html.unescape(text or ""))
    return match.group(0).rstrip(".,);\"'") if match else ""


def _resolve_link(link: str) -> str:
    """Resolve a search-feed redirect when possible, without failing the scan."""
    if not link:
        return ""
    if X_URL_RE.match(link):
        return link
    try:
        response = requests.get(
            link,
            headers={"User-Agent": "Mozilla/5.0 (compatible; YCFounderSignal/1.0)"},
            timeout=10,
            allow_redirects=True,
        )
        final = response.url or ""
        return _x_url_from_text(final) or final
    except requests.RequestException:
        return link


def _entry_text(entry) -> str:
    title = BeautifulSoup(entry.get("title", ""), "html.parser").get_text(" ", strip=True)
    summary = BeautifulSoup(entry.get("summary", ""), "html.parser").get_text(" ", strip=True)
    content = " ".join(
        BeautifulSoup(item.get("value", ""), "html.parser").get_text(" ", strip=True)
        for item in entry.get("content", [])
        if item.get("value")
    )
    return " ".join(part for part in (title, summary, content) if part).strip()


def _looks_like_signal(text: str) -> bool:
    if not any(pattern.search(text) for pattern in YC_PATTERNS):
        return False
    return any(pattern.search(text) for pattern in ANNOUNCEMENT_PATTERNS)


def search_public_x(queries, max_entries=40):
    """Yield indexed X candidates as dictionaries.

    Each result has ``url``, ``text``, and ``author`` where available. A
    search-engine result is accepted only when it points to X/Twitter or when
    the feed text itself contains a canonical X status URL.
    """
    seen = set()
    for query in queries:
        encoded = quote_plus(query)
        for template in SEARCH_ENGINES:
            feed_url = template.format(query=encoded)
            try:
                feed = feedparser.parse(feed_url)
            except Exception:
                continue
            for entry in feed.entries[:max_entries]:
                text = _entry_text(entry)
                if not _looks_like_signal(text):
                    continue
                link = _x_url_from_text(text)
                raw_link = entry.get("link", "")
                if not link and raw_link:
                    resolved = _resolve_link(raw_link)
                    link = resolved if X_URL_RE.match(resolved) else ""
                if not link or link in seen:
                    continue
                seen.add(link)
                author = entry.get("author", "") or "Unknown founder"
                yield {"url": link, "text": text, "author": author}


def default_queries():
    common = (
        '("got into YC" OR "accepted into YC" OR "YC S26" '
        'OR "Y Combinator" OR "backed by Y Combinator" OR "Speedrun batch")'
    )
    return [f"site:x.com {common}", f"site:twitter.com {common}"]
