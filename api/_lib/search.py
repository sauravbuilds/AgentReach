"""Reddit discussion search through Exa's free hosted search (plain HTTP: no login, no CLI, no key)."""
import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone

from . import agentreach, feed, glossary

MAX_DAYS = 90
DEFAULT_DAYS = 90
FETCH_LIMIT = 50
SEARCH_TIMEOUT = 20
# Longest a search waits for any one source before returning what the others found.
SOURCE_DEADLINE = 25
EXA_URL = "https://mcp.exa.ai/mcp"
USER_AGENT = "DiscussionFinder/1.0"

_NUMBER_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30}
_WINDOW_RE = re.compile(
    r"\b(?:in\s+the\s+|within\s+the\s+|from\s+the\s+|for\s+the\s+|in\s+|within\s+|from\s+|for\s+)?"
    r"(?:last|past|previous)\s+(?:(\d+|a|an|one|two|three|four|five|six)\s+)?(day|week|month)s?\b",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(r"^(Title|URL|Published|Author):\s*(.*)$")
_SUBREDDIT_RE = re.compile(r"reddit\.com/r/([^/]+)/", re.IGNORECASE)
_REDDIT_URL_RE = re.compile(r"^https://(?:www\.|old\.)?reddit\.com/r/[^/\s]+/", re.IGNORECASE)
# "u/name · 17 points · September 25, 2026 at 5:18 PM UTC" comment bylines inside Exa highlights.
_BYLINE_RE = re.compile(
    r"\*{0,2}u/[\w-]+\*{0,2}\s*[·•\ufffd]\s*\d+\s+points?\s*[·•\ufffd]\s*[A-Z][a-z]+ \d{1,2}, \d{4} at [\d:]+ [AP]M UTC"
)
# "in r/macapps · 39 points · 28 comments · September 30, 2026 at 5:59 PM UTC" post header line.
_POST_HEADER_RE = re.compile(
    r"in r/\w+\s*[·•�]\s*\d+\s+points?\s*[·•�]\s*\d+\s+comments?\s*[·•�]\s*"
    r"[A-Z][a-z]+ \d{1,2}, \d{4} at [\d:]+ [AP]M UTC"
)


_COUNT = r"\d+|one|two|three|four|five|six"
# "RNOR 15 days", "RNOR in 2 weeks": a bare duration at the END of the text. Only the end, so a topic such as
# "30 days of fitness" is left alone.
_BARE_RE = re.compile(
    rf"(?:\b(?:in|within|for|over)\s+(?:the\s+)?)?(?:\b(?:last|past)\s+)?({_COUNT})\s*(day|week|month)s?\s*$", re.IGNORECASE
)
_KEYWORD_RE = re.compile(r"\b(today|yesterday|this\s+week|this\s+month)\b", re.IGNORECASE)
_KEYWORD_DAYS = {"today": 1, "yesterday": 2, "this week": 7, "this month": 30}


def _to_count(raw: str | None) -> int:
    if raw is None:
        return 1
    return int(raw) if raw.isdigit() else _NUMBER_WORDS[raw.lower()]


def parse_window(text: str) -> tuple[str, int, bool]:
    """Return (topic without the date phrase, days, capped).

    "budgeting app last 2 weeks" -> ("budgeting app", 14, False). No phrase means DEFAULT_DAYS.
    Also understands "RNOR 15 days", "RNOR in 2 weeks", "RNOR today" and "RNOR this week".
    """
    match = _WINDOW_RE.search(text)
    if match:
        days = max(1, _to_count(match.group(1))) * _UNIT_DAYS[match.group(2).lower()]
    else:
        match = _BARE_RE.search(text)
        if match:
            days = max(1, _to_count(match.group(1))) * _UNIT_DAYS[match.group(2).lower()]
        else:
            match = _KEYWORD_RE.search(text)
            if not match:
                return text.strip(), DEFAULT_DAYS, False
            days = _KEYWORD_DAYS[re.sub(r"\s+", " ", match.group(1).lower())]
    topic = (text[: match.start()] + " " + text[match.end():]).strip(" ,.-")
    capped = days > MAX_DAYS
    return topic or text.strip(), min(days, MAX_DAYS), capped


def _parse_exa_text(text: str) -> list[dict]:
    """Split Exa's `Title: / URL: / Published: / Author:` blocks into items."""
    items = []
    for block in re.split(r"\n(?=Title: )", "\n" + text.strip()):
        block = block.strip()
        if not block.startswith("Title:"):
            continue
        fields: dict[str, str] = {}
        body: list[str] = []
        lines = block.splitlines()
        i = 0
        while i < len(lines):
            m = _HEADER_RE.match(lines[i])
            if not m:
                break
            fields[m.group(1).lower()] = m.group(2).strip()
            i += 1
        body = [ln for ln in lines[i:] if ln.strip() not in ("Highlights:", "Text:")]
        na = lambda v: None if v in (None, "", "N/A") else v  # noqa: E731
        items.append(
            {
                "title": na(fields.get("title")),
                "url": na(fields.get("url")),
                "author": na(fields.get("author")),
                "published_at": na(fields.get("published")),
                "text": "\n".join(body).strip(),
            }
        )
    return items


BUSY_MESSAGE = "The search service is busy right now. Please try again in a minute."


def _exa_search(query: str, limit: int = FETCH_LIMIT) -> list[dict]:
    """One search call. Retries once if the free service says it is busy (it rate-limits bursts)."""
    try:
        return _exa_search_once(query, limit)
    except RuntimeError as exc:
        if str(exc) != BUSY_MESSAGE:
            raise
        time.sleep(1.5)
        return _exa_search_once(query, limit)


def _exa_search_once(query: str, limit: int) -> list[dict]:
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "web_search_exa", "arguments": {"query": query, "objective": query, "numResults": limit}},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        EXA_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as resp:
            raw = resp.read(5_000_000).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise RuntimeError(BUSY_MESSAGE)
        raise RuntimeError(f"The search service returned an error ({exc.code}).")
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RuntimeError("Could not reach the search service. Please try again.")
    payloads = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")] or [raw]
    try:
        msg = json.loads(payloads[-1])
    except json.JSONDecodeError:
        raise RuntimeError("The search service sent an unreadable answer.")
    if "error" in msg:
        text = str(msg["error"].get("message", msg["error"]))
        if re.search(r"429|rate[ _-]?limit|too many requests|quota", text, re.IGNORECASE):
            raise RuntimeError(BUSY_MESSAGE)
        raise RuntimeError(f"The search service reported an error: {text[:200]}")
    result = msg.get("result") or {}
    text = "\n".join(c.get("text", "") for c in result.get("content", []) if c.get("type") == "text")
    if result.get("isError"):
        raise RuntimeError(f"The search service reported an error: {text[:200]}")
    return _parse_exa_text(text)


