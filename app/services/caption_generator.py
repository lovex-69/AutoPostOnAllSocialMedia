"""
Caption generation service.

Builds platform-specific captions directly from user input fields
(tool name, description, website, handle).  No external API calls.

Returns a dict with keys: x, linkedin, instagram, youtube.
"""

from typing import Dict, Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)


def generate_captions(
    tool_name: str,
    description: Optional[str],
    website: Optional[str],
    handle: Optional[str],
) -> Dict[str, str]:
    """Build platform-specific captions from user-provided fields.

    Args:
        tool_name: Name of the AI tool.
        description: Short description of the tool.
        website: Official website URL.
        handle: Creator/brand social handle.

    Returns:
        Dict with keys ``x``, ``linkedin``, ``instagram``, ``youtube``.
    """
    desc = description or ""
    site = website or ""
    credit = f" by {handle}" if handle else ""

    # ── Shared building blocks ────────────────────────────────────────────
    headline = f"{tool_name}{credit}"
    body = f"{desc}\n\n{site}".strip() if desc else site

    # ── X / Twitter  (≤ 280 chars) ────────────────────────────────────────
    x_caption = f"{headline} — {desc}" if desc else headline
    if site:
        x_caption += f"\n{site}"
    x_caption = x_caption[:280]

    # ── LinkedIn ──────────────────────────────────────────────────────────
    linkedin_parts = [headline]
    if desc:
        linkedin_parts.append(desc)
    if site:
        linkedin_parts.append(site)
    linkedin_caption = "\n\n".join(linkedin_parts)

    # ── Instagram ─────────────────────────────────────────────────────────
    instagram_parts = [headline]
    if desc:
        instagram_parts.append(desc)
    if site:
        instagram_parts.append(f"Link: {site}")
    instagram_caption = "\n\n".join(instagram_parts)

    # ── YouTube ───────────────────────────────────────────────────────────
    youtube_parts = [headline]
    if desc:
        youtube_parts.append(desc)
    if site:
        youtube_parts.append(site)
    youtube_caption = "\n\n".join(youtube_parts)

    # ── Facebook ──────────────────────────────────────────────────────────
    facebook_parts = [headline]
    if desc:
        facebook_parts.append(desc)
    if site:
        facebook_parts.append(site)
    facebook_caption = "\n\n".join(facebook_parts)

    logger.info("Captions built from user input for '%s'", tool_name)

    return {
        "x": x_caption,
        "linkedin": linkedin_caption,
        "instagram": instagram_caption,
        "facebook": facebook_caption,
        "youtube": youtube_caption,
    }
