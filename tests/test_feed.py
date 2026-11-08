import time
from datetime import datetime, timezone

import pytest

from _lib import feed, search

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <author><name>/u/someone</name></author>
  <content type="html">&lt;!-- SC_OFF --&gt;&lt;div class="md"&gt;&lt;p&gt;My RNOR window &amp;amp; FEMA question&lt;/p&gt;&lt;/div&gt;&lt;!-- SC_ON --&gt; submitted by &lt;a href="/user/someone"&gt; /u/someone &lt;/a&gt; [link] [comments]</content>
  <id>t3_abc123</id>
  <link href="https://www.reddit.com/r/IndiaTax/comments/abc123/rnor_question/" />
  <updated>2026-11-08T03:43:02+00:00</updated>
  <published>2026-11-08T03:40:00+00:00</published>
  <title>RNOR &amp; residency question</title>
 </entry>
 <entry>
  <author><name>/u/mod</name></author>
  <content type="html">Community for people returning to India</content>
  <id>t5_xyz</id>
  <link href="https://www.reddit.com/r/returnToIndia/" />
  <updated>2020-05-04T17:28:00+00:00</updated>
  <title>returnToIndia</title>
 </entry>
</feed>"""


def test_parse_keeps_posts_only_and_cleans_text():
    items = feed.parse(ATOM)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "RNOR & residency question"
    assert item["url"] == "https://www.reddit.com/r/IndiaTax/comments/abc123/rnor_question/"
    assert item["author"] == "u/someone"
    assert item["published_at"] == "2026-11-08T03:40:00+00:00"
    assert item["text"] == "My RNOR window & FEMA question"


def test_time_filter_matches_the_window():
    assert [feed._time_filter(d) for d in (1, 2, 7, 8, 30, 31, 90)] == ["day", "week", "week", "month", "month", "year", "year"]


def test_rate_limit_triggers_a_cooldown_and_skips_reddit(monkeypatch):
    import urllib.error

    calls = []

    class Headers(dict):
        def get(self, k, d=None):
            return dict.get(self, k.lower(), d)

    def blocked(req, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", Headers({"x-ratelimit-reset": "29"}), None)

    monkeypatch.setattr(feed, "_blocked_until", 0.0)
    monkeypatch.setattr(feed.urllib.request, "urlopen", blocked)
    with pytest.raises(feed.FeedUnavailable, match="429"):
        feed.search("rnor", 7)
    with pytest.raises(feed.FeedUnavailable, match="cooling"):
        feed.search("rnor", 7)  # no second request while Reddit asked us to wait
    assert len(calls) == 1
    assert feed._blocked_until - time.monotonic() <= 29.5
    monkeypatch.setattr(feed, "_blocked_until", 0.0)


def test_feed_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "off")
    assert not feed.enabled()
    monkeypatch.setenv("FRESH_FEED", "on")
    assert feed.enabled()


def _post(slug, title, date):
    return {"url": f"https://www.reddit.com/r/IndiaTax/comments/{slug}/x/", "title": title, "text": "", "published_at": date, "author": "u/a"}


def test_search_merges_fresh_feed_with_search_results_and_survives_feed_failure(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [_post("old1", "RNOR old but in window", now)])
    monkeypatch.setattr(feed, "search", lambda q, d: [_post("new1", "RNOR brand new post", now), _post("old1", "RNOR old but in window", now)])
    out = search.search_reddit("RNOR", 7)
    assert sorted(r["title"] for r in out["results"]) == ["RNOR brand new post", "RNOR old but in window"]  # deduped
    assert "Reddit feed" in out["engine"]

    def down(q, d):
        raise feed.FeedUnavailable("HTTP 429")

    monkeypatch.setattr(feed, "search", down)
    out = search.search_reddit("RNOR", 7)
    assert [r["title"] for r in out["results"]] == ["RNOR old but in window"]  # still works without the feed
    assert "Reddit feed" not in (out["engine"] or "")


def test_glossary_filter_applies_to_feed_results_too(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [])
    monkeypatch.setattr(feed, "search", lambda q, d: [_post("a", "Kan telenor fikse dette?", now), _post("b", "RNOR status after returning", now)])
    assert [r["title"] for r in search.search_reddit("RNOR", 7)["results"]] == ["RNOR status after returning"]


def test_feed_xml_with_entity_definitions_is_refused():
    evil = '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaa">]><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    with pytest.raises(feed.ET.ParseError):
        feed.parse(evil)


def test_search_reports_whether_the_feed_answered(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [])
    monkeypatch.setattr(feed, "search", lambda q, d: [_post("n", "RNOR news", now)])
    assert search.search_reddit("RNOR", 7)["fresh"] == "ok"

    def down(q, d):
        raise feed.FeedUnavailable("HTTP 429")

    monkeypatch.setattr(feed, "search", down)
    assert search.search_reddit("RNOR", 7)["fresh"] == "unavailable"
    monkeypatch.setenv("FRESH_FEED", "off")
    assert search.search_reddit("RNOR", 7)["fresh"] == "off"
    assert search.search_all("RNOR", 7)["fresh"] == "off"
