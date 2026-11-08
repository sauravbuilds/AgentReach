import json
import time
import urllib.error
from datetime import datetime, timezone

import pytest

from _lib import feed, glossary, search

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


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    monkeypatch.setattr(feed, "_blocked_until", 0.0)
    monkeypatch.setattr(search, "_feed_cache_get", None)
    yield
    monkeypatch.setattr(feed, "_blocked_until", 0.0)


def test_parse_keeps_posts_only_and_cleans_text():
    items = feed.parse(ATOM)
    assert len(items) == 1
    item = items[0]
    assert item["title"] == "RNOR & residency question"
    assert item["url"] == "https://www.reddit.com/r/IndiaTax/comments/abc123/rnor_question/"
    assert item["author"] == "u/someone"
    assert item["published_at"] == "2026-11-08T03:40:00+00:00"
    assert item["text"] == "My RNOR window & FEMA question"


def test_inline_formatting_does_not_split_a_word():
    assert feed._text_of('<div class="md"><p>Elected First Black Female Gove<strong>rnor</strong> today</p></div>') == (
        "Elected First Black Female Governor today"
    )
    assert feed._text_of('<div class="md"><p>one</p><p>two</p></div>') == "one two"  # block tags still separate


def test_feed_xml_with_entity_definitions_is_refused():
    evil = '<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY a "aaaa">]><feed xmlns="http://www.w3.org/2005/Atom"></feed>'
    with pytest.raises(feed.ET.ParseError):
        feed.parse(evil)


class Headers(dict):
    def get(self, k, d=None):
        return dict.get(self, k.lower(), d)


def test_rate_limit_triggers_a_cooldown_and_skips_reddit(monkeypatch):
    calls = []

    def blocked(req, timeout=None):
        calls.append(1)
        raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", Headers({"x-ratelimit-reset": "29"}), None)

    monkeypatch.setattr(feed.urllib.request, "urlopen", blocked)
    with pytest.raises(feed.FeedUnavailable, match="429"):
        feed.fetch("rnor")
    with pytest.raises(feed.FeedUnavailable, match="cooling"):
        feed.fetch("rnor")  # no second request while Reddit asked us to wait
    assert len(calls) == 1
    assert feed._blocked_until - time.monotonic() <= 29.5


def test_feed_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "off")
    assert not feed.enabled()
    monkeypatch.setenv("FRESH_FEED", "on")
    assert feed.enabled()


def _post(slug, title, date, text="", sub="IndiaTax"):
    return {"url": f"https://www.reddit.com/r/{sub}/comments/{slug}/x/", "title": title, "text": text, "published_at": date, "author": "u/a"}


NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731


def test_feed_plan_shares_one_request_between_wordings_of_the_same_term():
    for text, must in (("rnor", []), ("rnor status", ["status"]), ("rnor tax", ["tax"]), ("what is rnor", [])):
        base, extra = search._feed_plan(text, glossary.context_for(text))
        assert base == "RNOR" and extra == must, text
    base, extra = search._feed_plan("people comparing budgeting apps", glossary.context_for("people comparing budgeting apps"))
    assert base == "people comparing budgeting apps" and extra == []
    base, _ = search._feed_plan("one two three four five six", glossary.context_for("one two three four five six"))
    assert base == "one two three four"  # Reddit ANDs words, so long queries are trimmed


def test_search_merges_fresh_feed_with_search_results_and_survives_feed_failure(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [_post("old1", "RNOR old but in window", NOW())])
    monkeypatch.setattr(feed, "fetch", lambda q: [_post("new1", "RNOR brand new post", NOW()), _post("old1", "RNOR old but in window", NOW())])
    out = search.search_reddit("RNOR", 7)
    assert sorted(r["title"] for r in out["results"]) == ["RNOR brand new post", "RNOR old but in window"]  # deduped
    assert "Reddit feed" in out["engine"] and out["fresh"] == "ok"

    def down(q):
        raise feed.FeedUnavailable("HTTP 429")

    monkeypatch.setattr(feed, "fetch", down)
    out = search.search_reddit("RNOR", 7)
    assert [r["title"] for r in out["results"]] == ["RNOR old but in window"]
    assert "Reddit feed" not in (out["engine"] or "") and out["fresh"] == "unavailable"


def test_glossary_filter_applies_to_feed_results_too(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [])
    monkeypatch.setattr(feed, "fetch", lambda q: [_post("a", "Kan telenor fikse dette?", NOW()), _post("b", "RNOR status after returning", NOW())])
    assert [r["title"] for r in search.search_reddit("RNOR", 7)["results"]] == ["RNOR status after returning"]


def test_word_split_by_markup_is_not_a_match(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [])
    posts = [
        _post("a", "World reacts to midterm elections", NOW(), text="Elected First Black Female Gove**rnor **"),
        _post("b", "My RNOR years", NOW(), text="I am **RNOR** now"),  # real mention in bold still counts
    ]
    monkeypatch.setattr(feed, "fetch", lambda q: posts)
    assert [r["title"] for r in search.search_reddit("RNOR", 7)["results"]] == ["My RNOR years"]


