"""
API routes for the ExecutionPosting frontend.

Endpoints:
  POST  /api/tools           — create a new AI tool record (JSON or multipart form)
  POST  /api/tools/bulk      — bulk-create tools from JSON array
  GET   /api/tools           — list all AI tool records
  GET   /api/tools/{id}      — fetch a single tool
  PATCH /api/tools/{id}      — update a tool (e.g. set status to READY)
  POST  /api/tools/{id}/retry — retry failed platforms for a tool
  DELETE /api/tools/{id}     — delete a tool
  POST  /api/webhook/post    — external webhook trigger
  GET   /api/health/tokens   — token expiry dashboard
  GET   /api/analytics       — posting analytics
"""

import hmac
import json
import os
import shutil
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from dateutil import parser as dateutil_parser
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.database import get_db
from app.models import AITool
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["tools"])


# ── Auth dependency ───────────────────────────────────────────────────────────

def verify_auth(x_auth_key: Optional[str] = Header(None)):
    """Require a valid secret key in the X-Auth-Key header."""
    if not x_auth_key or not hmac.compare_digest(x_auth_key, settings.APP_SECRET_KEY):
        raise HTTPException(status_code=401, detail="Unauthorized.")
    return True

# Directory for user-uploaded videos
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ── Helper ────────────────────────────────────────────────────────────────────

def _tool_to_dict(t: AITool) -> dict:
    """Serialise an AITool row to a JSON-safe dict."""
    return {
        "id": t.id,
        "tool_name": t.tool_name,
        "handle": t.handle,
        "description": t.description,
        "website": t.website,
        "video_url": t.video_url,
        "status": t.status,
        "linkedin_status": t.linkedin_status,
        "instagram_status": t.instagram_status,
        "facebook_status": t.facebook_status,
        "youtube_status": t.youtube_status,
        "x_status": t.x_status,
        "error_log": t.error_log,
        "scheduled_at": t.scheduled_at.isoformat() if t.scheduled_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "posted_at": t.posted_at.isoformat() if t.posted_at else None,
    }


# ── Create tool ──────────────────────────────────────────────────────────────

