"""
Background scheduler — polls the database every N minutes and processes
any ``READY`` AI-tool records through the full posting pipeline.

Key features:
  * Retry decorator (3 attempts, exponential backoff) for every platform call.
  * Per-platform status columns updated individually.
  * Local video file cleaned up after each record is processed.
"""

import time
import functools
from datetime import datetime, timezone
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import SessionLocal
from app.models import AITool
from app.services.caption_generator import generate_captions
from app.services.video_downloader import cleanup_video, download_video
from app.services.linkedin_service import post_to_linkedin
from app.services.instagram_service import post_to_instagram
from app.services.youtube_service import post_to_youtube
from app.services.x_service import post_to_x
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── Retry decorator ──────────────────────────────────────────────────────────

def retry(max_attempts: int = 3, backoff: int = 2) -> Callable:
    """Decorator that retries a function on exception with exponential backoff.

    Args:
        max_attempts: Total number of attempts before giving up.
        backoff: Base delay in seconds (doubles after each failure).
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = backoff
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    if attempt == max_attempts:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__, max_attempts, exc,
                        )
                        return False
                    logger.warning(
                        "%s attempt %d/%d failed (%s). Retrying in %ds...",
                        func.__name__, attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
                    delay *= 2
            return False
        return wrapper
    return decorator


# Wrap each platform call with retry
_post_linkedin = retry(settings.MAX_RETRIES, settings.RETRY_BACKOFF_SECONDS)(post_to_linkedin)
_post_instagram = retry(settings.MAX_RETRIES, settings.RETRY_BACKOFF_SECONDS)(post_to_instagram)
_post_youtube = retry(settings.MAX_RETRIES, settings.RETRY_BACKOFF_SECONDS)(post_to_youtube)
_post_x = retry(settings.MAX_RETRIES, settings.RETRY_BACKOFF_SECONDS)(post_to_x)


# ── Core job ──────────────────────────────────────────────────────────────────

def _process_tool(tool: AITool, db) -> None:  # noqa: ANN001
    """Run the full pipeline for a single AI-tool record."""
    logger.info("Processing tool: %s (id=%d)", tool.tool_name, tool.id)

    # 1. Generate captions ─────────────────────────────────────────────────
    captions = generate_captions(
        tool_name=tool.tool_name,
        description=tool.description,
        website=tool.website,
        handle=tool.handle,
    )

    # 2. Download video ────────────────────────────────────────────────────
    try:
        video_path = download_video(tool.video_url, tool.tool_name)
    except RuntimeError:
        logger.error("Skipping tool %d — video download failed.", tool.id)
        tool.status = "FAILED"
        db.commit()
        return

    # 3. Post to each platform ─────────────────────────────────────────────
    success_count = 0

    # LinkedIn
    if settings.LINKEDIN_ACCESS_TOKEN and settings.LINKEDIN_ORG_ID:
        linkedin_ok = _post_linkedin(captions["linkedin"], video_path)
        tool.linkedin_status = "SUCCESS" if linkedin_ok else "FAILED"
        if linkedin_ok:
            success_count += 1
    else:
        tool.linkedin_status = "SKIPPED"
        logger.info("LinkedIn: skipped (credentials not configured).")

    # Instagram  (uses the *public* video URL, not local path)
    if settings.META_ACCESS_TOKEN and settings.INSTAGRAM_BUSINESS_ID:
        instagram_ok = _post_instagram(captions["instagram"], tool.video_url)
        tool.instagram_status = "SUCCESS" if instagram_ok else "FAILED"
        if instagram_ok:
            success_count += 1
    else:
        tool.instagram_status = "SKIPPED"
        logger.info("Instagram: skipped (credentials not configured).")

    # YouTube
    if settings.YOUTUBE_CLIENT_ID and settings.YOUTUBE_CLIENT_SECRET and settings.YOUTUBE_REFRESH_TOKEN:
        youtube_ok = _post_youtube(tool.tool_name, captions["youtube"], video_path)
        tool.youtube_status = "SUCCESS" if youtube_ok else "FAILED"
        if youtube_ok:
            success_count += 1
    else:
        tool.youtube_status = "SKIPPED"
        logger.info("YouTube: skipped (credentials not configured).")

    # X (Twitter)
    if settings.X_API_KEY and settings.X_API_SECRET and settings.X_ACCESS_TOKEN and settings.X_ACCESS_SECRET:
        x_ok = _post_x(captions["x"], video_path)
        tool.x_status = "SUCCESS" if x_ok else "FAILED"
        if x_ok:
            success_count += 1
    else:
        tool.x_status = "SKIPPED"
        logger.info("X/Twitter: skipped (credentials not configured).")

    # 4. Update overall status ─────────────────────────────────────────────
    # Count how many platforms were actually attempted
    attempted = sum(
        1 for s in (tool.linkedin_status, tool.instagram_status,
                     tool.youtube_status, tool.x_status)
        if s != "SKIPPED"
    )

    if success_count > 0:
        tool.status = "POSTED"
        tool.posted_at = datetime.now(timezone.utc)
        logger.info(
            "Tool %d posted to %d/%d platforms (%d skipped).",
            tool.id, success_count, attempted, 4 - attempted,
        )
    elif attempted == 0:
        tool.status = "FAILED"
        logger.warning(
            "Tool %d: no platforms are configured. Check your .env credentials.",
            tool.id,
        )
    else:
        tool.status = "FAILED"
        logger.warning("Tool %d failed on all %d attempted platforms.", tool.id, attempted)

    db.commit()

    # 5. Cleanup downloaded video ──────────────────────────────────────────
    cleanup_video(video_path)


def check_and_post() -> None:
    """Scheduler entry-point: fetch READY records and process each one."""
    db = SessionLocal()
    try:
        tools = db.query(AITool).filter(AITool.status == "READY").all()
        if not tools:
            logger.debug("No READY tools found.")
            return
        logger.info("Found %d READY tool(s) to process.", len(tools))
        for tool in tools:
            try:
                _process_tool(tool, db)
            except Exception as exc:
                logger.exception(
                    "Unhandled error processing tool %d: %s", tool.id, exc,
                )
                tool.status = "FAILED"
                db.commit()
    finally:
        db.close()


# ── Scheduler setup ──────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()


def start_scheduler() -> None:
    """Register the polling job and start the scheduler."""
    scheduler.add_job(
        check_and_post,
        "interval",
        minutes=settings.SCHEDULER_INTERVAL_MINUTES,
        id="social_media_poster",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — polling every %d minute(s).",
        settings.SCHEDULER_INTERVAL_MINUTES,
    )


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
