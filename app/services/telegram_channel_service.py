"""
Telegram Channel posting service — sends video posts to a Telegram channel.

Uses the Telegram Bot API to send a video + caption to a channel.
The bot must be added as an admin to the target channel.

Required env vars:
  TELEGRAM_BOT_TOKEN          — same bot used for notifications
  TELEGRAM_CHANNEL_ID         — channel username (@mychannel) or numeric ID
"""

import os
import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def post_to_telegram_channel(caption: str, video_path: str) -> bool:
    """Upload a video to the configured Telegram channel.

    Args:
        caption: Post caption/text (HTML formatting supported, max 1024 chars).
        video_path: Local path to the video file.

    Returns:
        True on success, False on failure.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    channel_id = getattr(settings, "TELEGRAM_CHANNEL_ID", None)

    if not token:
        logger.error("Telegram channel post failed: TELEGRAM_BOT_TOKEN not set.")
        return False
    if not channel_id:
        logger.error("Telegram channel post failed: TELEGRAM_CHANNEL_ID not set.")
        return False

    if not os.path.isfile(video_path):
        logger.error("Telegram channel post failed: video file not found: %s", video_path)
        return False

    file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if file_size_mb > 50:
        logger.warning("Video is %.1f MB — Telegram limit is 50 MB for bot uploads.", file_size_mb)
        # Try anyway; Telegram may reject it

    url = f"https://api.telegram.org/bot{token}/sendVideo"

    # Trim caption to 1024 chars (Telegram limit for video captions)
    trimmed_caption = caption[:1024] if caption else ""

    try:
        with open(video_path, "rb") as vf:
            resp = requests.post(
                url,
                data={
                    "chat_id": channel_id,
                    "caption": trimmed_caption,
                    "parse_mode": "HTML",
                    "supports_streaming": "true",
                },
                files={"video": (os.path.basename(video_path), vf, "video/mp4")},
                timeout=120,  # Large uploads may take time
            )

        if resp.ok:
            result = resp.json().get("result", {})
            msg_id = result.get("message_id", "?")
            logger.info(
                "Telegram channel: posted video to %s (message_id=%s)",
                channel_id, msg_id,
            )
            return True
        else:
            error = resp.json().get("description", resp.text)
            logger.error(
                "Telegram channel post failed (%d): %s",
                resp.status_code, error,
            )
            return False

    except requests.Timeout:
        logger.error("Telegram channel post timed out (video may be too large).")
        return False
    except Exception as exc:
        logger.exception("Telegram channel post error: %s", exc)
        return False
