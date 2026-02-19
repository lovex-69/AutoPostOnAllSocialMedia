"""
API routes for the ExecutionPosting frontend.

Endpoints:
  POST /api/tools       — create a new AI tool record (JSON or multipart form)
  GET  /api/tools       — list all AI tool records
  GET  /api/tools/{id}  — fetch a single tool
  PATCH /api/tools/{id} — update a tool (e.g. set status to READY)
"""

import os
import shutil
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AITool
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api", tags=["tools"])

# Directory for user-uploaded videos
UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


@router.post("/tools")
async def create_tool(
    tool_name: str = Form(...),
    handle: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    website: Optional[str] = Form(None),
    video_url: Optional[str] = Form(None),
    video_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """Create a new AI-tool record.

    Accepts either a ``video_url`` (direct MP4 link) **or** an uploaded
    ``video_file``.  At least one must be provided.
    """
    # Validate: at least one video source
    if not video_url and not video_file:
        raise HTTPException(
            status_code=400,
            detail="Provide either a video_url or upload a video_file.",
        )

    # Handle file upload — save to /uploads and generate a local path
    if video_file and video_file.filename:
        safe_name = "".join(
            c if c.isalnum() or c in "-_." else "_" for c in video_file.filename
        )
        dest_path = os.path.join(UPLOAD_DIR, safe_name)
        with open(dest_path, "wb") as fh:
            shutil.copyfileobj(video_file.file, fh)
        video_url = dest_path  # store the local path as the "url"
        logger.info("Video uploaded: %s", dest_path)

    tool = AITool(
        tool_name=tool_name,
        handle=handle or None,
        description=description or None,
        website=website or None,
        video_url=video_url,  # type: ignore[arg-type]
        status="READY",
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)

    logger.info("Tool created: id=%d name=%s", tool.id, tool.tool_name)

    return {
        "id": tool.id,
        "tool_name": tool.tool_name,
        "status": tool.status,
        "message": "Tool created and queued for posting.",
    }


@router.get("/tools")
async def list_tools(db: Session = Depends(get_db)):
    """Return all AI-tool records, newest first."""
    tools = db.query(AITool).order_by(AITool.created_at.desc()).all()
    return [
        {
            "id": t.id,
            "tool_name": t.tool_name,
            "handle": t.handle,
            "description": t.description,
            "website": t.website,
            "video_url": t.video_url,
            "status": t.status,
            "linkedin_status": t.linkedin_status,
            "instagram_status": t.instagram_status,
            "youtube_status": t.youtube_status,
            "x_status": t.x_status,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "posted_at": t.posted_at.isoformat() if t.posted_at else None,
        }
        for t in tools
    ]


@router.get("/tools/{tool_id}")
async def get_tool(tool_id: int, db: Session = Depends(get_db)):
    """Fetch a single tool by ID."""
    tool = db.query(AITool).filter(AITool.id == tool_id).first()
    if not tool:
        raise HTTPException(status_code=404, detail="Tool not found.")
    return {
        "id": tool.id,
        "tool_name": tool.tool_name,
        "handle": tool.handle,
        "description": tool.description,
        "website": tool.website,
        "video_url": tool.video_url,
        "status": tool.status,
        "linkedin_status": tool.linkedin_status,
        "instagram_status": tool.instagram_status,
        "youtube_status": tool.youtube_status,
        "x_status": tool.x_status,
        "created_at": tool.created_at.isoformat() if tool.created_at else None,
        "posted_at": tool.posted_at.isoformat() if tool.posted_at else None,
    }


@router.patch("/tools/{tool_id}")
async def update_tool_status(
    tool_id: int,
    status: str = Form(...),
    db: Session = Depends(get_db),
):
    """Update the overall status of a tool (e.g. set back to READY)."""
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
