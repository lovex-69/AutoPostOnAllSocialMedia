"""
LinkedIn service — upload a video and publish to a Company Page.

Flow:
  1. Register an upload via the ``/rest/videos`` API.
  2. Upload the video binary to the provided upload URL.
  3. Wait for the asset to finish processing.
  4. Create a post referencing the processed video asset.

Required token scopes: ``w_organization_social``, ``r_organization_social``

Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api
"""

import os
import time

import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# LinkedIn REST API (versioned) + legacy fallback
REST_URL = "https://api.linkedin.com/rest"
V2_URL = "https://api.linkedin.com/v2"

# Pinned API version  — update when LinkedIn retires this version
LINKEDIN_API_VERSION = "202306"


def _auth_headers(*, versioned: bool = True) -> dict[str, str]:
    """Return authorization headers for LinkedIn REST API."""
    headers = {
        "Authorization": f"Bearer {settings.LINKEDIN_ACCESS_TOKEN}",
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if versioned:
        headers["LinkedIn-Version"] = LINKEDIN_API_VERSION
    return headers


def _register_upload(file_size: int) -> tuple[str, str]:
    """Register a video upload and return (upload_url, video_urn).

    Tries the versioned REST API first; falls back to unversioned /v2.
    """
    owner = f"urn:li:organization:{settings.LINKEDIN_ORG_ID}"
    payload = {
        "initializeUploadRequest": {
            "owner": owner,
            "fileSizeBytes": file_size,
        }
    }

    # Attempt 1 — versioned REST endpoint
    url_rest = f"{REST_URL}/videos?action=initializeUpload"
    resp = requests.post(
        url_rest,
        json=payload,
        headers={**_auth_headers(versioned=True), "Content-Type": "application/json"},
        timeout=30,
    )

    if resp.status_code in (404, 426):
        logger.warning(
            "LinkedIn REST /videos returned %d — falling back to /v2/videos...",
            resp.status_code,
        )
        url_v2 = f"{V2_URL}/videos?action=initializeUpload"
        resp = requests.post(
            url_v2,
            json=payload,
            headers={**_auth_headers(versioned=False), "Content-Type": "application/json"},
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
    logger.info("LinkedIn: video registered — urn=%s", video_urn)
    return upload_url, video_urn


def _upload_binary(upload_url: str, video_path: str) -> None:
    """PUT the raw video bytes to the upload URL returned by LinkedIn."""
    headers = {
        **_auth_headers(versioned=False),
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
    url_rest = f"{REST_URL}/videos/{video_urn}"
    url_v2 = f"{V2_URL}/videos/{video_urn}"

    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = requests.get(url_rest, headers=_auth_headers(versioned=True), timeout=15)
        if resp.status_code in (404, 426):
            resp = requests.get(url_v2, headers=_auth_headers(versioned=False), timeout=15)

        if resp.ok:
            status = resp.json().get("status")
            logger.debug("LinkedIn video status: %s", status)
            if status == "AVAILABLE":
                return True
            if status in ("FAILED", "DELETED"):
                logger.error("LinkedIn: video processing ended with status=%s", status)
                return False
        else:
            logger.warning("LinkedIn: video status check returned %d", resp.status_code)
        time.sleep(10)
    return False


def _create_post(video_urn: str, caption: str) -> None:
    """Create a post referencing the video asset.

    Uses ``/rest/posts`` (preferred); falls back to ``/v2/ugcPosts`` (legacy).
    """
    org_urn = f"urn:li:organization:{settings.LINKEDIN_ORG_ID}"

    # ── Attempt 1: /rest/posts ────────────────────────────────────────────
    posts_payload = {
        "author": org_urn,
        "commentary": caption,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {
            "media": {
                "id": video_urn,
            }
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    resp = requests.post(
        f"{REST_URL}/posts",
        json=posts_payload,
        headers={**_auth_headers(versioned=True), "Content-Type": "application/json"},
        timeout=30,
    )

    if resp.status_code in (404, 426):
        logger.warning(
            "LinkedIn /rest/posts returned %d — falling back to /v2/ugcPosts...",
            resp.status_code,
        )
        ugc_payload = {
            "author": org_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": caption},
                    "shareMediaCategory": "VIDEO",
                    "media": [{"status": "READY", "media": video_urn}],
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        resp = requests.post(
            f"{V2_URL}/ugcPosts",
            json=ugc_payload,
            headers={**_auth_headers(versioned=False), "Content-Type": "application/json"},
            timeout=30,
        )

    if not resp.ok:
        logger.error(
            "LinkedIn post creation failed: %d %s", resp.status_code, resp.text[:500]
        )
    resp.raise_for_status()
    logger.info("LinkedIn: post published — %s", resp.headers.get("x-restli-id", "ok"))


def post_to_linkedin(caption: str, video_path: str) -> bool:
    """Upload a video and publish it to the LinkedIn Company Page.

    Args:
        caption: Post text.
        video_path: Local path to the MP4 file.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    try:
        file_size = os.path.getsize(video_path)

        logger.info("LinkedIn: registering upload (%s bytes)...", f"{file_size:,}")
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
