"""
Instagram Reel service — publish a Reel to an Instagram Business Account via
the Meta Graph API.

Flow:
  1. Create a media container with ``media_type=REELS``.
  2. Poll until the container status is ``FINISHED``.
  3. Publish the container.

Ref: https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing
"""

import time

import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

GRAPH_URL = "https://graph.facebook.com/v19.0"


def _create_container(video_url: str, caption: str) -> str:
    """Create an Instagram media container for a Reel.

    Note: The Graph API requires a *public* URL for the video,
    so video_url here should be the original remote URL (not local path).

    Posts as REELS (not VIDEO) to get Reels algorithm boost.
    share_to_feed=true ensures it also appears on the profile grid.
    """
    url = f"{GRAPH_URL}/{settings.INSTAGRAM_BUSINESS_ID}/media"
    params = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": settings.META_ACCESS_TOKEN,
    }
    resp = requests.post(url, data=params, timeout=30)
    resp.raise_for_status()
    container_id: str = resp.json()["id"]
    logger.info("Instagram: container created — %s", container_id)
    return container_id


def _wait_for_container(container_id: str, max_wait: int = 300) -> bool:
    """Poll until the container is ready to publish."""
    url = f"{GRAPH_URL}/{container_id}"
    params = {
        "fields": "status_code",
        "access_token": settings.META_ACCESS_TOKEN,
    }
    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = requests.get(url, params=params, timeout=15)
        if resp.ok:
            status = resp.json().get("status_code")
            if status == "FINISHED":
                return True
            if status == "ERROR":
                logger.error("Instagram: container processing failed.")
                return False
        time.sleep(10)
    logger.error("Instagram: container processing timed out.")
    return False


def _publish(container_id: str) -> None:
    """Publish a ready container."""
    url = f"{GRAPH_URL}/{settings.INSTAGRAM_BUSINESS_ID}/media_publish"
    params = {
        "creation_id": container_id,
        "access_token": settings.META_ACCESS_TOKEN,
    }
    resp = requests.post(url, data=params, timeout=30)
    resp.raise_for_status()
    logger.info("Instagram: Reel published — %s", resp.json().get("id"))


def post_to_instagram(caption: str, video_url: str) -> bool:
    """Publish a Reel to Instagram.

    Args:
        caption: Reel caption text.
        video_url: **Public** direct URL to the MP4
                   (Meta requires a URL it can fetch, not a local path).

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    try:
        container_id = _create_container(video_url, caption)

        if not _wait_for_container(container_id):
            return False

        _publish(container_id)
        return True

    except requests.RequestException as exc:
        logger.error("Instagram posting failed: %s", exc)
        return False
