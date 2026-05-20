"""Pure, side-effect-free logic: filtering, dedup, stage inference, status.

Kept separate from the worker/adapters so it is straightforward to unit test.
"""
from __future__ import annotations

import re
from datetime import datetime

VALID_STAGES = ["pre_seed", "seed", "series_a", "series_b", "series_c_plus", "unknown"]

# Tiny stopword set: only true filler words, never meaningful query terms.
_STOPWORDS = {"a", "an", "the", "and", "or", "for", "to", "of", "in", "at", "with", "on"}

# YC batch tag, e.g. "W25", "S24", "F23".
_BATCH_RE = re.compile(r"\b([WSF])(\d{2})\b")


def coerce_stage(stage: str | None) -> str:
    s = (stage or "unknown").strip().lower()
    return s if s in VALID_STAGES else "unknown"


def stage_allowed(stage: str, selected: list[str]) -> bool:
    """A job's stage passes only if the user selected it.

    `unknown` is excluded unless the user explicitly included it (SPEC 4.5).
    """
    return coerce_stage(stage) in set(selected or [])


def stage_from_yc_batch(text: str, *, now: datetime | None = None) -> str | None:
    """Infer a funding stage from a YC batch tag found in scraped text.

    Heuristic: a company's funding stage tends to track how long ago its
    batch was. Returns None when no batch tag is present.
    """
    if not text:
        return None
    match = _BATCH_RE.search(text)
    if not match:
        return None
    batch_year = 2000 + int(match.group(2))
    current_year = (now or datetime.utcnow()).year
    age = current_year - batch_year
    if age <= 0:
        return "pre_seed"
    if age == 1:
        return "seed"
    if age == 2:
        return "series_a"
    if age == 3:
        return "series_b"
    return "series_c_plus"


# Generic role words — present on almost every engineering posting, so they
# carry no distinguishing signal for matching.
_GENERIC_ROLE_WORDS = {
    "engineer", "engineers", "engineering", "developer", "developers", "dev",
    "software", "swe", "programmer", "coder", "role", "roles", "job", "jobs",
    "position", "team",
}
# Seniority tiers.
_SENIOR_WORDS = {"senior", "sr", "staff", "principal", "lead", "head", "expert"}
_FOUNDING_WORDS = {"founding", "founder", "early"}
_JUNIOR_WORDS = {"junior", "jr", "intern", "internship", "entry", "grad", "apprentice"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#]+", (text or "").lower())


def relevant_query_terms(query: str) -> list[str]:
    """A query's distinguishing words — everything except filler, generic role
    words and seniority words. e.g. 'founding product engineer' -> ['product'].
    """
    skip = _STOPWORDS | _GENERIC_ROLE_WORDS | _SENIOR_WORDS | _FOUNDING_WORDS | _JUNIOR_WORDS
    return [w for w in _words(query) if len(w) >= 2 and w not in skip]


def title_matches_query(title: str, query: str) -> bool:
    """Strict, intent-aware relevance check between a job title and the query.

    High-signal matching (SPEC 4.5 "query relevance"):
      * Every distinguishing query word must appear in the title — e.g.
        'founding product engineer' only matches titles containing 'product',
        never a plain 'engineer' or 'senior' role.
      * Seniority must agree: a 'founding'/'early' query never matches a
        senior/staff/principal/lead title, and a 'senior' query only matches
        senior titles. ('founding' is not 'senior'.)

    An empty title is left for later (post-normalization) filters to judge.
    """
    tokens = [w for w in _words(query) if len(w) >= 2 and w not in _STOPWORDS]
    if not tokens:
        return True
    title_words = set(_words(title))
    if not title_words:
        return True

    query_words = set(tokens)
    q_senior = bool(query_words & _SENIOR_WORDS)
    q_founding = bool(query_words & _FOUNDING_WORDS)
    q_junior = bool(query_words & _JUNIOR_WORDS)
    t_senior = bool(title_words & _SENIOR_WORDS)
    t_junior = bool(title_words & _JUNIOR_WORDS)

    # Seniority intent must not conflict with the title's seniority.
    if (q_founding or q_junior) and t_senior:
        return False
    if q_senior and (not t_senior or t_junior):
        return False

    # Every distinguishing word must be present in the title.
    return all(term in title_words for term in relevant_query_terms(query))


def job_matches_location(job: dict, location: str | None, raw_text: str = "") -> bool:
    """Optional location filter. No location -> always passes."""
    if not location or not location.strip():
        return True
    loc = location.strip().lower()
    haystack = " ".join(
        [
            str(job.get("title", "")),
            str(job.get("summary", "")),
            str(job.get("location", "") or ""),
            raw_text or "",
        ]
    ).lower()
    if loc in {"remote", "anywhere", "distributed"}:
        return any(w in haystack for w in ("remote", "anywhere", "distributed"))
    if loc in haystack:
        return True
    city = loc.split(",")[0].strip()
    return bool(city) and city in haystack


def dedup_jobs(jobs: list[dict], *, seen_urls=None, seen_company_title=None) -> list[dict]:
    """Drop duplicates by url, or by (company, title). SPEC 4.7.

    Optional `seen_*` sets let the caller dedup against already-stored jobs.
    """
    seen_urls = set(seen_urls or set())
    seen_company_title = set(seen_company_title or set())
    out: list[dict] = []
    for job in jobs:
        url = (job.get("url") or "").strip().lower().rstrip("/")
        company = (job.get("company") or "").strip().lower()
        title = (job.get("title") or "").strip().lower()
        company_title = (company, title)
        if url and url in seen_urls:
            continue
        if company and title and company_title in seen_company_title:
            continue
        if url:
            seen_urls.add(url)
        if company and title:
            seen_company_title.add(company_title)
        out.append(job)
    return out


def compute_final_status(outcomes: list[str], total_jobs: int) -> str:
    """Roll per-source outcomes + job count into a final search status.

    outcomes: per-source values of `success` / `needs_attention` / `failed`.
    """
    if outcomes and all(o == "success" for o in outcomes):
        return "complete"
    if total_jobs > 0:
        return "partial"
    return "failed"
