"""Background search orchestration with concurrent source adapters."""

import asyncio
import json
import os
import time
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.adapters.google_adapter import scrape_google
from app.adapters.yc_adapter import scrape_yc
from app.adapters.checkpoint import CheckpointContext
from app.config import get_settings
from app.database import engine
from app.llm.gemini import canonicalize_tech_stack, normalize_job
from app.models import (
    FundingStage,
    JobResult,
    Search,
    SearchStatus,
    SourceName,
    SourceOutcome,
    SourceStatusRecord,
)
from app.schemas import YcFilters
from app.services.crunchbase import crunchbase_stage_resolver
from app.services.location import job_matches_location
from app.services.quota import DedupTracker, parse_yc_filters
from app.services.relevance import job_matches_query
from app.services.yc_stage import infer_stage_from_yc_text


class SearchWorker:
    """In-process async worker queue."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._running_searches: set[str] = set()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def enqueue(self, search_id: str) -> None:
        await self._queue.put(search_id)

    async def _loop(self) -> None:
        while True:
            search_id = await self._queue.get()
            asyncio.create_task(self._run_search(search_id))

    async def _run_search(self, search_id: str) -> None:
        if search_id in self._running_searches:
            return
        self._running_searches.add(search_id)
        try:
            await self._execute_search(search_id)
        finally:
            self._running_searches.discard(search_id)

    async def _execute_search(self, search_id: str) -> None:
        with Session(engine) as session:
            search = session.get(Search, search_id)
            if not search:
                return
            search.status = SearchStatus.running
            search.updated_at = datetime.utcnow()
            session.add(search)
            session.commit()

            stages = json.loads(search.stages_json)
            sources = json.loads(search.sources_json)
            query = search.query
            location = search.location
            allow_unknown = FundingStage.unknown.value in stages
            yc_filters = YcFilters.model_validate(
                parse_yc_filters(getattr(search, "yc_filters_json", "{}"))
            )

        settings = get_settings()
        os.makedirs(settings.playwright_storage_dir, exist_ok=True)

        source_results: dict[str, dict] = {}
        dedup = DedupTracker()
        results_lock = asyncio.Lock()
        saved_total = {"n": 0}

        with Session(engine) as session:
            existing = session.exec(
                select(JobResult).where(JobResult.search_id == search_id)
            ).all()
            for job in existing:
                dedup.mark(job.title, job.company, job.url)
            saved_total["n"] = len(existing)

        async def make_checkpoint_callbacks(source: str):
            async def on_start(src: str, remaining: int, message: str) -> None:
                with Session(engine) as s:
                    sr = s.get(Search, search_id)
                    if sr:
                        sr.status = SearchStatus.needs_attention
                        sr.checkpoint_source = src
                        sr.checkpoint_remaining_seconds = remaining
                        sr.checkpoint_message = message
                        sr.updated_at = datetime.utcnow()
                        s.add(sr)
                        s.commit()

            async def on_tick(src: str, remaining: int) -> None:
                with Session(engine) as s:
                    sr = s.get(Search, search_id)
                    if sr and sr.status == SearchStatus.needs_attention:
                        sr.checkpoint_remaining_seconds = remaining
                        sr.checkpoint_message = (
                            f"The {src} source needs you — solve the wall in the open browser. "
                            f"{remaining}s remaining."
                        )
                        sr.updated_at = datetime.utcnow()
                        s.add(sr)
                        s.commit()

            async def on_end(src: str) -> None:
                with Session(engine) as s:
                    sr = s.get(Search, search_id)
                    if sr:
                        sr.status = SearchStatus.running
                        sr.checkpoint_source = None
                        sr.checkpoint_remaining_seconds = None
                        sr.checkpoint_message = None
                        sr.updated_at = datetime.utcnow()
                        s.add(sr)
                        s.commit()

            return CheckpointContext(on_start, on_tick, on_end)

        async def run_source(source_name: str) -> None:
            start = time.monotonic()
            checkpoint = await make_checkpoint_callbacks(source_name)
            storage_path = os.path.join(
                settings.playwright_storage_dir, f"{source_name}-state.json"
            )

            with Session(engine) as session:
                record = session.exec(
                    select(SourceStatusRecord).where(
                        SourceStatusRecord.search_id == search_id,
                        SourceStatusRecord.source == source_name,
                    )
                ).first()
                if not record:
                    record = SourceStatusRecord(
                        search_id=search_id,
                        source=SourceName(source_name),
                        outcome=SourceOutcome.running,
                    )
                else:
                    record.outcome = SourceOutcome.running
                record.updated_at = datetime.utcnow()
                session.add(record)
                session.commit()

            try:
                if source_name == "google":
                    result = await scrape_google(
                        query,
                        checkpoint,
                        storage_path,
                        location=location,
                    )
                elif source_name == "yc":
                    result = await scrape_yc(
                        query,
                        stages,
                        yc_filters,
                        checkpoint,
                        storage_path,
                        location=location,
                    )
                else:
                    return

                jobs_saved = 0
                gemini_failed = 0
                stage_filtered = 0
                relevance_filtered = 0
                location_filtered = 0
                effective_location = (
                    (getattr(result, "resolved_location", None) or location)
                    if source_name == "yc"
                    else location
                )

                async def process_raw_job(raw, stage_lookup=None) -> None:
                    nonlocal jobs_saved, gemini_failed, stage_filtered, relevance_filtered, location_filtered

                    async with results_lock:
                        if saved_total["n"] >= settings.max_results_per_search:
                            return

                    normalized = await normalize_job(raw)
                    if not normalized:
                        gemini_failed += 1
                        return

                    if raw.source == "yc":
                        stage = infer_stage_from_yc_text(
                            raw.text, raw.link_title, normalized.company, normalized.summary
                        )
                    elif stage_lookup is not None:
                        stage = await stage_lookup.resolve_stage(normalized.company)
                    else:
                        stage = "unknown"

                    normalized = normalized.model_copy(update={"stage": stage})
                    if normalized.stage not in stages:
                        if normalized.stage == "unknown" and allow_unknown:
                            pass
                        else:
                            stage_filtered += 1
                            return

                    if not job_matches_query(
                        query,
                        normalized.title,
                        normalized.summary,
                        link_title=raw.link_title,
                        page_text=raw.text[:4000],
                        relaxed=(raw.source in ("yc", "google")),
                    ):
                        relevance_filtered += 1
                        return

                    if not job_matches_location(
                        effective_location,
                        normalized.title,
                        normalized.summary,
                        link_title=raw.link_title,
                        page_text=raw.text[:4000],
                    ):
                        location_filtered += 1
                        return

                    if dedup.is_duplicate(normalized.title, normalized.company, normalized.url):
                        return

                    tech = canonicalize_tech_stack(normalized.tech_stack)
                    with Session(engine) as session:
                        job = JobResult(
                            search_id=search_id,
                            title=normalized.title,
                            company=normalized.company,
                            stage=FundingStage(normalized.stage),
                            tech_stack_json=json.dumps(tech),
                            compensation=normalized.compensation,
                            summary=normalized.summary,
                            url=normalized.url,
                            source=SourceName(normalized.source),
                            posted_date=normalized.posted_date,
                        )
                        session.add(job)
                        session.commit()
                    dedup.mark(normalized.title, normalized.company, normalized.url)
                    jobs_saved += 1
                    async with results_lock:
                        saved_total["n"] += 1

                if source_name == "google":
                    async with crunchbase_stage_resolver.session(
                        checkpoint, settings.playwright_storage_dir
                    ) as stage_lookup:
                        for raw in result.jobs:
                            await process_raw_job(raw, stage_lookup)
                            async with results_lock:
                                if saved_total["n"] >= settings.max_results_per_search:
                                    break
                else:
                    for raw in result.jobs:
                        await process_raw_job(raw)

                outcome = SourceOutcome.success if result.success else SourceOutcome.needs_attention
                if not result.success and jobs_saved == 0:
                    outcome = SourceOutcome.needs_attention

                status_message = result.message
                if jobs_saved == 0 and result.success:
                    if len(result.jobs) == 0:
                        status_message = result.message or "No job pages scraped"
                    else:
                        status_message = (
                            f"Scraped {len(result.jobs)} pages; 0 matched filters "
                            f"(stage: {stage_filtered}, relevance: {relevance_filtered}, "
                            f"location: {location_filtered}, gemini: {gemini_failed})"
                        )

                elapsed = time.monotonic() - start
                with Session(engine) as session:
                    record = session.exec(
                        select(SourceStatusRecord).where(
                            SourceStatusRecord.search_id == search_id,
                            SourceStatusRecord.source == source_name,
                        )
                    ).first()
                    if record:
                        record.outcome = outcome
                        record.jobs_found = jobs_saved
                        record.walls_hit = result.walls_hit
                        record.elapsed_seconds = round(elapsed, 1)
                        record.message = status_message
                        record.updated_at = datetime.utcnow()
                        session.add(record)
                        session.commit()

                source_results[source_name] = {
                    "outcome": outcome.value,
                    "jobs": jobs_saved,
                    "walls": result.walls_hit,
                    "success": result.success,
                }
            except Exception as exc:
                elapsed = time.monotonic() - start
                with Session(engine) as session:
                    record = session.exec(
                        select(SourceStatusRecord).where(
                            SourceStatusRecord.search_id == search_id,
                            SourceStatusRecord.source == source_name,
                        )
                    ).first()
                    if record:
                        record.outcome = SourceOutcome.failed
                        record.elapsed_seconds = round(elapsed, 1)
                        record.message = str(exc)
                        record.updated_at = datetime.utcnow()
                        session.add(record)
                        session.commit()
                source_results[source_name] = {
                    "outcome": "failed",
                    "jobs": 0,
                    "walls": checkpoint.walls_hit,
                    "success": False,
                }

        source_order = sorted(sources, key=lambda name: 0 if name == "google" else 1)
        for source_name in source_order:
            await run_source(source_name)

        with Session(engine) as session:
            search = session.get(Search, search_id)
            records = session.exec(
                select(SourceStatusRecord).where(SourceStatusRecord.search_id == search_id)
            ).all()
            jobs_count = len(
                session.exec(
                    select(JobResult).where(JobResult.search_id == search_id)
                ).all()
            )

            any_needs_attention = any(
                r.outcome == SourceOutcome.needs_attention for r in records
            )
            any_failed = any(r.outcome == SourceOutcome.failed for r in records)
            any_scrape_ok = any(r.outcome == SourceOutcome.success for r in records)
            has_jobs = jobs_count > 0

            if has_jobs:
                if any_needs_attention or (any_failed and any_scrape_ok):
                    search.status = SearchStatus.partial
                else:
                    search.status = SearchStatus.complete
            elif any_needs_attention:
                search.status = SearchStatus.partial
            elif any_scrape_ok and not any_failed:
                search.status = SearchStatus.complete
            elif any_scrape_ok and any_failed:
                search.status = SearchStatus.partial
            elif any_failed:
                search.status = SearchStatus.failed
            else:
                search.status = SearchStatus.complete

            search.checkpoint_source = None
            search.checkpoint_remaining_seconds = None
            search.checkpoint_message = None
            search.updated_at = datetime.utcnow()
            session.add(search)
            session.commit()


search_worker = SearchWorker()
