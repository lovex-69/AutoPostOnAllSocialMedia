"""
Telegram Bot — Bidirectional communication.

Runs a polling loop in the scheduler that checks for new messages
from the configured TELEGRAM_CHAT_ID and responds to commands:

  /status      — current queue & posting summary
  /recent      — last 5 posted tools
  /queue       — tools waiting to be posted
  /health      — token health overview
  /help        — command list
  /post <name> <url> — quick-post a tool from Telegram

All responses are sent back to the same chat.
"""

import requests
from datetime import datetime, timezone

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

# Track the last update_id we've processed (avoid re-processing)
_last_update_id: int = 0

BOT_TOKEN = None
CHAT_ID = None


def _init():
    global BOT_TOKEN, CHAT_ID
    BOT_TOKEN = settings.TELEGRAM_BOT_TOKEN
    CHAT_ID = settings.TELEGRAM_CHAT_ID


def _api(method: str, **kwargs):
    """Call Telegram Bot API."""
    if not BOT_TOKEN:
        _init()
    if not BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        r = requests.post(url, json=kwargs, timeout=15)
        return r.json() if r.ok else None
    except Exception as exc:
        logger.warning("Telegram API error (%s): %s", method, exc)
        return None


def _reply(text: str):
    """Send a message to the configured chat."""
    if not CHAT_ID:
        _init()
    if not CHAT_ID:
        return
    _api("sendMessage", chat_id=CHAT_ID, text=text, parse_mode="HTML")


def _handle_status(db):
    """Respond with overall status summary."""
    from sqlalchemy import func
    from app.models import AITool

    total = db.query(func.count(AITool.id)).scalar()
    posted = db.query(func.count(AITool.id)).filter(AITool.status == "POSTED").scalar()
    failed = db.query(func.count(AITool.id)).filter(AITool.status == "FAILED").scalar()
    ready = db.query(func.count(AITool.id)).filter(AITool.status == "READY").scalar()
    rate = round(posted / max(total, 1) * 100, 1)

    msg = (
        "📊 <b>ExecutionPosting Status</b>\n\n"
        f"Total tools: <b>{total}</b>\n"
        f"✅ Posted: <b>{posted}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n"
        f"⏳ In Queue: <b>{ready}</b>\n"
        f"📈 Success Rate: <b>{rate}%</b>"
    )
    _reply(msg)


def _handle_recent(db):
    """Send the 5 most recently posted tools."""
    from app.models import AITool

    tools = (
        db.query(AITool)
        .filter(AITool.status == "POSTED")
        .order_by(AITool.posted_at.desc())
        .limit(5)
        .all()
    )
    if not tools:
        _reply("No posts yet.")
        return

    lines = ["📋 <b>Recent Posts</b>\n"]
    for t in tools:
        platforms = []
        for p in ("linkedin", "instagram", "facebook", "youtube", "x"):
            st = getattr(t, f"{p}_status")
            icon = "✅" if st == "SUCCESS" else ("❌" if st == "FAILED" else "⏭")
            platforms.append(f"{icon}{p[:2].upper()}")
        posted_str = t.posted_at.strftime("%b %d, %H:%M") if t.posted_at else "—"
        lines.append(f"• <b>{t.tool_name}</b> — {posted_str}\n  {' '.join(platforms)}")

    _reply("\n".join(lines))


def _handle_queue(db):
    """Show tools waiting to be posted."""
    from app.models import AITool

    tools = (
        db.query(AITool)
        .filter(AITool.status == "READY")
        .order_by(AITool.created_at.asc())
        .limit(10)
        .all()
    )
    if not tools:
        _reply("✨ Queue is empty — nothing waiting.")
        return

    lines = ["⏳ <b>Queue</b>\n"]
    for i, t in enumerate(tools, 1):
        sched = ""
        if t.scheduled_at:
            sched = f" (scheduled {t.scheduled_at.strftime('%b %d, %H:%M')})"
        lines.append(f"{i}. <b>{t.tool_name}</b>{sched}")

    _reply("\n".join(lines))


