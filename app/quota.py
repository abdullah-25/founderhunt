"""Per-user daily search quota (SPEC 4.6). Disabled by default."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from app.config import get_settings
from app.models import Search


def searches_in_window(session: Session, user_id: str, hours: int = 24) -> int:
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    stmt = select(func.count(Search.id)).where(
        Search.user_id == user_id, Search.created_at >= cutoff
    )
    return int(session.exec(stmt).one())


def quota_status(session: Session, user_id: str) -> dict:
    settings = get_settings()
    if not settings.quota_enabled:
        return {
            "enabled": False,
            "limit": settings.daily_quota,
            "used": None,
            "remaining": None,
        }
    used = searches_in_window(session, user_id)
    return {
        "enabled": True,
        "limit": settings.daily_quota,
        "used": used,
        "remaining": max(0, settings.daily_quota - used),
    }


def quota_exceeded(session: Session, user_id: str) -> bool:
    settings = get_settings()
    if not settings.quota_enabled:
        return False
    return searches_in_window(session, user_id) >= settings.daily_quota
