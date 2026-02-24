"""
Database engine, session factory, and declarative base for SQLAlchemy.
"""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import settings

# ── Engine ────────────────────────────────────────────────────────────────────
# Append sslmode=require for cloud databases if not already set
_db_url = settings.DATABASE_URL
if "supabase" in _db_url and "sslmode" not in _db_url:
    _db_url += "?sslmode=require" if "?" not in _db_url else "&sslmode=require"

engine = create_engine(
    _db_url,
    pool_pre_ping=True,      # verify connections before checkout
    pool_size=5,
    max_overflow=10,
)

# ── Session factory ───────────────────────────────────────────────────────────
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# ── Declarative base ─────────────────────────────────────────────────────────
Base = declarative_base()


def run_migrations() -> None:
    """Add any missing columns to existing tables (lightweight schema migration).

    This lets us evolve the schema without destroying existing data, since
    ``Base.metadata.create_all()`` won't add new columns to existing tables.
    """
    _migrations = [
        # (table, column, SQL type)
        ("ai_tools", "scheduled_at", "TIMESTAMPTZ"),
        ("ai_tools", "error_log", "TEXT"),
        ("ai_tools", "facebook_status", "VARCHAR(20) NOT NULL DEFAULT 'PENDING'"),
        ("ai_tools", "video_hash", "VARCHAR(64)"),
        ("ai_tools", "telegram_channel_status", "VARCHAR(20) NOT NULL DEFAULT 'PENDING'"),
        ("ai_tools", "reddit_status", "VARCHAR(20) NOT NULL DEFAULT 'PENDING'"),
    ]
    with engine.connect() as conn:
        for table, column, col_type in _migrations:
            # Check if column already exists
            result = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :table AND column_name = :col"
                ),
                {"table": table, "col": column},
            )
            if not result.fetchone():
                conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {col_type}'))
                conn.commit()


def get_db():
    """Yield a database session; close it once the caller is finished.

    Intended for use as a FastAPI dependency or a context-manager helper.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