@router.post("/tools")
async def create_tool(
    tool_name: str = Form(...),
    handle: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
    video_url: Optional[str] = Form(None),
    scheduled_at: Optional[str] = Form(None),
    video_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Create a new AI-tool record."""
    if not video_url and not video_file:
        raise HTTPException(
            status_code=400,
            detail="Provide either a video_url or upload a video_file.",
        )

    if video_file and video_file.filename:
        safe_name = "".join(
            c if c.isalnum() or c in "-_." else "_" for c in video_file.filename
        )
        dest_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(dest_path, "wb") as fh:
            shutil.copyfileobj(video_file.file, fh)
        video_url = dest_path
        logger.info("Video uploaded: %s", dest_path)

    parsed_schedule = None
    if scheduled_at and scheduled_at.strip():
        try:
            parsed_schedule = dateutil_parser.isoparse(scheduled_at.strip())
            if parsed_schedule.tzinfo is None:
                parsed_schedule = parsed_schedule.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=400,
                detail="Invalid scheduled_at format. Use ISO-8601.",
            )

    tool = AITool(
        tool_name=tool_name,
        handle=handle or None,
        description=description or None,
        website=website or None,
        video_url=video_url,  # type: ignore[arg-type]
        status="READY",
        scheduled_at=parsed_schedule,
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)

    logger.info("Tool created: id=%d name=%s", tool.id, tool.tool_name)

    return {
        "id": tool.id,
        "tool_name": tool.tool_name,
        "status": tool.status,
        "scheduled_at": tool.scheduled_at.isoformat() if tool.scheduled_at else None,
        "message": "Tool created and queued for posting.",
    }


# ── Bulk upload ──────────────────────────────────────────────────────────────

@router.post("/tools/bulk")
async def bulk_create_tools(
    tools: List[dict] = Body(...),
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Bulk-create tools from a JSON array.

    Each item must have at least ``tool_name`` and ``video_url``.
    Optional: ``handle``, ``description``, ``website``, ``scheduled_at``.
    """
    created = []
    errors = []

    for idx, item in enumerate(tools):
        name = item.get("tool_name")
        video = item.get("video_url")
        if not name or not video:
            errors.append({"index": idx, "error": "Missing tool_name or video_url"})
            continue

        parsed_schedule = None
        sched = item.get("scheduled_at")
        if sched:
            try:
                parsed_schedule = dateutil_parser.isoparse(str(sched).strip())
                if parsed_schedule.tzinfo is None:
                    parsed_schedule = parsed_schedule.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                errors.append({"index": idx, "error": f"Invalid scheduled_at: {sched}"})
                continue

        tool = AITool(
            tool_name=name,
            handle=item.get("handle"),
            description=item.get("description"),
            website=item.get("website"),
            video_url=video,
            status="READY",
            scheduled_at=parsed_schedule,
        )
        db.add(tool)
        db.flush()
        created.append({"id": tool.id, "tool_name": tool.tool_name})

    db.commit()
    logger.info("Bulk upload: %d created, %d errors", len(created), len(errors))

    return {"created": created, "errors": errors, "total": len(created)}


# ── List tools ───────────────────────────────────────────────────────────────

@router.get("/tools")
async def list_tools(db: Session = Depends(get_db), _auth: bool = Depends(verify_auth)):
    """Return all AI-tool records, newest first."""
    tools = db.query(AITool).order_by(AITool.created_at.desc()).all()
    return [_tool_to_dict(t) for t in tools]


# ── Get single tool ─────────────────────────────────────────────────────────

@router.get("/tools/{tool_id}")
async def get_tool(tool_id: int, db: Session = Depends(get_db), _auth: bool = Depends(verify_auth)):
    """Fetch a single tool by ID."""
    tool = db.query(AITool).filter(AITool.id == tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found.")
    return _tool_to_dict(tool)


# ── Update tool status ──────────────────────────────────────────────────────

@router.patch("/tools/{tool_id}")
async def update_tool_status(
    tool_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Update the overall status of a tool."""
    tool = db.query(AITool).filter(AITool.id == tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found.")

    allowed = {"DRAFT", "READY", "POSTED", "FAILED"}
    if status not in allowed:
        raise HTTPException(
            status_code=400, detail=f"Status must be one of {allowed}."
        )

    tool.status = status
    if status == "POSTED":
        tool.posted_at = datetime.now(timezone.utc)
    db.commit()

    return {"id": tool.id, "status": tool.status}


# ── Retry failed platforms ──────────────────────────────────────────────────

@router.post("/tools/{tool_id}/retry")
async def retry_tool(
    tool_id: int,
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Reset FAILED platform statuses to PENDING and set tool back to READY."""
    tool = db.query(AITool).filter(AITool.id == tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found.")

    reset_count = 0
    for attr in ("linkedin_status", "instagram_status", "facebook_status",
                 "youtube_status", "x_status"):
        if getattr(tool, attr) == "FAILED":
            setattr(tool, attr, "PENDING")
            reset_count += 1

    if reset_count == 0:
        raise HTTPException(status_code=400, detail="No failed platforms to retry.")

    tool.status = "READY"
    tool.error_log = None
    db.commit()
    logger.info("Tool %d: %d platform(s) reset for retry.", tool_id, reset_count)

    return {"id": tool.id, "status": tool.status, "platforms_reset": reset_count}


# ── Delete tool ──────────────────────────────────────────────────────────────

@router.delete("/tools/{tool_id}")
async def delete_tool(
    tool_id: int,
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Delete a tool record permanently."""
    tool = db.query(AITool).filter(AITool.id == tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found.")

    db.delete(tool)
    db.commit()
    logger.info("Tool %d deleted.", tool_id)

    return {"deleted": True, "id": tool_id}


# ── External webhook trigger ────────────────────────────────────────────────

@router.post("/webhook/post")
async def webhook_create_tool(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """External webhook — create and queue a tool from JSON body.

    Useful for Zapier, n8n, Make.com, or custom integrations.
    Required: ``tool_name``, ``video_url``.
    """
    name = payload.get("tool_name")
    video = payload.get("video_url")
    if not name or not video:
        raise HTTPException(status_code=400, detail="tool_name and video_url are required.")

    parsed_schedule = None
    sched = payload.get("scheduled_at")
    if sched:
        try:
            parsed_schedule = dateutil_parser.isoparse(str(sched).strip())
            if parsed_schedule.tzinfo is None:
                parsed_schedule = parsed_schedule.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass

    tool = AITool(
        tool_name=name,
        handle=payload.get("handle"),
        description=payload.get("description"),
        website=payload.get("website"),
        video_url=video,
        status="READY",
        scheduled_at=parsed_schedule,
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)

    logger.info("Webhook: tool created id=%d name=%s", tool.id, tool.tool_name)
    return {"id": tool.id, "status": "READY", "message": "Queued via webhook."}


# ── Token health dashboard ──────────────────────────────────────────────────

@router.get("/health/tokens")
async def token_health(_auth: bool = Depends(verify_auth)):
    """Check configured platform tokens and their status."""
    import requests

    platforms = {}

    # Meta (Instagram + Facebook)
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
                expires = data.get("expires_at", 0)
                is_valid = data.get("is_valid", False)
                if expires:
                    exp_dt = datetime.fromtimestamp(expires, tz=timezone.utc)
                    days_left = (exp_dt - datetime.now(timezone.utc)).days
                else:
                    days_left = None
                platforms["meta"] = {
                    "configured": True,
                    "valid": is_valid,
                    "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat() if expires else None,
                    "days_left": days_left,
                    "scopes": data.get("scopes", []),
                }
            else:
                platforms["meta"] = {"configured": True, "valid": False, "error": r.json().get("error", {}).get("message", "Unknown")}
        except Exception as exc:
            platforms["meta"] = {"configured": True, "valid": False, "error": str(exc)}
    else:
        platforms["meta"] = {"configured": False}

    # LinkedIn
    platforms["linkedin"] = {"configured": bool(settings.LINKEDIN_ACCESS_TOKEN)}

    # YouTube
    platforms["youtube"] = {
        "configured": bool(settings.YOUTUBE_CLIENT_ID and settings.YOUTUBE_CLIENT_SECRET and settings.YOUTUBE_REFRESH_TOKEN)
    }

    # X / Twitter
    platforms["x"] = {
        "configured": bool(settings.X_API_KEY and settings.X_API_SECRET and settings.X_ACCESS_TOKEN and settings.X_ACCESS_SECRET)
    }

    # Notifications
    platforms["discord"] = {"configured": bool(settings.DISCORD_WEBHOOK_URL)}
    platforms["telegram"] = {"configured": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID)}

    # AI
    platforms["gemini"] = {"configured": bool(settings.GEMINI_API_KEY)}

    return platforms


# ── Analytics ────────────────────────────────────────────────────────────────

@router.get("/analytics")
async def get_analytics(db: Session = Depends(get_db), _auth: bool = Depends(verify_auth)):
    """Return posting analytics/stats."""
    total = db.query(func.count(AITool.id)).scalar()
    posted = db.query(func.count(AITool.id)).filter(AITool.status == "POSTED").scalar()
    failed = db.query(func.count(AITool.id)).filter(AITool.status == "FAILED").scalar()
    ready = db.query(func.count(AITool.id)).filter(AITool.status == "READY").scalar()
    draft = db.query(func.count(AITool.id)).filter(AITool.status == "DRAFT").scalar()

    # Per-platform success rates
    platform_stats = {}
    for platform in ("linkedin", "instagram", "facebook", "youtube", "x"):
        col = getattr(AITool, f"{platform}_status")
        success = db.query(func.count(AITool.id)).filter(col == "SUCCESS").scalar()
        fail = db.query(func.count(AITool.id)).filter(col == "FAILED").scalar()
        skip = db.query(func.count(AITool.id)).filter(col == "SKIPPED").scalar()
        platform_stats[platform] = {
            "success": success,
            "failed": fail,
            "skipped": skip,
            "success_rate": round(success / max(success + fail, 1) * 100, 1),
        }

    # Recent posts (last 10)
    recent = db.query(AITool).filter(AITool.status == "POSTED").order_by(AITool.posted_at.desc()).limit(10).all()

    return {
        "total": total,
        "posted": posted,
        "failed": failed,
        "ready": ready,
        "draft": draft,
        "success_rate": round(posted / max(total, 1) * 100, 1),
        "platforms": platform_stats,
        "recent": [_tool_to_dict(t) for t in recent],
    }
