"""
Database models.

`AITool` maps to the ``ai_tools`` table and tracks every tool through the
DRAFT → READY → POSTED / FAILED lifecycle, with granular per-platform status.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.database import Base


class AITool(Base):
    """Represents a single AI-tool record that will be posted to social media."""

    __tablename__ = "ai_tools"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    tool_name: str = Column(String(255), nullable=False)
    handle: str | None = Column(String(255), nullable=True)
    description: str | None = Column(Text, nullable=True)
    website: str | None = Column(String(512), nullable=True)
    video_url: str = Column(String(1024), nullable=False)

    # Overall lifecycle status: DRAFT | READY | POSTED | FAILED
    status: str = Column(String(20), default="DRAFT", nullable=False)

    # ── Per-platform status (PENDING | SUCCESS | FAILED) ──────────────────
    linkedin_status: str = Column(String(20), default="PENDING", nullable=False)
    instagram_status: str = Column(String(20), default="PENDING", nullable=False)
    youtube_status: str = Column(String(20), default="PENDING", nullable=False)
    x_status: str = Column(String(20), default="PENDING", nullable=False)

    created_at: datetime = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    posted_at: datetime | None = Column(DateTime(timezone=True), nullable=True)
    scheduled_at: datetime | None = Column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<AITool id={self.id} name={self.tool_name!r} status={self.status}>"
