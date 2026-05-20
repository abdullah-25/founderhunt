from app.adapters.google_adapter import (
    build_google_query,
    collect_unique_job_links,
    is_job_board_url,
    resolve_google_link,
)


def test_resolve_google_redirect():
    href = "https://www.google.com/url?q=https://boards.greenhouse.io/acme/jobs/123&sa=U"
    assert resolve_google_link(href) == "https://boards.greenhouse.io/acme/jobs/123"


def test_collect_links_from_redirects():
    raw = [
        {
            "href": "https://www.google.com/url?q=https://jobs.lever.co/acme/abc",
            "text": "Founding Engineer",
            "snippet": "Acme - Founding Engineer\nSeed-stage startup...",
        },
        {"href": "https://www.google.com/search?q=test", "text": "skip"},
    ]
    links = collect_unique_job_links(raw)
    assert len(links) == 1
    assert links[0]["href"] == "https://jobs.lever.co/acme/abc"
    assert "Founding Engineer" in links[0]["snippet"]


def test_is_job_board_url():
    assert is_job_board_url("https://jobs.ashbyhq.com/acme/role")
    assert not is_job_board_url("https://example.com/careers")
