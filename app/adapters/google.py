"""Google search adapter (SPEC 3.2).

Drives a visible Chromium window, runs a Google web search scoped to common
ATS hosts, then visits each result and collects raw page text. Walls are
handed off to the human checkpoint protocol; a checkpoint timeout ends the
source but keeps whatever was collected. Everything else degrades gracefully
so the worker never crashes on a brittle selector.
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote_plus, urlparse

from app.adapters.base import checkpoint, extract_text, new_context, save_state
from app.checkpoint import CheckpointTimeout
from app.config import Settings
from app.models import Search
from app.normalize import title_matches_query

_ATS_HOSTS = ("ashbyhq.com", "lever.co", "greenhouse.io")
_PACING_SECONDS = 1.5


def build_query(search: Search) -> str:
    parts = [f'"{search.query}"', "startup", "founding engineer"]
    if search.location:
        parts.append(search.location)
    parts.append("site:ashbyhq.com OR site:lever.co OR site:greenhouse.io")
    return " ".join(parts)


def _is_ats_link(href: str | None) -> bool:
    if not href or not href.startswith("http"):
        return False
    host = urlparse(href).netloc.lower()
    return any(h in host for h in _ATS_HOSTS)


async def _accept_consent(page) -> None:
    for label in ("Accept all", "I agree", "Accept", "Reject all"):
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count() > 0:
                await btn.first.click(timeout=2500)
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                return
        except Exception:
            pass


async def scrape(playwright, search: Search, reporter, settings: Settings) -> list[dict]:
    """Return up to max_results_per_search raw job dicts from Google."""
    timeout = settings.checkpoint_timeout_seconds
    limit = settings.max_results_per_search
    raw_jobs: list[dict] = []

    browser = await playwright.chromium.launch(headless=settings.playwright_headless)
    context = await new_context(browser, settings, "google")
    page = await context.new_page()
    try:
        reporter.progress("Searching Google...")
        query = quote_plus(build_query(search))
        # udm=14 -> classic "Web" results, avoiding the AI overview surface.
        await page.goto(
            f"https://www.google.com/search?q={query}&udm=14",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await _accept_consent(page)
        await checkpoint(page, reporter, timeout)

        # Collect result links to known ATS hosts. Keep only results whose
        # title matches the query intent (SPEC 4.5) — irrelevant postings are
        # dropped here, before they are ever fetched or sent to Gemini.
        results: list[dict] = []
        skipped = 0
        try:
            anchors = page.locator("a:has(h3)")
            count = min(await anchors.count(), 40)
            for i in range(count):
                anchor = anchors.nth(i)
                href = await anchor.get_attribute("href")
                if not _is_ats_link(href) or any(r["url"] == href for r in results):
                    continue
                title = ""
                try:
                    title = (await anchor.locator("h3").first.inner_text()).strip()
                except Exception:
                    pass
                if title and not title_matches_query(title, search.query):
                    skipped += 1
                    continue
                snippet = ""
                try:
                    snippet = await anchor.evaluate(
                        "el => (el.closest('div.g, div[data-hveid]') || el).innerText"
                    )
                except Exception:
                    pass
                results.append({"url": href, "snippet": (snippet or "")[:4000]})
                if len(results) >= limit:
                    break
        except Exception as exc:
            reporter.progress(f"Google: could not read results ({exc})")

        if not results:
            reporter.progress(
                f"Google: no results matched the query "
                f"({skipped} off-target result(s) skipped)."
            )

        for result in results:
            try:
                await page.goto(result["url"], wait_until="domcontentloaded", timeout=30000)
                await checkpoint(page, reporter, timeout)
                # SPAs (Ashby/Lever) need a beat to render the posting.
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                text = await extract_text(page)
                raw_jobs.append(
                    {
                        "url": result["url"],
                        "raw_text": text,
                        "snippet": result["snippet"],
                        "source": "google",
                    }
                )
            except CheckpointTimeout:
                raise
            except Exception as exc:
                reporter.progress(f"Google: skipped a link ({exc})")
                if result["snippet"]:
                    raw_jobs.append(
                        {
                            "url": result["url"],
                            "raw_text": result["snippet"],
                            "snippet": result["snippet"],
                            "source": "google",
                        }
                    )
            await asyncio.sleep(_PACING_SECONDS)

        await save_state(context, settings, "google")
    except CheckpointTimeout:
        reporter.progress("Google: checkpoint timed out — returning partial results.")
    finally:
        try:
            await context.close()
            await browser.close()
        except Exception:
            pass
    return raw_jobs[:limit]
