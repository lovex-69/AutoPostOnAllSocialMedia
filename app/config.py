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
    LINKEDIN_PERSON_URN: Optional[str] = None  # e.g. "urn:li:person:AbC123"

    # ── Meta / Instagram / Facebook (optional until configured) ────────────
    META_ACCESS_TOKEN: Optional[str] = None
    INSTAGRAM_BUSINESS_ID: Optional[str] = None
    FACEBOOK_PAGE_ID: Optional[str] = None

    # ── YouTube (optional until configured) ───────────────────────────────
    YOUTUBE_CLIENT_ID: Optional[str] = None
    YOUTUBE_CLIENT_SECRET: Optional[str] = None
    YOUTUBE_REFRESH_TOKEN: Optional[str] = None
    YOUTUBE_TRANSFORM_VIDEO: bool = True  # Transform video to avoid Content ID

    # ── Supabase Storage (for royalty-free music) ─────────────────────────
    SUPABASE_URL: Optional[str] = None       # e.g. https://xxxx.supabase.co
    SUPABASE_ANON_KEY: Optional[str] = None  # public anon key
    SUPABASE_MUSIC_BUCKET: str = "music"      # bucket name

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

    # ── Telegram Channel posting ────────────────────────────────────────────
    TELEGRAM_CHANNEL_ID: Optional[str] = None  # @channel or numeric chat ID

    # ── Reddit ────────────────────────────────────────────────────────────────
    REDDIT_CLIENT_ID: Optional[str] = None
    REDDIT_CLIENT_SECRET: Optional[str] = None
    REDDIT_USERNAME: Optional[str] = None
    REDDIT_PASSWORD: Optional[str] = None
    REDDIT_SUBREDDIT: Optional[str] = None   # e.g. "AItools" (no r/ prefix)

    # ── Notifications (optional) ──────────────────────────────────────────
    DISCORD_WEBHOOK_URL: Optional[str] = None
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()  # type: ignore[call-arg]
