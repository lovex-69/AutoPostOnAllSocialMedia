"""
Video transformer — pre-processes videos before YouTube upload to minimise
copyright / Content ID strikes while keeping audience retention HIGH.

Strategy:
  1. Strip ORIGINAL audio (copyrighted → Content ID trigger)
  2. Replace with royalty-free background music:
     a) User-provided MP3s from ``music/`` folder (preferred — best quality)
     b) Auto-generated ambient lo-fi beat via FFmpeg synthesis (fallback)
  3. Add branded text overlay (makes content "transformative" / fair-use)
  4. Slight speed adjustment (1.05×) — shifts video fingerprint
  5. Re-encode with unique params — further differentiates

Why this works:
  • No original audio → no audio fingerprint match (blocks ~80% of strikes)
  • Replacement music is royalty-free → safe from Content ID
  • Overlay + speed + re-encode dodges visual fingerprinting
  • Background music → viewers stay engaged (silence kills retention)

Requires: ``ffmpeg`` (pre-installed on Render's Ubuntu image).
Falls back gracefully to the original file if ffmpeg is unavailable.
"""

import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.utils.logger import get_logger

logger = get_logger(__name__)

_TRANSFORM_DIR = Path(tempfile.gettempdir()) / "execution_posting_yt_transform"

# User-provided royalty-free music directory
_MUSIC_DIR = Path(__file__).resolve().parent.parent.parent / "music"


def _ffmpeg_available() -> bool:
    """Check if ffmpeg is on the system PATH."""
    return shutil.which("ffmpeg") is not None


# ── Music source selection ────────────────────────────────────────────────────

def _get_user_music_track() -> str | None:
    """Pick a random track from the ``music/`` folder.

    Supports MP3, WAV, OGG, M4A, FLAC, AAC formats.
    Returns ``None`` if no tracks are available.
    """
    if not _MUSIC_DIR.exists():
        return None

    extensions = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}
    tracks = [
        f for f in _MUSIC_DIR.iterdir()
        if f.is_file() and f.suffix.lower() in extensions
    ]

    if not tracks:
        return None

    chosen = random.choice(tracks)
    logger.info("Music: selected user track '%s' from %d available.", chosen.name, len(tracks))
    return str(chosen)


