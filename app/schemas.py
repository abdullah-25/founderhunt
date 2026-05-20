from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models import FundingStage, SearchStatus, SourceName, SourceOutcome


class YcFilters(BaseModel):
    role: Literal["engineering", "design", "product", "sales", "marketing", "operations"] = (
        "engineering"
    )
    commitment: Literal["fulltime", "parttime", "intern", "cofounder"] = "fulltime"
    remote: Literal["any", "remote", "onsite"] = "any"


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    location: Optional[str] = Field(default=None, max_length=120)
    stages: list[FundingStage] = Field(..., min_length=1)
    sources: list[SourceName] = Field(..., min_length=1)
    yc_filters: YcFilters = Field(default_factory=YcFilters)


class SearchCreatedResponse(BaseModel):
    search_id: str


class SourceStatusResponse(BaseModel):
    source: SourceName
    outcome: SourceOutcome
    jobs_found: int
    walls_hit: int
    elapsed_seconds: float
    message: Optional[str] = None


class JobResultResponse(BaseModel):
    title: str
    company: str
    stage: FundingStage
    tech_stack: list[str]
    compensation: Optional[str]
    summary: str
    url: str
    source: SourceName
    posted_date: Optional[str]


class SearchResponse(BaseModel):
    search_id: str
    status: SearchStatus
    query: str
    location: Optional[str] = None
    stages: list[FundingStage]
    sources: list[SourceName]
    yc_filters: YcFilters = Field(default_factory=YcFilters)
    results: list[JobResultResponse]
    source_statuses: list[SourceStatusResponse]
    checkpoint_message: Optional[str] = None
    checkpoint_remaining_seconds: Optional[int] = None
    checkpoint_source: Optional[str] = None


class QuotaResponse(BaseModel):
    limit: int
    used: int
    remaining: int
    enabled: bool = True


class ResumeRequest(BaseModel):
    source: SourceName


class NormalizedJob(BaseModel):
    title: str
    company: str
    stage: Literal[
        "pre_seed", "seed", "series_a", "series_b", "series_c_plus", "unknown"
    ]
    tech_stack: list[str] = Field(default_factory=list)
    compensation: Optional[str] = None
    summary: str
    url: str
    source: Literal["google", "yc"]
    posted_date: Optional[str] = None
