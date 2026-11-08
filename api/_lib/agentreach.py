"""Adapter for Agent Reach, an open-source (MIT) tool that gives AI agents web access through one CLI
with JSON output: https://github.com/Panniantong/Agent-Reach

We call its Reddit search (public web route, no login) and turn the JSON into the plain items the rest
of the app uses. It runs as a subprocess of the same Python, so it works on a laptop and on a host that
installed it from requirements.txt.
"""
import importlib.util
import json
import os
import subprocess
import sys

TIMEOUT = 40
LABEL = "Agent Reach"
BUSY_MESSAGE = "The search service is busy right now. Please try again in a minute."


def available() -> bool:
    return importlib.util.find_spec("agent_reach") is not None


def _env() -> dict:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if os.environ.get("VERCEL"):  # hosts like Vercel only allow writing under /tmp
        env["HOME"] = env["USERPROFILE"] = env["XDG_DATA_HOME"] = env["XDG_CACHE_HOME"] = "/tmp"
    return env


def search_reddit(query: str, limit: int) -> list[dict]:
    cmd = [
        sys.executable, "-c", "from agent_reach.cli import main; main()",
        "reddit", "search", "--limit", str(limit), "--backend", "public-web",
        "--", query,  # "--" ends option parsing, so text starting with "-" is never read as an option
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT, env=_env())
    except subprocess.TimeoutExpired:
        raise RuntimeError("The search took too long. Please try again.")
    try:
        data = json.loads(proc.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise RuntimeError("The search tool sent an unreadable answer.")
    if not data.get("ok"):
        code = (data.get("error") or {}).get("code", "")
        if code == "RATE_LIMITED":
            raise RuntimeError(BUSY_MESSAGE)
        raise RuntimeError(f"The search tool reported a problem ({code or 'error'}).")
    return [
        {
            "title": item.get("title"),
            "url": item.get("url"),
            "author": item.get("author"),
            "published_at": item.get("published_at"),
            "text": item.get("text") or "",
        }
        for item in data.get("items", [])
    ]
