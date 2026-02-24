"""
Facebook Reels service — publish a Reel (or video) to a Facebook Page via
the Meta Graph API.

Flow:
  1. Exchange the user access token for a Page Access Token.
  2. Initiate an upload session for a Resumable Upload.
  3. Upload the video binary.
  4. Finish the upload and publish the Reel.

Uses the same META_ACCESS_TOKEN as Instagram (the token must have
``pages_manage_posts`` and ``pages_read_engagement`` permissions).
The user token is automatically exchanged for a Page token at runtime.

Ref: https://developers.facebook.com/docs/video-api/guides/reels-publishing
"""

import os
import time
from datetime import datetime, timezone

import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

GRAPH_URL = "https://graph.facebook.com/v19.0"

# Cache the page token with a TTL so it auto-refreshes if the user token changes
_page_token_cache: str | None = None
_page_token_cached_at: datetime | None = None
_PAGE_TOKEN_TTL_SECONDS = 3600  # Re-fetch every hour


def clear_page_token_cache() -> None:
    """Invalidate the cached page token (call when user token is refreshed)."""
    global _page_token_cache, _page_token_cached_at
    _page_token_cache = None
    _page_token_cached_at = None
    logger.info("Facebook: page token cache cleared.")


def _get_page_access_token() -> str:
    """Exchange the user access token for a Page Access Token.

    The Graph API requires a Page token (not a user token) to publish
    content on behalf of a Page. Caches with TTL to avoid excessive API calls.
    """
    global _page_token_cache, _page_token_cached_at

    # Return cached token if still fresh
    if _page_token_cache and _page_token_cached_at:
        elapsed = (datetime.now(timezone.utc) - _page_token_cached_at).total_seconds()
        if elapsed < _PAGE_TOKEN_TTL_SECONDS:
            return _page_token_cache

    page_id = settings.FACEBOOK_PAGE_ID
    resp = requests.get(
        f"{GRAPH_URL}/me/accounts",
        params={
            "fields": "id,access_token",
            "access_token": settings.META_ACCESS_TOKEN,
        },
        timeout=15,
    )
    resp.raise_for_status()

    for page in resp.json().get("data", []):
        if page["id"] == page_id:
            _page_token_cache = page["access_token"]
            _page_token_cached_at = datetime.now(timezone.utc)
            logger.info("Facebook: obtained Page Access Token for page %s", page_id)
            return _page_token_cache

    raise RuntimeError(
        f"Facebook: Page {page_id} not found in /me/accounts. "
        "Check that the user token has pages_manage_posts permission for this page."
    )


def _upload_video_to_facebook(video_path: str) -> str:
    """Upload a video file to Facebook and return the video_id."""
    page_id = settings.FACEBOOK_PAGE_ID
    page_token = _get_page_access_token()
    file_size = os.path.getsize(video_path)

    # Step 1 — Start an upload session
    start_url = f"{GRAPH_URL}/{page_id}/video_reels"
    start_params = {
        "upload_phase": "start",
        "access_token": page_token,
    }
    resp = requests.post(start_url, data=start_params, timeout=30)
    resp.raise_for_status()
    video_id = resp.json()["video_id"]
    logger.info("Facebook: upload session started — video_id=%s", video_id)

    # Step 2 — Upload binary
    upload_url = f"{GRAPH_URL}/{video_id}"
    headers = {
        "Authorization": f"OAuth {page_token}",
        "offset": "0",
        "file_size": str(file_size),
    }
    with open(video_path, "rb") as f:
        upload_resp = requests.post(
            upload_url,
            headers=headers,
            data=f,
            timeout=300,
        )
    upload_resp.raise_for_status()
    logger.info("Facebook: video binary uploaded for video_id=%s", video_id)

    return video_id


def _publish_reel(video_id: str, caption: str) -> None:
    """Finish the upload and publish the Reel on the Page.

    Publishes explicitly as a Reel (not a regular video) for maximum
    reach via Facebook's Reels algorithm.
    """
    page_id = settings.FACEBOOK_PAGE_ID
    page_token = _get_page_access_token()

    url = f"{GRAPH_URL}/{page_id}/video_reels"
    params = {
        "upload_phase": "finish",
        "video_id": video_id,
        "title": caption[:255] if caption else "",
        "description": caption,
        "video_state": "PUBLISHED",
        "access_token": page_token,
    }
    resp = requests.post(url, data=params, timeout=60)
    resp.raise_for_status()
    result = resp.json()

    if result.get("success"):
        logger.info("Facebook: Reel published — video_id=%s", video_id)
    else:
        logger.warning("Facebook: publish response — %s", result)


def post_to_facebook(caption: str, video_path: str) -> bool:
    """Publish a Reel to a Facebook Page.

    Args:
        caption: Post caption / description.
        video_path: Local path to the downloaded MP4 file.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    try:
        video_id = _upload_video_to_facebook(video_path)
        _publish_reel(video_id, caption)
        return True
    except requests.RequestException as exc:
        logger.error("Facebook posting failed: %s", exc)
        return False
