"""
Database engine, session factory, and declarative base for SQLAlchemy.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,      # verify connections before checkout
    pool_size=5,
    max_overflow=10,
)

# ── Session factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ── Declarative base ─────────────────────────────────────────────────────────
Base = declarative_base()


def get_db():
    """Yield a database session; close it once the caller is finished.

    Intended for use as a FastAPI dependency or a context-manager helper.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
