"""Fresh posts from Reddit's public search feed (Atom/RSS), best effort.

The main search source lags by weeks. Reddit's own feed is current, but without a login it allows only about
one request every 30 seconds per IP address and often refuses servers outright. So this is an extra layer:
used when Reddit answers, skipped quietly when it does not, and it honours the cooldown Reddit asks for.

One request returns the newest ~100 posts for a search term over the last year, and the caller filters them
by date and by any extra words. That keeps the number of requests to Reddit as low as possible.
"""
import html
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

USER_AGENT = "DiscussionFinder/1.0 (open-source research project)"
TIMEOUT = 12
_NS = {"a": "http://www.w3.org/2005/Atom"}
_lock = threading.Lock()
_blocked_until = 0.0  # monotonic time before which we do not ask Reddit again

# Inline formatting must not split a word ("Gove<strong>rnor</strong>" is "Governor", not "Gove rnor").
_INLINE_TAG_RE = re.compile(r"</?(?:strong|em|b|i|u|s|span|a|code|del|sup|sub)(?:\s[^>]*)?>", re.IGNORECASE)


class FeedUnavailable(RuntimeError):
    """Reddit did not give us the feed right now (rate limit, block, network)."""


def enabled() -> bool:
    return os.environ.get("FRESH_FEED", "on").strip().lower() not in ("off", "0", "false", "no")


def _text_of(content_html: str) -> str:
    """Post text from the feed's HTML, without the 'submitted by ...' boilerplate."""
    md = re.search(r'<div class="md">(.*?)</div>', content_html, re.DOTALL)
    raw = md.group(1) if md else content_html
    raw = _INLINE_TAG_RE.sub("", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"submitted by\s+/u/\S+.*$", "", text, flags=re.DOTALL)
    return re.sub(r"\s+", " ", text).strip()


def parse(xml_text: str) -> list[dict]:
    """Posts only (ids starting t3_). Subreddit entries (t5_) and comments are skipped."""
    if "<!DOCTYPE" in xml_text or "<!ENTITY" in xml_text:  # entity tricks (e.g. "billion laughs") have no place in a feed
        raise ET.ParseError("unexpected DTD")
    items = []
    for entry in ET.fromstring(xml_text).findall("a:entry", _NS):
        if not (entry.findtext("a:id", "", _NS) or "").startswith("t3_"):
            continue
        link = entry.find("a:link", _NS)
        author = (entry.findtext("a:author/a:name", "", _NS) or "").strip()
        items.append(
            {
                "title": html.unescape(entry.findtext("a:title", "", _NS) or "").strip(),
                "url": link.get("href") if link is not None else None,
                "author": author.removeprefix("/") or None,
                "published_at": entry.findtext("a:published", "", _NS) or entry.findtext("a:updated", "", _NS),
                "text": _text_of(entry.findtext("a:content", "", _NS) or ""),
            }
        )
    return items


def fetch(query: str) -> list[dict]:
    """The newest posts matching `query` over the last year (Reddit returns up to ~100)."""
    global _blocked_until
    with _lock:
        if time.monotonic() < _blocked_until:
            raise FeedUnavailable("cooling down")
    url = "https://www.reddit.com/search.rss?" + urllib.parse.urlencode({"q": query, "sort": "new", "t": "year", "limit": 100})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=TIMEOUT) as resp:
            body = resp.read(3_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        reset = exc.headers.get("x-ratelimit-reset") or exc.headers.get("retry-after") or ""
        wait = float(reset) if reset.replace(".", "", 1).isdigit() else 60.0
        with _lock:
            _blocked_until = time.monotonic() + min(max(wait, 5.0), 300.0)
        raise FeedUnavailable(f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise FeedUnavailable("network")
    try:
        return parse(body)
    except ET.ParseError:
        raise FeedUnavailable("unreadable feed")
