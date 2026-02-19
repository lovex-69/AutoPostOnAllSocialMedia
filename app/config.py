"""
Application configuration — loads all secrets and settings from environment
variables via pydantic-settings.  A single `settings` instance is used across
the entire application.
"""

from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Typed, validated configuration sourced from `.env` / environment."""

    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str

    # ── Gemini (Google AI) — optional, not currently used ───────────────
    GEMINI_API_KEY: Optional[str] = None

    # ── LinkedIn (optional until configured) ──────────────────────────────
    LINKEDIN_ACCESS_TOKEN: Optional[str] = None
    LINKEDIN_ORG_ID: Optional[str] = None

    # ── Meta / Instagram (optional until configured) ──────────────────────
    META_ACCESS_TOKEN: Optional[str] = None
    INSTAGRAM_BUSINESS_ID: Optional[str] = None

    # ── YouTube (optional until configured) ───────────────────────────────
    YOUTUBE_CLIENT_ID: Optional[str] = None
    YOUTUBE_CLIENT_SECRET: Optional[str] = None
    YOUTUBE_REFRESH_TOKEN: Optional[str] = None

    # ── X / Twitter (optional until configured) ──────────────────────────
    X_API_KEY: Optional[str] = None
    X_API_SECRET: Optional[str] = None
    X_ACCESS_TOKEN: Optional[str] = None
    X_ACCESS_SECRET: Optional[str] = None

    # ── Frontend (Vercel) ──────────────────────────────────────────────────
    FRONTEND_URL: Optional[str] = "*"

    # ── Auth ──────────────────────────────────────────────────────────────
    APP_SECRET_KEY: str = "change-me-to-a-strong-random-string"

    # ── Scheduler ─────────────────────────────────────────────────────────
    SCHEDULER_INTERVAL_MINUTES: int = 5

    # ── Retry ─────────────────────────────────────────────────────────────
    MAX_RETRIES: int = 3
    RETRY_BACKOFF_SECONDS: int = 2

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()  # type: ignore[call-arg]
