import time
from datetime import datetime, timedelta, timezone

import pytest

from _lib import search
from _lib.search import DEFAULT_DAYS, MAX_DAYS, parse_window


@pytest.mark.parametrize(
    "text, topic, days, capped",
    [
        ("budgeting app last 2 weeks", "budgeting app", 14, False),
        ("budgeting app", "budgeting app", DEFAULT_DAYS, False),
        ("budgeting apps in the last 7 days", "budgeting apps", 7, False),
        ("past week budgeting apps", "budgeting apps", 7, False),
        ("last month ynab opinions", "ynab opinions", 30, False),
        ("ynab last three weeks", "ynab", 21, False),
        ("ynab last 45 days", "ynab", 45, False),
        ("ynab last 6 months", "ynab", MAX_DAYS, True),
        ("ynab last 200 days", "ynab", MAX_DAYS, True),
    ],
)
def test_parse_window(text, topic, days, capped):
    assert parse_window(text) == (topic, days, capped)


def test_parse_window_only_date_phrase_keeps_text_as_topic():
    topic, days, _ = parse_window("last 7 days")
    assert topic == "last 7 days" and days == 7


def test_parse_window_zero_count_is_at_least_one_day():
    assert parse_window("ynab last 0 days")[1] == 1


EXA_TEXT = """Title: What is the best free budgeting app? : r/SavingMoney
URL: https://www.reddit.com/r/SavingMoney/comments/1st8lnt/what_is_the_best_free_budgeting_app/
Published: 2026-04-23T04:34:36.000Z
Author: u/Tasty
Highlights:
Excellent for budgets

---

Title: Another post
URL: https://www.reddit.com/r/ynab/comments/abc/another_post/
Published: N/A
Author: N/A
Text:
body text here
"""


def test_parse_exa_text():
    items = search._parse_exa_text(EXA_TEXT)
    assert [i["url"].split("/")[4] for i in items] == ["SavingMoney", "ynab"]
    assert items[0]["author"] == "u/Tasty" and items[0]["published_at"].startswith("2026-04-23")
    assert items[1]["published_at"] is None and items[1]["author"] is None
    assert items[1]["text"] == "body text here"


def _item(url, age_days, title="Post : r/test", text="body"):
    posted = datetime.now(timezone.utc) - timedelta(days=age_days)
    return {"url": url, "title": title, "text": text, "author": "u/x", "published_at": posted.isoformat()}


def _reddit(slug):
    return f"https://www.reddit.com/r/test/comments/{slug}/post/"


def test_search_filters_old_posts_dedupes_and_sorts(monkeypatch):
    items = [_item(_reddit("a"), 2), _item(_reddit("b"), 40), _item(_reddit("c"), 1), _item(_reddit("a"), 2)]
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: items)
    out = search.search_reddit("topic", 7)
    assert [r["url"] for r in out["results"]] == [_reddit("c"), _reddit("a")]
    assert out["checked"] == 3
    assert out["results"][0]["subreddit"] == "r/test"
    assert out["results"][0]["title"] == "Post"


def test_search_ignores_non_reddit_and_undated_posts(monkeypatch):
    items = [
        {"url": "https://example.com/x", "title": "t", "text": "", "published_at": datetime.now(timezone.utc).isoformat()},
        {"url": _reddit("u"), "title": "t", "text": "", "published_at": None},
    ]
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: items)
    assert search.search_reddit("topic", 30)["results"] == []


def test_search_raises_when_every_query_fails(monkeypatch):
    def boom(q, *a):
        raise RuntimeError("service down")

    monkeypatch.setattr(search, "_exa_search", boom)
    with pytest.raises(RuntimeError, match="service down"):
        search.search_reddit("topic", 7)


def test_search_survives_one_failed_query(monkeypatch):
    calls = []

    def flaky(q, *a):
        calls.append(q)
        if len(calls) == 1 and "2026" not in q:
            raise RuntimeError("one blip")
        return [_item(_reddit("ok"), 1)]

    monkeypatch.setattr(search, "_exa_search", flaky)
    assert len(search.search_reddit("topic", 7)["results"]) == 1


