# SPEC.md — "FounderHunt" Founding-Engineer Job Aggregator

> This file is the **single source of truth** for the build. It is given, verbatim and unchanged, to every AI coding tool being evaluated. Do not add, remove, or reword requirements per tool — that would break the fairness of the comparison.
>
> **This repository:** `main` keeps this baseline spec. The working implementation lives on the `cursor/*` branch (e.g. `cursor/yc-location-filter-and-scrape-fixes`).

## 1. Summary

Build **FounderHunt**: an end-to-end web application that helps a user find **founding-engineer / early-engineer roles at startups**, filtered by **funding stage** (pre-seed, seed, Series A, etc.).

The user enters a job search (e.g. "founding software engineer"), an optional **location** (e.g. "Toronto", "Remote"), plus a set of acceptable funding stages, and toggles two sources: **Google search** and **Y Combinator's workatastartup.com**. A background worker performs the ingestion using **Playwright** to drive a real browser. When a source hits an anti-bot wall, sign-in page, or captcha, the worker **opens the browser visibly, gives the user 60 seconds to handle it manually, then resumes**. Results are extracted and normalized using a **Gemini model**, and shown to the user as a clean table with **CSV export**.

This project's hard engineering challenge is the **human-in-the-loop checkpoint state machine**: each adapter must try silently, detect a wall, hand off to a human, wait with a bounded timeout, resume cleanly on success, and degrade gracefully on timeout — without blocking other sources or other users' searches.

**As built:** both Google and YC adapters run a **visible** Chromium window from the start; sources run **sequentially** (Google, then YC) so the user can follow one browser at a time. Optional **query-relevance** and **location** filters drop scraped jobs that do not match the user's inputs after normalization.

## 2. Tech Stack (FIXED — do not substitute)

- **Backend:** Python 3.11+, FastAPI
- **Browser automation:** **Playwright** (Python), Chromium
- **LLM:** **Google Gemini** via the official `google-generativeai` Python SDK. Recommended model: `gemini-2.0-flash` (generous free tier). The user supplies `GEMINI_API_KEY` via `.env`.
  - **As built:** default model is `gemini-3.1-flash-lite` (override with `GEMINI_MODEL`).
- **Worker / queue:** A background task system. `arq` + Redis, or Celery + Redis, or FastAPI `BackgroundTasks` with an in-process queue — your choice, but the ingestion MUST run outside the request/response cycle.
- **Persistence:** SQLite (via SQLModel or SQLAlchemy)
- **Frontend:** A single-page UI. Plain HTML/CSS/JS, or a lightweight React setup — your choice. **Clean and minimal** — no heavy framework needed.
- **Run model:** Single documented command (e.g. `make run` or a short README recipe). Note: because Playwright needs to open a visible browser on the user's host, the worker process — or at least the adapters that use Playwright — must run on the host, not inside a headless container. Docker is optional; document whatever you choose.

## 3. Data Sources (only these two)

### 3.1 Y Combinator — `workatastartup.com`

- URL pattern: `https://www.workatastartup.com/companies?jobType=fulltime&role=eng`
- The adapter drives Chromium via Playwright, applies the user's query as a search/filter, and collects job postings from YC-backed companies.
- A sign-in wall is expected; see Section 5 for the checkpoint protocol.

**As built:**

- Listing URL uses `role` and `commitment` query params (e.g. `engineering`, `fulltime`).
- After login, the adapter sets sidebar filters (**Role**, **Commitment**, optional **Remote**) and types the job query into the main search box.
- If the user supplied a **location**, the adapter types it into the sidebar **Location → Search …** field and selects the **first autocomplete suggestion** (e.g. `toronto` → `Toronto, ON, Canada`).
- Job listings are scrolled and collected via **View job** links; up to **10** postings per search.

### 3.2 Google search

- The adapter drives Chromium via Playwright to issue a Google search query composed from the user's input (e.g. `"founding software engineer" startup site:ashbyhq.com OR site:lever.co OR site:greenhouse.io`). It collects result links, then visits each link and extracts the job posting.
- A "are you human?" / reCAPTCHA wall may be hit; see Section 5.

**As built:**

- Runs in a **visible** browser; types the query into google.com's search box (avoiding Google AI-mode direct URLs where possible) and falls back to classic **Web** results (`udm=14`) when needed.
- Appends the user's **location** to the Google query when provided.
- Visits Ashby / Lever / Greenhouse links from web results; waits for SPA job pages to render; uses SERP snippet text as fallback when a posting page is slow.