def _generate_ambient_track(duration_secs: float) -> str | None:
    """Generate a lo-fi ambient background track using FFmpeg's audio synthesis.

    Creates a layered ambient soundscape:
      • Warm pad (filtered pink noise → dreamy texture)
      • Sub bass drone (low sine wave → warmth)
      • Gentle high shimmer (filtered noise → airiness)

    The result sounds like a chill lo-fi background track — pleasant,
    non-distracting, and 100% copyright-free (generated, not sampled).

    Returns path to the generated WAV file, or None on failure.
    """
    if not _ffmpeg_available():
        return None

    _TRANSFORM_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _TRANSFORM_DIR / "ambient_bg.wav"

    # Build a multi-layered ambient track using FFmpeg's lavfi filters
    # Layer 1: Warm pink noise pad with bandpass (the "bed")
    # Layer 2: Low sine drone for warmth
    # Layer 3: High-frequency shimmer (filtered white noise)
    # All mixed together and slightly compressed
    filter_complex = (
        # Layer 1: Warm pad — pink noise through a bandpass filter
        f"anoisesrc=color=pink:duration={duration_secs}:seed={random.randint(1,99999)},"
        "highpass=f=200,lowpass=f=800,"
        "volume=0.25,"
        "tremolo=f=0.3:d=0.4"                 # gentle pulsing
        "[pad];"
        # Layer 2: Sub bass drone — two detuned sine waves
        f"sine=frequency=65:duration={duration_secs},"
        "volume=0.15"
        "[bass1];"
        f"sine=frequency=97.5:duration={duration_secs},"   # perfect 5th above
        "volume=0.10"
        "[bass2];"
        # Layer 3: High shimmer — filtered white noise
        f"anoisesrc=color=white:duration={duration_secs}:seed={random.randint(1,99999)},"
        "highpass=f=4000,lowpass=f=8000,"
        "volume=0.06,"
        "tremolo=f=0.5:d=0.3"                 # faster shimmer
        "[shimmer];"
        # Mix all layers
        "[pad][bass1][bass2][shimmer]amix=inputs=4:duration=first:dropout_transition=3,"
        # Master processing: slight compression + fade in/out
        "compand=attacks=0.3:decays=0.8:points=-80/-80|-45/-30|-27/-20|0/-10,"
        f"afade=t=in:st=0:d=2,afade=t=out:st={max(0, duration_secs - 2)}:d=2"
    )

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=stereo",    # dummy input for complex filter
        "-filter_complex", filter_complex,
        "-t", str(duration_secs),
        "-c:a", "pcm_s16le",
        str(out_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            logger.warning("Ambient track generation failed: %s", result.stderr[-300:])
            return None

        if out_path.exists() and out_path.stat().st_size > 0:
            logger.info("Generated ambient track: %s (%.1fs)", out_path.name, duration_secs)
            return str(out_path)

    except Exception as exc:
        logger.warning("Ambient generation error: %s", exc)

    return None


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 60.0  # default assumption for Shorts


# ── Main transformer ─────────────────────────────────────────────────────────

def transform_for_youtube(
    video_path: str,
    tool_name: str,
    *,
    add_overlay: bool = True,
    speed_factor: float = 1.05,
    overlay_text: str | None = None,
    music_volume: float = 0.35,
) -> str:
    """Create a YouTube-safe version of the video with background music.

    Audio strategy (in priority order):
      1. If ``music/`` folder has tracks → pick one randomly
      2. Otherwise → generate ambient lo-fi beat via FFmpeg synthesis
      3. If all fails → strip audio entirely (last resort)

    Args:
        video_path: Path to the original MP4 file.
        tool_name: Name of the AI tool (used in the overlay).
        add_overlay: Burn a branded text bar into the video.
        speed_factor: Playback speed multiplier (1.05 = barely perceptible).
        overlay_text: Custom overlay string.
        music_volume: Volume of background music (0.0–1.0, default 0.35).

    Returns:
        Path to the transformed video file.  Falls back to *original*
        ``video_path`` if ffmpeg is missing or the transform fails.
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

    # ── Get video duration ────────────────────────────────────────────────
    duration = _get_video_duration(video_path)
    adjusted_duration = duration / speed_factor  # after speed-up

    # ── Find background music ─────────────────────────────────────────────
    music_path = _get_user_music_track()
    music_source = "user"

    if not music_path:
        music_path = _generate_ambient_track(adjusted_duration)
        music_source = "generated"

    has_music = music_path is not None
    if has_music:
        logger.info("YouTube transform: using %s music → %s", music_source,
                     Path(music_path).name if music_path else "none")
    else:
        logger.warning("No music available — video will have no audio.")

    # ── Build ffmpeg command ──────────────────────────────────────────────
    overlay = overlay_text or "AI Tool Review | execution.ai"
    overlay_escaped = overlay.replace(":", r"\:").replace("'", r"'\''")

    # Video filters
    vfilters: list[str] = []
    if speed_factor and speed_factor != 1.0:
        pts_factor = round(1.0 / speed_factor, 6)
        vfilters.append(f"setpts={pts_factor}*PTS")
    if add_overlay:
        vfilters.append(
            f"drawtext=text='{overlay_escaped}'"
            ":fontsize=22"
            ":fontcolor=white"
            ":x=(w-text_w)/2"
            ":y=h-45"
            ":box=1"
            ":boxcolor=black@0.6"
            ":boxborderw=8"
        )

    vfilter_str = ",".join(vfilters) if vfilters else None

    # Assemble command
    cmd: list[str] = ["ffmpeg", "-y", "-i", video_path]

    if has_music:
        # Add music as second input
        cmd += ["-i", music_path]

        # Build the command: strip original audio, use music instead
        if vfilter_str:
            cmd += ["-vf", vfilter_str]

        # Audio: loop music if shorter than video, trim to video length,
        # set volume, and fade out at the end
        fade_start = max(0, adjusted_duration - 1.5)
        audio_filter = (
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{adjusted_duration},"
            f"volume={music_volume},"
            f"afade=t=in:st=0:d=1.5,"
            f"afade=t=out:st={fade_start}:d=1.5[bgm]"
        )
        cmd += ["-filter_complex", audio_filter, "-map", "0:v", "-map", "[bgm]"]
    else:
        # No music available — strip audio entirely
        if vfilter_str:
            cmd += ["-vf", vfilter_str]
        cmd.append("-an")

    # Output encoding
    cmd += [
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-max_muxing_queue_size", "1024",
        "-shortest",                           # trim to shortest stream
        str(out_path),
    ]

    logger.info(
        "YouTube transform: music=%s speed=%.2fx overlay=%s volume=%.0f%%",
        music_source if has_music else "none",
        speed_factor,
        bool(add_overlay),
        music_volume * 100,
    )
    logger.debug("FFmpeg cmd: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            logger.error("FFmpeg failed (rc=%d): %s", result.returncode, result.stderr[-500:])
            return video_path

        if out_path.exists() and out_path.stat().st_size > 0:
            logger.info(
                "YouTube transform OK: %s → %s (%s bytes, music=%s)",
                video_path,
                out_path,
                f"{out_path.stat().st_size:,}",
                music_source if has_music else "none",
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
