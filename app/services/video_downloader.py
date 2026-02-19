"""
Video downloader service.

Downloads a direct MP4 URL to a local temporary path so platform services can
upload files from disk.  Also handles the case where the video is already a
local file (e.g. uploaded via the frontend).
"""

import os
import shutil
import tempfile
from pathlib import Path

import requests

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Base directory for downloaded videos — use a cross-platform temp dir
VIDEO_DIR = Path(tempfile.gettempdir()) / "execution_posting_videos"


def _is_local_path(value: str) -> bool:
    """Return True if *value* looks like a local filesystem path rather than a URL."""
    # Absolute Windows path (e.g. E:\...) or UNC (\\server\...)
    if len(value) >= 2 and value[1] == ":":
        return True
    if value.startswith("\\\\"):
        return True
    # Absolute Unix-style path
    if value.startswith("/"):
        return True
    # Relative path that exists on disk (uploads/...)
    if os.path.exists(value):
        return True
    return False


def download_video(video_url: str, tool_name: str) -> str:
    """Obtain a local MP4 file for the given video source.

    If *video_url* is already a local file path (e.g. from a user upload),
    the file is copied into the temp working directory so that cleanup
    never removes the original upload.

    If *video_url* is an HTTP(S) URL the file is stream-downloaded.

    Args:
        video_url: URL **or** local path to the MP4 file.
        tool_name: Used to build the local filename.

    Returns:
        Absolute path to the local video file.

    Raises:
        RuntimeError: When the download / copy fails.
    """
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    # Sanitise the tool name for use as a filename
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)
    dest = VIDEO_DIR / f"{safe_name}.mp4"

    # ── Local file path ───────────────────────────────────────────────────
    if _is_local_path(video_url):
        src = Path(video_url)
        if not src.exists():
            logger.error("Local video file not found: %s", src)
            raise RuntimeError(f"Local video file not found: {src}")

        logger.info("Copying local video for '%s': %s → %s", tool_name, src, dest)
        shutil.copy2(str(src), str(dest))

        file_size = os.path.getsize(dest)
        logger.info("Video ready: %s (%s bytes)", dest, f"{file_size:,}")
        return str(dest)

    # ── Remote URL ────────────────────────────────────────────────────────
    logger.info("Downloading video for '%s' from %s", tool_name, video_url)

    try:
        with requests.get(video_url, stream=True, timeout=120) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    fh.write(chunk)

        file_size = os.path.getsize(dest)
        logger.info(
            "Video saved: %s (%s bytes)", dest, f"{file_size:,}"
        )
        return str(dest)

    except requests.RequestException as exc:
        logger.error("Video download failed for '%s': %s", tool_name, exc)
        raise RuntimeError(f"Video download failed: {exc}") from exc


def cleanup_video(video_path: str) -> None:
    """Delete a local video file after it has been posted.

    Args:
        video_path: Absolute path to the file to remove.
    """
    try:
        if os.path.exists(video_path):
            os.remove(video_path)
            logger.info("Cleaned up video file: %s", video_path)
    except OSError as exc:
        logger.warning("Failed to delete video file %s: %s", video_path, exc)
