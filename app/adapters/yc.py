"""Y Combinator workatastartup.com adapter (SPEC 3.1).

Flow: open a visible Chromium window at workatastartup.com, hand off to the
human checkpoint so the user can sign in, then — once signed in — navigate to
the company listing and extract jobs using the user's filters.

The sign-in window re-opens if the timer elapses while still signed out, so
the browser is never closed out from under the user mid-login. Detection of
the signed-in state is reliable: workatastartup.com shows a "Log in" CTA only
while signed out (signed in, it shows the user's name instead).
"""
from __future__ import annotations

import asyncio

from app.adapters.base import checkpoint, extract_text, new_context, save_state
from app.checkpoint import CheckpointTimeout, page_has_wall
from app.config import Settings
from app.models import Search
from app.normalize import title_matches_query

_HOME_URL = "https://www.workatastartup.com/"
_PACING_SECONDS = 1.5
# How many times the sign-in window is re-opened before giving up.
_LOGIN_WINDOWS = 5

# workatastartup.com listing role param values.
_ROLE_PARAM = {
    "engineering": "eng",
    "eng": "eng",
    "design": "design",
    "product": "product",
    "sales": "sales",
    "marketing": "marketing",
    "operations": "operations",
}


def listing_url(search: Search) -> str:
    """Build the company-listing URL from the user's YC filters (SPEC 4.1)."""
    filters = search.yc_filters or {}
    role = _ROLE_PARAM.get(str(filters.get("role") or "engineering").lower(), "eng")
    commitment = str(filters.get("commitment") or "fulltime").lower()
    return (
        "https://www.workatastartup.com/companies"
        f"?role={role}&jobType={commitment}&layout=list-compact"
    )


async def _looks_signed_in(page) -> bool:
    """Positive signal that the workatastartup.com session is authenticated."""
    for selector in (
        "a:has-text('My profile')",
        "a:has-text('Companies & jobs')",
        "a[href*='/jobs/']",
        "input[placeholder*='job title' i]",
    ):
        try:
            if await page.locator(selector).count() > 0:
                return True
        except Exception:
            pass
    return False


async def yc_wall(page) -> bool:
    """A wall is present if there is a generic anti-bot wall, or if YC is
    showing a signed-out state — a visible "Log in" / "Sign in" CTA, which
    workatastartup.com only renders while the user is not signed in.

    A positive signed-in signal clears the wall even if a stray "Log in"
    string lingers somewhere on the page.
    """
    if await page_has_wall(page):
        return True
    if await _looks_signed_in(page):
        return False
    for name in ("log in", "login", "sign in"):
        for role in ("link", "button"):
            try:
                control = page.get_by_role(role, name=name)
                if await control.count() > 0 and await control.first.is_visible():
                    return True
            except Exception:
                pass
    return False


async def _settle(page) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await asyncio.sleep(1.2)


async def _wait_for_signin(page, reporter, timeout: float) -> bool:
    """Hand off to the human and wait for sign-in. Re-opens the window if the
    timer elapses while still signed out — never closes the browser mid-login.
    Returns True once signed in.
    """
    for attempt in range(_LOGIN_WINDOWS):
        try:
            await checkpoint(page, reporter, timeout, wall_detector=yc_wall)
            return True  # no wall, or the wall cleared in time
        except CheckpointTimeout:
            if not await yc_wall(page):
                return True  # signed in right as the timer expired
            if attempt < _LOGIN_WINDOWS - 1:
                reporter.progress("YC: sign-in window elapsed — reopening it.")
    return False


async def _apply_query(page, search: Search, reporter) -> None:
    """Type the job query into the main search box (best effort)."""
    for selector in (
        "input[placeholder*='job title' i]",
        "input[placeholder*='tech stack' i]",
        "input[placeholder*='Search by' i]",
        "input[type='search']",
        "input[name='query']",
    ):
        try:
            box = page.locator(selector)
            if await box.count() > 0:
                await box.first.fill(search.query, timeout=4000)
                await box.first.press("Enter")
                await asyncio.sleep(2.0)
                return
        except Exception:
            pass
    reporter.progress("YC: search box not found, using default listing.")


async def _apply_location(page, search: Search, reporter) -> None:
    """Type the location into the sidebar Location field and pick the first
    autocomplete suggestion (best effort; a post-scrape filter also applies).
    """
    if not search.location:
        return
    for selector in (
        "input:near(:text-is('Location'))",
        "input[placeholder*='Location' i]",
    ):
        try:
            box = page.locator(selector)
            if await box.count() > 0:
                await box.first.fill(search.location, timeout=4000)
                await asyncio.sleep(1.5)
                option = page.locator("[role='option'], .autocomplete-suggestion li")
                if await option.count() > 0:
                    await option.first.click(timeout=3000)
                await asyncio.sleep(1.0)
                return
        except Exception:
            pass
    reporter.progress("YC: location filter not applied (field not found).")


