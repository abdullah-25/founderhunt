"""Location filtering for normalized job results."""

from __future__ import annotations

import re
from typing import Optional

LOCATION_ALIASES: dict[str, list[str]] = {
    "san francisco": ["sf", "bay area", "silicon valley"],
    "new york": ["nyc", "new york city"],
    "los angeles": ["la"],
    "seattle": [],
    "austin": [],
    "boston": [],
    "london": [],
    "toronto": ["toronto, on", "gta", "greater toronto area"],
    "remote": ["work from home", "wfh", "distributed", "anywhere"],
}

REMOTE_TERMS = (
    "remote",
    "work from home",
    "work-from-home",
    "wfh",
    "fully remote",
    "remote-first",
    "remote first",
    "distributed team",
    "anywhere",
)


def _normalized_location(location: str) -> str:
    return re.sub(r"\s+", " ", location.strip().lower())


def location_search_terms(location: str) -> list[str]:
    loc = _normalized_location(location)
    if not loc:
        return []

    terms = [loc]
    for canonical, aliases in LOCATION_ALIASES.items():
        if loc == canonical or loc in aliases:
            if canonical not in terms:
                terms.append(canonical)
            for alias in aliases:
                if alias not in terms:
                    terms.append(alias)
    return terms


def job_matches_location(
    location: Optional[str],
    title: str,
    summary: str,
    *,
    link_title: str = "",
    page_text: str = "",
) -> bool:
    """Return True when job content matches the requested location filter."""
    if not location or not location.strip():
        return True

    haystack = " ".join([title, summary, link_title, page_text]).lower()
    terms = location_search_terms(location)
    if not terms:
        return True

    city = location.split(",")[0].strip().lower()
    if city and city not in terms:
        terms.append(city)

    remote_terms = set(LOCATION_ALIASES["remote"] + ["remote"])
    if any(term in remote_terms for term in terms):
        return any(marker in haystack for marker in REMOTE_TERMS)

    for term in terms:
        if len(term) <= 3:
            if re.search(rf"\b{re.escape(term)}\b", haystack):
                return True
        elif term in haystack:
            return True

    return False
