"""Human-in-the-loop checkpoint state machine for Playwright adapters."""

import asyncio
import os
import time
from typing import Awaitable, Callable, Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright

from app.adapters.wall_detection import detect_wall, wall_cleared
from app.config import get_settings


class CheckpointContext:
    """Shared mutable state for checkpoint callbacks during a search."""

    def __init__(
        self,
        on_checkpoint_start: Optional[Callable[[str, int, str], Awaitable[None]]] = None,
        on_checkpoint_tick: Optional[Callable[[str, int], Awaitable[None]]] = None,
        on_checkpoint_end: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        self.on_checkpoint_start = on_checkpoint_start
        self.on_checkpoint_tick = on_checkpoint_tick
        self.on_checkpoint_end = on_checkpoint_end
        self.walls_hit = 0


async def launch_visible_browser(
    playwright: Playwright,
    page_url: str,
    storage_state_path: Optional[str],
    *,
    user_agent: Optional[str] = None,
    existing_browser: Optional[Browser] = None,
) -> tuple[Browser, BrowserContext, Page]:
    """Close any existing browser and launch a visible one at the wall URL."""
    if existing_browser is not None:
        try:
            await existing_browser.close()
        except Exception:
            pass

    browser = await playwright.chromium.launch(headless=False, slow_mo=50)
    ctx_kwargs: dict = {}
    if storage_state_path and os.path.exists(storage_state_path):
        ctx_kwargs["storage_state"] = storage_state_path
    if user_agent:
        ctx_kwargs["user_agent"] = user_agent
    context = await browser.new_context(**ctx_kwargs)
    page = await context.new_page()
    await page.goto(page_url, wait_until="domcontentloaded", timeout=45000)
    try:
        await page.bring_to_front()
    except Exception:
        pass
    return browser, context, page


async def handle_wall_if_present(
    page: Page,
    source_name: str,
    checkpoint: CheckpointContext,
    playwright: Playwright,
    browser_ref: list,
    context_ref: list,
    storage_state_path: Optional[str],
    *,
    user_agent: Optional[str] = None,
    reuse_visible_browser: bool = False,
) -> tuple[bool, Page]:
    """
    Detect wall, hand off to human in a visible browser, wait up to 60s.
    Returns (can_continue, active_page).
    """
    if not await detect_wall(page, source=source_name):
        return True, page

    checkpoint.walls_hit += 1
    settings = get_settings()
    timeout = settings.checkpoint_timeout_seconds
    current_url = page.url

    if context_ref and context_ref[0] is not None:
        try:
            if storage_state_path:
                await context_ref[0].storage_state(path=storage_state_path)
        except Exception:
            pass

    existing = browser_ref[0] if browser_ref else None
    if (
        reuse_visible_browser
        and existing is not None
        and existing.is_connected()
        and context_ref
        and context_ref[0] is not None
    ):
        try:
            await page.bring_to_front()
        except Exception:
            pass
    else:
        browser, context, page = await launch_visible_browser(
            playwright,
            current_url,
            storage_state_path,
            user_agent=user_agent,
            existing_browser=existing,
        )
        browser_ref[:] = [browser]
        context_ref[:] = [context]

    message = (
        f"The {source_name} source needs you — "
        + (
            "click Log in and sign in to Work at a Startup. "
            if source_name == "yc"
            else "solve the wall in the open browser. "
        )
        + f"{timeout}s remaining."
    )
    if checkpoint.on_checkpoint_start:
        await checkpoint.on_checkpoint_start(source_name, timeout, message)

    deadline = time.monotonic() + timeout
    cleared = False
    while time.monotonic() < deadline:
        remaining = max(0, int(deadline - time.monotonic()))
        if checkpoint.on_checkpoint_tick:
            await checkpoint.on_checkpoint_tick(source_name, remaining)
        if await wall_cleared(page, source=source_name):
            cleared = True
            break
        await asyncio.sleep(1)

    if checkpoint.on_checkpoint_end:
        await checkpoint.on_checkpoint_end(source_name)

    return cleared, page


async def navigate_with_checkpoint(
    page: Page,
    url: str,
    source_name: str,
    checkpoint: CheckpointContext,
    playwright: Playwright,
    browser_ref: list,
    context_ref: list,
    storage_state_path: Optional[str],
    wait_until: str = "domcontentloaded",
    *,
    user_agent: Optional[str] = None,
) -> tuple[Page, bool]:
    """Navigate and run checkpoint protocol if a wall appears."""
    await page.goto(url, wait_until=wait_until, timeout=45000)
    ok, page = await handle_wall_if_present(
        page,
        source_name,
        checkpoint,
        playwright,
        browser_ref,
        context_ref,
        storage_state_path,
        user_agent=user_agent,
    )
    return page, ok
