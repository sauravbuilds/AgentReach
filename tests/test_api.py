import pytest
from fastapi.testclient import TestClient

import index
from _lib import db

ALICE = {"X-Client-Id": "alice-aaaaaaaaaaaaaaaaaaaa"}
BOB = {"X-Client-Id": "bob-bbbbbbbbbbbbbbbbbbbbbb"}


def _result():
    return {"title": "T", "url": "https://www.reddit.com/r/x/comments/1/t/", "source": "Reddit", "summary": "s",
            "published_at": "2026-01-01T00:00:00+00:00", "_full": "x"}


@pytest.fixture()
def client(monkeypatch):
    with db.connect() as c:
        for table in ("messages", "chats", "search_cache", "search_log"):
            c.run(f"DELETE FROM {table}")
    calls = []

    def fake_search(topic, days):
        calls.append((topic, days))
        return {"results": [_result()], "checked": 5, "days": days, "errors": {}}

    monkeypatch.setattr(index, "search_all", fake_search)
    c = TestClient(index.app)
    c.calls = calls
    return c


def _chat(client, who=ALICE):
    return client.post("/api/chats", headers=who).json()["id"]


def _send(client, cid, text, who=ALICE):
    return client.post(f"/api/chats/{cid}/messages", json={"content": text}, headers=who)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["ok"] and "Reddit" in body["sources"]


def test_visitor_id_is_required_and_validated(client):
    assert client.get("/api/chats").status_code == 400
    assert client.get("/api/chats", headers={"X-Client-Id": "short"}).status_code == 400
    assert client.get("/api/chats", headers={"X-Client-Id": "bad id with spaces!!!!!!"}).status_code == 400


def test_visitors_only_see_their_own_chats(client):
    a, b = _chat(client, ALICE), _chat(client, BOB)
    _send(client, a, "ynab")
    assert [c["id"] for c in client.get("/api/chats", headers=ALICE).json()] == [a]
    assert [c["id"] for c in client.get("/api/chats", headers=BOB).json()] == [b]
    assert client.get(f"/api/chats/{a}", headers=BOB).status_code == 404
    assert _send(client, a, "hello", BOB).status_code == 404
    assert client.patch(f"/api/chats/{a}", json={"title": "mine now"}, headers=BOB).status_code == 404
    client.delete(f"/api/chats/{a}", headers=BOB)
    assert client.get(f"/api/chats/{a}", headers=ALICE).status_code == 200  # Bob could not delete it


def test_history_is_saved_and_reloadable(client):
    cid = _chat(client)
    _send(client, cid, "ynab last 7 days")
    chat = client.get(f"/api/chats/{cid}", headers=ALICE).json()
    assert chat["title"] == "ynab last 7 days"
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    assert chat["messages"][1]["meta"]["results"][0]["title"] == "T"


def test_message_validation(client):
    cid = _chat(client)
    assert _send(client, cid, "  ").status_code == 400
    assert _send(client, cid, "a" * (index.MAX_MESSAGE_CHARS + 1)).status_code == 400
    assert _send(client, 9999, "x").status_code == 404


def test_search_result_shape_and_private_field_removed(client):
    cid = _chat(client)
    meta = _send(client, cid, "ynab last 7 days").json()["message"]["meta"]
    assert meta["days"] == 7 and meta["topic"] == "ynab" and meta["checked"] == 5
    assert "_full" not in meta["results"][0]


def test_repeat_search_is_cached_across_visitors(client):
    a, b = _chat(client, ALICE), _chat(client, BOB)
    _send(client, a, "ynab last 7 days", ALICE)
    _send(client, b, "YNAB last 7 days", BOB)
    assert len(client.calls) == 1
    _send(client, a, "ynab last 14 days", ALICE)
    assert len(client.calls) == 2


def test_partial_results_are_not_cached(client, monkeypatch):
    calls = []

    def flaky(topic, days):
        calls.append(1)
        errors = {"Reddit": "too slow"} if len(calls) == 1 else {}
        return {"results": [_result()], "checked": 1, "days": days, "errors": errors}

    monkeypatch.setattr(index, "search_all", flaky)
    cid = _chat(client)
    assert _send(client, cid, "ynab").json()["message"]["meta"]["source_errors"] == {"Reddit": "too slow"}
    assert _send(client, cid, "ynab").json()["message"]["meta"]["source_errors"] == {}
    _send(client, cid, "ynab")
    assert len(calls) == 2


