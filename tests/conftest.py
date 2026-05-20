"""Test configuration. Env vars are set BEFORE any app import so the cached
settings and the database engine pick up the test database."""
import os
import tempfile

_TMP = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["QUOTA_ENABLED"] = "false"
os.environ["GEMINI_API_KEY"] = "test-key-not-used"
os.environ["PLAYWRIGHT_HEADLESS"] = "true"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture()
def client(monkeypatch):
    """A TestClient whose background ingestion is stubbed out (no Playwright)."""
    import app.main as main
    from app.db import init_db

    monkeypatch.setattr(main, "_launch", lambda coro: coro.close())
    init_db()
    return TestClient(main.app)
