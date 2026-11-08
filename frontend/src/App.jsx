import { useEffect, useRef, useState } from "react";
import * as api from "./api.js";

function formatDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

function ResultCard({ r }) {
  return (
    <a className="card" href={r.url} target="_blank" rel="noopener noreferrer">
      <div className="card-title">{r.title}</div>
      {r.summary && <p className="card-summary">{r.summary}</p>}
      <div className="card-meta">
        <span>{r.source}{r.subreddit ? ` · ${r.subreddit}` : ""}</span>
        <span>{formatDate(r.published_at)}</span>
      </div>
      <div className="card-link">{r.url}</div>
    </a>
  );
}

function Message({ m }) {
  const meta = m.meta;
  const [source, setSource] = useState("All");
  const sources = [...new Set((meta?.results || []).map((r) => r.source))];
  const shown = (meta?.results || []).filter((r) => source === "All" || r.source === source);
  return (
    <div className={`msg ${m.role}`}>
      <div className="bubble">{m.content}</div>
      {meta?.days && m.role === "assistant" && (
        <div className="note">Window: last {meta.days} days{meta.checked ? ` · ${meta.checked} posts checked` : ""}</div>
      )}
      {meta?.interpreted?.map((i) => (
        <div key={i.term} className="note"><strong>{i.term}</strong>: {i.meaning}</div>
      ))}
      {meta?.capped && <div className="note">Searches are capped at the last 90 days, so I used 90.</div>}
      {Object.entries(meta?.source_errors || {}).map(([name, msg]) => (
        <div key={name} className="note">Could not search {name}: {msg}</div>
      ))}
      {sources.length > 1 && (
        <div className="chips">
          {["All", ...sources].map((name) => (
            <button
              key={name}
              type="button"
              className={`chip ${source === name ? "on" : ""}`}
              onClick={() => setSource(name)}
            >
              {name}
            </button>
          ))}
        </div>
      )}
      {shown.map((r) => <ResultCard key={r.url} r={r} />)}
    </div>
  );
}

export default function App() {
  const [chats, setChats] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [editingId, setEditingId] = useState(null);
  const [draftTitle, setDraftTitle] = useState("");
  const endRef = useRef(null);

  useEffect(() => {
    if (!busy) return;
    setElapsed(0);
    const t = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(t);
  }, [busy]);

  async function commitRename(id) {
    const title = draftTitle.trim();
    setEditingId(null);
    if (!title) return;
    try {
      await api.renameChat(id, title);
      refresh();
    } catch (err) {
      setError(err.message);
    }
  }

  const refresh = () => api.listChats().then(setChats).catch(() => setError("Cannot reach the server."));

  useEffect(() => { refresh(); }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, busy]);

  async function open(id) {
    setActiveId(id);
    setError("");
    const chat = await api.getChat(id);
    setMessages(chat.messages);
  }

  function newChat() {
    setActiveId(null);
    setMessages([]);
    setError("");
  }

  async function remove(e, id) {
    e.stopPropagation();
    await api.deleteChat(id);
    if (id === activeId) newChat();
    refresh();
  }

  async function submit(e) {
    e.preventDefault();
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setError("");
    setBusy(true);
    try {
      let id = activeId;
      if (!id) {
        id = (await api.createChat()).id;
        setActiveId(id);
      }
      setMessages((prev) => [...prev, { id: `tmp-${Date.now()}`, role: "user", content: text }]);
      const { message } = await api.sendMessage(id, text);
      setMessages((prev) => [...prev, message]);
      refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <button className="new" onClick={newChat}>+ New chat</button>
        <div className="history">
          {chats.map((c) => (
            <div
              key={c.id}
              className={`item ${c.id === activeId ? "active" : ""}`}
              onClick={() => open(c.id)}
              onDoubleClick={() => { setEditingId(c.id); setDraftTitle(c.title); }}
              title="Double-click to rename"
            >
              {editingId === c.id ? (
                <input
                  className="rename"
                  value={draftTitle}
                  autoFocus
                  maxLength={60}
                  onClick={(e) => e.stopPropagation()}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  onBlur={() => commitRename(c.id)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") e.currentTarget.blur();
                    if (e.key === "Escape") { setDraftTitle(""); e.currentTarget.blur(); }
                  }}
                />
              ) : (
                <span className="item-title">{c.title}</span>
              )}
              <button className="del" onClick={(e) => remove(e, c.id)} aria-label="Delete chat">×</button>
            </div>
          ))}
        </div>
        <div className="sidebar-note">Your chats are saved in this browser and are private to you.</div>
      </aside>

      <main className="main">
        <div className="hint">
          <div>Enter the topic or a description of what you want to find.</div>
          <div className="hint-timing">Reddit results usually take a few seconds. X / Twitter is still being built and will be added soon.</div>
        </div>

        <div className="thread">
          {messages.length === 0 && !busy && (
            <div className="empty">Try: "people comparing budgeting apps, last 30 days"</div>
          )}
          {messages.map((m) => <Message key={m.id} m={m} />)}
          {busy && <div className="msg assistant"><div className="bubble muted">Searching... {elapsed}s{elapsed >= 8 ? " (this can take up to a minute)" : ""}</div></div>}
          {error && <div className="error">{error}</div>}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={submit}>
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Topic or discussion, optionally with a date range..."
            disabled={busy}
            autoFocus
          />
          <button type="submit" disabled={busy || !input.trim()}>Search</button>
        </form>
      </main>
    </div>
  );
}
