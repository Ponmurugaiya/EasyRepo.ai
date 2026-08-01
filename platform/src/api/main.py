"""FastAPI application for EasyRepo AI Codebase Intelligence Platform."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.dependencies import get_db, get_db_url
from src.api import routers
from src.api.routers import ask as ask_router
from src.api.routers import repositories as repositories_router
from src.api.routers import retrieval as retrieval_router
from src.jobs.queue import task_queue
from src.storage.db import get_engine, init_db
from src.storage.models import Base

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load .env from repo root if present (dev convenience)
# ---------------------------------------------------------------------------
_env_path = Path(__file__).resolve().parents[3] / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown events."""
    logger.info("Starting EasyRepo API...")

    db_url = get_db_url()
    engine = get_engine(db_url)

    try:
        # Verify connectivity and schema completeness
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            existing_tables = set(
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )

        init_db(db_url)

        required_tables = {"repositories", "entities", "relationships"}
        missing_tables = sorted(required_tables - {t for t in existing_tables})
        if missing_tables:
            raise RuntimeError(
                f"Database schema initialization incomplete. "
                f"Missing tables: {', '.join(missing_tables)}"
            )

        logger.info("Database connection verified on startup")
    except Exception as e:
        logger.error(f"Database connection failed on startup: {e}")
        raise RuntimeError(f"Database is not ready for the API: {e}") from e

    # ------------------------------------------------------------------
    # Start the Procrastinate worker inside the same process.
    # open_async() opens the psycopg connection pool using the DATABASE_URL
    # environment variable (or libpq defaults).  The worker runs as a
    # concurrent asyncio task and is cancelled cleanly on shutdown.
    # ------------------------------------------------------------------
    async with task_queue.open_async():
        # Apply procrastinate's own DDL (procrastinate_jobs etc.) — idempotent
        await task_queue.schema_manager.apply_schema_async()
        logger.info("Procrastinate schema ready")

        worker_task = asyncio.create_task(
            task_queue.run_worker_async(
                queues=["ingestion"],
                install_signal_handlers=False,
            )
        )
        logger.info("Procrastinate ingestion worker started")

        yield

        logger.info("Shutting down EasyRepo API — stopping worker...")
        worker_task.cancel()
        try:
            await asyncio.wait_for(worker_task, timeout=30)
        except asyncio.TimeoutError:
            logger.warning("Worker did not stop within 30 s — forcing shutdown")
        except asyncio.CancelledError:
            logger.info("Worker stopped gracefully")

    logger.info("Shutting down EasyRepo API...")


app = FastAPI(
    title="EasyRepo AI Codebase Intelligence Platform",
    description="AI-powered codebase intelligence with graph-retrieval context expansion and citation grounding",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(repositories_router.router, prefix="/repositories", tags=["repositories"])
app.include_router(retrieval_router.router, prefix="/repositories", tags=["retrieval"])
app.include_router(ask_router.router, prefix="/repositories", tags=["ask"])


@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Health check endpoint that verifies database connectivity."""
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection failed",
        )


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "EasyRepo AI Codebase Intelligence Platform",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }
