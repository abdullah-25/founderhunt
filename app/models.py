from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


class FundingStage(str, Enum):
    pre_seed = "pre_seed"
    seed = "seed"
    series_a = "series_a"
    series_b = "series_b"
    series_c_plus = "series_c_plus"
    unknown = "unknown"


class SourceName(str, Enum):
    google = "google"
    yc = "yc"


class SearchStatus(str, Enum):
    pending = "pending"
    running = "running"
    needs_attention = "needs_attention"
    partial = "partial"
    complete = "complete"
    failed = "failed"


class SourceOutcome(str, Enum):
    success = "success"
    needs_attention = "needs_attention"
    failed = "failed"
    running = "running"
    pending = "pending"


class Search(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(index=True)
    query: str
    location: Optional[str] = None
    stages_json: str
    sources_json: str
    yc_filters_json: str = "{}"
    status: SearchStatus = Field(default=SearchStatus.pending)
    checkpoint_source: Optional[str] = None
    checkpoint_remaining_seconds: Optional[int] = None
    checkpoint_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class JobResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    search_id: str = Field(index=True, foreign_key="search.id")
    title: str
    company: str
    stage: FundingStage
    tech_stack_json: str = "[]"
    compensation: Optional[str] = None
    summary: str
    url: str
    source: SourceName
    posted_date: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SourceStatusRecord(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    search_id: str = Field(index=True, foreign_key="search.id")
    source: SourceName
    outcome: SourceOutcome = Field(default=SourceOutcome.pending)
    jobs_found: int = 0
    walls_hit: int = 0
    elapsed_seconds: float = 0.0
    message: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class QuotaUsage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)
    used_at: datetime = Field(default_factory=datetime.utcnow)
