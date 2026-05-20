"""Tests for the human-in-the-loop checkpoint protocol (SPEC section 5)."""
import asyncio

import pytest

from app.checkpoint import CheckpointTimeout, detect_wall, run_checkpoint


# --- Wall detection (SPEC 5.1) ---------------------------------------------


def test_detect_wall_by_login_url():
    assert detect_wall(url="https://accounts.google.com/signin/v2")
    assert detect_wall(url="https://www.workatastartup.com/login")


def test_detect_wall_by_title():
    assert detect_wall(title="Just a moment...")
    assert detect_wall(title="Sign in - Work at a Startup")


def test_detect_wall_by_selector():
    assert detect_wall(selectors_present=["iframe[src*='recaptcha']"])
    assert detect_wall(selectors_present=["input[type='password']"])


def test_detect_wall_by_body_text():
    assert detect_wall(body_text="Please verify you are human before continuing.")
    assert detect_wall(body_text="Our systems have detected unusual traffic from your network")


def test_no_wall_on_normal_page():
    assert not detect_wall(
        url="https://www.workatastartup.com/companies?role=eng",
        title="Companies hiring",
        body_text="Browse founding engineer roles at YC startups.",
    )


# --- Checkpoint state machine (SPEC 5.2, 5.3) ------------------------------


class FakeReporter:
    source = "test"

    def __init__(self):
        self.started = False
        self.cleared = False
        self.timed_out = False

    def wall_started(self, deadline):
        self.started = True

    def wall_cleared(self):
        self.cleared = True

    def wall_timed_out(self):
        self.timed_out = True


async def test_checkpoint_passes_when_no_wall():
    reporter = FakeReporter()

    async def detector(_page):
        return False

    ok = await run_checkpoint(None, reporter, 1, wall_detector=detector, poll_interval=0.05)
    assert ok is True
    assert reporter.started is False


async def test_checkpoint_resumes_when_wall_clears_in_time():
    state = {"wall": True}
    reporter = FakeReporter()

    async def detector(_page):
        return state["wall"]

    async def clear_soon():
        await asyncio.sleep(0.2)
        state["wall"] = False

    asyncio.create_task(clear_soon())
    ok = await run_checkpoint(None, reporter, 3, wall_detector=detector, poll_interval=0.05)
    assert ok is True
    assert reporter.started and reporter.cleared
    assert reporter.timed_out is False


async def test_checkpoint_times_out_when_wall_persists():
    reporter = FakeReporter()

    async def detector(_page):
        return True

    with pytest.raises(CheckpointTimeout):
        await run_checkpoint(None, reporter, 0.3, wall_detector=detector, poll_interval=0.05)
    assert reporter.started and reporter.timed_out
    assert reporter.cleared is False
