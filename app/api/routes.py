import json
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlmodel import Session, select

from app.database import get_session
from app.models import (
    FundingStage,
    JobResult,
    Search,
    SearchStatus,
    SourceName,
    SourceOutcome,
    SourceStatusRecord,
)
from app.schemas import (
    JobResultResponse,
    QuotaResponse,
    ResumeRequest,
    SearchCreatedResponse,
    SearchRequest,
    SearchResponse,
    SourceStatusResponse,
    YcFilters,
)
from app.services.quota import (
    check_and_consume_quota,
    get_quota_info,
    parse_sources,
    parse_stages,
    parse_yc_filters,
)
from app.services.search_worker import search_worker

router = APIRouter(prefix="/api")


def get_user_id(x_user_id: Optional[str] = Header(default=None, alias="X-User-Id")) -> str:
    if not x_user_id or not x_user_id.strip():
        raise HTTPException(status_code=400, detail="X-User-Id header is required")
    return x_user_id.strip()


def _job_to_response(job: JobResult) -> JobResultResponse:
    return JobResultResponse(
        title=job.title,
        company=job.company,
        stage=job.stage,
        tech_stack=json.loads(job.tech_stack_json),
        compensation=job.compensation,
        summary=job.summary,
        url=job.url,
        source=job.source,
        posted_date=job.posted_date,
    )


def _build_search_response(session: Session, search: Search) -> SearchResponse:
    jobs = session.exec(
        select(JobResult).where(JobResult.search_id == search.id)
    ).all()
    records = session.exec(
        select(SourceStatusRecord).where(SourceStatusRecord.search_id == search.id)
    ).all()
    return SearchResponse(
        search_id=search.id,
        status=search.status,
        query=search.query,
        location=search.location,
        stages=[FundingStage(s) for s in parse_stages(search.stages_json)],
        sources=[SourceName(s) for s in parse_sources(search.sources_json)],
        yc_filters=YcFilters.model_validate(parse_yc_filters(getattr(search, "yc_filters_json", "{}"))),
        results=[_job_to_response(j) for j in jobs],
        source_statuses=[
            SourceStatusResponse(
                source=r.source,
                outcome=r.outcome,
                jobs_found=r.jobs_found,
                walls_hit=r.walls_hit,
                elapsed_seconds=r.elapsed_seconds,
                message=r.message,
            )
            for r in records
        ],
        checkpoint_message=search.checkpoint_message,
        checkpoint_remaining_seconds=search.checkpoint_remaining_seconds,
        checkpoint_source=search.checkpoint_source,
    )


@router.post("/search", status_code=202, response_model=SearchCreatedResponse)
async def create_search(
    body: SearchRequest,
    user_id: str = Depends(get_user_id),
    session: Session = Depends(get_session),
):
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="query must not be empty")
    if not body.stages:
        raise HTTPException(status_code=400, detail="stages must not be empty")
    if not body.sources:
        raise HTTPException(status_code=400, detail="sources must not be empty")

    ok, _, _ = check_and_consume_quota(session, user_id)
    if not ok:
        raise HTTPException(status_code=429, detail="Daily search quota exceeded")

    search = Search(
        user_id=user_id,
        query=body.query.strip(),
        location=body.location.strip() if body.location and body.location.strip() else None,
        stages_json=json.dumps([s.value for s in body.stages]),
        sources_json=json.dumps([s.value for s in body.sources]),
        yc_filters_json=json.dumps(body.yc_filters.model_dump()),
        status=SearchStatus.pending,
    )
    session.add(search)
    session.commit()
    session.refresh(search)

    for source in body.sources:
        session.add(
            SourceStatusRecord(
                search_id=search.id,
                source=source,
                outcome=SourceOutcome.pending,
            )
        )
    session.commit()

    await search_worker.enqueue(search.id)
    return SearchCreatedResponse(search_id=search.id)


@router.get("/search/{search_id}", response_model=SearchResponse)
def get_search(
    search_id: str,
    user_id: str = Depends(get_user_id),
    session: Session = Depends(get_session),
):
    search = session.get(Search, search_id)
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")
    if search.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return _build_search_response(session, search)


@router.post("/search/{search_id}/resume", response_model=SearchCreatedResponse)
async def resume_search(
    search_id: str,
    body: ResumeRequest,
    user_id: str = Depends(get_user_id),
    session: Session = Depends(get_session),
):
    """Retry a needs_attention source after checkpoint timeout (stretch S2)."""
    search = session.get(Search, search_id)
    if not search:
        raise HTTPException(status_code=404, detail="Search not found")
    if search.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    source = body.source.value

    record = session.exec(
        select(SourceStatusRecord).where(
            SourceStatusRecord.search_id == search_id,
            SourceStatusRecord.source == source,
        )
    ).first()
    if not record or record.outcome != SourceOutcome.needs_attention:
        raise HTTPException(status_code=400, detail="Source is not awaiting resume")

    record.outcome = SourceOutcome.pending
    search.status = SearchStatus.running
    search.updated_at = search.updated_at
    session.add(record)
    session.add(search)
    session.commit()

    await search_worker.enqueue(search_id)
    return SearchCreatedResponse(search_id=search_id)


@router.get("/quota", response_model=QuotaResponse)
def get_quota(
    user_id: str = Depends(get_user_id),
    session: Session = Depends(get_session),
):
    limit, used, remaining, enabled = get_quota_info(session, user_id)
    return QuotaResponse(limit=limit, used=used, remaining=remaining, enabled=enabled)


@router.get("/health")
def health():
    return {"status": "ok"}
