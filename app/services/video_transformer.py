"""
Video transformer — pre-processes videos before YouTube upload to minimise
copyright / Content ID strikes.

Transformations applied (YouTube-specific):
  1. Strip original audio (the #1 Content ID trigger — audio fingerprinting)
  2. Add branded text overlay (makes content legally "transformative")
  3. Slight speed adjustment (1.05×) — shifts video fingerprint
  4. Re-encode with unique bitrate/params — further differentiates

Why this works:
  • Content ID matches audio fingerprints first. No audio → no audio match.
  • The visual overlay + speed shift changes the video hash enough to dodge
    visual fingerprint matching.
  • Framing as "review / educational" content strengthens fair-use argument.

Requires: ``ffmpeg`` (pre-installed on Render's Ubuntu image).
Falls back gracefully to the original file if ffmpeg is unavailable.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.utils.logger import get_logger

logger = get_logger(__name__)

_TRANSFORM_DIR = Path(tempfile.gettempdir()) / "execution_posting_yt_transform"


def _ffmpeg_available() -> bool:
    """Check if ffmpeg is on the system PATH."""
    return shutil.which("ffmpeg") is not None


def transform_for_youtube(
    video_path: str,
    tool_name: str,
    *,
    strip_audio: bool = True,
    add_overlay: bool = True,
    speed_factor: float = 1.05,
    overlay_text: str | None = None,
) -> str:
    """Create a YouTube-safe version of the video.

    Args:
        video_path: Path to the original MP4 file.
        tool_name: Name of the AI tool (used in the overlay).
        strip_audio: Remove the audio track entirely (recommended).
        add_overlay: Burn a branded text bar into the video.
        speed_factor: Playback speed multiplier (1.05 = 5 % faster,
                      barely perceptible but changes the fingerprint).
        overlay_text: Custom overlay string.  Defaults to
                      ``"AI Tool Review | execution.ai"``.

    Returns:
        Path to the transformed video file.  If ffmpeg is missing or the
        transform fails, returns the *original* ``video_path`` unchanged
        (graceful fallback).
    """
    if not _ffmpeg_available():
        logger.warning(
            "ffmpeg not found — skipping YouTube video transformation. "
            "Install ffmpeg to enable copyright-avoidance transforms."
        )
        return video_path

    _TRANSFORM_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in tool_name)
    out_path = _TRANSFORM_DIR / f"yt_{safe_name}.mp4"

    # Build the ffmpeg filter chain
    overlay = overlay_text or "AI Tool Review | execution.ai"
    # Escape special chars for ffmpeg drawtext
    overlay_escaped = overlay.replace(":", r"\:").replace("'", r"'\''")

    filters: list[str] = []

    # Speed adjustment — setpts changes video speed
    if speed_factor and speed_factor != 1.0:
        pts_factor = round(1.0 / speed_factor, 6)
        filters.append(f"setpts={pts_factor}*PTS")

    # Branded text overlay at bottom of frame
    if add_overlay:
        filters.append(
            f"drawtext=text='{overlay_escaped}'"
            ":fontsize=22"
            ":fontcolor=white"
            ":x=(w-text_w)/2"
            ":y=h-45"
            ":box=1"
            ":boxcolor=black@0.6"
            ":boxborderw=8"
        )

    filter_str = ",".join(filters) if filters else None

    # Assemble the ffmpeg command
    cmd: list[str] = ["ffmpeg", "-y", "-i", video_path]

    if filter_str:
        cmd += ["-vf", filter_str]

    if strip_audio:
        cmd.append("-an")                       # drop audio entirely
    else:
        # Pitch-shift audio slightly to dodge audio fingerprint
        atempo = speed_factor if speed_factor != 1.0 else 1.0
        cmd += ["-af", f"atempo={atempo}"]

    # Re-encode with slightly different params than the source
    cmd += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",                          # decent quality
        "-movflags", "+faststart",             # web-optimised
        "-max_muxing_queue_size", "1024",
        str(out_path),
    ]

    logger.info(
        "YouTube transform: strip_audio=%s speed=%.2fx overlay=%s",
        strip_audio, speed_factor, bool(add_overlay),
    )
    logger.debug("FFmpeg cmd: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,               # 5-min safety limit
        )
        if result.returncode != 0:
            logger.error("FFmpeg failed (rc=%d): %s", result.returncode, result.stderr[-500:])
            return video_path          # fallback

        if out_path.exists() and out_path.stat().st_size > 0:
            logger.info(
                "YouTube transform OK: %s → %s (%s bytes)",
                video_path,
                out_path,
                f"{out_path.stat().st_size:,}",
            )
            return str(out_path)

        logger.warning("FFmpeg produced empty output — using original video.")
        return video_path

    except subprocess.TimeoutExpired:
        logger.error("FFmpeg timed out after 300s — using original video.")
        return video_path
    except Exception as exc:
        logger.error("YouTube transform error: %s — using original video.", exc)
        return video_path


def cleanup_transformed(video_path: str) -> None:
    """Delete a transformed video file after upload.

    Only removes files inside the transform temp directory.
    """
    try:
        p = Path(video_path)
        if p.exists() and str(p).startswith(str(_TRANSFORM_DIR)):
            p.unlink()
            logger.debug("Cleaned up transformed file: %s", p.name)
    except OSError as exc:
        logger.warning("Could not clean transformed file %s: %s", video_path, exc)
