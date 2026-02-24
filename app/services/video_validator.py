"""
Video validator service — checks uploaded/URL videos for platform compliance.

Returns structured warnings about:
  * Duration limits per platform (Shorts 60s, Reels 90s, etc.)
  * File size limits per platform
  * Format validation (must be MP4)
  * Aspect ratio hints

All checks are *warnings* — the user can still proceed ("force upload").
"""

import hashlib
import os
import subprocess
import json
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Platform limits ──────────────────────────────────────────────────────────
PLATFORM_LIMITS = {
    "youtube": {
        "label": "YouTube Shorts",
        "max_duration_sec": 60,
        "max_size_mb": 256 * 1024,  # 256 GB effectively unlimited
        "recommended_aspect": "9:16",
        "min_duration_sec": 3,
    },
    "instagram": {
        "label": "Instagram Reels",
        "max_duration_sec": 90,
        "max_size_mb": 1024,  # 1 GB
        "recommended_aspect": "9:16",
        "min_duration_sec": 3,
    },
    "facebook": {
        "label": "Facebook Reels",
        "max_duration_sec": 90,
        "max_size_mb": 1024,  # 1 GB
        "recommended_aspect": "9:16",
        "min_duration_sec": 3,
    },
    "linkedin": {
        "label": "LinkedIn Video",
        "max_duration_sec": 600,  # 10 minutes
        "max_size_mb": 200,
        "recommended_aspect": "any",
        "min_duration_sec": 3,
    },
    "x": {
        "label": "X / Twitter",
        "max_duration_sec": 140,
        "max_size_mb": 512,
        "recommended_aspect": "any",
        "min_duration_sec": 0.5,
    },
}


def _probe_video(file_path: str) -> Optional[dict]:
    """Use ffprobe to extract video metadata.

    Returns dict with: duration, width, height, size_mb, codec, format.
    Returns None if ffprobe fails or the file doesn't exist.
    """
    if not os.path.isfile(file_path):
        return None

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            logger.warning("ffprobe failed for %s: %s", file_path, result.stderr)
            return None

        data = json.loads(result.stdout)
        fmt = data.get("format", {})
        video_stream = None
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video":
                video_stream = stream
                break

        duration = float(fmt.get("duration", 0))
        size_bytes = int(fmt.get("size", 0))
        size_mb = round(size_bytes / (1024 * 1024), 2)
        width = int(video_stream.get("width", 0)) if video_stream else 0
        height = int(video_stream.get("height", 0)) if video_stream else 0
        codec = video_stream.get("codec_name", "unknown") if video_stream else "unknown"
        format_name = fmt.get("format_name", "unknown")

        return {
            "duration_sec": round(duration, 1),
            "size_mb": size_mb,
            "size_bytes": size_bytes,
            "width": width,
            "height": height,
            "codec": codec,
            "format": format_name,
            "aspect_ratio": f"{width}:{height}" if width and height else "unknown",
            "is_vertical": height > width if width and height else None,
        }
    except FileNotFoundError:
        logger.warning("ffprobe not found — video validation disabled")
        return None
    except Exception as exc:
        logger.warning("ffprobe error: %s", exc)
        return None


def compute_video_hash(file_path: str) -> Optional[str]:
    """Compute a SHA-256 hash of the video file for duplicate detection.

    Uses a chunked read to handle large files efficiently. Only hashes
    the first 10MB and last 10MB for speed on very large files.
    """
    if not os.path.isfile(file_path):
        return None

    try:
        file_size = os.path.getsize(file_path)
        hasher = hashlib.sha256()
        chunk_size = 1024 * 1024  # 1MB chunks
        boundary = 10 * 1024 * 1024  # 10MB

        with open(file_path, "rb") as f:
            if file_size <= boundary * 2:
                # Small file: hash everything
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
            else:
                # Large file: hash first 10MB + last 10MB + file size
                read = 0
                while read < boundary:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    read += len(chunk)

                f.seek(-boundary, 2)  # seek from end
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)

                # Include file size to differentiate similar-start/end files
                hasher.update(str(file_size).encode())

        return hasher.hexdigest()
    except Exception as exc:
        logger.warning("Failed to hash video: %s", exc)
        return None


