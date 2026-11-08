# Discussion Finder

Type a topic (or describe the kind of discussion you want), optionally add how far back to look ("last 7 days", "past 2 weeks"; max 90 days), and get links to recent Reddit posts with a short description of each. Every visitor gets their own private chat history, no login needed.

X / Twitter is planned but not built yet. See [ROADMAP.md](ROADMAP.md).

## How it works
- `frontend/`: React + Vite single page app.
- `api/`: one Python (FastAPI) function. Searches Reddit through Exa's free hosted search over plain HTTP, saves chats, caches results for 15 minutes and rate-limits searches.
- Storage: Postgres when `DATABASE_URL` is set (what the hosted version uses), otherwise a local SQLite file.
- Each browser gets a random private id (kept in its `localStorage`) that scopes its chats. Clearing browser data or switching browsers starts a fresh history.

## Deploy on Vercel (one link anyone can open)
1. Push this repo to GitHub.
2. On vercel.com choose **Add New, Project**, import the repo, and leave the build settings alone (`vercel.json` sets them). Deploy.
3. In the project, open **Storage**, create a **Neon (Postgres)** database and connect it to the project. This adds `DATABASE_URL` automatically.
4. Redeploy once (Deployments, three dots, Redeploy) so the function sees `DATABASE_URL`.
5. Open `https://<your-project>.vercel.app/api/health`. It should show `"persistent_storage": true`. Then open the main link and run a search.

Optional: add `ANTHROPIC_API_KEY` in the project's Environment Variables for AI summaries (this costs money per search).

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

Open http://localhost:5173 (the dev server proxies `/api` to port 8000; set `API_PROXY` to use another port). Locally, chats go to a SQLite file named `chats.db`.

## Configuration
Copy `.env.example` to `.env` if you need any of these. All are optional. See the comments in that file.

## Tests
```bash
python -m pytest -q
```