def test_extra_words_are_checked_against_the_shared_feed_copy(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [])
    posts = [_post("a", "RNOR and tax planning", NOW()), _post("b", "RNOR window explained", NOW(), text="nothing else")]
    asked = []
    monkeypatch.setattr(feed, "fetch", lambda q: asked.append(q) or posts)
    assert [r["title"] for r in search.search_reddit("rnor taxes", 7)["results"]] == ["RNOR and tax planning"]  # "taxes" finds "tax"
    assert asked == ["RNOR"]


def test_saved_feed_copy_is_reused_and_used_when_reddit_refuses(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [])
    saved = {}
    ages = []

    def get(key, max_age):
        ages.append(max_age)
        return saved.get(key) if (max_age >= search.FEED_STALE_TTL or saved.get("fresh") == "yes") else None

    monkeypatch.setattr(search, "_feed_cache_get", get)
    monkeypatch.setattr(feed, "fetch", lambda q: [_post("n", "RNOR news", NOW())])
    out = search.search_reddit("RNOR", 7)
    assert out["fresh"] == "ok" and len(out["results"]) == 1
    (key, payload), = out["cache_writes"]  # the caller stores it; searching never writes
    assert key == "feed|rnor" and json.loads(payload)[0]["title"] == "RNOR news"

    saved[key] = payload  # now only an older copy exists, and Reddit refuses
    def down(q):
        raise feed.FeedUnavailable("HTTP 429")
    monkeypatch.setattr(feed, "fetch", down)
    out = search.search_reddit("RNOR", 7)
    assert out["fresh"] == "stale" and "saved copy" in out["engine"] and len(out["results"]) == 1

    saved["fresh"] = "yes"  # a recent copy: Reddit is not asked at all
    monkeypatch.setattr(feed, "fetch", lambda q: pytest.fail("must not ask Reddit"))
    out = search.search_reddit("RNOR", 7)
    assert out["fresh"] == "ok" and out["cache_writes"] == []


def test_feed_storage_errors_never_break_search(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [_post("e", "RNOR from search", NOW())])

    def broken(key, max_age):
        raise RuntimeError("database down")

    monkeypatch.setattr(search, "_feed_cache_get", broken)
    out = search.search_reddit("RNOR", 7)
    assert [r["title"] for r in out["results"]] == ["RNOR from search"] and out["fresh"] == "unavailable"


def test_search_all_passes_feed_saves_up(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [])
    monkeypatch.setattr(feed, "fetch", lambda q: [_post("n", "RNOR news", NOW())])
    out = search.search_all("RNOR", 7)
    assert out["fresh"] == "ok" and [w[0] for w in out["cache_writes"]] == ["feed|rnor"]


def test_search_reports_whether_the_feed_answered(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "on")
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [])
    monkeypatch.setattr(feed, "fetch", lambda q: [_post("n", "RNOR news", NOW())])
    assert search.search_reddit("RNOR", 7)["fresh"] == "ok"

    def down(q):
        raise feed.FeedUnavailable("HTTP 429")

    monkeypatch.setattr(feed, "fetch", down)
    assert search.search_reddit("RNOR", 7)["fresh"] == "unavailable"
    monkeypatch.setenv("FRESH_FEED", "off")
    assert search.search_reddit("RNOR", 7)["fresh"] == "off"
    assert search.search_all("RNOR", 7)["fresh"] == "off"


def test_crossposts_collapse_into_one_result_with_also_in(monkeypatch):
    monkeypatch.setenv("FRESH_FEED", "off")
    t = "Pre-arrival checklist if you're returning to India from USA"
    posts = [
        _post("c1", t, "2026-11-05T12:20:00+00:00", text="RNOR years", sub="NrisTaxproblems"),
        _post("c2", t, "2026-11-05T16:50:00+00:00", text="RNOR years", sub="IndiaInvestments"),
        _post("c3", t, "2026-11-02T10:00:00+00:00", text="RNOR years", sub="nri"),
        _post("d1", "RNOR", "2026-11-04T10:00:00+00:00", text="RNOR", sub="a"),
        _post("d2", "RNOR", "2026-11-03T10:00:00+00:00", text="RNOR", sub="b"),  # too short a title to be sure it is a cross-post
    ]
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: posts)
    out = search.search_reddit("RNOR", 90)["results"]
    top = [r for r in out if r["title"] == t]
    assert len(top) == 1
    assert top[0]["subreddit"] == "r/IndiaInvestments"  # the newest copy is shown
    assert sorted(a["subreddit"] for a in top[0]["also_in"]) == ["r/NrisTaxproblems", "r/nri"]
    assert len([r for r in out if r["title"] == "RNOR"]) == 2
