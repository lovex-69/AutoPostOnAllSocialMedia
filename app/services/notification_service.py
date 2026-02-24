"""
Notification service — sends alerts to Discord and/or Telegram when posts
succeed, fail, or encounter errors.

Supports:
  * Discord webhooks (no bot token needed)
  * Telegram Bot API

Both are optional — if the webhook/token isn't configured, that channel is
silently skipped.
"""

import requests

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _send_discord(message: str, color: int = 0x00FF00) -> None:
    """Send a rich embed to a Discord channel via webhook."""
    url = settings.DISCORD_WEBHOOK_URL
    if not url:
        return

    payload = {
        "embeds": [
            {
                "title": "ExecutionPosting",
                "description": message,
                "color": color,
            }
        ]
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.ok:
            logger.debug("Discord notification sent.")
        else:
            logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("Discord notification failed: %s", exc)


def _send_telegram(message: str) -> None:
    """Send a message to a Telegram chat via Bot API."""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.ok:
            logger.debug("Telegram notification sent.")
        else:
            logger.warning("Telegram API returned %d: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)


# ── Public helpers ────────────────────────────────────────────────────────────

def notify_success(tool_name: str, tool_id: int, platforms: dict) -> None:
    """Send a success notification with per-platform breakdown."""
    lines = [f"✅ <b>{tool_name}</b> (#{tool_id}) posted successfully!"]
    for platform, status in platforms.items():
        icon = "✅" if status == "SUCCESS" else ("⏭" if status == "SKIPPED" else "❌")
        lines.append(f"  {icon} {platform}: {status}")

    msg = "\n".join(lines)
    _send_discord(msg, color=0x00FF00)  # green
    _send_telegram(msg)


def notify_failure(tool_name: str, tool_id: int, platforms: dict, error: str = "") -> None:
    """Send a failure notification."""
    lines = [f"❌ <b>{tool_name}</b> (#{tool_id}) FAILED"]
    for platform, status in platforms.items():
        icon = "✅" if status == "SUCCESS" else ("⏭" if status == "SKIPPED" else "❌")
        lines.append(f"  {icon} {platform}: {status}")
    if error:
        lines.append(f"\nError: {error}")

    msg = "\n".join(lines)
    _send_discord(msg, color=0xFF0000)  # red
    _send_telegram(msg)


def notify_token_expiry(platform: str, days_left: int) -> None:
    """Warn about an upcoming token expiration."""
    msg = f"⚠️ <b>{platform}</b> token expires in <b>{days_left} days</b>! Refresh it soon."
    _send_discord(msg, color=0xFFA500)  # orange
    _send_telegram(msg)


def notify_info(message: str) -> None:
    """Send a generic informational notification."""
    _send_discord(message, color=0x3498DB)  # blue
    _send_telegram(message)
