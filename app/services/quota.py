import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlmodel import Session, func, select

from app.config import get_settings
from app.models import QuotaUsage, Search


def parse_stages(stages_json: str) -> list[str]:
    return json.loads(stages_json)


def parse_sources(sources_json: str) -> list[str]:
    return json.loads(sources_json)


def parse_yc_filters(yc_filters_json: str) -> dict:
    try:
        return json.loads(yc_filters_json or "{}")
    except json.JSONDecodeError:
        return {}


def check_and_consume_quota(session: Session, user_id: str) -> tuple[bool, int, int]:
    settings = get_settings()
    if not settings.quota_enabled:
        return True, 0, settings.daily_search_quota

    cutoff = datetime.utcnow() - timedelta(hours=24)
    stmt = select(func.count()).select_from(QuotaUsage).where(
        QuotaUsage.user_id == user_id,
        QuotaUsage.used_at >= cutoff,
    )
    used = session.exec(stmt).one()
    remaining = max(0, settings.daily_search_quota - used)
    if used >= settings.daily_search_quota:
        return False, used, 0
    session.add(QuotaUsage(user_id=user_id))
    session.commit()
    return True, used + 1, remaining - 1


def get_quota_info(session: Session, user_id: str) -> tuple[int, int, int, bool]:
    settings = get_settings()
    if not settings.quota_enabled:
        return settings.daily_search_quota, 0, settings.daily_search_quota, False

    cutoff = datetime.utcnow() - timedelta(hours=24)
    stmt = select(func.count()).select_from(QuotaUsage).where(
        QuotaUsage.user_id == user_id,
        QuotaUsage.used_at >= cutoff,
    )
    used = session.exec(stmt).one()
    remaining = max(0, settings.daily_search_quota - used)
    return settings.daily_search_quota, used, remaining, True


def result_dedup_key(title: str, company: str, url: str) -> tuple[str, tuple[str, str]]:
    normalized_url = re.sub(r"[#?].*$", "", url.strip().lower())
    normalized_title = re.sub(r"\s+", " ", title.strip().lower())
    normalized_company = re.sub(r"\s+", " ", company.strip().lower())
    pair = (normalized_title, normalized_company)
    return normalized_url or f"{normalized_company}::{normalized_title}", pair


@dataclass
class DedupTracker:
    seen_urls: set[str] = field(default_factory=set)
    seen_pairs: set[tuple[str, str]] = field(default_factory=set)

    def is_duplicate(self, title: str, company: str, url: str) -> bool:
        url_key, pair = result_dedup_key(title, company, url)
        if url_key in self.seen_urls:
            return True
        if pair in self.seen_pairs:
            return True
        return False

    def mark(self, title: str, company: str, url: str) -> None:
        url_key, pair = result_dedup_key(title, company, url)
        self.seen_urls.add(url_key)
        self.seen_pairs.add(pair)
