"""
Smart scheduler — suggests optimal posting times per platform based on
audience engagement data and best-practice research.

Features:
  * Recommends best hours (UTC) per platform & day of week
  * Avoids posting within a cooldown window of the last post
  * Spreads posts across platforms to avoid rate limits
  * Content freshness scoring
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── Optimal posting windows (UTC hours) based on engagement research ────────
# Source: aggregated from Hootsuite, Sprout Social, Buffer 2024/2025 studies
# Times are in UTC — users in IST (+5:30) should note peak times are ~14:00-18:00 UTC

OPTIMAL_HOURS = {
    "linkedin": {
        "weekday": [7, 8, 10, 12, 17],     # Tue-Thu best; mornings + lunch
        "weekend": [9, 10],                  # Lower engagement
        "best_days": [1, 2, 3],              # Tue, Wed, Thu (0=Mon)
    },
    "instagram": {
        "weekday": [6, 9, 12, 15, 18],      # Morning, lunch, evening
        "weekend": [9, 11, 14],              # Slightly later on weekends
        "best_days": [1, 2, 4],              # Tue, Wed, Fri
    },
    "facebook": {
        "weekday": [9, 11, 13, 15],          # Mid-morning to afternoon
        "weekend": [10, 12],                 # Late morning
        "best_days": [0, 2, 4],              # Mon, Wed, Fri
    },
    "youtube": {
        "weekday": [12, 14, 15, 17],         # Afternoon (people browse at lunch/after work)
        "weekend": [9, 10, 11, 15],          # Morning + afternoon
        "best_days": [3, 4, 5, 6],           # Thu, Fri, Sat, Sun
    },
    "x": {
        "weekday": [8, 9, 12, 17, 18],      # Morning commute, lunch, evening
        "weekend": [9, 12],                  # Lower engagement
        "best_days": [0, 1, 2, 3],           # Mon-Thu
    },
}

# Minimum hours between posts to the same platform
COOLDOWN_HOURS = 4


def suggest_posting_time(
    platform: Optional[str] = None,
    last_posted_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Suggest the next optimal posting time.

    Args:
        platform: Specific platform name, or None for general best time.
        last_posted_at: When the last post was made (for cooldown).
        now: Current time (defaults to UTC now).

    Returns:
        {
            "suggested_time": "2025-01-15T14:00:00Z",
            "reason": "Best time for Instagram on Wednesday",
            "day_name": "Wednesday",
            "hour": 14,
            "is_prime_time": True,
        }
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Apply cooldown from last post
    earliest = now + timedelta(minutes=5)  # at least 5 min in the future
    if last_posted_at:
        cooldown_end = last_posted_at + timedelta(hours=COOLDOWN_HOURS)
        if cooldown_end > earliest:
            earliest = cooldown_end

    platforms = [platform] if platform else list(OPTIMAL_HOURS.keys())

    best_time = None
    best_score = -1
    best_reason = ""
    best_platform = ""

    for plat in platforms:
        hours_config = OPTIMAL_HOURS.get(plat, OPTIMAL_HOURS["instagram"])
        best_days = hours_config.get("best_days", list(range(7)))

        # Check the next 48 hours in 1-hour increments
        for hour_offset in range(48):
            candidate = earliest.replace(minute=0, second=0, microsecond=0) + timedelta(hours=hour_offset)
            if candidate < earliest:
                continue

            weekday = candidate.weekday()  # 0=Mon
            hour = candidate.hour
            is_weekend = weekday >= 5

            optimal_hours = hours_config["weekend"] if is_weekend else hours_config["weekday"]

            score = 0
            if hour in optimal_hours:
                score += 10
                # Boost for best days
                if weekday in best_days:
                    score += 5
                # Slight preference for earlier optimal slots
                score += (len(optimal_hours) - optimal_hours.index(hour))

            if score > best_score:
                best_score = score
                best_time = candidate
                best_platform = plat
                day_name = candidate.strftime("%A")
                if score >= 10:
                    best_reason = f"Peak engagement for {plat.title()} on {day_name} at {hour}:00 UTC"
                else:
                    best_reason = f"Next available slot for {plat.title()}"

    if best_time is None:
        best_time = earliest
        best_reason = "No optimal slot found — using next available time"

    return {
        "suggested_time": best_time.isoformat(),
        "reason": best_reason,
        "platform": best_platform,
        "day_name": best_time.strftime("%A"),
        "hour": best_time.hour,
        "is_prime_time": best_score >= 10,
    }


def get_schedule_suggestions(
    last_posted_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> list:
    """Return optimal time suggestions for ALL platforms.

    Returns a list of suggestion dicts, one per platform, sorted by time.
    """
    suggestions = []
    for platform in OPTIMAL_HOURS:
        suggestion = suggest_posting_time(
            platform=platform,
            last_posted_at=last_posted_at,
            now=now,
        )
        suggestions.append(suggestion)

    # Sort by suggested time
    suggestions.sort(key=lambda s: s["suggested_time"])
    return suggestions


def check_content_freshness(
    tool_name: str,
    existing_tools: list,
    days_threshold: int = 7,
) -> Optional[dict]:
    """Check if similar content was posted recently.

    Returns a warning dict if a tool with the same name was posted within
    the threshold, or None if the content is fresh.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days_threshold)

    for existing in existing_tools:
        if (
            existing.get("tool_name", "").strip().lower() == tool_name.strip().lower()
            and existing.get("status") == "POSTED"
        ):
            posted_at = existing.get("posted_at")
            if posted_at and isinstance(posted_at, str):
                try:
                    posted_dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
            elif isinstance(posted_at, datetime):
                posted_dt = posted_at
            else:
                continue

            if posted_dt > cutoff:
                days_ago = (now - posted_dt).days
                return {
                    "type": "freshness",
                    "severity": "warning",
                    "message": (
                        f"'{tool_name}' was posted {days_ago} day(s) ago. "
                        f"Posting the same tool within {days_threshold} days may "
                        f"reduce engagement and appear as spam."
                    ),
                    "existing_id": existing.get("id"),
                    "posted_at": posted_dt.isoformat(),
                    "days_ago": days_ago,
                }

    return None


def get_queue_position(
    scheduled_at: Optional[datetime],
    ready_count: int,
    scheduler_interval_minutes: int = 5,
) -> dict:
    """Estimate when a tool will be processed based on queue position.

    Returns posting estimate information.
    """
    now = datetime.now(timezone.utc)

    if scheduled_at and scheduled_at > now:
        wait_minutes = (scheduled_at - now).total_seconds() / 60
        return {
            "position": "scheduled",
            "estimated_at": scheduled_at.isoformat(),
            "wait_minutes": round(wait_minutes),
            "message": f"Scheduled for {scheduled_at.strftime('%b %d at %H:%M UTC')}",
        }

    # Immediate posting — depends on queue size and scheduler interval
    estimated_wait = ready_count * 2 + scheduler_interval_minutes  # ~2 min per tool + next cycle
    estimated_at = now + timedelta(minutes=estimated_wait)

    return {
        "position": ready_count + 1,
        "estimated_at": estimated_at.isoformat(),
        "wait_minutes": estimated_wait,
        "message": (
            f"Queue position: #{ready_count + 1}. "
            f"Estimated processing in ~{estimated_wait} minutes."
        ),
    }
