"""
LinkedIn service — upload a video and publish to LinkedIn.

Supports TWO modes (auto-detected from config):
  • **Organization page** — requires ``LINKEDIN_ORG_ID`` + a token with
    ``w_organization_social`` (Community Management API).
  • **Personal profile** — requires ``LINKEDIN_PERSON_URN`` + a token with
    ``w_member_social`` ("Share on LinkedIn" product).

Flow (using the versioned REST API — ``/rest/*``):
  1. Initialize video upload via ``/rest/videos?action=initializeUpload``.
  2. Upload each binary chunk to the returned URL(s).
  3. Finalize the upload via ``/rest/videos?action=finalizeUpload``.
  4. Poll the video status until ``AVAILABLE``.
  5. Create a post via ``/rest/posts``.

Ref: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api
"""

import os
import time
from urllib.parse import quote

import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

REST_URL = "https://api.linkedin.com/rest"
LINKEDIN_VERSION = "202602"


# ── Helpers ─────────────────────────────────────────────────────────


def _author_urn() -> str:
    """Return the correct author URN based on what credentials are configured.

    Prefers organization posting; falls back to personal profile.
    """
    if settings.LINKEDIN_ORG_ID:
        return f"urn:li:organization:{settings.LINKEDIN_ORG_ID}"
    if settings.LINKEDIN_PERSON_URN:
        urn = settings.LINKEDIN_PERSON_URN
        if not urn.startswith("urn:"):
            urn = f"urn:li:person:{urn}"
        return urn
    raise RuntimeError(
        "No LinkedIn author configured (need LINKEDIN_ORG_ID or LINKEDIN_PERSON_URN)"
    )


def _api_headers(content_type: str = "application/json") -> dict[str, str]:
    """Return authorization + versioning headers for the LinkedIn REST API."""
    return {
        "Authorization": f"Bearer {settings.LINKEDIN_ACCESS_TOKEN}",
        "LinkedIn-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
        "Content-Type": content_type,
    }


# ── Video upload (REST API) ────────────────────────────────────────


def _init_upload(file_size: int) -> dict:
    """Initialize a video upload and return the full response value."""
    owner = _author_urn()

    payload = {
        "initializeUploadRequest": {
            "owner": owner,
            "fileSizeBytes": file_size,
            "uploadCaptions": False,
            "uploadThumbnail": False,
        }
    }

    resp = requests.post(
        f"{REST_URL}/videos?action=initializeUpload",
        json=payload,
        headers=_api_headers(),
        timeout=30,
    )

    if not resp.ok:
        logger.error(
            "LinkedIn video init failed: %d %s", resp.status_code, resp.text[:500]
        )
        resp.raise_for_status()

    data = resp.json()["value"]
    logger.info(
        "LinkedIn: video initialized — %s (owner=%s, parts=%d)",
        data["video"],
        owner,
        len(data.get("uploadInstructions", [])),
    )
    return data


def _upload_chunks(video_path: str, init_data: dict) -> list[str]:
    """Upload binary chunks and return the list of ETags."""
    with open(video_path, "rb") as fh:
        video_bytes = fh.read()

    etags: list[str] = []
    instructions = init_data.get("uploadInstructions", [])

    for idx, instr in enumerate(instructions):
        upload_url = instr["uploadUrl"]
        first_byte = instr.get("firstByte", 0)
        last_byte = instr.get("lastByte", len(video_bytes) - 1)
        chunk = video_bytes[first_byte : last_byte + 1]

        logger.info(
            "LinkedIn: uploading part %d/%d (%s bytes)",
            idx + 1,
            len(instructions),
            f"{len(chunk):,}",
        )

        resp = requests.put(
            upload_url,
            data=chunk,
            headers={"Content-Type": "application/octet-stream"},
            timeout=300,
        )

        if not resp.ok:
            logger.error(
                "LinkedIn chunk upload failed (part %d): %d %s",
                idx + 1,
                resp.status_code,
                resp.text[:500],
            )
            resp.raise_for_status()

        etag = resp.headers.get("ETag", "")
        if etag:
            etags.append(etag)

    return etags


def _finalize_upload(video_urn: str, upload_token: str, etags: list[str]) -> None:
    """Finalize a multi-part video upload."""
    payload = {
        "finalizeUploadRequest": {
            "video": video_urn,
            "uploadToken": upload_token,
            "uploadedPartIds": etags,
        }
    }
    resp = requests.post(
        f"{REST_URL}/videos?action=finalizeUpload",
        json=payload,
        headers=_api_headers(),
        timeout=30,
    )
    if not resp.ok:
        logger.error(
            "LinkedIn video finalize failed: %d %s",
            resp.status_code,
            resp.text[:500],
        )
        resp.raise_for_status()
    logger.info("LinkedIn: video upload finalized.")


def _wait_for_processing(video_urn: str, max_wait: int = 300) -> bool:
    """Poll ``/rest/videos/{urn}`` until the video is ``AVAILABLE``."""
    encoded_urn = quote(video_urn, safe="")
    url = f"{REST_URL}/videos/{encoded_urn}"
    headers = {
        "Authorization": f"Bearer {settings.LINKEDIN_ACCESS_TOKEN}",
        "LinkedIn-Version": LINKEDIN_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }

    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.ok:
            status = resp.json().get("status", "unknown")
            logger.debug("LinkedIn video status: %s", status)
            if status == "AVAILABLE":
                return True
            if status in ("FAILED", "DELETED", "PROCESSING_FAILED"):
                logger.error(
                    "LinkedIn: video processing ended with status=%s", status
                )
                return False
        else:
            logger.warning(
                "LinkedIn: video status poll %d %s",
                resp.status_code,
                resp.text[:200],
            )
        time.sleep(10)
    return False


# ── Post creation (REST API) ──────────────────────────────────────


def _create_post(video_urn: str, caption: str) -> None:
    """Create a post referencing the uploaded video via ``/rest/posts``."""
    author = _author_urn()

    payload = {
        "author": author,
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
        json=payload,
        headers=_api_headers(),
        timeout=30,
    )

    if not resp.ok:
        logger.error(
            "LinkedIn post creation failed: %d %s",
            resp.status_code,
            resp.text[:500],
        )
        resp.raise_for_status()

    post_id = resp.headers.get("x-restli-id", "ok")
    logger.info("LinkedIn: post published — %s", post_id)


# ── Public entry point ─────────────────────────────────────────────


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

        logger.info(
            "LinkedIn: initializing upload (%s bytes) as %s...",
            f"{file_size:,}",
            _author_urn(),
        )
        init_data = _init_upload(file_size)
        video_urn = init_data["video"]
        upload_token = init_data.get("uploadToken", "")

        logger.info("LinkedIn: uploading video chunks...")
        etags = _upload_chunks(video_path, init_data)

        logger.info("LinkedIn: finalizing upload...")
        _finalize_upload(video_urn, upload_token, etags)

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
