"""FastAPI application: search API, results, CSV export, quota (SPEC 4)."""
from __future__ import annotations

import asyncio
import csv
import io
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlmodel import select

from app.db import get_session, init_db
from app.models import Job, Search, SourceOutcome
from app.quota import quota_exceeded, quota_status
from app.schemas import ResumeRequest, SearchRequest
from app.worker import request_continue, resume_source, run_search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("founderhunt")

_STATIC_DIR = Path(__file__).parent / "static"

# Strong references to background tasks so they are not garbage-collected.
_background: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="FounderHunt",
    description="Founding-engineer job aggregator with a human-in-the-loop "
    "checkpoint protocol for anti-bot walls.",
    version="1.0.0",
    lifespan=lifespan,
)


def _launch(coro) -> None:
    """Schedule background ingestion. Patched out in tests."""
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


def _user_id(x_user_id: str | None) -> str:
    return (x_user_id or "anonymous").strip() or "anonymous"


# --- Search -----------------------------------------------------------------


@app.post("/api/search", status_code=202)
async def create_search(
    payload: SearchRequest, x_user_id: str | None = Header(default=None)
):
    """Submit a search. Returns a search_id immediately; ingestion runs in the
    background. Empty query/stages/sources are rejected with HTTP 422."""
    user_id = _user_id(x_user_id)
    with get_session() as s:
        if quota_exceeded(s, user_id):
            raise HTTPException(
                status_code=429, detail="Daily search quota exceeded."
            )
        search = Search(
            user_id=user_id,
            query=payload.query,
            location=payload.location,
            stages=payload.stages,
            sources=payload.sources,
            yc_filters=payload.yc_filters.model_dump() if payload.yc_filters else {},
            status="pending",
        )
        s.add(search)
        s.commit()
        s.refresh(search)
        search_id = search.id

    _launch(run_search(search_id))
    return JSONResponse(
        status_code=202, content={"search_id": search_id, "status": "pending"}
    )


def _source_view(outcome: SourceOutcome) -> dict:
    seconds_remaining = None
    if outcome.wall_active and outcome.wall_deadline:
        seconds_remaining = max(
            0, int((outcome.wall_deadline - datetime.utcnow()).total_seconds())
        )
    return {
        "source": outcome.source,
        "outcome": outcome.outcome,
        "jobs_found": outcome.jobs_found,
        "walls_hit": outcome.walls_hit,
        "elapsed_seconds": outcome.elapsed_seconds,
        "message": outcome.message,
        "wall_active": outcome.wall_active,
        "seconds_remaining": seconds_remaining,
    }


def _job_view(job: Job) -> dict:
    return {
        "title": job.title,
        "company": job.company,
        "stage": job.stage,
        "tech_stack": job.tech_stack,
        "compensation": job.compensation,
        "summary": job.summary,
        "url": job.url,
        "source": job.source,
        "posted_date": job.posted_date,
    }


@app.get("/api/search/{search_id}")
def get_search(search_id: str):
    """Return the search status, per-source breakdown, and jobs found so far."""
    with get_session() as s:
        search = s.get(Search, search_id)
        if not search:
            raise HTTPException(status_code=404, detail="search not found")
        jobs = s.exec(
            select(Job).where(Job.search_id == search_id).order_by(Job.created_at)
        ).all()
        outcomes = s.exec(
            select(SourceOutcome).where(SourceOutcome.search_id == search_id)
        ).all()
        return {
            "search_id": search.id,
            "status": search.status,
            "query": search.query,
            "location": search.location,
            "stages": search.stages,
            "sources": search.sources,
            "created_at": search.created_at.isoformat(),
            "sources_breakdown": [_source_view(o) for o in outcomes],
            "jobs": [_job_view(j) for j in jobs],
        }


@app.get("/api/search/{search_id}/export.csv")
def export_csv(search_id: str):
    """Download the current results table as CSV."""
    with get_session() as s:
        search = s.get(Search, search_id)
        if not search:
            raise HTTPException(status_code=404, detail="search not found")
        jobs = s.exec(
            select(Job).where(Job.search_id == search_id).order_by(Job.created_at)
        ).all()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["Job Title", "Startup", "Stage", "Tech Stack", "Compensation",
         "Summary", "Source", "Link"]
    )
    def _csv_safe(val: str) -> str:
        """Prevent CSV injection in spreadsheet applications."""
        if val and val[0] in ("=", "+", "-", "@"):
            return "'" + val
        return val

    for job in jobs:
        writer.writerow(
            [
                _csv_safe(job.title),
                _csv_safe(job.company),
                job.stage,
                _csv_safe(", ".join(job.tech_stack or [])),
                _csv_safe(job.compensation or ""),
                _csv_safe(job.summary or ""),
                job.source,
                job.url,
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="founderhunt-{search_id}.csv"'
        },
    )


@app.post("/api/search/{search_id}/resume")
async def resume_search_source(search_id: str, payload: ResumeRequest):
    """Retry a source that ended in `needs_attention` (stretch goal S2)."""
    with get_session() as s:
        search = s.get(Search, search_id)
        if not search:
            raise HTTPException(status_code=404, detail="search not found")
        if payload.source not in search.sources:
            raise HTTPException(
                status_code=400, detail="source was not part of this search"
            )
        outcome = s.exec(
            select(SourceOutcome).where(
                SourceOutcome.search_id == search_id,
                SourceOutcome.source == payload.source,
            )
        ).first()
        if not outcome or outcome.outcome != "needs_attention":
            raise HTTPException(
                status_code=409,
                detail="source is not in a needs_attention state",
            )

    _launch(resume_source(search_id, payload.source))
    return {"search_id": search_id, "source": payload.source, "status": "resuming"}


@app.post("/api/search/{search_id}/continue")
async def continue_checkpoint(search_id: str, payload: ResumeRequest):
    """Tell a source that the human has cleared its wall (signed in / solved a
    captcha), so the worker resumes immediately instead of waiting for the
    timer or page-based detection."""
    with get_session() as s:
        outcome = s.exec(
            select(SourceOutcome).where(
                SourceOutcome.search_id == search_id,
                SourceOutcome.source == payload.source,
            )
        ).first()
        if not outcome:
            raise HTTPException(status_code=404, detail="source not found")
        if not outcome.wall_active:
            raise HTTPException(
                status_code=409, detail="that source is not waiting at a wall"
            )
    if not request_continue(search_id, payload.source):
        raise HTTPException(status_code=409, detail="source is no longer running")
    return {"search_id": search_id, "source": payload.source, "status": "continuing"}


# --- Quota ------------------------------------------------------------------


@app.get("/api/quota")
def get_quota(x_user_id: str | None = Header(default=None)):
    """Remaining searches for the user today. Identified by X-User-Id header."""
    with get_session() as s:
        return quota_status(s, _user_id(x_user_id))


@app.get("/api/health")
def health():
    return {"status": "ok"}


# Serve the single-page UI. Mounted last so /api/* routes win.
if _STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="static")
