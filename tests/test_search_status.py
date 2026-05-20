from app.models import SearchStatus, SourceOutcome


def test_finalize_status_scrape_ok_zero_jobs():
    """Successful scrape with no matching jobs should be complete, not failed."""
    records = [type("R", (), {"outcome": SourceOutcome.success, "jobs_found": 0})()]
    jobs_count = 0
    any_needs_attention = any(r.outcome == SourceOutcome.needs_attention for r in records)
    any_failed = any(r.outcome == SourceOutcome.failed for r in records)
    any_scrape_ok = any(r.outcome == SourceOutcome.success for r in records)
    has_jobs = jobs_count > 0

    if has_jobs:
        status = SearchStatus.partial if any_needs_attention else SearchStatus.complete
    elif any_needs_attention:
        status = SearchStatus.partial
    elif any_scrape_ok and not any_failed:
        status = SearchStatus.complete
    elif any_failed:
        status = SearchStatus.failed
    else:
        status = SearchStatus.complete

    assert status == SearchStatus.complete


def test_finalize_status_all_failed():
    records = [type("R", (), {"outcome": SourceOutcome.failed, "jobs_found": 0})()]
    any_failed = any(r.outcome == SourceOutcome.failed for r in records)
    any_scrape_ok = any(r.outcome == SourceOutcome.success for r in records)
    assert any_failed and not any_scrape_ok
