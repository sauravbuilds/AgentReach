from datetime import datetime, timezone

import pytest

from _lib import glossary, search


def keeps(topic, text):
    return glossary.context_for(topic).keeps(text)


def test_rnor_rejects_lookalikes_from_the_reported_bug():
    for noise in [
        "Kan telenor endelig fikse dette? Har du Telenor fiber?",
        "Why can I still not buy Noor with renown? Rainbow Six",
        "Bane-Nor og Steinberg Stasjon",
        "What are ya'll honest opinions on Ren & Nora individually and together",
        "Alevels college: pick A or B, or neither",
        "Streik rammer Equinor-ansatte",
    ]:
        assert not keeps("RNOR", noise), noise


def test_rnor_keeps_real_discussions_in_any_wording():
    assert keeps("RNOR", "Returning NRI in RNOR status looking for advice from CAs")
    assert keeps("RNOR", "What is Resident but Not Ordinarily Resident status?")
    assert keeps("rnor", "My RNOR window ends next year")


def test_lowercase_input_still_finds_the_term():
    ctx = glossary.context_for("what is rnor")
    assert [e.name for e in ctx.entries] == ["RNOR"]
    assert "returning" in ctx.extra_words


def test_ambiguous_acronyms_also_need_an_india_context():
    assert not keeps("OCI", "Oracle Cloud Infrastructure OCI pricing for compute")
    assert keeps("OCI", "OCI card renewal at the Indian consulate")
    assert not keeps("FEMA", "FEMA flood insurance claim after the hurricane")
    assert keeps("FEMA", "FEMA rules for NRI sending money from India to the US")
    assert not keeps("AIS", "AIS vessel tracking data feed")
    assert keeps("AIS", "AIS and 26AS mismatch before filing my income tax return")


def test_unknown_acronyms_must_appear_as_a_whole_word():
    assert glossary.context_for("visa rules for students").unknown_acronyms == ()  # no acronym: no check
    assert glossary.context_for("XYZQ visa rules").unknown_acronyms == ("XYZQ",)
    ctx = glossary.context_for("HELOC rates")
    assert ctx.unknown_acronyms == ("HELOC",)
    assert ctx.keeps("Is a HELOC worth it right now?")
    assert not ctx.keeps("helocopter parents are ruining everything")


def test_common_capitalised_words_are_not_treated_as_acronyms():
    assert glossary.context_for("pros OR cons of renting").unknown_acronyms == ()
    assert glossary.context_for("budgeting app").filtering is False
    assert glossary.context_for("budgeting app").keeps("anything at all")


def test_entries_without_checks_only_add_context():
    ctx = glossary.context_for("moving back to India")
    assert not ctx.filtering and "RNOR" in ctx.extra_words


def test_several_terms_in_one_query():
    ctx = glossary.context_for("NRE vs NRO account")
    assert {e.name for e in ctx.entries} == {"NRE", "NRO"}
    assert ctx.keeps("NRE account interest is tax free for NRI in India")
    assert ctx.keeps("NRO account rules for Indian rent income")
    assert not ctx.keeps("my NRE sister is a nurse")


def test_every_entry_is_well_formed():
    names = [e.name for e in glossary.ENTRIES]
    assert len(names) == len(set(names))
    for e in glossary.ENTRIES:
        assert e.terms and e.meaning.endswith(".") and e.also
        for t in e.terms:  # every listed term must find its own entry
            assert e in glossary.context_for(f"question about {t} please").entries, (e.name, t)


def test_fix_text_repairs_garbled_utf8_and_leaves_normal_text():
    assert search._fix_text("fÃ¸r") == "før"
    assert search._fix_text("Equinor-ansatte: â€“ 14 timers") == "Equinor-ansatte: – 14 timers"
    assert search._fix_text("Plain text, ₹15 lakh, naïve café") == "Plain text, ₹15 lakh, naïve café"
    assert search._fix_text(None) is None and search._fix_text("") == ""


def test_month_labels_cover_the_window(monkeypatch):
    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 8, tzinfo=timezone.utc)

    monkeypatch.setattr(search, "datetime", Fixed)
    assert search._month_labels(7) == ["November 2026"]
    assert search._month_labels(30) == ["November 2026", "October 2026"]
    assert search._month_labels(90) == ["November 2026", "October 2026", "September 2026"]
    assert search._month_labels(400) == ["November 2026", "October 2026", "September 2026"]  # never more than 3 calls


def test_search_adds_context_words_and_filters_noise(monkeypatch):
    queries = []
    posted = datetime.now(timezone.utc).isoformat()

    def fake(q, *a):
        queries.append(q)
        return [
            {"url": "https://www.reddit.com/r/IndiaTax/comments/1/rnor_status/", "title": "RNOR status after returning", "text": "", "published_at": posted},
            {"url": "https://www.reddit.com/r/norge/comments/2/telenor/", "title": "Kan telenor fikse dette?", "text": "", "published_at": posted},
        ]

    monkeypatch.setattr(search, "_exa_search", fake)
    out = search.search_reddit("RNOR", 30)
    assert all("RNOR" in q and "returning" in q and "site:reddit.com" in q for q in queries)
    assert [r["title"] for r in out["results"]] == ["RNOR status after returning"]
    assert out["interpreted"][0]["term"] == "RNOR"
    assert out["newest_seen"] is not None and out["checked"] == 2


def test_busy_service_is_retried_once(monkeypatch):
    monkeypatch.setattr(search.time, "sleep", lambda s: None)
    calls = []

    def once(q, limit):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(search.BUSY_MESSAGE)
        return ["ok"]

    monkeypatch.setattr(search, "_exa_search_once", once)
    assert search._exa_search("q") == ["ok"] and len(calls) == 2


def test_other_errors_are_not_retried(monkeypatch):
    calls = []

    def once(q, limit):
        calls.append(1)
        raise RuntimeError("Could not reach the search service. Please try again.")

    monkeypatch.setattr(search, "_exa_search_once", once)
    with pytest.raises(RuntimeError):
        search._exa_search("q")
    assert len(calls) == 1