def validate_video(
    file_path: Optional[str] = None,
    video_url: Optional[str] = None,
    tool_name: Optional[str] = None,
    existing_tools: Optional[list] = None,
) -> dict:
    """Run all validation checks and return structured results.

    Args:
        file_path: Local path to the video file (for uploaded files).
        video_url: Original URL (for duplicate URL check).
        tool_name: Name of the tool (for duplicate name check).
        existing_tools: List of dicts with keys: tool_name, video_url, video_hash,
                        created_at, status — for duplicate detection.

    Returns:
        {
            "valid": True,    # False only if a hard block (no warnings-only)
            "warnings": [...],  # list of warning objects
            "info": {...},      # video metadata if probed
            "duplicates": [...],  # matching existing tools
            "video_hash": "...",  # SHA-256 hash (or None)
        }
    """
    warnings = []
    info = {}
    duplicates = []
    video_hash = None

    # ── 1. Probe video metadata (only for local files) ──────────────────
    if file_path and os.path.isfile(file_path):
        probe = _probe_video(file_path)
        if probe:
            info = probe
            duration = probe["duration_sec"]
            size_mb = probe["size_mb"]

            # Duration checks per platform
            for platform, limits in PLATFORM_LIMITS.items():
                max_dur = limits["max_duration_sec"]
                if duration > max_dur:
                    warnings.append({
                        "type": "duration",
                        "severity": "warning",
                        "platform": platform,
                        "message": (
                            f"Video is {_fmt_duration(duration)} — "
                            f"{limits['label']} limit is {_fmt_duration(max_dur)}. "
                            f"This platform may reject or trim the video."
                        ),
                        "detail": {
                            "actual": duration,
                            "limit": max_dur,
                        },
                    })

                min_dur = limits.get("min_duration_sec", 0)
                if duration < min_dur:
                    warnings.append({
                        "type": "duration",
                        "severity": "warning",
                        "platform": platform,
                        "message": (
                            f"Video is only {duration}s — "
                            f"{limits['label']} requires at least {min_dur}s."
                        ),
                        "detail": {"actual": duration, "limit": min_dur},
                    })

            # Size checks per platform
            for platform, limits in PLATFORM_LIMITS.items():
                max_mb = limits["max_size_mb"]
                if size_mb > max_mb:
                    warnings.append({
                        "type": "size",
                        "severity": "warning",
                        "platform": platform,
                        "message": (
                            f"Video is {size_mb:.0f}MB — "
                            f"{limits['label']} limit is {max_mb}MB. "
                            f"Upload will likely fail on this platform."
                        ),
                        "detail": {"actual_mb": size_mb, "limit_mb": max_mb},
                    })

            # Format check
            if probe["codec"] not in ("h264", "hevc", "h265", "vp9"):
                warnings.append({
                    "type": "format",
                    "severity": "info",
                    "platform": "all",
                    "message": (
                        f"Video codec is '{probe['codec']}'. "
                        f"H.264 (MP4) is recommended for best compatibility."
                    ),
                })

            # Aspect ratio hint
            if probe["is_vertical"] is False:
                warnings.append({
                    "type": "aspect_ratio",
                    "severity": "info",
                    "platform": "all",
                    "message": (
                        f"Video is landscape ({probe['width']}x{probe['height']}). "
                        f"Vertical (9:16) is recommended for Shorts, Reels, and Stories."
                    ),
                })

        # Compute hash for duplicate detection
        video_hash = compute_video_hash(file_path)

    # ── 2. Check file extension ──────────────────────────────────────────
    check_path = file_path or video_url or ""
    if check_path and not check_path.lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")):
        ext = os.path.splitext(check_path)[1] or "(none)"
        warnings.append({
            "type": "format",
            "severity": "warning",
            "platform": "all",
            "message": f"File extension is '{ext}'. MP4 is the safest format for all platforms.",
        })

    # ── 3. Duplicate detection ───────────────────────────────────────────
    if existing_tools:
        for existing in existing_tools:
            reasons = []

            # Same tool name (case-insensitive)
            if (
                tool_name
                and existing.get("tool_name")
                and tool_name.strip().lower() == existing["tool_name"].strip().lower()
            ):
                reasons.append("same tool name")

            # Same video URL
            if (
                video_url
                and existing.get("video_url")
                and video_url.strip() == existing["video_url"].strip()
            ):
                reasons.append("same video URL")

            # Same video hash
            if (
                video_hash
                and existing.get("video_hash")
                and video_hash == existing["video_hash"]
            ):
                reasons.append("identical video file")

            if reasons:
                duplicates.append({
                    "existing_id": existing.get("id"),
                    "existing_name": existing.get("tool_name"),
                    "existing_status": existing.get("status"),
                    "created_at": existing.get("created_at"),
                    "reasons": reasons,
                    "message": (
                        f"Possible duplicate of '{existing.get('tool_name')}' "
                        f"(#{existing.get('id')}, {existing.get('status')}): "
                        f"{', '.join(reasons)}."
                    ),
                })

        if duplicates:
            warnings.append({
                "type": "duplicate",
                "severity": "warning",
                "platform": "all",
                "message": (
                    f"Found {len(duplicates)} potential duplicate(s). "
                    f"This content may have already been posted."
                ),
                "duplicates": duplicates,
            })

    return {
        "valid": True,  # We never hard-block; all issues are warnings
        "warnings": warnings,
        "info": info,
        "duplicates": duplicates,
        "video_hash": video_hash,
    }


def get_platform_limits() -> dict:
    """Return platform limits for frontend display."""
    return PLATFORM_LIMITS


def _fmt_duration(seconds: float) -> str:
    """Format seconds into a human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if secs == 0:
        return f"{minutes}m"
    return f"{minutes}m {secs}s"