def test_clean_removes_comment_bylines_markdown_and_truncates():
    raw = "**u/some_user** � 17 points � September 25, 2026 at 5:18 PM UTC\n> Use Actual Budget. It is free."
    assert search._clean(raw) == "Use Actual Budget. It is free"
    assert len(search._clean("word " * 200)) <= 243


def test_clean_removes_post_header_line():
    raw = "in r/macapps · 39 points · 28 comments · September 30, 2026 at 5:59 PM UTC The real post text"
    assert search._clean(raw) == "The real post text"


def test_parse_date_handles_iso_and_garbage():
    assert search._parse_date("2026-01-02T03:04:05.000Z").day == 2
    assert search._parse_date("2026-01-02").tzinfo is not None
    assert search._parse_date("garbage") is None
    assert search._parse_date(None) is None


def test_search_all_merges_and_survives_one_failing_source(monkeypatch):
    def ok(topic, days):
        return {"results": [{"published_at": "2026-01-02T00:00:00+00:00", "url": "r1"}], "checked": 4, "days": days}

    def bad(topic, days):
        raise RuntimeError("down")

    monkeypatch.setattr(search, "SOURCES", {"Reddit": ok, "Other": bad})
    out = search.search_all("t", 7)
    assert [r["url"] for r in out["results"]] == ["r1"]
    assert out["errors"] == {"Other": "down"} and out["checked"] == 4


def test_search_all_sorts_across_sources(monkeypatch):
    def make(date, url):
        return lambda topic, days: {"results": [{"published_at": date, "url": url}], "checked": 1, "days": days}

    monkeypatch.setattr(search, "SOURCES", {"A": make("2026-01-01T00:00:00+00:00", "old"), "B": make("2026-01-05T00:00:00+00:00", "new")})
    assert [r["url"] for r in search.search_all("t", 7)["results"]] == ["new", "old"]


def test_search_all_gives_up_on_a_slow_source(monkeypatch):
    monkeypatch.setattr(search, "SOURCE_DEADLINE", 0.5)

    def slow(topic, days):
        time.sleep(3)
        return {"results": [], "checked": 0, "days": days}

    def fast(topic, days):
        return {"results": [{"published_at": "2026-01-02T00:00:00+00:00", "url": "r"}], "checked": 1, "days": days}

    monkeypatch.setattr(search, "SOURCES", {"Slow": slow, "Fast": fast})
    started = time.monotonic()
    out = search.search_all("t", 7)
    assert time.monotonic() - started < 2
    assert [r["url"] for r in out["results"]] == ["r"]
    assert "too slow" in out["errors"]["Slow"]


def test_only_https_reddit_post_links_are_accepted(monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    bad = ["javascript:alert(1)//reddit.com/r/x/", "http://www.reddit.com/r/x/comments/1/a/", "https://evil.com/?u=reddit.com/r/x/"]
    items = [{"url": u, "title": "t", "text": "", "published_at": now} for u in bad]
    items.append({"url": "https://old.reddit.com/r/x/comments/2/b/", "title": "ok", "text": "", "published_at": now})
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: items)
    assert [r["url"] for r in search.search_reddit("t", 7)["results"]] == ["https://old.reddit.com/r/x/comments/2/b/"]


@pytest.mark.parametrize(
    "text, topic, days, capped",
    [
        ("RNOR 15 days", "RNOR", 15, False),
        ("RNOR 2 weeks", "RNOR", 14, False),
        ("RNOR in 10 days", "RNOR", 10, False),
        ("RNOR for the last 3 days", "RNOR", 3, False),
        ("RNOR two weeks", "RNOR", 14, False),
        ("RNOR 4 months", "RNOR", MAX_DAYS, True),
        ("RNOR today", "RNOR", 1, False),
        ("RNOR this week", "RNOR", 7, False),
        ("RNOR this month", "RNOR", 30, False),
        ("RNOR yesterday", "RNOR", 2, False),
        ("30 days of fitness", "30 days of fitness", DEFAULT_DAYS, False),
        ("best 7 days itinerary tokyo", "best 7 days itinerary tokyo", DEFAULT_DAYS, False),
        ("RNOR last 7 days", "RNOR", 7, False),
    ],
)
def test_parse_window_bare_durations_and_keywords(text, topic, days, capped):
    assert parse_window(text) == (topic, days, capped)
