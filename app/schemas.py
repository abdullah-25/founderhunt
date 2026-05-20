"""Request/response models for the API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, field_validator

from app.normalize import VALID_STAGES
VALID_SOURCES = ["google", "yc"]


class YCFilters(BaseModel):
    role: str = "engineering"
    commitment: str = "fulltime"
    remote: Optional[bool] = None


class SearchRequest(BaseModel):
    query: str
    stages: list[str]
    sources: list[str]
    location: Optional[str] = None
    yc_filters: Optional[YCFilters] = None

    @field_validator("query")
    @classmethod
    def _query_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be empty")
        return v.strip()

    @field_validator("stages")
    @classmethod
    def _stages_valid(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one funding stage is required")
        bad = sorted(set(v) - set(VALID_STAGES))
        if bad:
            raise ValueError(f"invalid stages: {bad}")
        return list(dict.fromkeys(v))

    @field_validator("sources")
    @classmethod
    def _sources_valid(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("at least one source is required")
        bad = sorted(set(v) - set(VALID_SOURCES))
        if bad:
            raise ValueError(f"invalid sources: {bad}")
        return list(dict.fromkeys(v))


class ResumeRequest(BaseModel):
    source: str

    @field_validator("source")
    @classmethod
    def _source_valid(cls, v: str) -> str:
        if v not in VALID_SOURCES:
            raise ValueError(f"source must be one of {VALID_SOURCES}")
        return v
