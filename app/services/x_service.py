"""
X (Twitter) service — upload media via v1.1 chunked upload, then create a
tweet via the v2 endpoint.

Flow:
  1. INIT   — declare the upload.
  2. APPEND — send binary chunks.
  3. FINALIZE — complete the upload and get a media_id.
  4. Poll STATUS until processing_info indicates success.
  5. POST the tweet with the attached media_id.

Ref (media upload): https://developer.x.com/en/docs/media/upload-media/api-reference
Ref (tweets):       https://developer.x.com/en/docs/twitter-api/tweets/manage-tweets
"""

import os
import time
from typing import Optional

import requests
from requests_oauthlib import OAuth1

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
TWEET_URL = "https://api.twitter.com/2/tweets"

CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB


def _oauth() -> OAuth1:
    """Build an OAuth1 signer for every request."""
    return OAuth1(
        settings.X_API_KEY,
        client_secret=settings.X_API_SECRET,
        resource_owner_key=settings.X_ACCESS_TOKEN,
        resource_owner_secret=settings.X_ACCESS_SECRET,
    )


# ── Chunked media upload ─────────────────────────────────────────────────────

def _init_upload(file_size: int) -> str:
    """INIT command — returns a media_id string."""
    resp = requests.post(
        MEDIA_UPLOAD_URL,
        data={
            "command": "INIT",
            "total_bytes": file_size,
            "media_type": "video/mp4",
            "media_category": "tweet_video",
        },
        auth=_oauth(),
        timeout=30,
    )
    if not resp.ok:
        logger.error("X INIT failed: %d %s", resp.status_code, resp.text[:500])
    resp.raise_for_status()
    media_id: str = str(resp.json()["media_id"])
    logger.info("X: INIT complete — media_id=%s", media_id)
    return media_id


def _append_chunks(media_id: str, video_path: str) -> None:
    """APPEND command — upload file in ≤ 4 MB chunks."""
    segment = 0
    with open(video_path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            resp = requests.post(
                MEDIA_UPLOAD_URL,
                data={
                    "command": "APPEND",
                    "media_id": media_id,
                    "segment_index": segment,
                },
                files={"media_data": chunk},
                auth=_oauth(),
                timeout=120,
            )
            resp.raise_for_status()
            segment += 1
    logger.info("X: APPEND complete — %d segment(s) uploaded.", segment)


def _finalize(media_id: str) -> Optional[dict]:
    """FINALIZE command — returns processing_info if async processing needed."""
    resp = requests.post(
        MEDIA_UPLOAD_URL,
        data={
            "command": "FINALIZE",
            "media_id": media_id,
        },
        auth=_oauth(),
        timeout=30,
    )
    if not resp.ok:
        logger.error("X FINALIZE failed: %d %s", resp.status_code, resp.text[:500])
    resp.raise_for_status()
    return resp.json().get("processing_info")


def _wait_for_processing(media_id: str, processing_info: dict, max_wait: int = 300) -> bool:
    """Poll STATUS until the media is ready."""
    deadline = time.time() + max_wait
    check_after = processing_info.get("check_after_secs", 5)

    while time.time() < deadline:
        time.sleep(check_after)
        resp = requests.get(
            MEDIA_UPLOAD_URL,
            params={"command": "STATUS", "media_id": media_id},
            auth=_oauth(),
            timeout=15,
        )
        if not resp.ok:
            continue
        info = resp.json().get("processing_info", {})
        state = info.get("state")
        if state == "succeeded":
            return True
        if state == "failed":
            logger.error("X: media processing failed — %s", info.get("error"))
            return False
        check_after = info.get("check_after_secs", 5)

    logger.error("X: media processing timed out.")
    return False


def _create_tweet(caption: str, media_id: str) -> None:
    """POST a tweet via the v2 API with the uploaded media attached."""
    resp = requests.post(
        TWEET_URL,
        json={
            "text": caption,
            "media": {"media_ids": [media_id]},
        },
        auth=_oauth(),
        timeout=30,
    )
    if not resp.ok:
        logger.error(
            "X tweet creation failed: %d %s", resp.status_code, resp.text[:500]
        )
    resp.raise_for_status()
    tweet_id = resp.json().get("data", {}).get("id", "unknown")
    logger.info("X: tweet published — id=%s", tweet_id)


# ── Public entry point ────────────────────────────────────────────────────────

def post_to_x(caption: str, video_path: str) -> bool:
    """Upload a video and publish a tweet on X.

    Args:
        caption: Tweet text (kept under 280 chars by the caption generator).
        video_path: Local path to the MP4 file.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    try:
        file_size = os.path.getsize(video_path)

        logger.info("X: starting chunked upload (%s bytes)...", f"{file_size:,}")
        media_id = _init_upload(file_size)
        _append_chunks(media_id, video_path)

        processing_info = _finalize(media_id)
        if processing_info:
            logger.info("X: waiting for media processing...")
            if not _wait_for_processing(media_id, processing_info):
                return False

        _create_tweet(caption, media_id)
        return True

    except requests.RequestException as exc:
        resp_text = ""
        if hasattr(exc, "response") and exc.response is not None:
            resp_text = exc.response.text[:500]
        logger.error("X posting failed: %s — response: %s", exc, resp_text)
        return False
