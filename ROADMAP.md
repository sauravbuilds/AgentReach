# Discussion Finder: Roadmap

Goal: someone opens a link, types a topic, and gets links to recent public discussions with a short description. Their chat history is kept.

## Done
- **Search:** Reddit, through the open-source Agent Reach tool (installed from requirements.txt, MIT licence), with a direct call to Exa as a safety net. About 3 to 4 s per search.
- **Chat history:** saved per visitor (private random id in the browser), reloadable, renameable, deletable.
- **Date windows:** "last 7 days", "past 2 weeks", etc., capped at 90 days.
- **Speed and cost:** 15 minute shared cache (complete results only), per-visitor and per-IP rate limits.
- **Hosting setup:** Vercel (frontend + one Python function) with Postgres (Neon). See the README.
- **Tests:** 34 passing. The database layer was also run against a real Postgres.

## Not verified yet
- The actual Vercel deployment (needs your Vercel account). Everything else was run locally.
- Whether Exa's free hosted search stays free and allows traffic from Vercel's servers at volume.
- Fresh-clone setup from the README.

## Next
1. Deploy to Vercel and run the checks in the README (`/api/health`, one search, reload to see history).
2. X / Twitter. Agent Reach supports it, but only through a logged-in browser on a real machine. It worked on a laptop but was flaky and cannot run on Vercel. A hosted version needs a different route (a paid X API or a hosted scraping service). Add it to `SOURCES` in `api/_lib/search.py`.
3. Optional: real accounts (so history follows a person across devices), AI summaries, more sources.

## Decisions
- No login: history is tied to the browser. Simple for visitors, but clearing browser data loses it.
- Postgres on hosting, SQLite locally, one code path for both.
- LinkedIn is out: the available tooling only searches job postings, not posts.
