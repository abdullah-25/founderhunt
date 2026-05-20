"""Query relevance filtering for normalized job results."""

import re

STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "for",
        "in",
        "at",
        "to",
        "of",
        "on",
        "with",
        "by",
        "as",
        "is",
        "are",
        "be",
        "job",
        "role",
        "position",
    }
)


def significant_terms(query: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", query.lower())
    terms = [w for w in words if len(w) > 1 and w not in STOP_WORDS]
    return terms or words


def job_matches_query(
    query: str,
    title: str,
    summary: str,
    *,
    link_title: str = "",
    page_text: str = "",
    relaxed: bool = False,
) -> bool:
    """Return True when query terms match job content."""
    terms = significant_terms(query)
    if not terms:
        return True

    haystack = " ".join([title, summary, link_title, page_text]).lower()
    phrase = " ".join(terms)
    if phrase in haystack:
        return True

    matched = sum(1 for term in terms if re.search(rf"\b{re.escape(term)}\b", haystack))

    if relaxed:
        threshold = max(1, (len(terms) + 1) // 2)
        return matched >= threshold

    return matched == len(terms)