def test_failed_search_is_reported_not_crashed(client, monkeypatch):
    monkeypatch.setattr(index, "search_all", lambda t, d: {"results": [], "checked": 0, "days": d, "errors": {"Reddit": "down"}})
    cid = _chat(client)
    msg = _send(client, cid, "ynab").json()["message"]
    assert "search failed" in msg["content"] and msg["meta"]["error"] == "down"


def test_rate_limit_per_visitor(client, monkeypatch):
    monkeypatch.setattr(index, "LIMIT_PER_MINUTE", 3)
    cid = _chat(client)
    for i in range(3):
        assert _send(client, cid, f"topic {i}").status_code == 200
    blocked = _send(client, cid, "topic 99")
    assert blocked.status_code == 429 and "fast" in blocked.json()["detail"]
    other = _chat(client, BOB)
    assert _send(client, other, "topic 100", BOB).status_code == 200  # a different visitor is unaffected


def test_cache_hits_do_not_count_toward_the_limit(client, monkeypatch):
    monkeypatch.setattr(index, "LIMIT_PER_MINUTE", 2)
    cid = _chat(client)
    for _ in range(6):
        assert _send(client, cid, "same topic").status_code == 200


def test_rename_chat(client):
    cid = _chat(client)
    assert client.patch(f"/api/chats/{cid}", json={"title": "  My topic "}, headers=ALICE).json()["title"] == "My topic"
    assert client.patch(f"/api/chats/{cid}", json={"title": " "}, headers=ALICE).status_code == 400
    assert client.patch("/api/chats/9999", json={"title": "x"}, headers=ALICE).status_code == 404


def test_chats_per_visitor_are_capped(client, monkeypatch):
    monkeypatch.setattr(db, "MAX_CHATS_PER_OWNER", 3)
    for _ in range(3):
        assert client.post("/api/chats", headers=ALICE).status_code == 200
    assert client.post("/api/chats", headers=ALICE).status_code == 429
    assert client.post("/api/chats", headers=BOB).status_code == 200  # other visitors unaffected


def test_internal_errors_are_not_shown_to_visitors(client, monkeypatch):
    def broken(*a, **k):
        raise Exception("password authentication failed for user neondb_owner at ep-secret-host.neon.tech")

    monkeypatch.setattr(index.db, "cache_get", broken)
    cid = _chat(client)
    msg = _send(client, cid, "ynab").json()["message"]
    assert "neon" not in msg["content"] and "neon" not in msg["meta"]["error"]
    assert "Something went wrong" in msg["content"]


PG = "postgresql://user:pw@ep-example.neon.tech/db?sslmode=require"


def test_postgres_connection_string_is_found_under_any_vercel_name(monkeypatch):
    names = ("DATABASE_URL", "POSTGRES_URL", "STORAGE_DATABASE_URL", "STORAGE_POSTGRES_URL", "STORAGE_DATABASE_URL_UNPOOLED", "NEON_DATABASE_URL")
    for n in names:
        monkeypatch.delenv(n, raising=False)
    assert db._pg_env_var() is None and not db.using_postgres()
    monkeypatch.setenv("STORAGE_DATABASE_URL_UNPOOLED", PG + "&unpooled=1")  # unpooled alone is not used
    assert db._pg_env_var() is None
    monkeypatch.setenv("STORAGE_DATABASE_URL", PG)
    assert db._pg_env_var() == "STORAGE_DATABASE_URL" and db._pg_url() == PG
    monkeypatch.setenv("POSTGRES_URL", PG)  # plain names win over prefixed ones
    assert db._pg_env_var() == "POSTGRES_URL"
    monkeypatch.setenv("DATABASE_URL", PG)
    assert db._pg_env_var() == "DATABASE_URL"


def test_non_postgres_values_are_ignored(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "not-a-url")
    monkeypatch.setenv("STORAGE_DATABASE_URL", "mysql://x")
    assert db._pg_env_var() is None


def test_health_reports_the_variable_name_but_never_the_value(client, monkeypatch):
    monkeypatch.setenv("STORAGE_DATABASE_URL", PG)
    monkeypatch.setattr(db, "_pg_url", lambda: "")  # keep the tests on SQLite
    body = client.get("/api/health").json()
    assert "ep-example" not in str(body) and "pw" not in str(body.get("database_variable"))
    assert body["database_variable"] == "STORAGE_DATABASE_URL"
