# Deploying and running Discussion Finder

For whoever hosts or develops the app. Visitors only need the [README](README.md).

## How it works
- `frontend/`: React + Vite single page app.
- `api/`: one Python (FastAPI) function.
  - Searches Reddit through [Agent Reach](https://github.com/Panniantong/Agent-Reach) (open source, MIT), installed as a Python dependency from `requirements.txt` and run as a subprocess. Its Reddit route uses Exa's free hosted search (no login, no key). If Agent Reach is missing or fails on the host, the app falls back to calling Exa directly, so search keeps working.
  - Adds context for known NRI/India terms and filters out look-alike results (`api/_lib/glossary.py`).
  - Saves chats, caches complete results for 15 minutes and rate-limits searches.
- Storage: Postgres when `DATABASE_URL` is set (hosted), otherwise a local SQLite file.
- Each browser gets a random private id in `localStorage`; it scopes that browser's chats.

## Deploy on Vercel
1. Push the repo to GitHub (the repository must contain `api/`, `frontend/`, `vercel.json` and `requirements.txt`).
2. On vercel.com choose **Add New, Project**, import the repo and leave the build settings alone (`vercel.json` sets them). Deploy.
3. In the project open **Storage**, create a **Neon (Postgres)** database and connect it to the project. This adds `DATABASE_URL`.
   - Supabase works too (it is Postgres). Use Vercel's Supabase integration, or create a Supabase project and set `DATABASE_URL` to its **Transaction pooler** connection string. The app only uses the database connection, not Supabase's other features. Note that free Supabase projects pause after a week of inactivity and must be restored by hand, which would break a shared link; Neon wakes up by itself.
4. Redeploy once so the function picks up `DATABASE_URL`.
5. Check `https://<project>.vercel.app/api/health`. `"search_engine"` shows which engine is active ("Agent Reach" or "Exa"). It must show `"persistent_storage": true` and `"database": "postgres"`. `"database_variable"` names the environment variable it found (the name only, never the value). If it shows `"database": "temporary"`, chat history will disappear: the app cannot see a database connection string. Check the project's Environment Variables for `DATABASE_URL` (a prefixed name such as `STORAGE_DATABASE_URL` also works), make sure it is enabled for Production, and redeploy.
6. Run a search, reload the page, and confirm the history is still there.

Optional environment variables (Project Settings, Environment Variables): `SEARCH_ENGINE=exa` to skip Agent Reach and call Exa directly (the default is Agent Reach). `ANTHROPIC_API_KEY` for AI summaries (costs money per search), `SUMMARY_MODEL`.

## Known limits to be aware of
- **Freshness.** The main search source (Exa, also behind Agent Reach's no-login Reddit route) lags by weeks. To cover recent days the app also asks Reddit's public search feed (`api/_lib/feed.py`). Reddit allows that feed only about one request per 30 seconds per IP address and may refuse hosting providers, so the app asks Reddit as rarely as it can: one request per search term (for example `RNOR`), kept as a shared saved copy in the database for 10 minutes. Wordings of the same term ("rnor tax", "rnor status", "rnor last 7 days") reuse that copy. If Reddit refuses, an older saved copy (up to 6 hours) is used when one exists; with none, searches fall back to the lagging source. A result set says "via Agent Reach + Reddit feed" when the feed contributed. The reliable fix is Reddit's official API (free app at reddit.com/prefs/apps, two keys). Set `FRESH_FEED=off` to disable the feed.
- **Cache.** Complete, non-empty answers are cached for 15 minutes. `CACHE_VERSION` in `api/index.py` is part of the cache key: bump it whenever search behaviour changes so answers saved by an older version are not reused. Empty answers and answers missing a source are never cached.
- **Rate limits.** The free Exa endpoint refuses bursts. Each search makes at most 3 calls and retries once when it is told it is busy. Heavy traffic needs a paid Exa key or another source.
- **No accounts.** History is per browser; it cannot follow a person across devices.
- **X / Twitter** is not part of the hosted app. See `ROADMAP.md`.

## Run locally
Needs Python 3.12+ and Node 18+.

```bash
pip install -r requirements-dev.txt
uvicorn index:app --app-dir api --port 8000
```

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The dev server proxies `/api` to port 8000 (set `API_PROXY` to use another port). Locally chats are stored in `chats.db`. Copy `.env.example` to `.env` for optional settings.

## Tests
```bash
python -m pytest -q
```
