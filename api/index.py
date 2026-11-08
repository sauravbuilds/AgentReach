import json
import logging
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from _lib import db
from _lib.search import MAX_DAYS, SOURCES, active_engine, parse_window, search_all
from _lib.summarize import summarize

MAX_MESSAGE_CHARS = 500
CACHE_TTL_SECONDS = 900
LIMIT_PER_MINUTE = 6  # searches per visitor per minute
LIMIT_PER_DAY = 120  # searches per visitor per day
LIMIT_IP_PER_MINUTE = 20  # searches per IP address per minute (covers visitors who reset their id)
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{16,64}$")


def _load_env() -> None:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_env()

app = FastAPI(title="Discussion Finder")
_origins = [o for o in os.environ.get("FRONTEND_ORIGIN", "").split(",") if o]
if _origins:
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_methods=["*"], allow_headers=["*"])


def owner_id(x_client_id: str | None = Header(default=None)) -> str:
    if not x_client_id or not _CLIENT_ID_RE.match(x_client_id):
        raise HTTPException(400, "Missing or invalid visitor id. Reload the page and try again.")
    return x_client_id


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def message_row(r: dict) -> dict:
    return {
        "id": r["id"],
        "role": r["role"],
        "content": r["content"],
        "meta": json.loads(r["results_json"]) if r["results_json"] else None,
        "created_at": r["created_at"],
    }


class NewMessage(BaseModel):
    content: str


class RenameChat(BaseModel):
    title: str


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "sources": list(SOURCES),
        "persistent_storage": db.is_persistent(),
        "database": "postgres" if db.using_postgres() else ("sqlite" if db.is_persistent() else "temporary"),
        "search_engine": active_engine(),
        "database_variable": db.postgres_variable_name(),  # the variable's name only, never its value
    }


@app.get("/api/chats")
def list_chats(x_client_id: str | None = Header(default=None)):
    return db.list_chats(owner_id(x_client_id))


@app.post("/api/chats")
def create_chat(x_client_id: str | None = Header(default=None)):
    chat = db.create_chat(owner_id(x_client_id))
    if chat is None:
        raise HTTPException(429, f"You have {db.MAX_CHATS_PER_OWNER} chats. Delete some old ones to start a new one.")
    return chat


@app.get("/api/chats/{chat_id}")
def get_chat(chat_id: int, x_client_id: str | None = Header(default=None)):
    chat = db.get_chat(owner_id(x_client_id), chat_id)
    if not chat:
        raise HTTPException(404, "Chat not found")
    return {**chat, "messages": [message_row(m) for m in chat["messages"]]}


@app.patch("/api/chats/{chat_id}")
def rename_chat(chat_id: int, body: RenameChat, x_client_id: str | None = Header(default=None)):
    title = body.title.strip()[:60]
    if not title:
        raise HTTPException(400, "Title is empty")
    if not db.rename_chat(owner_id(x_client_id), chat_id, title):
        raise HTTPException(404, "Chat not found")
    return {"id": chat_id, "title": title}


@app.delete("/api/chats/{chat_id}")
def delete_chat(chat_id: int, x_client_id: str | None = Header(default=None)):
    db.delete_chat(owner_id(x_client_id), chat_id)
    return {"ok": True}


def _check_rate_limit(owner: str, ip: str) -> None:
    per_min_owner, per_min_ip = db.recent_searches(owner, ip, 60)
    per_day_owner, _ = db.recent_searches(owner, ip, 86400)
    if per_min_owner >= LIMIT_PER_MINUTE or per_min_ip >= LIMIT_IP_PER_MINUTE:
        raise HTTPException(429, "You are searching a bit fast. Please wait a few seconds and try again.")
    if per_day_owner >= LIMIT_PER_DAY:
        raise HTTPException(429, "You have reached today's search limit. Please come back tomorrow.")


@app.post("/api/chats/{chat_id}/messages")
def send_message(chat_id: int, body: NewMessage, request: Request, response: Response, x_client_id: str | None = Header(default=None)):
    started = time.monotonic()
    with db.connect():  # one database connection for the whole request
        result = _send_message(chat_id, body, request, x_client_id)
    response.headers["Server-Timing"] = f"total;dur={(time.monotonic() - started) * 1000:.0f}"
    return result


def _send_message(chat_id: int, body: NewMessage, request: Request, x_client_id: str | None):
    owner = owner_id(x_client_id)
    text = body.content.strip()
    if not text:
        raise HTTPException(400, "Message is empty")
    if len(text) > MAX_MESSAGE_CHARS:
        raise HTTPException(400, f"Message is too long (max {MAX_MESSAGE_CHARS} characters)")
    current_title = db.chat_title(owner, chat_id)
    if current_title is None:
        raise HTTPException(404, "Chat not found")
    ip = client_ip(request)
    _check_rate_limit(owner, ip)

    db.add_message(chat_id, "user", text, title=text[:60] if current_title == "New chat" else None)

    topic, days, capped = parse_window(text)
    key = f"{topic.lower()}|{days}"
    meta: dict
    try:
        cached = db.cache_get(key, CACHE_TTL_SECONDS)
        if cached:
            data = json.loads(cached)
            results, checked, used_ai, errors = data["results"], data["checked"], data["ai"], {}
            interpreted, newest_seen, engine = data.get("interpreted", []), data.get("newest_seen"), data.get("engine")
        else:
            db.log_search(owner, ip)  # only real searches count toward the limit, cache hits are free
            found = search_all(topic, days)
            if not found["results"] and len(found["errors"]) == len(SOURCES):
                raise RuntimeError("; ".join(found["errors"].values()))
            results, checked, errors = found["results"], found["checked"], found["errors"]
            interpreted, newest_seen, engine = found.get("interpreted", []), found.get("newest_seen"), found.get("engine")
            used_ai = summarize(results)
            for r in results:
                r.pop("_full", None)
            # Never cache a partial result: a retry should try the failed source (or the live feed) again.
            if not errors and found.get("fresh") != "unavailable":
                db.cache_put(
                    key,
                    json.dumps(
                        {"results": results, "checked": checked, "ai": used_ai, "interpreted": interpreted, "newest_seen": newest_seen, "engine": engine}
                    ),
                )
        meta = {
            "topic": topic,
            "days": days,
            "capped": capped,
            "checked": checked,
            "ai_summaries": used_ai,
            "source_errors": errors,
            "interpreted": interpreted,
            "newest_seen": newest_seen,
            "engine": engine,
            "results": results,
        }
        if results:
            content = f'Found {len(results)} post(s) about "{topic}" from the last {days} days.'
        else:
            content = (
                f'No posts about "{topic}" from the last {days} days '
                f"(checked {checked} posts, none matched). Try a longer window or different words."
            )
    except RuntimeError as exc:  # our own search errors are written to be shown to visitors
        meta = {"topic": topic, "days": days, "capped": capped, "results": [], "error": str(exc)}
        content = f"The search failed: {exc}"
    except Exception:
        logging.getLogger("uvicorn.error").exception("search request crashed")
        message = "Something went wrong on our side. Please try again."
        meta = {"topic": topic, "days": days, "capped": capped, "results": [], "error": message}
        content = f"The search failed: {message}"

    row = db.add_message(chat_id, "assistant", content, json.dumps(meta))
    return {"message": message_row(row), "title": db.chat_title(owner, chat_id), "max_days": MAX_DAYS}
