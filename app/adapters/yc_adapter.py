import asyncio
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode

from playwright.async_api import Page, async_playwright

from app.adapters.checkpoint import CheckpointContext, handle_wall_if_present
from app.adapters.wall_detection import is_yc_listing_ready, is_yc_logged_in
from app.config import get_settings
from app.llm.gemini import RawJobPage
from app.schemas import YcFilters

YC_COMMITMENT_UI = {
    "fulltime": "Fulltime",
    "parttime": "Part-time",
    "intern": "Intern",
    "cofounder": "Co-founder",
}

YC_ROLE_UI = {
    "engineering": "Engineering",
    "design": "Design",
    "product": "Product",
    "sales": "Sales",
    "marketing": "Marketing",
    "operations": "Operations",
}


@dataclass
class AdapterResult:
    jobs: list[RawJobPage]
    walls_hit: int
    success: bool
    message: Optional[str] = None
    resolved_location: Optional[str] = None


def build_yc_listing_url(yc_filters: YcFilters) -> str:
    params = {
        "role": yc_filters.role,
        "commitment": yc_filters.commitment,
    }
    return f"https://www.workatastartup.com/companies?{urlencode(params)}"


async def _click_login(page: Page) -> None:
    login = page.locator(
        'a[href*="login"], a[href*="sign_in"], a:has-text("Log in"), button:has-text("Log in")'
    ).first
    try:
        if await login.count() > 0 and await login.is_visible():
            await login.click()
            await page.wait_for_timeout(1500)
    except Exception:
        pass


async def wait_for_yc_login(
    page: Page,
    listing_url: str,
    checkpoint: CheckpointContext,
    playwright,
    browser_ref: list,
    context_ref: list,
    storage_state_path: Optional[str],
) -> tuple[bool, Page]:
    settings = get_settings()

    for _ in range(settings.yc_login_max_attempts):
        if await is_yc_logged_in(page):
            break

        await _click_login(page)
        if await is_yc_logged_in(page):
            break

        _, page = await handle_wall_if_present(
            page,
            "yc",
            checkpoint,
            playwright,
            browser_ref,
            context_ref,
            storage_state_path,
            reuse_visible_browser=True,
        )
        if await is_yc_logged_in(page):
            break

    if not await is_yc_logged_in(page):
        return False, page

    await page.goto(listing_url, wait_until="domcontentloaded", timeout=45000)
    try:
        await page.wait_for_selector("text=/Showing \\d+ matching startups/i", timeout=20000)
    except Exception:
        pass
    await page.wait_for_timeout(1500)
    return await is_yc_listing_ready(page), page


async def _click_sidebar_filter(page: Page, value: str) -> None:
    """Click a YC sidebar filter chip/button by visible label."""
    try:
        aside = page.locator("aside").first
        root = aside if await aside.count() > 0 else page

        chip = root.locator(
            f'button:has-text("{value}"), label:has-text("{value}"), '
            f'a:has-text("{value}"), [role="button"]:has-text("{value}")'
        ).filter(has_text=value).first

        if await chip.count() > 0 and await chip.is_visible():
            await chip.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass


async def apply_yc_location_filter(page: Page, location: Optional[str]) -> Optional[str]:
    """Type user location into YC sidebar and select the first autocomplete option."""
    if not location or not location.strip():
        return None

    loc = location.strip()
    try:
        aside = page.locator("aside").first
        root = aside if await aside.count() > 0 else page

        field = root.locator(
            '[role="combobox"][placeholder="Search ..."], input[placeholder="Search ..."]'
        ).first
        if await field.count() == 0 or not await field.is_visible():
            return None

        await field.click()
        await field.fill("")
        await field.press_sequentially(loc, delay=60)
        await page.wait_for_timeout(900)

        options = page.locator('[role="listbox"] [role="option"]')
        try:
            await options.first.wait_for(state="visible", timeout=5000)
        except Exception:
            await field.press("ArrowDown")
            await page.wait_for_timeout(400)

        if await options.count() == 0:
            return None

        selected = (await options.first.inner_text()).strip()
        await options.first.click()
        await page.wait_for_timeout(2000)
        return selected or None
    except Exception:
        return None


async def apply_yc_sidebar_filters(page: Page, yc_filters: YcFilters) -> None:
    role_label = YC_ROLE_UI.get(yc_filters.role, yc_filters.role.title())
    commitment_label = YC_COMMITMENT_UI.get(yc_filters.commitment, yc_filters.commitment)

    await _click_sidebar_filter(page, role_label)
    await _click_sidebar_filter(page, commitment_label)

    if yc_filters.remote == "remote":
        await _click_sidebar_filter(page, "Remote")
    elif yc_filters.remote == "onsite":
        await _click_sidebar_filter(page, "On-site")

    await page.wait_for_timeout(1500)


