import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import google.generativeai as genai

from app.config import get_settings
from app.schemas import NormalizedJob


@dataclass
class RawJobPage:
    url: str
    text: str
    source: str
    link_title: str = ""


STAGE_VALUES = [
    "pre_seed",
    "seed",
    "series_a",
    "series_b",
    "series_c_plus",
    "unknown",
]


def _ensure_gemini_configured() -> None:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    genai.configure(api_key=settings.gemini_api_key)


def _build_prompt(raw: RawJobPage) -> str:
    return f"""Extract job posting details from the scraped page text below.
Return ONLY valid JSON with this exact schema:
{{
  "title": "job title",
  "company": "startup name",
  "stage": "unknown",
  "tech_stack": ["tech1", "tech2"],
  "compensation": "salary/equity string or null",
  "summary": "short summary max 280 chars",
  "url": "{raw.url}",
  "source": "{raw.source}",
  "posted_date": "YYYY-MM-DD or null"
}}

Return job fields from the posting text. Always set "stage" to "unknown" (funding stage is resolved separately via a Crunchbase lookup).
Keep summary at or under 280 characters.

Link title hint: {raw.link_title or "none"}

Page text:
{raw.text[:12000]}
"""


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    return json.loads(text)


async def infer_stage_from_crunchbase_page(company_name: str, page_text: str) -> str:
    settings = get_settings()
    _ensure_gemini_configured()
    model = genai.GenerativeModel(settings.gemini_model)
    prompt = f"""Read this Crunchbase company page text for "{company_name}" and determine the startup's current funding stage.

Return ONLY valid JSON:
{{"stage": one of {STAGE_VALUES}}}

Use the most recent funding round shown (e.g. Seed -> seed, Series A -> series_a, Series C or later -> series_c_plus, Pre-Seed/Angel -> pre_seed).
Use unknown only if the page text does not contain usable funding information.

Page text:
{page_text[:12000]}
"""
    try:
        response = await model.generate_content_async(prompt)
        data = _parse_json_response(response.text)
        stage = str(data.get("stage", "unknown")).strip().lower().replace("-", "_").replace(" ", "_")
        if stage in STAGE_VALUES:
            return stage
        if stage.startswith("series_") and stage[7:] >= "c":
            return "series_c_plus"
        return "unknown"
    except Exception:
        return "unknown"


async def normalize_job(raw: RawJobPage) -> Optional[NormalizedJob]:
    settings = get_settings()
    _ensure_gemini_configured()
    model = genai.GenerativeModel(settings.gemini_model)
    prompt = _build_prompt(raw)
    try:
        response = await model.generate_content_async(prompt)
        data = _parse_json_response(response.text)
        data["url"] = raw.url
        data["source"] = raw.source
        if len(data.get("summary", "")) > 280:
            data["summary"] = data["summary"][:277] + "..."
        return NormalizedJob.model_validate(data)
    except Exception:
        return None


TECH_CANONICAL = {
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "psql": "PostgreSQL",
    "node": "Node.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "react.js": "React",
    "reactjs": "React",
    "vue.js": "Vue",
    "vuejs": "Vue",
    "k8s": "Kubernetes",
    "golang": "Go",
    "aws": "AWS",
    "gcp": "GCP",
}


def canonicalize_tech_stack(tech_stack: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in tech_stack:
        key = item.strip().lower()
        canonical = TECH_CANONICAL.get(key, item.strip())
        if canonical.lower() not in seen:
            seen.add(canonical.lower())
            result.append(canonical)
    return result