def _clean(text: str, title: str = "", limit: int = 240) -> str:
    text = _POST_HEADER_RE.sub(" ", _BYLINE_RE.sub(" ", text or ""))
    text = re.sub(r"\*\*|__|`|^>+\s?|^#+\s*", "", text, flags=re.MULTILINE)
    if title and text.lstrip().startswith(title):
        text = text.lstrip()[len(title):]
    text = re.sub(r"^\s*Posted by\s+u/\S+\s*", "", text)
    text = re.sub(r"\n\s*(\.\.\.|---)\s*\n", "\n", text)
    text = re.sub(r"\s+", " ", text).strip(" .-\u2026")
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# SEARCH_ENGINE=agent-reach (default) uses the open-source Agent Reach CLI and falls back to calling Exa
# directly if Agent Reach is missing or fails on the host. SEARCH_ENGINE=exa skips Agent Reach.
def _engine_setting() -> str:
    return os.environ.get("SEARCH_ENGINE", "agent-reach").strip().lower()


def active_engine() -> str:
    return agentreach.LABEL if _engine_setting() != "exa" and agentreach.available() else "Exa"


def _run_query(query: str) -> tuple[list[dict], str]:
    """Search once. Returns (items, engine label that produced them)."""
    if _engine_setting() != "exa" and agentreach.available():
        try:
            return agentreach.search_reddit(query, FETCH_LIMIT), agentreach.LABEL
        except Exception:
            pass  # fall back to calling the search service directly
    return _exa_search(query), "Exa"


