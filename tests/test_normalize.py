"""Unit tests for the pure normalization / filtering logic (SPEC 4.5, 4.7)."""
from datetime import datetime

from app.normalize import (
    compute_final_status,
    dedup_jobs,
    job_matches_location,
    stage_allowed,
    stage_from_yc_batch,
    title_matches_query,
)


def test_dedup_by_url_ignores_trailing_slash():
    jobs = [
        {"url": "http://a.com/1", "company": "A", "title": "Eng"},
        {"url": "http://a.com/1/", "company": "A", "title": "Eng"},
    ]
    assert len(dedup_jobs(jobs)) == 1


def test_dedup_by_company_and_title():
    jobs = [
        {"url": "http://a.com/1", "company": "Acme", "title": "Founding Eng"},
        {"url": "http://b.com/2", "company": "acme", "title": "founding eng"},
    ]
    assert len(dedup_jobs(jobs)) == 1


def test_dedup_against_existing():
    new = [{"url": "http://a.com/1", "company": "A", "title": "Eng"}]
    kept = dedup_jobs(new, seen_urls={"http://a.com/1"})
    assert kept == []


def test_stage_allowed():
    assert stage_allowed("seed", ["seed", "series_a"])
    assert not stage_allowed("series_b", ["seed"])


def test_unknown_stage_excluded_unless_selected():
    assert not stage_allowed("unknown", ["seed", "series_a"])
    assert stage_allowed("unknown", ["seed", "unknown"])


def test_title_relevance_requires_distinguishing_word():
    # "founding product engineer" -> title must contain "product".
    assert title_matches_query("Founding Product Engineer", "founding product engineer")
    assert title_matches_query("Product Engineer", "founding product engineer")
    assert not title_matches_query("Backend Engineer", "founding product engineer")
    assert not title_matches_query("Applied AI Engineer", "founding product engineer")


def test_title_relevance_founding_is_not_senior():
    assert not title_matches_query("Senior Product Engineer", "founding product engineer")
    assert not title_matches_query("Staff Engineer", "founding engineer")
    assert title_matches_query("Founding Engineer", "founding engineer")
    assert title_matches_query("Backend Engineer", "founding engineer")


def test_title_relevance_senior_requires_senior_title():
    assert title_matches_query("Senior Backend Engineer", "senior backend engineer")
    assert not title_matches_query("Backend Engineer", "senior backend engineer")
    assert not title_matches_query("Senior Frontend Engineer", "senior backend engineer")


def test_title_relevance_empty_title_and_generic_query():
    assert title_matches_query("", "founding product engineer")  # defer to later
    assert title_matches_query("Anything Engineer", "engineer")  # generic query


def test_location_match():
    assert job_matches_location({"summary": "Based in Toronto, ON"}, "Toronto")
    assert not job_matches_location({"summary": "NYC office only"}, "Toronto")
    assert job_matches_location({"summary": "anything"}, None)


def test_remote_location():
    assert job_matches_location({"summary": "Fully remote team"}, "Remote")
    assert not job_matches_location({"summary": "Onsite in Berlin"}, "Remote")


def test_stage_from_yc_batch():
    now = datetime(2026, 1, 1)
    assert stage_from_yc_batch("Hiring now · W26", now=now) == "pre_seed"
    assert stage_from_yc_batch("S25 batch company", now=now) == "seed"
    assert stage_from_yc_batch("Older company, S23", now=now) == "series_b"
    assert stage_from_yc_batch("Founded long ago, S21", now=now) == "series_c_plus"
    assert stage_from_yc_batch("no batch tag here") is None


def test_compute_final_status():
    assert compute_final_status(["success", "success"], 5) == "complete"
    assert compute_final_status(["success", "success"], 0) == "complete"
    assert compute_final_status(["success", "needs_attention"], 3) == "partial"
    assert compute_final_status(["failed", "needs_attention"], 0) == "failed"
