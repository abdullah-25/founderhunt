from app.services.crunchbase import find_crunchbase_org_url


def test_find_crunchbase_org_url_from_google_redirect():
    raw = [
        {
            "href": "https://www.google.com/url?q=https://www.crunchbase.com/organization/acme-inc",
            "text": "Acme Inc - Crunchbase",
        }
    ]
    assert find_crunchbase_org_url(raw) == "https://www.crunchbase.com/organization/acme-inc"


def test_find_crunchbase_org_url_skips_non_crunchbase():
    raw = [{"href": "https://example.com", "text": "nope"}]
    assert find_crunchbase_org_url(raw) is None
