"""Resolve funding stage via Google -> Crunchbase page -> Gemini."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import quote_plus

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from app.adapters.checkpoint import CheckpointContext, navigate_with_checkpoint
from app.adapters.google_adapter import GOOGLE_USER_AGENT, resolve_google_link
from app.adapters.wall_detection import detect_wall
from app.llm.gemini import infer_stage_from_crunchbase_page

CHECKPOINT_SOURCE = "crunchbase"


def find_crunchbase_org_url(raw_links: list[dict]) -> Optional[str]:
    for link in raw_links:
        href = resolve_google_link(link.get("href", ""))
        lower = href.lower()
        if "crunchbase.com/organization/" in lower:
            return href.split("?")[0].split("#")[0]
    return None


class StageLookupSession:
    def __init__(
        self,
        playwright: Playwright,
        browser_ref: list[Browser],
        context_ref: list[BrowserContext],
        checkpoint: CheckpointContext,
        storage_path: str,
    ) -> None:
        self._playwright = playwright
        self._browser_ref = browser_ref
        self._context_ref = context_ref
        self._checkpoint = checkpoint
        self._storage_path = storage_path
        self._cache: dict[str, str] = {}

    async def resolve_stage(self, company_name: str) -> str:
        name = company_name.strip()
        if not name:
            return "unknown"

        cache_key = name.lower()
        if cache_key in self._cache:
            return self._cache[cache_key]

        stage = await self._lookup_stage(name)
        self._cache[cache_key] = stage
        await asyncio.sleep(1.0)
        return stage

    async def _lookup_stage(self, company_name: str) -> str:
        context = self._context_ref[0]
        page = await context.new_page()
        try:
            search_url = (
                "https://www.google.com/search?q="
                + quote_plus(f"{company_name} crunchbase")
            )
            page, ok = await navigate_with_checkpoint(
                page,
                search_url,
                CHECKPOINT_SOURCE,
                self._checkpoint,
                self._playwright,
                self._browser_ref,
                self._context_ref,
                self._storage_path,
                user_agent=GOOGLE_USER_AGENT,
            )
            if not ok:
                return "unknown"

            context = self._context_ref[0]
            raw_links = await page.eval_on_selector_all(
                "#search a, div#rso a, a",
                """els => els.map(a => ({href: a.href, text: a.innerText.trim()}))
                    .filter(x => x.href && /^https?:/.test(x.href))""",
            )
            crunchbase_url = find_crunchbase_org_url(raw_links)
            if not crunchbase_url:
                return "unknown"

            await page.close()
            page = await context.new_page()
            page, ok = await navigate_with_checkpoint(
                page,
                crunchbase_url,
                CHECKPOINT_SOURCE,
                self._checkpoint,
                self._playwright,
                self._browser_ref,
                self._context_ref,
                self._storage_path,
                user_agent=GOOGLE_USER_AGENT,
            )
            if not ok:
                return "unknown"

            if await detect_wall(page, source=CHECKPOINT_SOURCE):
                return "unknown"

            body_text = await page.inner_text("body")
            return await infer_stage_from_crunchbase_page(company_name, body_text)
        except Exception:
            return "unknown"
        finally:
            try:
                await page.close()
            except Exception:
                pass


class CrunchbaseStageResolver:
    @asynccontextmanager
    async def session(
        self,
        checkpoint: CheckpointContext,
        storage_dir: str,
    ) -> AsyncIterator[StageLookupSession]:
        os.makedirs(storage_dir, exist_ok=True)
        storage_path = os.path.join(storage_dir, "crunchbase-lookup-state.json")

        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            browser_ref: list[Browser] = [browser]
            ctx_kwargs = {"user_agent": GOOGLE_USER_AGENT}
            if os.path.exists(storage_path):
                ctx_kwargs["storage_state"] = storage_path
            context = await browser.new_context(**ctx_kwargs)
            context_ref: list[BrowserContext] = [context]

            lookup = StageLookupSession(
                playwright, browser_ref, context_ref, checkpoint, storage_path
            )
            try:
                yield lookup
            finally:
                try:
                    await context_ref[0].storage_state(path=storage_path)
                except Exception:
                    pass
                try:
                    await browser_ref[0].close()
                except Exception:
                    pass


crunchbase_stage_resolver = CrunchbaseStageResolver()

# Backwards-compatible alias used by search worker imports
crunchbase_client = crunchbase_stage_resolver
