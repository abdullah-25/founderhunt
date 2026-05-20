import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.database import get_session
from app.main import app
from app.models import QuotaUsage
from app.services.quota import DedupTracker, check_and_consume_quota, get_quota_info


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_search_requires_user_header(client):
    r = client.post("/api/search", json={"query": "x", "stages": ["seed"], "sources": ["google"]})
    assert r.status_code == 400


def test_search_validation(client):
    headers = {"X-User-Id": "user-a"}
    r = client.post(
        "/api/search",
        json={"query": "", "stages": ["seed"], "sources": ["google"]},
        headers=headers,
    )
    assert r.status_code == 422


def test_quota_tracking(client):
    headers = {"X-User-Id": "quota-user"}
    r = client.get("/api/quota", headers=headers)
    assert r.status_code == 200
    data = r.json()
    assert data["limit"] == 10
    assert data["remaining"] == 10


def test_dedup_tracker():
    tracker = DedupTracker()
    assert tracker.is_duplicate("Engineer", "Acme", "https://x.com/job") is False
    tracker.mark("Engineer", "Acme", "https://x.com/job")
    assert tracker.is_duplicate("Engineer", "Acme", "https://x.com/job") is True
    assert tracker.is_duplicate("Engineer", "Acme", "https://other.com/job") is True


@pytest.mark.asyncio
async def test_wall_url_detection():
    from app.adapters.wall_detection import detect_google_wall, url_indicates_wall

    assert await url_indicates_wall("https://example.com/login") is True
    assert await url_indicates_wall("https://example.com/jobs/123") is False


@pytest.mark.asyncio
async def test_google_wall_skips_normal_results(monkeypatch):
    from app.adapters.wall_detection import detect_wall

    class FakePage:
        url = "https://www.google.com/search?q=test"

        async def title(self):
            return "founding engineer - Google Search"

        async def inner_text(self, _selector):
            return "About 42 results found for founding engineer"

        class locator:
            @staticmethod
            def __call__(*_args, **_kwargs):
                return FakeLocator()


    class FakeLocator:
        async def count(self):
            return 1

    assert await detect_wall(FakePage(), source="google") is False