> Important: do **not** implement automated CAPTCHA solving. Walls are handled exclusively by the human checkpoint protocol in Section 5.

## 4. Functional Requirements (CORE — must all be attempted)

### 4.1 Search submission

- `POST /api/search` accepts: `query` (string, required, e.g. "founding software engineer"), `stages` (list of one or more of `pre_seed`, `seed`, `series_a`, `series_b`, `series_c_plus`, `unknown`), `sources` (list of one or more of `google`, `yc`).
- Returns a `search_id` immediately (HTTP 202). Ingestion happens in the background.
- Rejects empty `query`, empty `stages`, or empty `sources` with HTTP 4xx.

**As built — optional fields:**

- `location` (string, optional) — e.g. `Toronto`, `Remote`, `San Francisco`.
- `yc_filters` (object, optional) — `role`, `commitment`, `remote` for YC sidebar filters when `yc` is selected.

### 4.2 Background ingestion

- A worker picks up the search, queries each selected source via its Playwright adapter, and writes normalized results to the database tagged with the `search_id`.
- Sources are queried **concurrently** (one Playwright browser context per source is fine — they must not serialize end-to-end).

**As built:** sources run **sequentially** (Google first, then YC) for clearer visible-browser handoff. Each search stores at most **10** normalized jobs (`max_results_per_search`).

### 4.3 Results retrieval

- `GET /api/search/{search_id}` returns the search status (`pending` / `running` / `needs_attention` / `partial` / `complete` / `failed`) and all job results found so far. The UI polls this (or uses SSE/websockets) to update live.

### 4.4 Per-result normalization via Gemini

Every job, regardless of source, is normalized to this schema, with **Gemini** doing the extraction from the raw page text:

- `title` — the job title as posted
- `company` — the startup name
- `stage` — one of: `pre_seed`, `seed`, `series_a`, `series_b`, `series_c_plus`, `unknown`
- `tech_stack` — list of technologies mentioned (may be empty)
- `compensation` — string, may be null (salary range, equity, or null if not stated)
- `summary` — a short (≤ 280 chars) summary of the role
- `url` — link to the job posting
- `source` — `google` or `yc`
- `posted_date` — date string if available, else null

### 4.5 Stage filtering

- Only jobs whose inferred `stage` matches one of the user's selected `stages` are returned. (Jobs with `stage = unknown` are excluded unless the user explicitly includes `unknown`.)
- For YC: stage is inferred from the company page on YC + Gemini's judgement based on visible signals (batch year, hiring signals, funding language). You do not need a separate Crunchbase integration — Gemini infers from whatever the adapter scraped about the company.
- For Google: stage is inferred by Gemini from the job posting text and any company-snippet context visible during the search.

**As built:**

- **Gemini** still normalizes job fields (`title`, `company`, `summary`, etc.) from raw page text; `stage` is set to `unknown` in that step and resolved separately.
- **Google:** funding stage is resolved per company via **Google → Crunchbase org page → Gemini** (checkpoint handoff if a wall appears during lookup). No Crunchbase API key.
- **YC:** funding stage is inferred from **YC batch tags** in listing/job text (e.g. `W25`, `S24`) mapped to `pre_seed` / `seed` / `series_a` / etc.
- After stage filtering, jobs must also pass **query relevance** and optional **location** matching against title, summary, and scraped page text.

### 4.6 Per-user quota

- Each user may submit **10 searches per day**. Identify the user by an `X-User-Id` header (or a cookie — your choice, document it).
- The 11th search in a 24h window returns HTTP 429.
- `GET /api/quota` returns the user's remaining searches for the day.

**As built:** quota enforcement is **off by default** (`QUOTA_ENABLED=false`; UI shows "Daily quota: suspended"). The quota endpoints and tracking remain available for later enablement.

### 4.7 Deduplication

- The same job (same `url`, or same `company` + `title`) is stored once per search even if it appears via both sources.

### 4.8 Frontend UI

- A clean, minimal search form with:
  - a text field for the query,
  - checkboxes/toggles for the funding stages,
  - checkboxes/toggles for the two sources,
  - a Search button.
- A **results table** with columns: Job Title · Startup · Stage · Tech Stack · Compensation · Summary · Source · Link.
- An **"Export as CSV"** button that downloads the current table.
- A visible status indicator showing the search state, including a clear `needs_attention` state when the worker is waiting for the user (see Section 5).
- A visible indicator of remaining daily quota.
- No heavy styling; legible and functional > flashy.

