"""Database engine and session helpers."""
from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_settings = get_settings()
_connect_args = (
    {"check_same_thread": False}
    if _settings.database_url.startswith("sqlite")
    else {}
)
engine = create_engine(_settings.database_url, connect_args=_connect_args)


def init_db() -> None:
    # Import models so their tables register on SQLModel.metadata.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)
