import asyncio
import os
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

from playwright.async_api import Page, async_playwright

from app.adapters.checkpoint import CheckpointContext, handle_wall_if_present
from app.adapters.wall_detection import detect_wall
from app.config import get_settings
from app.llm.gemini import RawJobPage

GOOGLE_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

JOB_BOARD_HINTS = (
    "ashbyhq.com",
    "jobs.ashbyhq.com",
    "lever.co",
    "greenhouse.io",
    "boards.greenhouse.io",
)


@dataclass
class AdapterResult:
    jobs: list[RawJobPage]
    walls_hit: int
    success: bool
    message: Optional[str] = None


def build_google_query(user_query: str, location: Optional[str] = None) -> str:
    base = user_query.strip()
    boards = "site:ashbyhq.com OR site:lever.co OR site:greenhouse.io OR site:jobs.ashbyhq.com"
    query = f'"{base}" startup {boards}'
    if location and location.strip():
        query = f'{query} "{location.strip()}"'
    return query


def resolve_google_link(href: str) -> str:
    if "google." not in href:
        return href
    parsed = urlparse(href)
    if parsed.path not in ("/url", "/aclk"):
        return href
    params = parse_qs(parsed.query)
    for key in ("q", "url"):
        if key in params and params[key]:
            return unquote(params[key][0])
    return href


def is_job_board_url(url: str) -> bool:
    lower = url.lower()
    return any(hint in lower for hint in JOB_BOARD_HINTS)


def collect_unique_job_links(raw_links: list[dict]) -> list[dict]:
    seen_urls: set[str] = set()
    unique_links: list[dict] = []
    for link in raw_links:
        href = resolve_google_link(link.get("href", ""))
        if not href or not is_job_board_url(href):
            continue
        clean = re.sub(r"[#?].*$", "", href)
        if clean in seen_urls:
            continue
        seen_urls.add(clean)
        unique_links.append(
            {
                "href": href,
                "text": link.get("text", ""),
                "snippet": link.get("snippet", ""),
            }
        )
    return unique_links


async def wait_for_job_page_content(page: Page) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=15000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    try:
        await page.wait_for_function(
            """() => {
                const selectors = [
                    '[data-qa="job-description"]',
                    '.posting-header',
                    '.section.page',
                    '[class*="JobPosting"]',
                    'main',
                    'article',
                ];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText && el.innerText.trim().length >= 80) {
                        return true;
                    }
                }
                return document.body && document.body.innerText.trim().length >= 150;
            }""",
            timeout=12000,
        )
    except Exception:
        await page.wait_for_timeout(2500)


async def extract_job_page_text(page: Page) -> str:
    selectors = (
        '[data-qa="job-description"]',
        ".posting-header",
        ".section.page",
        '[class*="JobPosting"]',
        '[class*="jobPosting"]',
        "main",
        "article",
        "#content",
        ".content",
    )
    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if await loc.count() > 0:
                text = await loc.inner_text()
                if len(text.strip()) >= 80:
                    return text
        except Exception:
            continue
    try:
        return await page.inner_text("body")
    except Exception:
        return ""


async def dismiss_google_consent(page: Page) -> None:
    for selector in (
        'button:has-text("Accept all")',
        'button:has-text("Reject all")',
        'button:has-text("I agree")',
    ):
        try:
            btn = page.locator(selector).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1000)
                return
        except Exception:
            continue


async def switch_to_web_results(page: Page) -> None:
    """Leave Google AI mode and show classic web results."""
    for label in ("Web", "All", "News"):
        try:
            tab = page.locator(
                f'a:has-text("{label}"), button:has-text("{label}"), span:has-text("{label}")'
            ).first
            if await tab.count() > 0 and await tab.is_visible():
                await tab.click()
                await page.wait_for_timeout(1500)
                break
        except Exception:
            continue


