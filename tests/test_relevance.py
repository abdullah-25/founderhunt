from app.services.relevance import job_matches_query, significant_terms


def test_significant_terms():
    assert significant_terms("founding software engineer") == [
        "founding",
        "software",
        "engineer",
    ]


def test_matches_title():
    assert job_matches_query(
        "founding engineer",
        "Founding Engineer",
        "Build our core platform.",
    )


def test_relaxed_allows_partial_match():
    assert job_matches_query(
        "founding software engineer",
        "Founding Product Engineer",
        "Build the core product.",
        relaxed=True,
    )
    assert not job_matches_query(
        "founding software engineer",
        "Product Manager",
        "Lead product strategy.",
        relaxed=True,
    )


def test_matches_summary_when_not_in_title():
    assert job_matches_query(
        "founding engineer",
        "Software Engineer",
        "Join as a founding engineer on the infra team.",
    )


def test_rejects_unrelated_role():
    assert not job_matches_query(
        "founding engineer",
        "Product Manager",
        "Lead product strategy at our seed-stage startup.",
    )


def test_uses_link_title_and_page_text():
    assert job_matches_query(
        "founding engineer",
        "Software Engineer",
        "General backend role.",
        link_title="Acme - Founding Engineer",
        page_text="",
    )
    assert job_matches_query(
        "founding engineer",
        "Backend Developer",
        "General backend role.",
        link_title="",
        page_text="We are hiring a founding engineer to own systems design.",
    )


def test_phrase_match():
    assert job_matches_query(
        "machine learning",
        "ML Engineer",
        "Work on machine learning pipelines.",
    )
