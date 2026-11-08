"""Reddit discussion search through Exa's free hosted search (plain HTTP: no login, no CLI, no key)."""
import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone

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
    r"\b(?:in\s+the\s+|within\s+the\s+|from\s+the\s+|in\s+|within\s+|from\s+)?"
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


def parse_window(text: str) -> tuple[str, int, bool]:
    """Return (topic without the date phrase, days, capped).

    "budgeting app last 2 weeks" -> ("budgeting app", 14, False). No phrase means DEFAULT_DAYS.
    """
    match = _WINDOW_RE.search(text)
    if not match:
        return text.strip(), DEFAULT_DAYS, False
    count_raw, unit = match.group(1), match.group(2).lower()
    if count_raw is None:
        count = 1
    elif count_raw.isdigit():
        count = int(count_raw)
    else:
        count = _NUMBER_WORDS[count_raw.lower()]
    days = max(1, count) * _UNIT_DAYS[unit]
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


def _exa_search(query: str, limit: int = FETCH_LIMIT) -> list[dict]:
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
            raise RuntimeError("The search service is busy right now. Please try again in a minute.")
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
            raise RuntimeError("The search service is busy right now. Please try again in a minute.")
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


def search_reddit(topic: str, days: int) -> dict:
    """Search Reddit for a topic and keep posts from the last `days` days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    year = datetime.now(timezone.utc).year
    # Exa has no date filter and ranks by relevance, so we ask twice (plain and
    # year-biased) and filter by published date afterwards.
    queries = [f"{topic} site:reddit.com", f"{topic} {year} site:reddit.com"]

    def attempt(q: str):
        try:
            return _exa_search(q), None
        except Exception as exc:  # keep going if one query fails
            return [], str(exc)

    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        outcomes = list(pool.map(attempt, queries))
    seen: dict[str, dict] = {}
    errors = [err for _, err in outcomes if err]
    for items, err in outcomes:
        if err:
            continue
        for item in items:
            url = item.get("url")
            if url and _REDDIT_URL_RE.match(url) and url not in seen:
                seen[url] = item
    if not seen and errors:
        raise RuntimeError(errors[0])

    results = []
    for url, item in seen.items():
        posted = _parse_date(item.get("published_at"))
        if posted is None or posted < cutoff:
            continue
        title = (item.get("title") or "").strip() or url
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
                "summary": _clean(item.get("text", ""), title),
                "_full": (item.get("text") or "")[:1500],
            }
        )
    results.sort(key=lambda r: r["published_at"], reverse=True)
    return {"results": results, "checked": len(seen), "days": days}


# label shown in the UI -> search function. More sources (X, ...) plug in here.
SOURCES = {"Reddit": search_reddit}


def search_all(topic: str, days: int) -> dict:
    """Search every source in parallel. One failing source does not fail the others."""
    pool = ThreadPoolExecutor(max_workers=max(1, len(SOURCES)))
    futures = {name: pool.submit(fn, topic, days) for name, fn in SOURCES.items()}
    pool.shutdown(wait=False)  # do not block on a source that blows its deadline
    results: list[dict] = []
    checked = 0
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
    results.sort(key=lambda r: r["published_at"], reverse=True)
    return {"results": results, "checked": checked, "days": days, "errors": errors}
