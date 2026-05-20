from app.adapters.google_adapter import build_google_query
from app.services.location import job_matches_location, location_search_terms


def test_build_google_query_with_location():
    query = build_google_query("founding engineer", "San Francisco")
    assert '"founding engineer"' in query
    assert '"San Francisco"' in query
    assert "site:ashbyhq.com" in query


def test_build_google_query_without_location():
    query = build_google_query("founding engineer")
    assert query.endswith("site:jobs.ashbyhq.com")
    assert query.count('"') == 2


def test_location_search_terms_aliases():
    terms = location_search_terms("SF")
    assert "san francisco" in terms
    assert "sf" in terms


def test_matches_city_in_summary():
    assert job_matches_location(
        "San Francisco",
        "Software Engineer",
        "Hybrid role based in San Francisco.",
    )


def test_matches_remote():
    assert job_matches_location(
        "remote",
        "Backend Engineer",
        "This is a fully remote position.",
    )


def test_rejects_wrong_city():
    assert not job_matches_location(
        "Austin",
        "Software Engineer",
        "Hybrid role based in San Francisco.",
    )


def test_matches_toronto_resolved_location():
    assert job_matches_location(
        "Toronto, ON, Canada",
        "Software Engineer",
        "Join our Toronto office.",
    )


def test_matches_toronto_alias():
    assert job_matches_location(
        "toronto",
        "Software Engineer",
        "Hybrid in Toronto, ON.",
    )

    assert job_matches_location(None, "Engineer", "Any city.")
    assert job_matches_location("", "Engineer", "Any city.")
