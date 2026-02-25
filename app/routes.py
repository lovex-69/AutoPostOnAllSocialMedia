"""
API routes for the ExecutionPosting frontend.

Endpoints:
  POST  /api/tools              — create a new AI tool record (JSON or multipart form)
  POST  /api/tools/validate     — pre-submit validation (returns warnings + duplicates)
  POST  /api/tools/bulk         — bulk-create tools from JSON array
  GET   /api/tools              — list all AI tool records
  GET   /api/tools/{id}         — fetch a single tool
  PATCH /api/tools/{id}         — update a tool (e.g. set status to READY)
  POST  /api/tools/{id}/retry   — retry failed platforms for a tool
  DELETE /api/tools/{id}        — delete a tool
  POST  /api/webhook/post       — external webhook trigger
  GET   /api/health/tokens      — token expiry dashboard
  GET   /api/analytics          — posting analytics
  GET   /api/analytics/export   — CSV export of all tools
  GET   /api/platform-limits    — platform duration/size limits
  GET   /api/schedule/suggest   — smart scheduling suggestions
    GET   /api/music              — list music files in Supabase bucket
    POST  /api/music/upload       — upload royalty-free music to Supabase bucket
    DELETE /api/music/{path}      — delete one music file from Supabase bucket
"""

import csv
import hmac
import io
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from dateutil import parser as dateutil_parser
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import settings
from app.database import get_db
from app.models import AITool
from app.services.supabase_music_uploader import (
    SupabaseMusicUploadError,
    delete_music_from_supabase,
    list_music_in_supabase,
    upload_music_to_supabase,
)
from app.services.video_validator import validate_video, get_platform_limits, compute_video_hash
from app.services.smart_scheduler import (
    get_schedule_suggestions,
    check_content_freshness,
    get_queue_position,
)
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
ALLOWED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}


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
        "telegram_channel_status": t.telegram_channel_status,
        "reddit_status": t.reddit_status,
        "error_log": t.error_log,
        "video_hash": t.video_hash,
        "scheduled_at": t.scheduled_at.isoformat() if t.scheduled_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "posted_at": t.posted_at.isoformat() if t.posted_at else None,
    }