async def apply_yc_search_query(page: Page, user_query: str) -> None:
    search_selectors = [
        'searchbox[name="search"]',
        'input[placeholder*="Search by job title" i]',
        'input[placeholder*="tech stack" i]',
        'input[placeholder*="job title" i]',
        "aside input[type='text']",
        'input[type="search"]',
    ]
    for selector in search_selectors:
        try:
            field = page.locator(selector).first
            if await field.count() > 0 and await field.is_visible():
                await field.click()
                await field.fill(user_query)
                await field.press("Enter")
                await page.wait_for_timeout(3000)
                break
        except Exception:
            continue


async def scroll_listings(page: Page, scrolls: int) -> None:
    for _ in range(scrolls):
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1200)


async def collect_yc_listing_jobs(page: Page, max_results: int) -> list[tuple[str, str, str]]:
    """Return (url, link_title, card_text) from visible listing cards."""
    collected: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    buttons = page.locator('a:has-text("View job")')
    count = await buttons.count()
    for i in range(min(count, max_results * 3)):
        btn = buttons.nth(i)
        try:
            href = await btn.get_attribute("href")
            if not href:
                continue
            if href.startswith("/"):
                href = f"https://www.workatastartup.com{href}"
            if href in seen:
                continue

            card_text = await btn.evaluate(
                """(el) => {
                    let node = el;
                    for (let depth = 0; depth < 10 && node; depth++) {
                        const text = (node.innerText || '').trim();
                        if (text.length > 120 && text.includes('View job')) return text;
                        node = node.parentElement;
                    }
                    return (el.closest('div') || el.parentElement || el).innerText || '';
                }"""
            )
            link_title = ""
            for line in card_text.split("\n"):
                line = line.strip()
                if line and line != "View job" and "match" not in line.lower():
                    link_title = line
                    break

            seen.add(href)
            collected.append((href, link_title, card_text))
        except Exception:
            continue

        if len(collected) >= max_results:
            break

    return collected


async def scrape_yc(
    user_query: str,
    stages: list[str],
    yc_filters: YcFilters,
    checkpoint: CheckpointContext,
    storage_state_path: Optional[str] = None,
    location: Optional[str] = None,
) -> AdapterResult:
    del stages  # stage filtering uses YC batch tags in the worker
    settings = get_settings()
    jobs: list[RawJobPage] = []
    browser_ref: list = []
    context_ref: list = []
    listing_url = build_yc_listing_url(yc_filters)
    resolved_location: Optional[str] = None

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=False)
        browser_ref.append(browser)
        ctx_kwargs = {"viewport": {"width": 1400, "height": 900}}
        if storage_state_path and os.path.exists(storage_state_path):
            ctx_kwargs["storage_state"] = storage_state_path
        context = await browser.new_context(**ctx_kwargs)
        context_ref.append(context)
        page = await context.new_page()

        try:
            await page.goto(listing_url, wait_until="domcontentloaded", timeout=45000)
        except Exception:
            await browser.close()
            return AdapterResult([], checkpoint.walls_hit, False, "Could not open YC companies page")

        logged_in, page = await wait_for_yc_login(
            page,
            listing_url,
            checkpoint,
            playwright,
            browser_ref,
            context_ref,
            storage_state_path,
        )
        if not logged_in:
            await browser.close()
            return AdapterResult(
                [],
                checkpoint.walls_hit,
                False,
                "YC login timed out — sign in via Log in, then use Resume or run again",
            )

        await apply_yc_sidebar_filters(page, yc_filters)
        resolved_location = await apply_yc_location_filter(page, location)
        await apply_yc_search_query(page, user_query)
        await apply_yc_sidebar_filters(page, yc_filters)
        if location and not resolved_location:
            resolved_location = await apply_yc_location_filter(page, location)

        if not await is_yc_listing_ready(page):
            await page.goto(listing_url, wait_until="domcontentloaded", timeout=45000)
            await apply_yc_sidebar_filters(page, yc_filters)
            resolved_location = await apply_yc_location_filter(page, location) or resolved_location
            await apply_yc_search_query(page, user_query)

        await scroll_listings(page, settings.yc_listing_scrolls)
        listing_jobs = await collect_yc_listing_jobs(page, settings.max_results_per_search)
        if not listing_jobs:
            await browser.close()
            return AdapterResult(
                [],
                checkpoint.walls_hit,
                True,
                "Logged in to YC but no View job links found — try a broader query",
            )

        context = context_ref[0]
        for href, title_hint, card_text in listing_jobs:
            page_text = card_text
            job_page = await context.new_page()
            try:
                await job_page.goto(href, wait_until="domcontentloaded", timeout=45000)
                body_text = await job_page.inner_text("body")
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
                    source="yc",
                    link_title=title_hint,
                )
            )
            if len(jobs) >= settings.max_results_per_search:
                break
            await asyncio.sleep(0.5)

        if storage_state_path:
            try:
                await context_ref[0].storage_state(path=storage_state_path)
            except Exception:
                pass
        await browser_ref[0].close()

    message = None
    if not jobs and listing_jobs:
        message = f"Found {len(listing_jobs)} YC listings but could not extract job text"
    return AdapterResult(
        jobs,
        checkpoint.walls_hit,
        True,
        message,
        resolved_location=resolved_location,
    )
