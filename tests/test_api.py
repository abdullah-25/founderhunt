"""API contract tests (SPEC 4.1, 4.3, 4.8 export, 4.6 quota)."""

VALID = {"query": "founding software engineer", "stages": ["seed"], "sources": ["yc"]}
TERMINAL_STATES = {
    "pending", "running", "needs_attention", "partial", "complete", "failed",
}


def test_rejects_empty_query(client):
    r = client.post("/api/search", json={**VALID, "query": "   "})
    assert r.status_code == 422


def test_rejects_empty_stages(client):
    r = client.post("/api/search", json={**VALID, "stages": []})
    assert r.status_code == 422


def test_rejects_empty_sources(client):
    r = client.post("/api/search", json={**VALID, "sources": []})
    assert r.status_code == 422


def test_rejects_invalid_stage(client):
    r = client.post("/api/search", json={**VALID, "stages": ["series_z"]})
    assert r.status_code == 422


def test_rejects_invalid_source(client):
    r = client.post("/api/search", json={**VALID, "sources": ["linkedin"]})
    assert r.status_code == 422


def test_valid_search_returns_202_with_id(client):
    r = client.post("/api/search", json=VALID)
    assert r.status_code == 202
    body = r.json()
    assert body["search_id"]
    assert body["status"] == "pending"


def test_search_accepts_optional_fields(client):
    r = client.post(
        "/api/search",
        json={**VALID, "location": "Toronto", "sources": ["google", "yc"],
              "yc_filters": {"role": "engineering", "commitment": "fulltime"}},
    )
    assert r.status_code == 202


def test_get_search_returns_status_and_shape(client):
    sid = client.post("/api/search", json=VALID).json()["search_id"]
    r = client.get(f"/api/search/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in TERMINAL_STATES
    assert body["query"] == VALID["query"]
    assert isinstance(body["jobs"], list)
    assert isinstance(body["sources_breakdown"], list)


def test_get_unknown_search_is_404(client):
    assert client.get("/api/search/does-not-exist").status_code == 404


def test_export_csv(client):
    sid = client.post("/api/search", json=VALID).json()["search_id"]
    r = client.get(f"/api/search/{sid}/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "Job Title" in r.text


def test_quota_endpoint_disabled_by_default(client):
    r = client.get("/api/quota", headers={"X-User-Id": "tester"})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_resume_rejects_non_needs_attention(client):
    sid = client.post("/api/search", json=VALID).json()["search_id"]
    r = client.post(f"/api/search/{sid}/resume", json={"source": "yc"})
    assert r.status_code == 409


def test_continue_rejects_when_no_active_wall(client):
    sid = client.post("/api/search", json=VALID).json()["search_id"]
    r = client.post(f"/api/search/{sid}/continue", json={"source": "yc"})
    assert r.status_code in (404, 409)


def test_openapi_docs_available(client):
    # Stretch goal S3.
    assert client.get("/openapi.json").status_code == 200
