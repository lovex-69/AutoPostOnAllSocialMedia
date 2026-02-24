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
You are an elite social-media growth strategist for "Execution AI", a brand that
discovers and showcases the most powerful AI tools via short-form video content.

Your ONLY goal: generate captions that MAXIMIZE reach, engagement, saves, shares,
and follower growth on each platform.

Tool info:
- Name: {tool_name}
- Description: {description}
- Website: {website}
- Creator/Handle: {handle}

Generate FIVE separate captions, one for each platform. Apply these growth tactics:

**UNIVERSAL RULES (apply to ALL platforms):**
- Start with a PATTERN-INTERRUPT hook (question, bold claim, or shocking stat)
- Include a clear CTA (save, share, follow, comment, tag a friend)
- Use curiosity gaps to stop the scroll
- Write like you're talking to a friend, not a brand
- Reference the tool's BENEFIT, not just features
- End with a reason to follow ("Follow @execution.ai for daily AI tools")

**PLATFORM RULES:**

1. **X / Twitter** (max 270 chars):
   - Open with a bold hook: "This AI tool is insane 🤯" or "Stop scrolling."
   - Super punchy, 1-2 sentences max
   - 2-3 viral hashtags (#AI #AITools #Tech)
   - Include website link if provided
   - End with "RT if you agree" or "Bookmark this 🔖"

2. **LinkedIn** (3-5 short paragraphs):
   - Start with a HOOK line that gets clicks on "...see more"
   - Professional but bold — thought-leadership tone
   - Add a personal/industry insight about WHY this tool matters
   - Include line breaks for readability
   - CTA: "Follow Execution AI for daily AI tool spotlights"
   - 5-8 hashtags at end: #AI #ArtificialIntelligence #Innovation #Tech #Productivity #AITools #FutureTech #Automation

3. **Instagram** (Reels-optimized):
   - Hook in first line (this shows in feed preview)
   - Emoji-rich, high energy, storytelling
   - Include "Save this for later 🔖" and "Share with a friend who needs this"
   - Say "Link in bio 🔗" instead of URL
   - "Follow @execution.ai for daily AI tools 🚀"
   - 20-30 hashtags (mix of big + niche): #AI #AITools #ArtificialIntelligence #Tech
     #Innovation #Reels #Viral #Trending #AIReels #TechReels #ProductivityHacks
     #FutureTech #MachineLearning #Automation #DigitalMarketing #Startup #Entrepreneur
     #TechTok #AIApp #AppReview #ToolReview #GrowthHacking #SaaS #NoCode

4. **Facebook** (Reels-optimized):
   - Conversational hook that makes people stop scrolling
   - 2-3 short paragraphs with emojis
   - CTA: "Share this with someone who needs it 👇"
   - "Follow our page for daily AI discoveries!"
   - 5-8 hashtags
   - Include website link if provided

5. **YouTube** (Shorts-optimized):
   - First line = compelling title (this becomes visible in search)
   - Description: SEO-rich, include what the tool does
   - Add #Shorts as FIRST hashtag
   - Include website link
   - "Subscribe for daily AI tool reviews! 🔔"
   - Tags: #Shorts #AI #AITools #Tech #Innovation #YouTubeShorts

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
    """Build engagement-optimized template captions when Gemini is unavailable."""
    desc = description or "a game-changing AI tool you need to try"
    site = website or ""
    credit = f" by {handle}" if handle else ""
    headline = f"{tool_name}{credit}"

    # X / Twitter — punchy hook + CTA
    x_lines = [f"Stop scrolling. This AI tool is insane 🤯"]
    x_lines.append(f"\n{headline} — {desc}")
    if site:
        x_lines.append(f"\n🔗 {site}")
    x_lines.append("\nBookmark this 🔖")
    x_lines.append("\n#AI #AITools #Tech")
    x_caption = "".join(x_lines)[:280]

    # LinkedIn — thought-leadership hook + CTA
    linkedin_parts = [
        f"Most people don't know about {tool_name} yet.\n\nBut it's about to change everything.",
        f"{desc}" if desc else f"This is one of the most powerful AI tools I've seen this year.",
    ]
    if site:
        linkedin_parts.append(f"🔗 Try it: {site}")
    linkedin_parts.append(
        "💡 Follow Execution AI for daily AI tool spotlights that keep you ahead of the curve."
    )
    linkedin_parts.append(
        "#AI #ArtificialIntelligence #Innovation #Tech #Productivity "
        "#AITools #FutureTech #Automation #MachineLearning #Startup"
    )
    linkedin_caption = "\n\n".join(linkedin_parts)

    # Instagram — Reels-optimized with max hashtags
    instagram_parts = [
        f"🤯 This AI tool just changed the game → {headline}",
        f"{desc}" if desc else "You NEED to try this.",
        "💾 Save this for later\n📤 Share with a friend who needs this",
        "🔗 Link in bio!",
        "👉 Follow @execution.ai for daily AI tools 🚀",
        (
            "#AI #AITools #ArtificialIntelligence #Tech #Innovation #Reels "
            "#Viral #Trending #AIReels #TechReels #ProductivityHacks "
            "#FutureTech #MachineLearning #Automation #DigitalMarketing "
            "#Startup #Entrepreneur #TechTok #AIApp #AppReview #ToolReview "
            "#GrowthHacking #SaaS #NoCode #AIHacks #DailyAI #Explore "
            "#ReelsViral #InstaReels #TrendingReels"
        ),
    ]
    instagram_caption = "\n\n".join(instagram_parts)

    # Facebook — Reels-optimized, conversational
    facebook_parts = [
        f"🚀 Have you tried {headline} yet?",
        f"{desc}" if desc else "This AI tool is a must-try.",
    ]
    if site:
        facebook_parts.append(f"🔗 Check it out: {site}")
    facebook_parts.append("👇 Share this with someone who needs it!")
    facebook_parts.append("💡 Follow our page for daily AI discoveries!")
    facebook_parts.append(
        "#AI #AITools #Tech #Innovation #FutureTech #Automation #Reels #Viral"
    )
    facebook_caption = "\n\n".join(facebook_parts)

    # YouTube — Shorts-optimized with #Shorts first
    youtube_parts = [
        f"{headline} — AI Tool You NEED to Try",
        f"{desc}" if desc else "One of the best AI tools right now.",
    ]
    if site:
        youtube_parts.append(f"🔗 Try it: {site}")
    youtube_parts.append("🔔 Subscribe for daily AI tool reviews!")
    youtube_parts.append(
        "#Shorts #AI #AITools #YouTubeShorts #Tech #Innovation "
        "#ArtificialIntelligence #Automation #FutureTech #Trending"
    )
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
