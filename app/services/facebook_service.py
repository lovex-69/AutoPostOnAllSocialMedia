"""
Facebook Reels service — publish a Reel (or video) to a Facebook Page via
the Meta Graph API.

Flow:
  1. Initiate an upload session for a Resumable Upload.
  2. Upload the video binary.
  3. Create a Reel (or video post) with the uploaded file handle.

Uses the same META_ACCESS_TOKEN as Instagram (the token must have
``pages_manage_posts`` and ``pages_read_engagement`` permissions).

Ref: https://developers.facebook.com/docs/video-api/guides/reels-publishing
"""

import os
import time

import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

GRAPH_URL = "https://graph.facebook.com/v19.0"


def _upload_video_to_facebook(video_path: str) -> str:
    """Upload a video file to Facebook and return the file handle.

    Uses the Resumable Upload API so that large files are supported.
    """
    page_id = settings.FACEBOOK_PAGE_ID
    access_token = settings.META_ACCESS_TOKEN
    file_size = os.path.getsize(video_path)
    file_name = os.path.basename(video_path)

    # Step 1 — Start an upload session
    start_url = f"{GRAPH_URL}/{page_id}/video_reels"
    start_params = {
        "upload_phase": "start",
        "access_token": access_token,
    }
    resp = requests.post(start_url, data=start_params, timeout=30)
    resp.raise_for_status()
    video_id = resp.json()["video_id"]
    logger.info("Facebook: upload session started — video_id=%s", video_id)

    # Step 2 — Upload binary
    upload_url = f"{GRAPH_URL}/{video_id}"
    headers = {
        "Authorization": f"OAuth {access_token}",
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
    """Finish the upload and publish the Reel on the Page."""
    page_id = settings.FACEBOOK_PAGE_ID
    access_token = settings.META_ACCESS_TOKEN

    url = f"{GRAPH_URL}/{page_id}/video_reels"
    params = {
        "upload_phase": "finish",
        "video_id": video_id,
        "title": caption[:255] if caption else "",
        "description": caption,
        "access_token": access_token,
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