**As built:** the form also includes an optional **Location** field and a **Y Combinator filters** fieldset (role, commitment, remote) shown when YC is selected. Per-source outcome breakdown (jobs found, walls, elapsed time) is shown during/after a search (stretch S1).

## 5. Human-in-the-Loop Checkpoint Protocol (THE HARD PART)

This is the central engineering challenge of the build.

### 5.1 Wall detection

Each Playwright adapter must, on every page navigation, **detect whether a wall has appeared**. A wall is any of:

- A login / sign-in screen (e.g. workatastartup.com requires login).
- A captcha / "I'm not a robot" / Cloudflare interstitial.
- A "verify you are human" page.

How to detect (any reasonable signal — use one or several): URL contains `login`/`signin`/`auth`; a known wall-screen selector is present (e.g. a `recaptcha` iframe, a sign-in form, a Cloudflare challenge marker); page title contains characteristic strings.

### 5.2 Handoff

When a wall is detected:

1. The adapter ensures the browser is **visible** (`headless=False`) — open one if the silent attempt was headless, OR launch with `headless=False` from the start on this adapter, your choice.
2. The search transitions to status `needs_attention`. The UI surfaces a clear message: "**The {source} source needs you — solve the wall in the open browser. {N}s remaining.**" (YC login prompts the user to click **Log in** and sign in.)
3. A **60-second timer** starts. The adapter waits for the wall to clear (URL no longer matches login pattern / wall selector gone / a known "logged-in" element appears).

**As built:** Google, YC, and Crunchbase lookups launch **visible** browsers from the start where practical; checkpoint reuse keeps the same visible window open instead of closing/relaunching mid-scrape.

### 5.3 Resume or timeout

- If the wall clears within 60 seconds, the adapter **resumes scraping from where it left off** for that source. The search status updates accordingly (`running`, then `partial`/`complete`).
- If the 60-second timer expires without the wall clearing, that source's task ends with `needs_attention`. The search proceeds with whatever other sources succeeded. The search ends in `partial` (or `failed` only if no source produced any jobs).

### 5.4 Repeat detection

Walls can re-appear later in a session (e.g. mid-scrape rate-limiting). The protocol re-engages every time a wall is detected — there is no "only once per source" restriction.

### 5.5 Isolation

A wall on one source does **not** affect the other source. The other source continues working concurrently. A wall in one user's search does not block another user's search.

### 5.6 Observability

The per-search response (or a health endpoint) must surface, per source: the outcome (`success` / `needs_attention` / `failed`), the count of jobs found, and the count of walls hit (zero or more).

## 6. Stretch Goals (attempt only if core + checkpoint are solid)

- `S1` — Per-source breakdown in the UI: how many jobs from each source, how many walls each one hit, time spent. **Done.**
- `S2` — A `POST /api/search/{id}/resume` endpoint that allows the user to retry a `needs_attention` source after the 60s timer expired. **Done.**
- `S3` — Auto-generated OpenAPI docs available and accurate.
- `S4` — Persistent Playwright storage state (the *browser session* may be saved between runs — note this is different from the "every-wall-detected" protocol above; the saved state may simply mean fewer walls appear). **Done** (`playwright-state/`).
- `S5` — Gemini-powered tech-stack normalization (e.g. canonicalize "Postgres" / "PostgreSQL" / "psql" into one tag). **Done.**

## 7. Deliverables

1. Running system via a single documented command (`make run` or similar).
2. A `README.md` with: setup steps, where to put `GEMINI_API_KEY`, chosen architecture (with a one-paragraph note on the checkpoint design), and any tradeoffs.
3. A test suite the build's author writes for their own code (separate from the evaluator's hidden acceptance suite).
4. All code in one git repository.

**As built:** `main` holds this spec only; implementation and README live on the `cursor/*` branch.

## 8. Constraints & Notes

- **No automated CAPTCHA solving.** Walls are handled exclusively via the human checkpoint in Section 5.
- Keep secrets out of the repo; use `.env.example`.
- The system should start cleanly from a fresh clone with `GEMINI_API_KEY` set and Playwright browsers installed (`playwright install chromium`).
- Reasonable per-source request pacing (don't hammer the sources between walls).
- Time budget per build: **2.5 hours**.
