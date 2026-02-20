"""
LinkedIn service — upload a video and publish to LinkedIn.

Supports TWO modes (auto-detected from config):
  • **Organization page** — requires ``LINKEDIN_ORG_ID`` + a token with
    ``w_organization_social`` (Community Management API).
  • **Personal profile** — requires ``LINKEDIN_PERSON_URN`` + a token with
    ``w_member_social`` ("Share on LinkedIn" product).

Flow:
  1. Register a video upload.
  2. Upload the video binary.
  3. Wait for the asset to finish processing.
  4. Create a post referencing the video.

Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api
"""

import os
import time

import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

V2_URL = "https://api.linkedin.com/v2"


def _author_urn() -> str:
    """Return the correct author URN based on what credentials are configured.

    Prefers organization posting; falls back to personal profile.
    """
    if settings.LINKEDIN_ORG_ID:
        return f"urn:li:organization:{settings.LINKEDIN_ORG_ID}"
    if settings.LINKEDIN_PERSON_URN:
        # Can be full URN ("urn:li:person:xxxx") or just the ID
        urn = settings.LINKEDIN_PERSON_URN
        if not urn.startswith("urn:"):
            urn = f"urn:li:person:{urn}"
        return urn
    raise RuntimeError("No LinkedIn author configured (need LINKEDIN_ORG_ID or LINKEDIN_PERSON_URN)")


def _auth_headers() -> dict[str, str]:
    """Return authorization headers for LinkedIn v2 API."""
    return {
        "Authorization": f"Bearer {settings.LINKEDIN_ACCESS_TOKEN}",
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _register_upload(file_size: int) -> tuple[str, str]:
    """Register a video upload and return (upload_url, video_urn)."""
    owner = _author_urn()

    payload = {
        "initializeUploadRequest": {
            "owner": owner,
            "fileSizeBytes": file_size,
        }
    }

    url = f"{V2_URL}/videos?action=initializeUpload"
    resp = requests.post(
        url,
        json=payload,
        headers={**_auth_headers(), "Content-Type": "application/json"},
        timeout=30,
    )

    if not resp.ok:
        logger.error(
            "LinkedIn video init failed: %d %s", resp.status_code, resp.text[:500]
        )
        resp.raise_for_status()

    data = resp.json()["value"]
    upload_url: str = data["uploadInstructions"][0]["uploadUrl"]
    video_urn: str = data["video"]
    logger.info("LinkedIn: video registered — urn=%s (owner=%s)", video_urn, owner)
    return upload_url, video_urn


def _upload_binary(upload_url: str, video_path: str) -> None:
    """PUT the raw video bytes to the upload URL."""
    headers = {
        **_auth_headers(),
        "Content-Type": "application/octet-stream",
    }
    with open(video_path, "rb") as fh:
        resp = requests.put(upload_url, data=fh, headers=headers, timeout=300)
    if not resp.ok:
        logger.error(
            "LinkedIn binary upload failed: %d %s", resp.status_code, resp.text[:500]
        )
    resp.raise_for_status()


def _wait_for_processing(video_urn: str, max_wait: int = 300) -> bool:
    """Poll until the video is ``AVAILABLE`` or timeout."""
    url = f"{V2_URL}/videos/{video_urn}"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = requests.get(url, headers=_auth_headers(), timeout=15)
        if resp.ok:
            status = resp.json().get("status")
            logger.debug("LinkedIn video status: %s", status)
            if status == "AVAILABLE":
                return True
            if status in ("FAILED", "DELETED"):
                logger.error("LinkedIn: video processing ended with status=%s", status)
                return False
        else:
            logger.warning("LinkedIn: video status poll %d %s",
                           resp.status_code, resp.text[:200])
        time.sleep(10)
    return False


def _create_post(video_urn: str, caption: str) -> None:
    """Create a UGC post referencing the video asset."""
    author = _author_urn()

    payload = {
        "author": author,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": caption},
                "shareMediaCategory": "VIDEO",
                "media": [
                    {
                        "status": "READY",
                        "media": video_urn,
                    }
                ],
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }
    resp = requests.post(
        f"{V2_URL}/ugcPosts",
        json=payload,
        headers={**_auth_headers(), "Content-Type": "application/json"},
        timeout=30,
    )

    if not resp.ok:
        logger.error(
            "LinkedIn post creation failed: %d %s", resp.status_code, resp.text[:500]
        )
    resp.raise_for_status()
    logger.info("LinkedIn: post published — %s", resp.headers.get("x-restli-id", "ok"))


def post_to_linkedin(caption: str, video_path: str) -> bool:
    """Upload a video and publish it to LinkedIn.

    Posts as organization if LINKEDIN_ORG_ID is set, otherwise as personal
    profile using LINKEDIN_PERSON_URN.

    Args:
        caption: Post text.
        video_path: Local path to the MP4 file.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    try:
        file_size = os.path.getsize(video_path)

        logger.info("LinkedIn: registering upload (%s bytes) as %s...",
                     f"{file_size:,}", _author_urn())
        upload_url, video_urn = _register_upload(file_size)

        logger.info("LinkedIn: uploading video binary...")
        _upload_binary(upload_url, video_path)

        logger.info("LinkedIn: waiting for processing...")
        if not _wait_for_processing(video_urn):
            logger.error("LinkedIn: video processing timed out or failed.")
            return False

        logger.info("LinkedIn: creating post...")
        _create_post(video_urn, caption)

        logger.info("LinkedIn: post published successfully.")
        return True

    except requests.RequestException as exc:
        resp_text = ""
        if hasattr(exc, "response") and exc.response is not None:
            resp_text = exc.response.text[:500]
        logger.error("LinkedIn posting failed: %s — response: %s", exc, resp_text)
        return False