async def run_google_web_search(
    page: Page,
    user_query: str,
    checkpoint: CheckpointContext,
    playwright,
    browser_ref: list,
    context_ref: list,
    storage_state_path: Optional[str],
    location: Optional[str] = None,
) -> tuple[bool, Page]:
    """Type query into google.com search box (classic web results, not AI mode)."""
    query = build_google_query(user_query, location)

    await page.goto("https://www.google.com/ncr", wait_until="domcontentloaded", timeout=45000)
    await dismiss_google_consent(page)

    if await detect_wall(page, source="google"):
        ok, page = await handle_wall_if_present(
            page,
            "google",
            checkpoint,
            playwright,
            browser_ref,
            context_ref,
            storage_state_path,
            user_agent=GOOGLE_USER_AGENT,
            reuse_visible_browser=True,
        )
        if not ok:
            return False, page

    search_box = page.locator('textarea[name="q"], input[name="q"]').first
    try:
        await search_box.wait_for(state="visible", timeout=10000)
        await search_box.click()
        await search_box.fill(query)
        await search_box.press("Enter")
        await page.wait_for_timeout(2500)
    except Exception:
        fallback_url = (
            f"https://www.google.com/search?q={quote_plus(query)}&udm=14&num=10"
        )
        await page.goto(fallback_url, wait_until="domcontentloaded", timeout=45000)

    if await detect_wall(page, source="google"):
        ok, page = await handle_wall_if_present(
            page,
            "google",
            checkpoint,
            playwright,
            browser_ref,
            context_ref,
            storage_state_path,
            user_agent=GOOGLE_USER_AGENT,
            reuse_visible_browser=True,
        )
        if not ok:
            return False, page

    await switch_to_web_results(page)

    try:
        await page.wait_for_selector("#rso, #search, div.g", timeout=15000)
    except Exception:
        fallback_url = (
            f"https://www.google.com/search?q={quote_plus(query)}&udm=14&num=10"
        )
        await page.goto(fallback_url, wait_until="domcontentloaded", timeout=45000)
        await switch_to_web_results(page)

    if await page.locator("#rso a, div#search a").count() == 0:
        return False, page

    return True, page


async def scrape_google(
    user_query: str,
    checkpoint: CheckpointContext,
    storage_state_path: Optional[str] = None,
    location: Optional[str] = None,
) -> AdapterResult:
    settings = get_settings()
    jobs: list[RawJobPage] = []
    browser_ref: list = []
    context_ref: list = []

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        browser_ref.append(browser)
        ctx_kwargs = {"user_agent": GOOGLE_USER_AGENT, "viewport": {"width": 1280, "height": 900}}
        if storage_state_path and os.path.exists(storage_state_path):
            ctx_kwargs["storage_state"] = storage_state_path
        context = await browser.new_context(**ctx_kwargs)
        context_ref.append(context)
        page = await context.new_page()

        ok, page = await run_google_web_search(
            page,
            user_query,
            checkpoint,
            playwright,
            browser_ref,
            context_ref,
            storage_state_path,
            location=location,
        )
        if not ok:
            await browser.close()
            return AdapterResult([], checkpoint.walls_hit, False, "Google search checkpoint timed out")

        context = context_ref[0]
        raw_links = await page.eval_on_selector_all(
            "#rso a, #search a, div.g a",
            """els => els.map(a => {
                const card = a.closest('div.g, .MjjYud, [data-sokoban-container]');
                const snippet = card ? card.innerText.trim() : '';
                return {href: a.href, text: a.innerText.trim(), snippet};
            }).filter(x => x.href && /^https?:/.test(x.href))""",
        )
        unique_links = collect_unique_job_links(raw_links)

        if not unique_links:
            await browser.close()
            return AdapterResult(
                [],
                checkpoint.walls_hit,
                True,
                "Google web search completed but no job-board links were found",
            )

        for link in unique_links[: settings.max_results_per_search]:
            href = link["href"]
            title_hint = link.get("text", "")
            snippet_hint = link.get("snippet", "")
            page_text = snippet_hint or title_hint
            job_page = await context.new_page()
            try:
                await job_page.goto(href, wait_until="domcontentloaded", timeout=45000)
                await wait_for_job_page_content(job_page)
                body_text = await extract_job_page_text(job_page)
                if len(body_text.strip()) > len(page_text):
                    page_text = body_text
            except Exception:
                pass
            finally:
                try:
                    await job_page.close()
                except Exception:
                    pass

            if len(page_text.strip()) < 40:
                continue

            jobs.append(
                RawJobPage(
                    url=href,
                    text=page_text[:15000],
                    source="google",
                    link_title=title_hint,
                )
            )
            if len(jobs) >= settings.max_results_per_search:
                break
            await asyncio.sleep(1.0)

        if storage_state_path:
            try:
                await context.storage_state(path=storage_state_path)
            except Exception:
                pass
        await browser.close()

    message = None
    if not jobs:
        message = (
            f"Found {len(unique_links)} job-board links but could not extract postings"
        )
    return AdapterResult(jobs, checkpoint.walls_hit, True, message)
