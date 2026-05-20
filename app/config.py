from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    database_url: str = "sqlite:///./founderhunt.db"
    daily_search_quota: int = 10
    quota_enabled: bool = False
    checkpoint_timeout_seconds: int = 60
    yc_login_max_attempts: int = 5
    yc_listing_scrolls: int = 6
    max_results_per_search: int = 10
    playwright_storage_dir: str = "./playwright-state"


@lru_cache
def get_settings() -> Settings:
    return Settings()
