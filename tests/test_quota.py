"""Tests for the per-user daily quota (SPEC 4.6)."""
from datetime import datetime, timedelta

from app import quota
from app.db import get_session, init_db
from app.models import Search


def _make_search(session, user_id, created_at=None):
    row = Search(
        user_id=user_id, query="q", stages=["seed"], sources=["yc"],
        yc_filters={}, status="pending",
    )
    if created_at:
        row.created_at = created_at
    session.add(row)
    session.commit()


def test_searches_in_window_counts_last_24h_only():
    init_db()
    with get_session() as s:
        uid = "quota-window-user"
        _make_search(s, uid)
        _make_search(s, uid)
        _make_search(s, uid, datetime.utcnow() - timedelta(hours=30))
        assert quota.searches_in_window(s, uid) == 2


def test_quota_disabled_by_default():
    init_db()
    with get_session() as s:
        status = quota.quota_status(s, "any-user")
        assert status["enabled"] is False
        assert status["remaining"] is None
        assert quota.quota_exceeded(s, "any-user") is False


def test_quota_enforced_when_enabled(monkeypatch):
    init_db()

    class FakeSettings:
        quota_enabled = True
        daily_quota = 2

    monkeypatch.setattr(quota, "get_settings", lambda: FakeSettings())
    with get_session() as s:
        uid = "quota-enforced-user"
        _make_search(s, uid)
        assert quota.quota_exceeded(s, uid) is False
        _make_search(s, uid)
        assert quota.quota_exceeded(s, uid) is True
        assert quota.quota_status(s, uid)["remaining"] == 0