@router.post("/music/upload")
async def upload_music_file(
    file: UploadFile = File(...),
    folder: Optional[str] = Form(None),
    upsert: bool = Form(False),
    _auth: bool = Depends(verify_auth),
):
    """Upload one royalty-free track to the Supabase music bucket."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Missing file name.")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported audio format. Allowed: {sorted(ALLOWED_AUDIO_EXTENSIONS)}",
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    try:
        result = upload_music_to_supabase(
            file_name=file.filename,
            file_bytes=payload,
            content_type=file.content_type or "application/octet-stream",
            folder=folder,
            upsert=upsert,
        )
        logger.info("Music uploaded to Supabase: %s", result["path"])
        return {
            "ok": True,
            "bucket": result["bucket"],
            "path": result["path"],
            "size": result["size"],
            "public_url": result["public_url"],
        }
    except SupabaseMusicUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/music")
async def list_music_files(
    folder: Optional[str] = None,
    limit: int = 200,
    _auth: bool = Depends(verify_auth),
):
    """List all music files from the Supabase music bucket."""
    try:
        items = list_music_in_supabase(folder=folder, limit=max(1, min(limit, 1000)))
        return {
            "ok": True,
            "bucket": settings.SUPABASE_MUSIC_BUCKET,
            "count": len(items),
            "items": items,
        }
    except SupabaseMusicUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/music/{object_path:path}")
async def delete_music_file(
    object_path: str,
    _auth: bool = Depends(verify_auth),
):
    """Delete one music file from the Supabase music bucket."""
    try:
        result = delete_music_from_supabase(object_path=object_path)
        logger.info("Music deleted from Supabase: %s", result["path"])
        return {"ok": True, **result}
    except SupabaseMusicUploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Create tool ──────────────────────────────────────────────────────────────

@router.post("/tools")
async def create_tool(
    tool_name: str = Form(...),
    handle: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
    video_url: Optional[str] = Form(None),
    scheduled_at: Optional[str] = Form(None),
    force: Optional[str] = Form(None),
    video_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Create a new AI-tool record.

    If ``force`` is not set to "true", runs validation first and returns
    warnings without creating the tool. The frontend should show these
    warnings and re-submit with ``force=true`` to proceed.
    """
    if not video_url and not video_file:
        raise HTTPException(
            status_code=400,
            detail="Provide either a video_url or upload a video_file.",
        )

    if video_file and video_file.filename:
        safe_name = "".join(
            c if c.isalnum() or c in "-_." else "_" for c in video_file.filename
        )
        # Prefix with UUID to prevent filename collisions across uploads
        unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
        dest_path = os.path.join(UPLOAD_DIR, unique_name)
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

    # ── Run validation (unless force=true) ───────────────────────────────
    is_force = force and force.strip().lower() == "true"

    # Gather existing tools for duplicate detection
    existing = db.query(AITool).all()
    existing_dicts = [
        {
            "id": t.id,
            "tool_name": t.tool_name,
            "video_url": t.video_url,
            "video_hash": t.video_hash,
            "status": t.status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "posted_at": t.posted_at.isoformat() if t.posted_at else None,
        }
        for t in existing
    ]

    validation = validate_video(
        file_path=video_url if video_url and os.path.isfile(video_url) else None,
        video_url=video_url,
        tool_name=tool_name,
        existing_tools=existing_dicts,
    )

    # Content freshness check
    freshness = check_content_freshness(tool_name, existing_dicts)
    if freshness:
        validation["warnings"].append(freshness)

    # If there are warnings and user hasn't forced, return warnings only
    if validation["warnings"] and not is_force:
        # Queue position estimate
        ready_count = db.query(func.count(AITool.id)).filter(AITool.status == "READY").scalar()
        queue_info = get_queue_position(parsed_schedule, ready_count, settings.SCHEDULER_INTERVAL_MINUTES)

        return {
            "needs_confirmation": True,
            "warnings": validation["warnings"],
            "duplicates": validation["duplicates"],
            "video_info": validation["info"],
            "queue_estimate": queue_info,
            "message": "Review the warnings below and confirm to proceed.",
        }

    tool = AITool(
        tool_name=tool_name,
        handle=handle or None,
        description=description or None,
        website=website or None,
        video_url=video_url,  # type: ignore[arg-type]
        video_hash=validation.get("video_hash"),
        status="READY",
        scheduled_at=parsed_schedule,
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)

    logger.info("Tool created: id=%d name=%s", tool.id, tool.tool_name)

    # Queue position estimate
    ready_count = db.query(func.count(AITool.id)).filter(AITool.status == "READY").scalar()
    queue_info = get_queue_position(parsed_schedule, ready_count, settings.SCHEDULER_INTERVAL_MINUTES)

    return {
        "id": tool.id,
        "tool_name": tool.tool_name,
        "status": tool.status,
        "scheduled_at": tool.scheduled_at.isoformat() if tool.scheduled_at else None,
        "queue_estimate": queue_info,
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
                 "youtube_status", "x_status", "telegram_channel_status", "reddit_status"):
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
    """Delete a tool record permanently and clean up its uploaded video."""
    tool = db.query(AITool).filter(AITool.id == tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found.")

    # Clean up uploaded video file if it exists in uploads/
    if tool.video_url:
        normalised = os.path.normpath(tool.video_url)
        uploads_dir = os.path.normpath(UPLOAD_DIR)
        if normalised.startswith(uploads_dir) and os.path.exists(normalised):
            try:
                os.remove(normalised)
                logger.info("Deleted uploaded video: %s", normalised)
            except OSError as exc:
                logger.warning("Could not delete video %s: %s", normalised, exc)

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
                if expires and expires > 0:
                    exp_dt = datetime.fromtimestamp(expires, tz=timezone.utc)
                    days_left = (exp_dt - datetime.now(timezone.utc)).days
                else:
                    # expires_at=0 means the token never expires
                    days_left = 9999
                platforms["meta"] = {
                    "configured": True,
                    "valid": is_valid,
                    "expires_at": datetime.fromtimestamp(expires, tz=timezone.utc).isoformat() if expires and expires > 0 else None,
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

    # Telegram Channel
    platforms["telegram_channel"] = {
        "configured": bool(settings.TELEGRAM_BOT_TOKEN and getattr(settings, 'TELEGRAM_CHANNEL_ID', None))
    }

    # Reddit
    platforms["reddit"] = {
        "configured": bool(
            getattr(settings, 'REDDIT_CLIENT_ID', None)
            and getattr(settings, 'REDDIT_SUBREDDIT', None)
        )
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
    for platform in ("linkedin", "instagram", "facebook", "youtube", "x", "telegram_channel", "reddit"):
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


# ── Posting Heatmap ──────────────────────────────────────────────────────────

@router.get("/analytics/heatmap")
async def posting_heatmap(
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Return posting activity for the last 365 days as a heatmap grid.

    Response: list of {date: "YYYY-MM-DD", count: N, tools: [...names]}
    """
    from datetime import date, timedelta as td
    from sqlalchemy import cast, Date

    today = date.today()
    start = today - td(days=364)

    # Group posts by date
    rows = (
        db.query(
            cast(AITool.posted_at, Date).label("day"),
            func.count(AITool.id).label("cnt"),
            func.array_agg(AITool.tool_name).label("names"),
        )
        .filter(AITool.posted_at.isnot(None))
        .filter(cast(AITool.posted_at, Date) >= start)
        .group_by("day")
        .all()
    )

    day_map = {r.day: {"count": r.cnt, "tools": r.names or []} for r in rows}

    # Also count READY/FAILED create dates for activity
    created_rows = (
        db.query(
            cast(AITool.created_at, Date).label("day"),
            func.count(AITool.id).label("cnt"),
        )
        .filter(cast(AITool.created_at, Date) >= start)
        .filter(AITool.posted_at.is_(None))
        .group_by("day")
        .all()
    )
    for r in created_rows:
        if r.day in day_map:
            day_map[r.day]["count"] += r.cnt
        else:
            day_map[r.day] = {"count": r.cnt, "tools": []}

    # Fill every day in range
    result = []
    d = start
    while d <= today:
        entry = day_map.get(d, {"count": 0, "tools": []})
        result.append({
            "date": d.isoformat(),
            "count": entry["count"],
            "tools": entry["tools"][:5],  # Cap tooltip list
        })
        d += td(days=1)

    return result


# ── Validate before creating ────────────────────────────────────────────────

@router.post("/tools/validate")
async def validate_tool(
    tool_name: str = Form(...),
    video_url: Optional[str] = Form(None),
    video_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Pre-validate a tool submission without creating it.

    Returns warnings about video duration, size, format, duplicates, and
    content freshness. The frontend should display these and let the user
    decide whether to proceed.
    """
    # Handle file upload for validation (save temporarily)
    temp_path = None
    if video_file and video_file.filename:
        temp_path = os.path.join(UPLOAD_DIR, f"_validate_{uuid.uuid4().hex[:8]}_{video_file.filename}")
        with open(temp_path, "wb") as fh:
            shutil.copyfileobj(video_file.file, fh)

    try:
        existing = db.query(AITool).all()
        existing_dicts = [
            {
                "id": t.id,
                "tool_name": t.tool_name,
                "video_url": t.video_url,
                "video_hash": t.video_hash,
                "status": t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "posted_at": t.posted_at.isoformat() if t.posted_at else None,
            }
            for t in existing
        ]

        file_to_probe = temp_path or (video_url if video_url and os.path.isfile(video_url) else None)
        url_to_check = video_url if video_url else None

        validation = validate_video(
            file_path=file_to_probe,
            video_url=url_to_check,
            tool_name=tool_name,
            existing_tools=existing_dicts,
        )

        freshness = check_content_freshness(tool_name, existing_dicts)
        if freshness:
            validation["warnings"].append(freshness)

        return validation
    finally:
        # Cleanup temp validation file
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


# ── Analytics CSV export ─────────────────────────────────────────────────────

@router.get("/analytics/export")
async def export_analytics_csv(
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Export all tool records as a downloadable CSV file."""
    tools = db.query(AITool).order_by(AITool.created_at.desc()).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Tool Name", "Handle", "Description", "Website", "Video URL",
        "Status", "LinkedIn", "Instagram", "Facebook", "YouTube", "X",
        "Telegram Channel", "Reddit",
        "Error Log", "Created At", "Posted At", "Scheduled At",
    ])
    for t in tools:
        writer.writerow([
            t.id, t.tool_name, t.handle or "", t.description or "", t.website or "",
            t.video_url or "", t.status,
            t.linkedin_status, t.instagram_status, t.facebook_status,
            t.youtube_status, t.x_status,
            t.telegram_channel_status, t.reddit_status,
            t.error_log or "",
            t.created_at.isoformat() if t.created_at else "",
            t.posted_at.isoformat() if t.posted_at else "",
            t.scheduled_at.isoformat() if t.scheduled_at else "",
        ])

    output.seek(0)
    filename = f"execution_posting_export_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Platform limits ──────────────────────────────────────────────────────────

@router.get("/platform-limits")
async def platform_limits(_auth: bool = Depends(verify_auth)):
    """Return per-platform video duration and size limits."""
    return get_platform_limits()


# ── Smart scheduling suggestions ─────────────────────────────────────────────

@router.get("/schedule/suggest")
async def schedule_suggestions(
    db: Session = Depends(get_db),
    _auth: bool = Depends(verify_auth),
):
    """Suggest optimal posting times per platform.

    Takes into account the most recent post time for cooldown spacing.
    """
    last_posted = (
        db.query(AITool.posted_at)
        .filter(AITool.posted_at.isnot(None))
        .order_by(AITool.posted_at.desc())
        .first()
    )
    last_posted_at = last_posted[0] if last_posted else None

    suggestions = get_schedule_suggestions(last_posted_at=last_posted_at)

    # Also return queue info
    ready_count = db.query(func.count(AITool.id)).filter(AITool.status == "READY").scalar()

    return {
        "suggestions": suggestions,
        "queue_size": ready_count,
        "last_posted_at": last_posted_at.isoformat() if last_posted_at else None,
    }
