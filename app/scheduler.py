"""
Background scheduler — polls the database every N minutes and processes
any ``READY`` AI-tool records through the full posting pipeline.

Key features:
  * Retry decorator (3 attempts, exponential backoff) for every platform call.
  * Per-platform status columns updated individually.
  * Local video file cleaned up after each record is processed.
"""

import os
import time
import functools
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests
from apscheduler.schedulers.background import BackgroundScheduler

from app.config import settings
from app.database import SessionLocal
from app.models import AITool
from app.services.caption_generator import generate_captions
from app.services.video_downloader import cleanup_video, download_video
from app.services.linkedin_service import post_to_linkedin
from app.services.instagram_service import post_to_instagram
from app.services.facebook_service import post_to_facebook
from app.services.youtube_service import post_to_youtube
from app.services.x_service import post_to_x
from app.services.notification_service import notify_success, notify_failure
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Directory where user uploads are stored
_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads")


# ── Retry decorator ──────────────────────────────────────────────────────────

def retry(max_attempts: int = 3, backoff: int = 2) -> Callable:
    """Decorator that retries a function on exception OR False return with
    exponential backoff.

    Args:
        max_attempts: Total number of attempts before giving up.
        backoff: Base delay in seconds (doubles after each failure).

    The last error message is stored on the wrapper as ``.last_error`` after
    each call so callers can inspect it for logging.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = backoff
            wrapper.last_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    result = func(*args, **kwargs)
                    if result is False:
                        raise RuntimeError(f"{func.__name__} returned False")
                    wrapper.last_error = None
                    return result
                except Exception as exc:
                    wrapper.last_error = str(exc)
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
        wrapper.last_error = None
        return wrapper
    return decorator


# Wrap each platform call with retry
_post_linkedin = retry(settings.MAX_RETRIES, settings.RETRY_BACKOFF_SECONDS)(post_to_linkedin)
_post_instagram = retry(settings.MAX_RETRIES, settings.RETRY_BACKOFF_SECONDS)(post_to_instagram)
_post_facebook = retry(settings.MAX_RETRIES, settings.RETRY_BACKOFF_SECONDS)(post_to_facebook)
_post_youtube = retry(settings.MAX_RETRIES, settings.RETRY_BACKOFF_SECONDS)(post_to_youtube)
_post_x = retry(settings.MAX_RETRIES, settings.RETRY_BACKOFF_SECONDS)(post_to_x)


# ── Cleanup helpers ───────────────────────────────────────────────────────────

# How long to keep uploaded videos (allows retries before cleanup)
_UPLOAD_RETENTION_DAYS = 2


def _cleanup_old_uploads() -> None:
    """Delete uploaded video files older than _UPLOAD_RETENTION_DAYS.

    Runs on a schedule so that videos remain available for retries but
    don't accumulate indefinitely on disk.
    """
    uploads_dir = Path(_UPLOAD_DIR)
    if not uploads_dir.exists():
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=_UPLOAD_RETENTION_DAYS)
    cleaned = 0

    for f in uploads_dir.iterdir():
        if not f.is_file():
            continue
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                cleaned += 1
                logger.info("Cleanup: deleted old upload %s (modified %s)", f.name, mtime.date())
        except OSError as exc:
            logger.warning("Cleanup: could not delete %s: %s", f.name, exc)

    if cleaned:
        logger.info("Cleanup: removed %d file(s) older than %d days.", cleaned, _UPLOAD_RETENTION_DAYS)


def cleanup_uploaded_file(video_url: str) -> None:
    """Delete a specific uploaded file (used when a tool record is deleted).

    Only removes files inside the uploads/ directory.
    """
    try:
        if not video_url:
            return
        normalised = os.path.normpath(video_url)
        uploads_dir = os.path.normpath(_UPLOAD_DIR)
        if normalised.startswith(uploads_dir) and os.path.exists(normalised):
            os.remove(normalised)
            logger.info("Deleted uploaded file: %s", normalised)
    except OSError as exc:
        logger.warning("Failed to delete uploaded file %s: %s", video_url, exc)


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
        tool.error_log = "Video download/copy failed — file may be missing or URL unreachable."
        db.commit()
        return

    # 3. Post to each platform ─────────────────────────────────────────────
    try:
        success_count = 0
        error_parts = []   # collect per-platform errors for the error_log

        # LinkedIn
        if settings.LINKEDIN_ACCESS_TOKEN and (settings.LINKEDIN_ORG_ID or settings.LINKEDIN_PERSON_URN):
            if tool.linkedin_status == "SUCCESS":
                logger.info("LinkedIn: already SUCCESS — skipping to avoid duplicate.")
            else:
                linkedin_ok = _post_linkedin(captions["linkedin"], video_path)
                tool.linkedin_status = "SUCCESS" if linkedin_ok else "FAILED"
                if linkedin_ok:
                    success_count += 1
                else:
                    error_parts.append(f"LinkedIn: {_post_linkedin.last_error or 'posting failed'}")
        else:
            tool.linkedin_status = "SKIPPED"
            logger.info("LinkedIn: skipped (credentials not configured).")

        # Instagram  (uses the *public* video URL, not local path)
        if settings.META_ACCESS_TOKEN and settings.INSTAGRAM_BUSINESS_ID:
            if tool.instagram_status == "SUCCESS":
                logger.info("Instagram: already SUCCESS — skipping to avoid duplicate.")
            else:
                instagram_ok = _post_instagram(captions["instagram"], tool.video_url)
                tool.instagram_status = "SUCCESS" if instagram_ok else "FAILED"
                if instagram_ok:
                    success_count += 1
                else:
                    error_parts.append(f"Instagram: {_post_instagram.last_error or 'posting failed'}")
        else:
            tool.instagram_status = "SKIPPED"
            logger.info("Instagram: skipped (credentials not configured).")

        # Facebook Reels  (uses local video path)
        if settings.META_ACCESS_TOKEN and settings.FACEBOOK_PAGE_ID:
            if tool.facebook_status == "SUCCESS":
                logger.info("Facebook: already SUCCESS — skipping to avoid duplicate.")
            else:
                facebook_ok = _post_facebook(captions["facebook"], video_path)
                tool.facebook_status = "SUCCESS" if facebook_ok else "FAILED"
                if facebook_ok:
                    success_count += 1
                else:
                    error_parts.append(f"Facebook: {_post_facebook.last_error or 'posting failed'}")
        else:
            tool.facebook_status = "SKIPPED"
            logger.info("Facebook: skipped (credentials not configured).")

        # YouTube Shorts
        if settings.YOUTUBE_CLIENT_ID and settings.YOUTUBE_CLIENT_SECRET and settings.YOUTUBE_REFRESH_TOKEN:
            if tool.youtube_status == "SUCCESS":
                logger.info("YouTube: already SUCCESS — skipping to avoid duplicate.")
            else:
                youtube_ok = _post_youtube(tool.tool_name, captions["youtube"], video_path)
                tool.youtube_status = "SUCCESS" if youtube_ok else "FAILED"
                if youtube_ok:
                    success_count += 1
                else:
                    error_parts.append(f"YouTube: {_post_youtube.last_error or 'posting failed'}")
        else:
            tool.youtube_status = "SKIPPED"
            logger.info("YouTube: skipped (credentials not configured).")

        # X (Twitter)
        if settings.X_API_KEY and settings.X_API_SECRET and settings.X_ACCESS_TOKEN and settings.X_ACCESS_SECRET:
            if tool.x_status == "SUCCESS":
                logger.info("X: already SUCCESS — skipping to avoid duplicate.")
            else:
                x_ok = _post_x(captions["x"], video_path)
                tool.x_status = "SUCCESS" if x_ok else "FAILED"
                if x_ok:
                    success_count += 1
                else:
                    error_parts.append(f"X: {_post_x.last_error or 'posting failed'}")
        else:
            tool.x_status = "SKIPPED"
            logger.info("X/Twitter: skipped (credentials not configured).")

        # Save error log if any failures
        tool.error_log = " | ".join(error_parts) if error_parts else None

        # 4. Update overall status ─────────────────────────────────────────
        # Count all SUCCESS platforms (including ones that were already done)
        total_success = sum(
            1 for s in (tool.linkedin_status, tool.instagram_status,
                        tool.facebook_status, tool.youtube_status, tool.x_status)
            if s == "SUCCESS"
        )
        attempted = sum(
            1 for s in (tool.linkedin_status, tool.instagram_status,
                         tool.facebook_status, tool.youtube_status, tool.x_status)
            if s != "SKIPPED"
        )

        if total_success > 0:
            tool.status = "POSTED"
            tool.posted_at = datetime.now(timezone.utc)
            logger.info(
                "Tool %d posted to %d/%d platforms (%d skipped).",
                tool.id, total_success, attempted, 5 - attempted,
            )
            notify_success(tool.tool_name, tool.id, {
                "LinkedIn": tool.linkedin_status,
                "Instagram": tool.instagram_status,
                "Facebook": tool.facebook_status,
                "YouTube": tool.youtube_status,
                "X": tool.x_status,
            })
        elif attempted == 0:
            tool.status = "FAILED"
            logger.warning(
                "Tool %d: no platforms are configured. Check your .env credentials.",
                tool.id,
            )
            notify_failure(tool.tool_name, tool.id, {}, "No platforms configured.")
        else:
            tool.status = "FAILED"
            logger.warning("Tool %d failed on all %d attempted platforms.", tool.id, attempted)
            notify_failure(tool.tool_name, tool.id, {
                "LinkedIn": tool.linkedin_status,
                "Instagram": tool.instagram_status,
                "Facebook": tool.facebook_status,
                "YouTube": tool.youtube_status,
                "X": tool.x_status,
            })

        db.commit()

    finally:
        # 5. ALWAYS cleanup temp video (keep original upload for retries)
        cleanup_video(video_path)


def check_and_post() -> None:
    """Scheduler entry-point: fetch READY records and process each one.

    Picks up tools with status READY whose ``scheduled_at`` is either NULL
    (post immediately) or in the past / now.
    """
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    try:
        from sqlalchemy import or_

        tools = (
            db.query(AITool)
            .filter(
                AITool.status == "READY",
                or_(AITool.scheduled_at.is_(None), AITool.scheduled_at <= now),
            )
            .all()
        )
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
                tool.error_log = f"Unhandled error: {exc}"
                db.commit()
    finally:
        db.close()


# ── Scheduler setup ──────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()

# ── Keep-alive ping (prevents Render free-tier from sleeping) ─────────────────

_RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")


def _keep_alive_ping() -> None:
    """Ping our own /health endpoint to prevent Render free-tier sleep."""
    if not _RENDER_URL:
        return
    try:
        resp = requests.get(f"{_RENDER_URL}/health", timeout=10)
        logger.debug("Keep-alive ping: %s", resp.status_code)
    except Exception as exc:
        logger.debug("Keep-alive ping failed: %s", exc)


def start_scheduler() -> None:
    """Register the polling job and start the scheduler.

    Also runs ``check_and_post()`` once immediately on startup so that any
    overdue scheduled posts (missed while the server was sleeping) are
    processed right away instead of waiting for the first interval tick.
    """
    scheduler.add_job(
        check_and_post,
        "interval",
        minutes=settings.SCHEDULER_INTERVAL_MINUTES,
        id="social_media_poster",
        replace_existing=True,
    )

    # Cleanup old uploads every 6 hours (keeps files for 2 days for retries)
    scheduler.add_job(
        _cleanup_old_uploads,
        "interval",
        hours=6,
        id="cleanup_old_uploads",
        replace_existing=True,
    )
    logger.info("Upload cleanup job registered (every 6h, retention=%dd).", _UPLOAD_RETENTION_DAYS)

    # Keep-alive: ping /health every 10 minutes to prevent Render sleep
    if _RENDER_URL:
        scheduler.add_job(
            _keep_alive_ping,
            "interval",
            minutes=10,
            id="keep_alive_ping",
            replace_existing=True,
        )
        logger.info("Keep-alive ping enabled for %s (every 10 min).", _RENDER_URL)

    scheduler.start()
    logger.info(
        "Scheduler started — polling every %d minute(s).",
        settings.SCHEDULER_INTERVAL_MINUTES,
    )

    # Immediately process any overdue / waiting posts from while we slept
    try:
        logger.info("Running startup catch-up check for overdue posts...")
        check_and_post()
    except Exception as exc:
        logger.exception("Startup catch-up failed: %s", exc)


def stop_scheduler() -> None:
    """Gracefully shut down the scheduler."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
