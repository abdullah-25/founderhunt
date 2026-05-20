"""Shared adapter helpers: persisted browser state and checkpoint plumbing."""
from __future__ import annotations

import os

from app.checkpoint import run_checkpoint
from app.config import Settings


def state_path(settings: Settings, source: str) -> str:
    return os.path.join(settings.playwright_state_dir, f"{source}.json")


async def new_context(browser, settings: Settings, source: str):
    """Create a browser context, reusing a persisted session if one exists (S4)."""
    path = state_path(settings, source)
    kwargs = {
        "viewport": {"width": 1280, "height": 900},
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
    }
    if os.path.exists(path):
        kwargs["storage_state"] = path
    return await browser.new_context(**kwargs)


async def save_state(context, settings: Settings, source: str) -> None:
    """Persist the browser session so future runs hit fewer walls (S4)."""
    try:
        os.makedirs(settings.playwright_state_dir, exist_ok=True)
        await context.storage_state(path=state_path(settings, source))
    except Exception:
        pass


async def checkpoint(page, reporter, timeout: float, wall_detector=None) -> bool:
    """Run the human-in-the-loop checkpoint for the current page.

    `wall_detector` lets an adapter supply source-specific wall logic (e.g. the
    YC adapter detecting a logged-out state); defaults to generic detection.
    """
    if wall_detector is None:
        return await run_checkpoint(page, reporter, timeout)
    return await run_checkpoint(page, reporter, timeout, wall_detector=wall_detector)


async def extract_text(page, limit: int = 16000) -> str:
    try:
        text = await page.inner_text("body", timeout=8000)
    except Exception:
        try:
            text = await page.content()
        except Exception:
            text = ""
    return (text or "").strip()[:limit]
