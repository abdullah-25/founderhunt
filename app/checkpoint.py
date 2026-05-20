"""Human-in-the-loop checkpoint protocol (SPEC section 5).

A wall is a login screen, captcha, or "verify you are human" interstitial.
When an adapter hits one, it calls `run_checkpoint`, which:

  1. flips the search to `needs_attention` and starts a bounded timer,
  2. waits, re-checking the live page, while the human acts in the browser,
  3. resumes the moment the wall clears, or
  4. raises `CheckpointTimeout` when the timer expires.

`detect_wall` is a pure function so the detection logic is unit-testable.
The timer loop is decoupled from Playwright via the `wall_detector` argument.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta

# --- Wall signals -----------------------------------------------------------

_LOGIN_URL_RE = re.compile(
    r"(login|sign[_-]?in|/auth\b|/sso\b|account/login|accounts\.google)", re.I
)
_WALL_TITLE_RE = re.compile(
    r"(sign in|log ?in|just a moment|verify you|are you a robot|captcha|"
    r"access denied|attention required|unusual traffic)",
    re.I,
)
_WALL_BODY_RE = re.compile(
    r"(verify you are human|i'?m not a robot|complete the (captcha|security check)|"
    r"checking your browser|unusual traffic from your|automated queries)",
    re.I,
)
_WALL_SELECTORS = {
    "iframe[src*='recaptcha']",
    "iframe[title*='recaptcha']",
    "#challenge-form",
    "#cf-challenge-running",
    ".cf-challenge",
    "input[type='password']",
    "form[action*='login']",
}


def detect_wall(
    url: str = "",
    title: str = "",
    body_text: str = "",
    selectors_present: list[str] | None = None,
) -> bool:
    """Return True if any anti-bot / sign-in wall signal is present.

    Any one signal is enough (SPEC 5.1). Pure function — no I/O.
    """
    if _LOGIN_URL_RE.search(url or ""):
        return True
    if _WALL_TITLE_RE.search(title or ""):
        return True
    if _WALL_BODY_RE.search((body_text or "")[:1200]):
        return True
    for selector in selectors_present or []:
        if selector in _WALL_SELECTORS:
            return True
    return False


async def page_has_wall(page) -> bool:
    """Evaluate wall signals against a live Playwright page."""
    try:
        url = page.url
    except Exception:
        return False
    try:
        title = await page.title()
    except Exception:
        title = ""
    present: list[str] = []
    for selector in ("iframe[src*='recaptcha']", "#challenge-form", "input[type='password']"):
        try:
            if await page.locator(selector).count() > 0:
                present.append(selector)
        except Exception:
            pass
    body = ""
    try:
        body = (await page.inner_text("body", timeout=2000))[:1200]
    except Exception:
        pass
    return detect_wall(url, title, body, present)


class CheckpointTimeout(Exception):
    """Raised when a wall is not cleared within the allotted time."""

    def __init__(self, source: str):
        self.source = source
        super().__init__(f"checkpoint timed out for source '{source}'")


async def run_checkpoint(
    page,
    reporter,
    timeout: float,
    *,
    wall_detector=page_has_wall,
    poll_interval: float = 1.5,
) -> bool:
    """Run the checkpoint state machine for the current page.

    Returns True when there is no wall, or one appeared and was cleared in
    time — either because the page no longer shows a wall, or because the
    human pressed "Continue" in the UI. Raises `CheckpointTimeout` if the
    timer expires first.

    `reporter` must expose `wall_started(deadline)`, `wall_cleared()`,
    `wall_timed_out()` and a `source` attribute. It may optionally expose
    `resume_requested()` -> bool for the manual "Continue" signal (see
    worker.OutcomeReporter).
    """
    if not await wall_detector(page):
        return True

    deadline = datetime.utcnow() + timedelta(seconds=timeout)
    reporter.wall_started(deadline)

    resume_requested = getattr(reporter, "resume_requested", None)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        await asyncio.sleep(poll_interval)
        # The human explicitly told us they cleared the wall.
        if resume_requested is not None and resume_requested():
            reporter.wall_cleared()
            return True
        try:
            if not await wall_detector(page):
                reporter.wall_cleared()
                return True
        except Exception:
            # A transient page error mid-check should not end the wait.
            pass

    reporter.wall_timed_out()
    raise CheckpointTimeout(getattr(reporter, "source", "source"))