async def _apply_remote(page, search: Search, reporter) -> None:
    """Set the sidebar Remote filter when the user asked for remote (best effort)."""
    if not (search.yc_filters or {}).get("remote"):
        return
    try:
        select_el = page.locator("select:near(:text-is('Remote'))").first
        if await select_el.count() > 0:
            for label in ("Remote only", "Remote", "Allows remote", "Yes"):
                try:
                    await select_el.select_option(label=label, timeout=2000)
                    await asyncio.sleep(0.8)
                    return
                except Exception:
                    pass
    except Exception:
        pass
    reporter.progress("YC: remote filter left at default (control not found).")


async def _collect_jobs(page, query: str, limit: int) -> list[dict]:
    """Collect postings from the listing, keeping only those whose title
    matches the query intent (SPEC 4.5). Returns [{url, title}]."""
    titles: dict[str, str] = {}
    for _ in range(10):
        try:
            anchors = page.locator("a[href*='/jobs/']")
            count = await anchors.count()
            for i in range(count):
                anchor = anchors.nth(i)
                href = await anchor.get_attribute("href")
                if not href:
                    continue
                if href.startswith("/"):
                    href = "https://www.workatastartup.com" + href
                if "/jobs/" not in href:
                    continue
                try:
                    label = (await anchor.inner_text()).strip()
                except Exception:
                    label = ""
                # A job has a title anchor and a "View job" button anchor; keep
                # the title text whenever we see it.
                if label and label.lower() not in ("view job", "view", "apply"):
                    titles[href] = label
                else:
                    titles.setdefault(href, "")
            relevant = [u for u, t in titles.items() if title_matches_query(t, query)]
            if len(relevant) >= limit:
                break
            await page.mouse.wheel(0, 2600)
            await asyncio.sleep(1.2)
        except Exception:
            break
    return [
        {"url": u, "title": t}
        for u, t in titles.items()
        if title_matches_query(t, query)
    ][:limit]


async def scrape(playwright, search: Search, reporter, settings: Settings) -> list[dict]:
    """Return up to max_results_per_search raw job dicts from YC."""
    timeout = settings.checkpoint_timeout_seconds
    limit = settings.max_results_per_search
    url = listing_url(search)
    raw_jobs: list[dict] = []

    browser = await playwright.chromium.launch(headless=settings.playwright_headless)
    context = await new_context(browser, settings, "yc")
    page = await context.new_page()
    try:
        # 1. Land on workatastartup.com and wait for the user to sign in.
        reporter.progress("Opening workatastartup.com — please sign in...")
        await page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=30000)
        await _settle(page)

        signed_in = await _wait_for_signin(page, reporter, timeout)
        if signed_in and hasattr(reporter, "timed_out"):
            reporter.timed_out = False
        if not signed_in:
            reporter.progress("YC: not signed in after several windows.")
        await save_state(context, settings, "yc")

        # 2. After sign-in, go to the workatastartup.com companies directory.
        await page.goto(_HOME_URL, wait_until="domcontentloaded", timeout=30000)
        await _settle(page)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await _settle(page)

        # 3. Apply the user's form fields as sidebar filters, then extract.
        reporter.progress("YC: applying your filters...")
        await _apply_query(page, search, reporter)
        await _apply_location(page, search, reporter)
        await _apply_remote(page, search, reporter)
        # Filtering can trigger rate-limiting walls mid-session (SPEC 5.4).
        await checkpoint(page, reporter, timeout, wall_detector=yc_wall)

        jobs = await _collect_jobs(page, search.query, limit)
        if not jobs:
            reporter.progress("YC: no postings matched your query intent.")

        for job in jobs:
            href = job["url"]
            try:
                await page.goto(href, wait_until="domcontentloaded", timeout=30000)
                await checkpoint(page, reporter, timeout, wall_detector=yc_wall)
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except Exception:
                    pass
                text = await extract_text(page)
                raw_jobs.append({"url": href, "raw_text": text, "source": "yc"})
            except CheckpointTimeout:
                raise
            except Exception as exc:
                reporter.progress(f"YC: skipped a posting ({exc})")
            await asyncio.sleep(_PACING_SECONDS)

        await save_state(context, settings, "yc")
    except CheckpointTimeout:
        reporter.progress("YC: checkpoint timed out — returning partial results.")
    finally:
        try:
            await context.close()
            await browser.close()
        except Exception:
            pass
    return raw_jobs[:limit]
