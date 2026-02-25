"""
FastAPI application entry-point.

Responsibilities:
  * Create database tables on startup.
  * Start the APScheduler background job.
  * Serve the static frontend.
  * Expose a ``/health`` endpoint and API routes.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.database import Base, engine, run_migrations
from app.routes import router as api_router
from app.scheduler import start_scheduler, stop_scheduler
from app.utils.logger import get_logger

logger = get_logger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle hook."""
    # ── Startup ───────────────────────────────────────────────────────────
    logger.info("Creating database tables (if they don't exist)...")
    Base.metadata.create_all(bind=engine)

    logger.info("Running lightweight migrations...")
    run_migrations()

    logger.info("Starting background scheduler...")
    start_scheduler()

    yield  # application is running

    # ── Shutdown ──────────────────────────────────────────────────────────
    logger.info("Shutting down scheduler...")
    stop_scheduler()


app = FastAPI(
    title="ExecutionPosting",
    description="Automated multi-platform social media posting for AI tools.",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS (allow Vercel frontend) ─────────────────────────────────────────────
allowed_origins = (
    [settings.FRONTEND_URL]
    if settings.FRONTEND_URL and settings.FRONTEND_URL != "*"
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────────
app.include_router(api_router)

# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def serve_frontend():
    """Serve the single-page frontend."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.api_route("/health", methods=["GET", "HEAD"], tags=["ops"])
async def health_check():
    """Simple liveness probe (supports GET + HEAD for UptimeRobot)."""
    return {"status": "healthy"}


@app.api_route("/healthz", methods=["GET", "HEAD"], tags=["ops"])
async def health_check_alias():
    """Alias liveness probe for external monitors."""
    return {"status": "healthy"}
