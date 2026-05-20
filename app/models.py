"""SQLModel tables: searches, jobs, and per-source outcomes."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.utcnow()


class Search(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    user_id: str = Field(index=True)
    query: str
    location: Optional[str] = None
    stages: list = Field(sa_column=Column(JSON))
    sources: list = Field(sa_column=Column(JSON))
    yc_filters: dict = Field(sa_column=Column(JSON))
    # pending | running | needs_attention | partial | complete | failed
    status: str = Field(default="pending", index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    id: str = Field(default_factory=_uuid, primary_key=True)
    search_id: str = Field(index=True, foreign_key="search.id")
    title: str
    company: str
    stage: str = "unknown"
    tech_stack: list = Field(sa_column=Column(JSON))
    compensation: Optional[str] = None
    summary: str = ""
    url: str = ""
    source: str = ""
    posted_date: Optional[str] = None
    created_at: datetime = Field(default_factory=utcnow)


class SourceOutcome(SQLModel, table=True):
    """One row per (search, source). Drives observability + checkpoint UI."""

    id: str = Field(default_factory=_uuid, primary_key=True)
    search_id: str = Field(index=True, foreign_key="search.id")
    source: str
    # running | success | needs_attention | failed
    outcome: str = "running"
    jobs_found: int = 0
    walls_hit: int = 0
    elapsed_seconds: float = 0.0
    message: str = ""
    wall_active: bool = False
    wall_deadline: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utcnow)
