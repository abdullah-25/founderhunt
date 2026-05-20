from app.adapters.yc_adapter import build_yc_listing_url
from app.schemas import YcFilters


def test_build_yc_listing_url():
    url = build_yc_listing_url(YcFilters(role="engineering", commitment="fulltime"))
    assert "role=engineering" in url
    assert "commitment=fulltime" in url
    assert url.startswith("https://www.workatastartup.com/companies?")
