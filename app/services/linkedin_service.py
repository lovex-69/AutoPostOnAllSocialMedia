"""
LinkedIn service — upload a video and publish to a Company Page.

Flow:
  1. Register an upload via the ``/videos`` API.
  2. Upload the video binary to the provided upload URL.
  3. Wait for the asset to finish processing.
  4. Create a UGC post referencing the processed video asset.

Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api
"""

import time

import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.linkedin.com/v2"
HEADERS: dict[str, str] = {}


def _auth_headers() -> dict[str, str]:
    """Return authorization headers for LinkedIn REST API."""
    return {
        "Authorization": f"Bearer {settings.LINKEDIN_ACCESS_TOKEN}",
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _register_upload() -> tuple[str, str]:
    """Register a video upload and return (upload_url, video_urn)."""
    url = f"{BASE_URL}/videos?action=initializeUpload"
    payload = {
        "initializeUploadRequest": {
            "owner": f"urn:li:organization:{settings.LINKEDIN_ORG_ID}",
            "fileSizeBytes": 0,  # unknown upfront; LinkedIn tolerates this
            "uploadCausalIGEMediaRecipe": "FEED",
            "uploadProtocol": "UPLOADING_STANDARD",
        }
    }
    resp = requests.post(url, json=payload, headers=_auth_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()["value"]
    upload_url: str = data["uploadInstructions"][0]["uploadUrl"]
    video_urn: str = data["video"]
    return upload_url, video_urn


def _upload_binary(upload_url: str, video_path: str) -> None:
    """PUT the raw video bytes to the upload URL returned by LinkedIn."""
    headers = {
        **_auth_headers(),
        "Content-Type": "application/octet-stream",
    }
    with open(video_path, "rb") as fh:
        resp = requests.put(upload_url, data=fh, headers=headers, timeout=300)
    resp.raise_for_status()


def _wait_for_processing(video_urn: str, max_wait: int = 300) -> bool:
    """Poll until the video is ``AVAILABLE`` or timeout."""
    url = f"{BASE_URL}/videos/{video_urn}"
    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = requests.get(url, headers=_auth_headers(), timeout=15)
        if resp.ok:
            status = resp.json().get("status")
            if status == "AVAILABLE":
                return True
            if status in ("FAILED", "DELETED"):
                return False
        time.sleep(10)
    return False


def _create_post(video_urn: str, caption: str) -> None:
    """Create a UGC share (PUBLISHED) referencing the video asset."""
    url = f"{BASE_URL}/ugcPosts"
    payload = {
        "author": f"urn:li:organization:{settings.LINKEDIN_ORG_ID}",
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
    resp = requests.post(url, json=payload, headers={
        **_auth_headers(),
        "Content-Type": "application/json",
    }, timeout=30)
    resp.raise_for_status()


def post_to_linkedin(caption: str, video_path: str) -> bool:
    """Upload a video and publish it to the LinkedIn Company Page.

    Args:
        caption: Post text.
        video_path: Local path to the MP4 file.

    Returns:
        ``True`` on success, ``False`` on failure.
    """
    try:
        logger.info("LinkedIn: registering upload...")
        upload_url, video_urn = _register_upload()

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
        logger.error("LinkedIn posting failed: %s", exc)
        return False