def _month_labels(days: int) -> list[str]:
    """"November 2026", "October 2026", ... for each month the window touches (at most 3)."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    y, m = now.year, now.month
    labels = []
    while True:
        labels.append(datetime(y, m, 1).strftime("%B %Y"))
        if (y, m) <= (cutoff.year, cutoff.month) or len(labels) >= 3:
            return labels
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)


def _fix_text(value: str | None) -> str | None:
    """Repair text that was UTF-8 but got decoded as Windows-1252 somewhere upstream ("fÃ¸r", "â€“")."""
    if not value or not re.search(r"[\u00c2\u00c3\u00e2][\u0080-\u00ff\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u0192\u02c6\u02dc\u2013-\u203a\u20ac\u2122]", value):
        return value
    for codec in ("cp1252", "latin-1"):
        try:
            return value.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return value


def search_reddit(topic: str, days: int) -> dict:
    """Search Reddit for a topic and keep posts from the last `days` days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    ctx = glossary.context_for(topic)
    # Exa has no date filter and ranks by relevance, so we ask once per month the window covers
    # (naming the month pulls in recent posts) and filter by published date afterwards.
    # Known terms (RNOR, NRE, ...) get context words so the search finds the right topic.
    queries = [" ".join(p for p in (topic, ctx.extra_words, label, "site:reddit.com") if p) for label in _month_labels(days)]

    def attempt(q: str):
        try:
            items, engine = _run_query(q)
            return items, None, engine
        except Exception as exc:  # keep going if one query fails
            return [], str(exc), None

    def fresh() -> tuple[list[dict], str]:
        """Current posts straight from Reddit's feed, when it lets us in. Never raises.
        The status is "off", "ok" or "unavailable"."""
        if not feed.enabled():
            return [], "off"
        try:
            return feed.search(topic, days), "ok"
        except feed.FeedUnavailable:
            return [], "unavailable"

    with ThreadPoolExecutor(max_workers=len(queries) + 1) as pool:
        fresh_future = pool.submit(fresh)
        outcomes = list(pool.map(attempt, queries))
        fresh_items, fresh_status = fresh_future.result()
    seen: dict[str, dict] = {}
    for item in fresh_items:  # first, so the current copy of a post wins when both sources return it
        url = item.get("url")
        if url and _REDDIT_URL_RE.match(url):
            seen.setdefault(url, item)
    errors = [err for _, err, _ in outcomes if err]
    engines = sorted({e for _, _, e in outcomes if e} | ({"Reddit feed"} if fresh_items else set()))
    for items, err, _ in outcomes:
        if err:
            continue
        for item in items:
            url = item.get("url")
            if url and _REDDIT_URL_RE.match(url) and url not in seen:
                seen[url] = item
    if not seen and errors:
        raise RuntimeError(errors[0])

    dates = [d for d in (_parse_date(i.get("published_at")) for i in seen.values()) if d]
    newest_seen = max(dates).isoformat() if dates else None
    results = []
    for url, item in seen.items():
        posted = _parse_date(item.get("published_at"))
        if posted is None or posted < cutoff:
            continue
        title = (_fix_text(item.get("title")) or "").strip() or url
        text = _fix_text(item.get("text")) or ""
        if not ctx.keeps(f"{title} {text}"):  # e.g. RNOR must not match "Telenor" or a game character called Noor
            continue
        subreddit = None
        m = re.search(r"\s:\s(r/\w+)\s*$", title)
        if m:
            title = title[: m.start()].strip()
        m = _SUBREDDIT_RE.search(url)
        if m:
            subreddit = f"r/{m.group(1)}"
        results.append(
            {
                "title": title,
                "url": url,
                "source": "Reddit",
                "subreddit": subreddit,
                "author": item.get("author"),
                "published_at": posted.isoformat(),
                "summary": _clean(text, title),
                "_full": text[:1500],
            }
        )
    results.sort(key=lambda r: r["published_at"], reverse=True)
    return {
        "results": results,
        "checked": len(seen),
        "days": days,
        "interpreted": ctx.interpreted(),
        "newest_seen": newest_seen,
        "engine": " + ".join(engines) if engines else None,
        "fresh": fresh_status,
    }


# label shown in the UI -> search function. More sources (X, ...) plug in here.
SOURCES = {"Reddit": search_reddit}


def search_all(topic: str, days: int) -> dict:
    """Search every source in parallel. One failing source does not fail the others."""
    pool = ThreadPoolExecutor(max_workers=max(1, len(SOURCES)))
    futures = {name: pool.submit(fn, topic, days) for name, fn in SOURCES.items()}
    pool.shutdown(wait=False)  # do not block on a source that blows its deadline
    results: list[dict] = []
    checked = 0
    interpreted: dict[str, str] = {}
    newest: list[str] = []
    engines: list[str] = []
    fresh_states: list[str] = []
    errors: dict[str, str] = {}
    deadline = time.monotonic() + SOURCE_DEADLINE
    for name, fut in futures.items():
        try:
            found = fut.result(timeout=max(0.1, deadline - time.monotonic()))
        except FutureTimeout:
            errors[name] = f"too slow right now (gave up after {SOURCE_DEADLINE}s)"
            continue
        except Exception as exc:
            errors[name] = str(exc)
            continue
        results.extend(found["results"])
        checked += found["checked"]
        if found.get("newest_seen"):
            newest.append(found["newest_seen"])
        if found.get("engine"):
            engines.append(found["engine"])
        fresh_states.append(found.get("fresh", "off"))
        for item in found.get("interpreted", []):
            interpreted.setdefault(item["term"], item["meaning"])
    results.sort(key=lambda r: r["published_at"], reverse=True)
    return {
        "results": results,
        "checked": checked,
        "days": days,
        "errors": errors,
        "interpreted": [{"term": t, "meaning": m} for t, m in interpreted.items()],
        "newest_seen": max(newest) if newest else None,
        "engine": " + ".join(dict.fromkeys(engines)) if engines else None,
        "fresh": "unavailable" if "unavailable" in fresh_states else ("ok" if "ok" in fresh_states else "off"),
    }
