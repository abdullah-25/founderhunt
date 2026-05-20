"""Gemini-powered normalization of raw job-page text (SPEC 4.4, 4.5, S5)."""
from __future__ import annotations

import json
import re

import google.generativeai as genai

from app.config import get_settings
from app.normalize import coerce_stage

_PROMPT = """You are a precise data extractor for startup job postings.

Below is raw text scraped from a single job posting page (source: {source}).
Extract ONE job and return STRICT JSON with exactly these keys:

  "title"        - the job title as posted (string)
  "company"      - the startup / company name (string)
  "stage"        - the company's funding stage, inferred from any visible
                   signals (funding language, batch year, hiring signals).
                   One of: pre_seed, seed, series_a, series_b,
                   series_c_plus, unknown. Use "unknown" if not inferable.
  "tech_stack"   - list of technologies mentioned. Canonicalize names
                   (e.g. "Postgres"/"psql" -> "PostgreSQL", "JS" ->
                   "JavaScript", "react.js" -> "React"). [] if none.
  "compensation" - salary range / equity as a string, or null if not stated.
  "summary"      - a concise summary of the role, 280 characters or fewer.
  "posted_date"  - the date the job was posted if present, else null.

Return only the JSON object, no markdown, no commentary.

JOB PAGE URL: {url}
RAW PAGE TEXT:
{text}
"""


class GeminiError(RuntimeError):
    pass


def _model():
    settings = get_settings()
    if not settings.gemini_api_key:
        raise GeminiError("GEMINI_API_KEY is not set (see .env / .env.example)")
    genai.configure(api_key=settings.gemini_api_key)
    return genai.GenerativeModel(settings.gemini_model)


def _parse_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    return json.loads(text)


def _coerce(data: dict, url: str, source: str) -> dict:
    tech = data.get("tech_stack") or []
    if not isinstance(tech, list):
        tech = [str(tech)]
    tech = [str(t).strip() for t in tech if str(t).strip()]

    summary = str(data.get("summary") or "").strip()
    if len(summary) > 280:
        summary = summary[:277].rstrip() + "..."

    comp = data.get("compensation")
    comp = str(comp).strip() if comp not in (None, "", "null") else None

    posted = data.get("posted_date")
    posted = str(posted).strip() if posted not in (None, "", "null") else None

    return {
        "title": str(data.get("title") or "Untitled role").strip(),
        "company": str(data.get("company") or "Unknown").strip(),
        "stage": coerce_stage(data.get("stage")),
        "tech_stack": tech,
        "compensation": comp,
        "summary": summary,
        "url": url,
        "source": source,
        "posted_date": posted,
    }


def normalize_job(raw: dict, source: str) -> dict:
    """Normalize one scraped job into the SPEC 4.4 schema using Gemini.

    Blocking (network) call — the worker invokes it via asyncio.to_thread.
    """
    text = (raw.get("raw_text") or raw.get("snippet") or "").strip()[:14000]
    url = raw.get("url", "")
    if not text:
        raise GeminiError("no page text to normalize")

    model = _model()
    response = model.generate_content(
        _PROMPT.format(source=source, url=url, text=text),
        generation_config={
            "temperature": 0,
            "response_mime_type": "application/json",
        },
    )
    return _coerce(_parse_json(response.text), url, source)


def gemini_ready() -> bool:
    return bool(get_settings().gemini_api_key)
