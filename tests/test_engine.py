import json
import subprocess

import pytest

from _lib import agentreach, search


class Done:
    def __init__(self, payload):
        self.stdout = json.dumps(payload).encode()


def ok(*items):
    return {"ok": True, "items": list(items), "error": None}


def test_adapter_runs_the_cli_and_normalises_items(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return Done(ok({"title": "T", "url": "https://www.reddit.com/r/x/comments/1/t/", "author": "u", "published_at": "2026-01-01", "text": None, "raw": {}}))

    monkeypatch.setattr(agentreach.subprocess, "run", fake_run)
    items = agentreach.search_reddit("rnor site:reddit.com", 50)
    assert items == [{"title": "T", "url": "https://www.reddit.com/r/x/comments/1/t/", "author": "u", "published_at": "2026-01-01", "text": ""}]
    assert "public-web" in seen["cmd"] and "reddit" in seen["cmd"] and "--limit" in seen["cmd"]
    assert seen["cmd"][-2:] == ["--", "rnor site:reddit.com"]  # the search text always comes after the "--" separator


def test_adapter_redirects_state_to_tmp_on_vercel(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert agentreach._env()["HOME"] == "/tmp"
    monkeypatch.delenv("VERCEL")
    assert agentreach._env().get("HOME") == __import__("os").environ.get("HOME")  # untouched elsewhere


def test_adapter_error_messages_are_visitor_safe(monkeypatch):
    monkeypatch.setattr(agentreach.subprocess, "run", lambda *a, **k: Done({"ok": False, "error": {"code": "RATE_LIMITED", "message": "secret internal detail"}}))
    with pytest.raises(RuntimeError, match="busy"):
        agentreach.search_reddit("q", 5)
    monkeypatch.setattr(agentreach.subprocess, "run", lambda *a, **k: Done({"ok": False, "error": {"code": "UPSTREAM_BROKEN", "message": "/home/secret/path"}}))
    with pytest.raises(RuntimeError) as e:
        agentreach.search_reddit("q", 5)
    assert "secret" not in str(e.value)

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)

    monkeypatch.setattr(agentreach.subprocess, "run", slow)
    with pytest.raises(RuntimeError, match="too long"):
        agentreach.search_reddit("q", 5)


def test_default_engine_is_agent_reach_when_installed(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE", raising=False)
    monkeypatch.setattr(agentreach, "available", lambda: True)
    monkeypatch.setattr(agentreach, "search_reddit", lambda q, n: [{"url": "u"}])
    assert search._run_query("q") == ([{"url": "u"}], "Agent Reach")
    assert search.active_engine() == "Agent Reach"


def test_falls_back_to_exa_when_agent_reach_fails_or_is_missing(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE", raising=False)
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [{"url": "exa"}])

    def broken(q, n):
        raise RuntimeError("tool crashed")

    monkeypatch.setattr(agentreach, "available", lambda: True)
    monkeypatch.setattr(agentreach, "search_reddit", broken)
    assert search._run_query("q") == ([{"url": "exa"}], "Exa")
    monkeypatch.setattr(agentreach, "available", lambda: False)
    assert search._run_query("q") == ([{"url": "exa"}], "Exa") and search.active_engine() == "Exa"


def test_exa_only_setting_skips_agent_reach(monkeypatch):
    monkeypatch.setenv("SEARCH_ENGINE", "exa")
    monkeypatch.setattr(agentreach, "available", lambda: True)
    monkeypatch.setattr(agentreach, "search_reddit", lambda q, n: pytest.fail("must not be called"))
    monkeypatch.setattr(search, "_exa_search", lambda q, *a: [])
    assert search._run_query("q") == ([], "Exa")


def test_results_report_which_engine_answered(monkeypatch):
    from datetime import datetime, timezone

    monkeypatch.delenv("SEARCH_ENGINE", raising=False)
    monkeypatch.setattr(agentreach, "available", lambda: True)
    posted = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(agentreach, "search_reddit", lambda q, n: [{"url": "https://www.reddit.com/r/a/comments/1/x/", "title": "x", "text": "", "published_at": posted}])
    out = search.search_reddit("budgeting", 30)
    assert out["engine"] == "Agent Reach" and len(out["results"]) == 1
    assert search.search_all("budgeting", 30)["engine"] == "Agent Reach"
