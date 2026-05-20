# FounderHunt

FounderHunt aggregates **founding-engineer / early-engineer roles** from Google job-board search and [Y Combinator's workatastartup.com](https://www.workatastartup.com), filtered by funding stage. Playwright drives a real Chromium browser; Gemini normalizes scraped pages into a consistent schema.

## Quick start

```bash
make setup    # venv, deps, Playwright Chromium, .env stub
# Edit .env and set GEMINI_API_KEY
make run      # http://localhost:8000
```

**Single command after first-time setup:**

```bash
make run
```

## Configuration

Copy `.env.example` to `.env` and set:

```
GEMINI_API_KEY=your_key_here
```

Optional overrides (see `app/config.py`): `GEMINI_MODEL`, `DATABASE_URL`, `DAILY_SEARCH_QUOTA`.

Install Playwright browsers once:

```bash
.venv/bin/playwright install chromium
```

(`make install` runs this automatically.)

## Architecture

- **FastAPI** serves the REST API and static SPA.
- **In-process async worker** (`SearchWorker`) runs ingestion outside the HTTP request cycle via an asyncio queue started at app lifespan.
- **SQLite + SQLModel** stores searches, per-source status, and normalized jobs.
- **Playwright adapters** (`google_adapter`, `yc_adapter`) scrape sources concurrently (one browser per source).
- **Gemini** (`gemini-3.1-flash-lite` by default) extracts/normalizes each job from raw page text.
- **Crunchbase stage lookup** Google-searches `"<company> crunchbase"`, opens the Crunchbase org page, and asks Gemini to infer funding stage from the page text (checkpoint handoff if Google/Crunchbase walls appear).

### Checkpoint design (Section 5)

Each adapter runs wall detection on every navigation (URL/title/selectors for login, CAPTCHA, Cloudflare). When a wall is detected:

1. Browser is brought **visible** (`headless=False`).
2. Search status becomes `needs_attention`; the UI shows a countdown message.
3. Adapter polls for up to **60 seconds** for the wall to clear.
4. On success, scraping resumes; on timeout, that source ends with `needs_attention` while other sources continue.

Walls can re-trigger anytime. Per-user searches are isolated via separate worker tasks and browser contexts. Playwright **storage state** is persisted under `playwright-state/` to reduce repeat login walls (stretch S4).

## API

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/search` | Submit search (202 + `search_id`) |
| GET | `/api/search/{id}` | Poll status + results |
| GET | `/api/quota` | Remaining daily searches |
| POST | `/api/search/{id}/resume` | Retry a timed-out source |
| GET | `/api/health` | Health check |

**User identity:** send `X-User-Id` header (the UI generates one in `localStorage`). **Quota:** 10 searches per user per 24h.

OpenAPI docs: [http://localhost:8000/docs](http://localhost:8000/docs)

## Tradeoffs

- **In-process worker** instead of Redis/arq keeps `make run` as a single command; acceptable for local/single-host use where Playwright must run on the host anyway.
- **YC launches visible browser** from the start (sign-in wall expected); Google starts headless and switches on wall detection.
- **Crunchbase via Google** avoids a paid API but depends on Google/Crunchbase not blocking automated lookups; checkpoint protocol handles captchas/logins.
- Scraping third-party sites is fragile; checkpoint protocol is the intended recovery path, not automated CAPTCHA solving.

## Tests

```bash
make test
```

## Run model note

Playwright needs a display on the host for checkpoint handoff. Do not run the worker inside a headless-only container if you expect to solve CAPTCHAs/logins manually.
