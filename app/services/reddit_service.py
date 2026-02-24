"""
Reddit posting service — submits video posts to a subreddit.

Uses Reddit's OAuth2 API to upload a video and create a post.

Required env vars:
  REDDIT_CLIENT_ID       — from https://www.reddit.com/prefs/apps (script app)
  REDDIT_CLIENT_SECRET   — app secret
  REDDIT_USERNAME        — Reddit account username
  REDDIT_PASSWORD        — Reddit account password
  REDDIT_SUBREDDIT       — target subreddit (without r/, e.g. "AItools")
"""

import os
import time
import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

_USER_AGENT = "ExecutionPosting/1.0 (by /u/{})".format(
    getattr(settings, "REDDIT_USERNAME", "bot")
)

# Cache the OAuth token
_token_cache: dict = {"token": None, "expires": 0}


def _get_access_token() -> str | None:
    """Obtain a Reddit OAuth2 access token using password grant."""
    client_id = getattr(settings, "REDDIT_CLIENT_ID", None)
    client_secret = getattr(settings, "REDDIT_CLIENT_SECRET", None)
    username = getattr(settings, "REDDIT_USERNAME", None)
    password = getattr(settings, "REDDIT_PASSWORD", None)

    if not all([client_id, client_secret, username, password]):
        logger.error("Reddit: missing credentials (CLIENT_ID/SECRET/USERNAME/PASSWORD).")
        return None

    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires"]:
        return _token_cache["token"]

    try:
        resp = requests.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=(client_id, client_secret),
            data={
                "grant_type": "password",
                "username": username,
                "password": password,
            },
            headers={"User-Agent": _USER_AGENT},
            timeout=15,
        )

        if not resp.ok:
            logger.error("Reddit auth failed (%d): %s", resp.status_code, resp.text)
            return None

        data = resp.json()
        token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)

        if not token:
            logger.error("Reddit auth: no access_token in response: %s", data)
            return None

        _token_cache["token"] = token
        _token_cache["expires"] = now + expires_in - 60  # refresh 60s early

        logger.info("Reddit: obtained OAuth token (expires in %ds).", expires_in)
        return token

    except Exception as exc:
        logger.exception("Reddit auth error: %s", exc)
        return None


def _api_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": _USER_AGENT,
    }


def post_to_reddit(caption: str, video_path: str, tool_name: str = "") -> bool:
    """Upload a video and create a post on the configured subreddit.

    Args:
        caption: Post body text (used as selftext if needed).
        video_path: Local path to the video file.
        tool_name: Used as the post title.

    Returns:
        True on success, False on failure.
    """
    subreddit = getattr(settings, "REDDIT_SUBREDDIT", None)
    if not subreddit:
        logger.error("Reddit: REDDIT_SUBREDDIT not configured.")
        return False

    if not os.path.isfile(video_path):
        logger.error("Reddit: video file not found: %s", video_path)
        return False

    token = _get_access_token()
    if not token:
        return False

    headers = _api_headers(token)
    title = f"{tool_name} — AI Tool You Need to Try" if tool_name else "Check out this AI Tool"

    try:
        # Step 1: Get upload lease from Reddit
        file_size = os.path.getsize(video_path)
        file_name = os.path.basename(video_path)
        mime = "video/mp4"

        resp = requests.post(
            "https://oauth.reddit.com/api/media/asset.json",
            headers=headers,
            data={
                "filepath": file_name,
                "mimetype": mime,
            },
            timeout=15,
        )

        if not resp.ok:
            logger.error("Reddit upload lease failed (%d): %s", resp.status_code, resp.text)
            return False

        lease = resp.json()
        asset = lease.get("asset", {})
        upload_url = "https:" + asset.get("upload_url", "")
        asset_id = asset.get("asset_id", "")

        # Build multipart form from lease fields
        fields = {}
        for item in lease.get("args", {}).get("fields", []):
            fields[item["name"]] = item["value"]

        # Step 2: Upload the file to Reddit's S3
        with open(video_path, "rb") as vf:
            resp2 = requests.post(
                upload_url,
                data=fields,
                files={"file": (file_name, vf, mime)},
                timeout=180,
            )

        if resp2.status_code not in (200, 201, 204):
            logger.error("Reddit S3 upload failed (%d): %s", resp2.status_code, resp2.text[:300])
            return False

        logger.info("Reddit: video uploaded (asset_id=%s)", asset_id)

        # Step 3: Submit the post with the video URL
        video_poster_url = f"https://reddit-uploaded-video.s3-accelerate.amazonaws.com/{asset_id}"
        websocket_url = lease.get("asset", {}).get("websocket_url", "")

        submit_data = {
            "sr": subreddit,
            "kind": "video",
            "title": title[:300],
            "url": video_poster_url,
            "video_poster_url": video_poster_url,
            "sendreplies": True,
            "resubmit": True,
            "api_type": "json",
        }

        resp3 = requests.post(
            "https://oauth.reddit.com/api/submit",
            headers=headers,
            data=submit_data,
            timeout=30,
        )

        if not resp3.ok:
            logger.error("Reddit submit failed (%d): %s", resp3.status_code, resp3.text[:300])
            return False

        result = resp3.json()
        errors = result.get("json", {}).get("errors", [])
        if errors:
            logger.error("Reddit submit errors: %s", errors)
            return False

        post_url = result.get("json", {}).get("data", {}).get("url", "")
        post_id = result.get("json", {}).get("data", {}).get("id", "?")
        logger.info("Reddit: posted to r/%s (id=%s, url=%s)", subreddit, post_id, post_url)
        return True

    except requests.Timeout:
        logger.error("Reddit post timed out.")
        return False
    except Exception as exc:
        logger.exception("Reddit post error: %s", exc)
        return False
