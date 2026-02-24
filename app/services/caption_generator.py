"""
Caption generation service.

Uses Google Gemini to generate engaging, platform-optimized captions with
relevant hashtags and emojis.  Falls back to a simple template builder if
Gemini is unavailable or if the API key is not configured.

Returns a dict with keys: x, linkedin, instagram, facebook, youtube.
"""

from typing import Dict, Optional

import google.generativeai as genai

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Gemini setup ──────────────────────────────────────────────────────────────
_gemini_ready = False
if settings.GEMINI_API_KEY:
    try:
        genai.configure(api_key=settings.GEMINI_API_KEY)
        _gemini_ready = True
        logger.info("Gemini AI configured for caption generation.")
    except Exception as exc:
        logger.warning("Gemini setup failed, using fallback captions: %s", exc)


# ── Gemini-powered captions ──────────────────────────────────────────────────

_PROMPT_TEMPLATE = """\
You are a social-media copywriter for a tech brand called "Execution AI".
Generate captions for an AI tool being showcased in a short-form video post.

Tool info:
- Name: {tool_name}
- Description: {description}
- Website: {website}
- Creator/Handle: {handle}

Generate FIVE separate captions, one for each platform. Each caption MUST be
optimised for that platform's culture, length limits, and audience.

Rules:
1. **X / Twitter**: Max 270 chars. Punchy, hook-driven. 2-3 relevant hashtags. Include website link if provided.
2. **LinkedIn**: Professional, insightful, 3-5 short paragraphs. Use line breaks. 3-5 hashtags at the end. Include website link if provided.
3. **Instagram**: Engaging, emoji-rich, storytelling tone. 8-15 hashtags at the end. Say "Link in bio" instead of the URL.
4. **Facebook**: Conversational, shareable, 2-3 paragraphs. 3-5 hashtags. Include website link if provided.
5. **YouTube**: Title-worthy first line, then a description. Include website link and relevant tags as hashtags.

IMPORTANT: Return ONLY a valid JSON object with exactly these keys:
{{"x": "...", "linkedin": "...", "instagram": "...", "facebook": "...", "youtube": "..."}}

Do NOT wrap in markdown code blocks. Return raw JSON only.
"""


def _generate_with_gemini(
    tool_name: str,
    description: str,
    website: str,
    handle: str,
) -> Optional[Dict[str, str]]:
    """Call Gemini to generate platform-specific captions."""
    if not _gemini_ready:
        return None

    prompt = _PROMPT_TEMPLATE.format(
        tool_name=tool_name,
        description=description or "An innovative AI tool",
        website=website or "N/A",
        handle=handle or "N/A",
    )

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)
        text = response.text.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3].strip()
        if text.startswith("json"):
            text = text[4:].strip()

        import json
        captions = json.loads(text)

        # Validate that all required keys are present
        required = {"x", "linkedin", "instagram", "facebook", "youtube"}
        if required.issubset(captions.keys()):
            logger.info("Gemini AI captions generated for '%s'", tool_name)
            return captions
        else:
            missing = required - set(captions.keys())
            logger.warning("Gemini response missing keys %s, using fallback.", missing)
            return None

    except Exception as exc:
        logger.warning("Gemini caption generation failed: %s. Using fallback.", exc)
        return None


# ── Fallback template captions ───────────────────────────────────────────────

def _fallback_captions(
    tool_name: str,
    description: Optional[str],
    website: Optional[str],
    handle: Optional[str],
) -> Dict[str, str]:
    """Build simple template captions when Gemini is unavailable."""
    desc = description or ""
    site = website or ""
    credit = f" by {handle}" if handle else ""
    headline = f"{tool_name}{credit}"

    # X / Twitter
    x_caption = f"🚀 {headline} — {desc}" if desc else f"🚀 {headline}"
    if site:
        x_caption += f"\n{site}"
    x_caption += "\n#AI #Tech #Innovation"
    x_caption = x_caption[:280]

    # LinkedIn
    linkedin_parts = [f"🔥 {headline}"]
    if desc:
        linkedin_parts.append(desc)
    if site:
        linkedin_parts.append(f"🔗 {site}")
    linkedin_parts.append("#AI #ArtificialIntelligence #Tech #Innovation")
    linkedin_caption = "\n\n".join(linkedin_parts)

    # Instagram
    instagram_parts = [f"🚀 {headline}"]
    if desc:
        instagram_parts.append(desc)
    instagram_parts.append("🔗 Link in bio!")
    instagram_parts.append(
        "#AI #ArtificialIntelligence #Tech #Innovation #AITools "
        "#FutureTech #Automation #MachineLearning"
    )
    instagram_caption = "\n\n".join(instagram_parts)

    # Facebook
    facebook_parts = [f"🚀 {headline}"]
    if desc:
        facebook_parts.append(desc)
    if site:
        facebook_parts.append(f"🔗 Check it out: {site}")
    facebook_parts.append("#AI #Tech #Innovation")
    facebook_caption = "\n\n".join(facebook_parts)

    # YouTube
    youtube_parts = [f"{headline} | AI Tool Spotlight"]
    if desc:
        youtube_parts.append(desc)
    if site:
        youtube_parts.append(f"🔗 {site}")
    youtube_parts.append("#AI #Tech #Innovation #AITools")
    youtube_caption = "\n\n".join(youtube_parts)

    return {
        "x": x_caption,
        "linkedin": linkedin_caption,
        "instagram": instagram_caption,
        "facebook": facebook_caption,
        "youtube": youtube_caption,
    }


# ── Public API ────────────────────────────────────────────────────────────────

def generate_captions(
    tool_name: str,
    description: Optional[str],
    website: Optional[str],
    handle: Optional[str],
) -> Dict[str, str]:
    """Generate platform-specific captions using Gemini AI with fallback.

    Args:
        tool_name: Name of the AI tool.
        description: Short description of the tool.
        website: Official website URL.
        handle: Creator/brand social handle.

    Returns:
        Dict with keys ``x``, ``linkedin``, ``instagram``, ``facebook``, ``youtube``.
    """
    # Try Gemini first
    captions = _generate_with_gemini(tool_name, description, website, handle)
    if captions:
        return captions

    # Fallback
    logger.info("Using fallback captions for '%s'", tool_name)
    return _fallback_captions(tool_name, description, website, handle)