def _handle_health():
    """Quick token health overview."""
    parts = ["🛡 <b>Token Health</b>\n"]

    if settings.META_ACCESS_TOKEN:
        try:
            r = requests.get(
                "https://graph.facebook.com/v19.0/debug_token",
                params={
                    "input_token": settings.META_ACCESS_TOKEN,
                    "access_token": settings.META_ACCESS_TOKEN,
                },
                timeout=10,
            )
            if r.ok:
                data = r.json().get("data", {})
                valid = data.get("is_valid", False)
                expires = data.get("expires_at", 0)
                if valid and expires and expires > 0:
                    from datetime import datetime as dt
                    exp_dt = dt.fromtimestamp(expires, tz=timezone.utc)
                    days = (exp_dt - datetime.now(timezone.utc)).days
                    parts.append(f"✅ Meta: Valid ({days} days left)")
                elif valid:
                    parts.append("✅ Meta: Valid (no expiry)")
                else:
                    parts.append("❌ Meta: Invalid")
            else:
                parts.append("❌ Meta: API error")
        except Exception:
            parts.append("❌ Meta: Connection error")
    else:
        parts.append("⚪ Meta: Not configured")

    checks = {
        "LinkedIn": bool(settings.LINKEDIN_ACCESS_TOKEN),
        "YouTube": bool(settings.YOUTUBE_CLIENT_ID and settings.YOUTUBE_REFRESH_TOKEN),
        "X/Twitter": bool(settings.X_API_KEY and settings.X_ACCESS_TOKEN),
        "Discord": bool(settings.DISCORD_WEBHOOK_URL),
        "Gemini AI": bool(settings.GEMINI_API_KEY),
    }
    for name, ok in checks.items():
        parts.append(f"{'✅' if ok else '⚪'} {name}: {'Configured' if ok else 'Not set'}")

    _reply("\n".join(parts))


def _handle_post(text: str, db):
    """Quick-post a tool: /post ToolName https://video.url"""
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 3:
        _reply("Usage: /post <tool_name> <video_url>\nExample: /post ChatGPT https://example.com/vid.mp4")
        return

    _, name, url = parts

    from app.models import AITool
    tool = AITool(
        tool_name=name,
        video_url=url,
        status="READY",
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)

    _reply(f"✅ <b>{name}</b> (#{tool.id}) queued for posting!\nIt will be processed in the next scheduler cycle (~5 min).")


def _handle_help():
    """List all commands."""
    _reply(
        "🤖 <b>ExecutionPosting Bot</b>\n\n"
        "/status — posting summary & stats\n"
        "/recent — last 5 posted tools\n"
        "/queue — tools waiting in queue\n"
        "/health — token health check\n"
        "/post <name> <url> — quick-add a tool\n"
        "/help — this message"
    )


def poll_telegram_updates():
    """Called by the scheduler every 30s to check for new messages.

    Only processes messages from the configured CHAT_ID for security.
    """
    global _last_update_id

    if not BOT_TOKEN:
        _init()
    if not BOT_TOKEN or not CHAT_ID:
        return

    result = _api("getUpdates", offset=_last_update_id + 1, timeout=1, allowed_updates=["message"])
    if not result or not result.get("ok"):
        return

    updates = result.get("result", [])
    if not updates:
        return

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        for update in updates:
            _last_update_id = update["update_id"]

            msg = update.get("message", {})
            chat_id = str(msg.get("chat", {}).get("id", ""))
            text = (msg.get("text") or "").strip()

            # Security: only respond to configured chat
            if chat_id != str(CHAT_ID):
                continue

            if not text or not text.startswith("/"):
                continue

            cmd = text.split()[0].lower()
            # Remove @botname suffix if present
            if "@" in cmd:
                cmd = cmd.split("@")[0]

            logger.info("Telegram command: %s", cmd)

            if cmd == "/status":
                _handle_status(db)
            elif cmd == "/recent":
                _handle_recent(db)
            elif cmd == "/queue":
                _handle_queue(db)
            elif cmd == "/health":
                _handle_health()
            elif cmd == "/post":
                _handle_post(text, db)
            elif cmd in ("/help", "/start"):
                _handle_help()
            else:
                _reply(f"Unknown command: {cmd}\nType /help for available commands.")
    except Exception as exc:
        logger.error("Telegram bot error: %s", exc)
    finally:
        db.close()
