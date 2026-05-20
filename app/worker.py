"""Background ingestion worker (SPEC 4.2).

Runs outside the request/response cycle as in-process asyncio tasks. Each
source runs as its own task with its own Chromium browser, so a wall on one
source never blocks the other (SPEC 5.5). Concurrency is configurable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from playwright.async_api import async_playwright
from sqlmodel import select

from app.adapters import google as google_adapter
from app.adapters import yc as yc_adapter
from app.checkpoint import CheckpointTimeout
from app.config import get_settings
from app.db import get_session
from app.gemini import gemini_ready, normalize_job
from app.models import Job, Search, SourceOutcome, utcnow
from app.normalize import (
    compute_final_status,
    dedup_jobs,
    job_matches_location,
    stage_allowed,
    stage_from_yc_batch,
    title_matches_query,
)

log = logging.getLogger("founderhunt.worker")

_ADAPTERS = {"google": google_adapter, "yc": yc_adapter}
_WALL_PROMPTS = {
    "google": "Solve the captcha in the open browser, then click Continue below.",
    "yc": "Sign in at workatastartup.com in the open browser, then click "
    "\"I've signed in — continue\" below.",
}

# One lock per search serializes the store step across concurrent sources.
_store_locks: dict[str, asyncio.Lock] = {}

# Per-(search, source) events for the manual "Continue" checkpoint signal.
_continue_events: dict[tuple[str, str], asyncio.Event] = {}


def request_continue(search_id: str, source: str) -> bool:
    """Signal that the human has cleared the wall for a source. Returns True
    if that source is currently running and the signal was delivered."""
    event = _continue_events.get((search_id, source))
    if event is None:
        return False
    event.set()
    return True


def _store_lock(search_id: str) -> asyncio.Lock:
    return _store_locks.setdefault(search_id, asyncio.Lock())


# --- Outcome reporter -------------------------------------------------------


class OutcomeReporter:
    """Adapter-facing handle that records progress, walls, and outcomes.

    Implements the interface `run_checkpoint` expects (`wall_started`,
    `wall_cleared`, `wall_timed_out`, `source`).
    """

    def __init__(self, search_id: str, source: str):
        self.search_id = search_id
        self.source = source
        self.walls_hit = 0
        self.timed_out = False
        self._resume = asyncio.Event()
        _continue_events[(search_id, source)] = self._resume

    def resume_requested(self) -> bool:
        """True (once) if the human pressed Continue since the wall started."""
        if self._resume.is_set():
            self._resume.clear()
            return True
        return False

    def _row(self, session) -> SourceOutcome:
        return session.exec(
            select(SourceOutcome).where(
                SourceOutcome.search_id == self.search_id,
                SourceOutcome.source == self.source,
            )
        ).first()

    def progress(self, message: str) -> None:
        log.info("[%s/%s] %s", self.search_id[:8], self.source, message)
        with get_session() as s:
            row = self._row(s)
            if row and not row.wall_active:
                row.message = message
                row.updated_at = utcnow()
                s.add(row)
                s.commit()

    def wall_started(self, deadline: datetime) -> None:
        self.walls_hit += 1
        self._resume.clear()  # a fresh wall ignores any stale Continue press
        log.info("[%s/%s] WALL detected — handing off to human", self.search_id[:8], self.source)
        with get_session() as s:
            row = self._row(s)
            if row:
                row.walls_hit += 1
                row.wall_active = True
                row.wall_deadline = deadline
                row.message = _WALL_PROMPTS.get(self.source, "Source needs you.")
                row.updated_at = utcnow()
                s.add(row)
            search = s.get(Search, self.search_id)
            if search and search.status in ("running", "pending"):
                search.status = "needs_attention"
                search.updated_at = utcnow()
                s.add(search)
            s.commit()

    def wall_cleared(self) -> None:
        log.info("[%s/%s] wall cleared — resuming", self.search_id[:8], self.source)
        self._resolve_wall("Wall cleared — resuming.")

    def wall_timed_out(self) -> None:
        self.timed_out = True
        log.info("[%s/%s] wall timed out", self.search_id[:8], self.source)
        self._resolve_wall("Wall not cleared in time.")

    def _resolve_wall(self, message: str) -> None:
        with get_session() as s:
            row = self._row(s)
            if row:
                row.wall_active = False
                row.wall_deadline = None
                row.message = message
                row.updated_at = utcnow()
                s.add(row)
            # Restore the search to `running` only if no source is still walled.
            others = s.exec(
                select(SourceOutcome).where(SourceOutcome.search_id == self.search_id)
            ).all()
            still_walled = any(o.wall_active for o in others if o.id != (row.id if row else None))
            search = s.get(Search, self.search_id)
            if search and search.status == "needs_attention" and not still_walled:
                search.status = "running"
                search.updated_at = utcnow()
                s.add(search)
            s.commit()

    def finish(self, outcome: str, jobs_found: int, elapsed: float, message: str) -> None:
        with get_session() as s:
            row = self._row(s)
            if row:
                row.outcome = outcome
                row.jobs_found = jobs_found
                row.elapsed_seconds = round(elapsed, 1)
                row.walls_hit = max(row.walls_hit, self.walls_hit)
                row.wall_active = False
                row.wall_deadline = None
                row.message = message
                row.updated_at = utcnow()
                s.add(row)
                s.commit()
        _continue_events.pop((self.search_id, self.source), None)


# --- DB helpers -------------------------------------------------------------


def _set_status(search_id: str, status: str) -> None:
    with get_session() as s:
        search = s.get(Search, search_id)
        if search:
            search.status = status
            search.updated_at = utcnow()
            s.add(search)
            s.commit()


def _ensure_outcome_rows(search_id: str, sources: list[str]) -> None:
    with get_session() as s:
        for source in sources:
            existing = s.exec(
                select(SourceOutcome).where(
                    SourceOutcome.search_id == search_id,
                    SourceOutcome.source == source,
                )
            ).first()
            if existing:
                existing.outcome = "running"
                existing.jobs_found = 0
                existing.walls_hit = 0
                existing.elapsed_seconds = 0.0
                existing.wall_active = False
                existing.wall_deadline = None
                existing.message = "Queued."
                existing.updated_at = utcnow()
                s.add(existing)
            else:
                s.add(
                    SourceOutcome(
                        search_id=search_id, source=source, message="Queued."
                    )
                )
        s.commit()


async def _store_jobs(search_id: str, candidates: list[dict], cap: int) -> int:
    """Dedup against already-stored jobs, then insert up to the cap (SPEC 4.7)."""
    async with _store_lock(search_id):
        with get_session() as s:
            existing = s.exec(select(Job).where(Job.search_id == search_id)).all()
            seen_urls = {
                (j.url or "").strip().lower().rstrip("/") for j in existing if j.url
            }
            seen_ct = {
                ((j.company or "").lower(), (j.title or "").lower()) for j in existing
            }
            remaining = cap - len(existing)
            if remaining <= 0:
                return 0
            fresh = dedup_jobs(
                candidates, seen_urls=seen_urls, seen_company_title=seen_ct
            )
            stored = 0
            for job in fresh[:remaining]:
                s.add(
                    Job(
                        search_id=search_id,
                        title=job["title"],
                        company=job["company"],
                        stage=job["stage"],
                        tech_stack=job["tech_stack"],
                        compensation=job["compensation"],
                        summary=job["summary"],
                        url=job["url"],
                        source=job["source"],
                        posted_date=job["posted_date"],
                    )
                )
                stored += 1
            s.commit()
            return stored


# --- Per-source ingestion ---------------------------------------------------


async def _run_source(playwright, search: Search, source: str) -> None:
    settings = get_settings()
    reporter = OutcomeReporter(search.id, source)
    started = time.monotonic()
    try:
        raw_jobs = await _ADAPTERS[source].scrape(playwright, search, reporter, settings)
    except CheckpointTimeout:
        reporter.timed_out = True
        raw_jobs = []
    except Exception as exc:  # noqa: BLE001 - isolate source failures
        log.exception("source %s failed", source)
        reporter.finish(
            "failed", 0, time.monotonic() - started, f"Source error: {exc}"
        )
        return

    reporter.progress(f"{source}: normalizing {len(raw_jobs)} posting(s) with Gemini...")
    candidates: list[dict] = []
    for raw in raw_jobs:
        try:
            job = await asyncio.to_thread(normalize_job, raw, source)
        except Exception as exc:  # noqa: BLE001 - skip a single bad posting
            reporter.progress(f"{source}: normalization issue ({exc})")
            continue
        if source == "yc":
            batch_stage = stage_from_yc_batch(raw.get("raw_text", ""))
            if batch_stage:
                job["stage"] = batch_stage
        raw_text = raw.get("raw_text", "") or raw.get("snippet", "")
        if not stage_allowed(job["stage"], search.stages):
            continue
        if not title_matches_query(job["title"], search.query):
            continue
        if not job_matches_location(job, search.location, raw_text):
            continue
        candidates.append(job)

    stored = await _store_jobs(search.id, candidates, settings.max_results_per_search)
    elapsed = time.monotonic() - started
    if reporter.timed_out:
        reporter.finish(
            "needs_attention",
            stored,
            elapsed,
            f"Timed out at a wall — kept {stored} job(s) collected before the wall.",
        )
    else:
        reporter.finish(
            "success", stored, elapsed, f"Done — kept {stored} matching job(s)."
        )


def _finalize(search_id: str) -> None:
    with get_session() as s:
        outcomes = s.exec(
            select(SourceOutcome).where(SourceOutcome.search_id == search_id)
        ).all()
        total_jobs = len(s.exec(select(Job).where(Job.search_id == search_id)).all())
        status = compute_final_status([o.outcome for o in outcomes], total_jobs)
        search = s.get(Search, search_id)
        if search:
            search.status = status
            search.updated_at = utcnow()
            s.add(search)
        s.commit()
    _store_locks.pop(search_id, None)


# --- Public entry points ----------------------------------------------------


async def run_search(search_id: str) -> None:
    """Ingest every selected source for a search, then finalize its status."""
    with get_session() as s:
        search = s.get(Search, search_id)
        if not search:
            return
        sources = list(search.sources)

    if not gemini_ready():
        _ensure_outcome_rows(search_id, sources)
        for source in sources:
            OutcomeReporter(search_id, source).finish(
                "failed", 0, 0.0, "GEMINI_API_KEY is not set — see README / .env."
            )
        _set_status(search_id, "failed")
        return

    _set_status(search_id, "running")
    _ensure_outcome_rows(search_id, sources)
    settings = get_settings()

    async with async_playwright() as playwright:
        with get_session() as s:
            search = s.get(Search, search_id)
        coros = [_run_source(playwright, search, src) for src in sources]
        if settings.sources_concurrent:
            await asyncio.gather(*coros, return_exceptions=True)
        else:
            for coro in coros:
                try:
                    await coro
                except Exception:  # noqa: BLE001
                    log.exception("sequential source run failed")

    _finalize(search_id)


async def resume_source(search_id: str, source: str) -> None:
    """Re-run a single source after its checkpoint expired (stretch goal S2)."""
    with get_session() as s:
        search = s.get(Search, search_id)
        if not search or source not in search.sources:
            return

    if not gemini_ready():
        return

    _ensure_outcome_rows(search_id, [source])
    _set_status(search_id, "running")

    async with async_playwright() as playwright:
        with get_session() as s:
            search = s.get(Search, search_id)
        await _run_source(playwright, search, source)

    _finalize(search_id)
