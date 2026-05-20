"""Runtime configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


class Settings:
    def __init__(self) -> None:
        self.gemini_api_key: str = os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip()
        self.database_url: str = os.getenv(
            "DATABASE_URL", "sqlite:///./founderhunt.db"
        ).strip()
        self.quota_enabled: bool = _bool("QUOTA_ENABLED", False)
        self.daily_quota: int = _int("DAILY_QUOTA", 10)
        self.checkpoint_timeout_seconds: int = _int("CHECKPOINT_TIMEOUT_SECONDS", 60)
        self.max_results_per_search: int = _int("MAX_RESULTS_PER_SEARCH", 10)
        self.playwright_headless: bool = _bool("PLAYWRIGHT_HEADLESS", False)
        self.sources_concurrent: bool = _bool("SOURCES_CONCURRENT", True)
        self.playwright_state_dir: str = os.getenv(
            "PLAYWRIGHT_STATE_DIR", "playwright-state"
        ).strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
