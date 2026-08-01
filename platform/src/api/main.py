"""FastAPI application for EasyRepo AI Codebase Intelligence Platform."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.dependencies import get_db, get_db_url, verify_api_key
from src.api.routers import ask as ask_router
from src.api.routers import auth as auth_router
from src.api.routers import repositories as repositories_router
from src.api.routers import retrieval as retrieval_router
from src.jobs.queue import open_task_queue, task_queue
from src.storage.db import get_engine, init_db

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


# ---------------------------------------------------------------------------
# CORS configuration
#
# CORS_ALLOWED_ORIGINS — comma-separated list of allowed origins.
# Default is "*" so local development works without any config.
# For production, set to explicit origins, e.g.:
#   CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com
# ---------------------------------------------------------------------------

def _parse_cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ALLOWED_ORIGINS", "*").strip()
    if raw == "*":
        return ["*"]
    return [o.strip() for o in raw.split(",") if o.strip()]


# ---------------------------------------------------------------------------
# Rate limiting
#
# Storage backend: Redis (REDIS_URL env var, default redis://localhost:6379).
# If Redis is unreachable at startup, falls back to in-memory storage with a
# warning — local dev works without a Redis container, but limits will not
# be shared across multiple API workers.
#
# Rate limits (all overridable via env vars):
#   RATE_LIMIT_INGEST  — POST /repositories  (default: 10/minute)
#   RATE_LIMIT_ASK     — POST .../ask        (default: 30/minute)
#   RATE_LIMIT_DEFAULT — all other endpoints (default: 60/minute)
#
# Format follows the `limits` library: "N/period"
# (period: second | minute | hour | day).
# ---------------------------------------------------------------------------

RATE_LIMIT_INGEST  = os.environ.get("RATE_LIMIT_INGEST",  "10/minute")
RATE_LIMIT_ASK     = os.environ.get("RATE_LIMIT_ASK",     "30/minute")
RATE_LIMIT_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "60/minute")

# Created without a storage URI — storage is configured in lifespan once the
# env is fully loaded, so REDIS_URL from .env is available.
_limiter = Limiter(key_func=get_remote_address)


def _configure_limiter_storage() -> str:
    """Attempt to connect to Redis and configure the limiter to use it.

    Returns a string describing the storage backend that was selected.
    Falls back to the default in-memory storage if Redis is unreachable.
    """
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        import redis as redis_lib
        from limits.storage import RedisStorage  # type: ignore[import]

        # Probe the connection before committing to it
        client = redis_lib.from_url(redis_url, socket_connect_timeout=2)
        client.ping()
        client.close()

        # Re-create the limiter with Redis storage.  slowapi reads the URI
        # string directly when passed to storage_uri.
        _limiter._storage = RedisStorage(redis_url)  # type: ignore[attr-defined]
        return f"Redis ({redis_url})"
    except Exception as exc:
        logger.warning(
            "Redis unavailable (%s) — rate limiter using in-memory storage. "
            "Limits will NOT be shared across multiple workers.",
            exc,
        )
        return "in-memory (Redis unavailable)"


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup/shutdown events."""
    logger.info("Starting EasyRepo API...")

    # Configure rate-limit storage now that .env has been loaded
    storage_desc = _configure_limiter_storage()
    logger.info("Rate-limit storage: %s", storage_desc)

    db_url = get_db_url()
    engine = get_engine(db_url)

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            existing_tables = set(
                row[0]
                for row in conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )

        init_db(db_url)

        required_tables = {"repositories", "entities", "relationships", "users", "user_repos"}
        missing_tables = sorted(required_tables - {t for t in existing_tables})
        if missing_tables:
            raise RuntimeError(
                f"Database schema initialization incomplete. "
                f"Missing tables: {', '.join(missing_tables)}"
            )
        logger.info("Database connection verified on startup")
    except Exception as e:
        logger.error("Database connection failed on startup: %s", e)
        raise RuntimeError(f"Database is not ready for the API: {e}") from e

    # ------------------------------------------------------------------
    # Start the Procrastinate worker inside the same process.
    # Jobs are persisted in Postgres — durable across API restarts.
    # To scale workers independently, run the procrastinate CLI separately
    # and remove run_worker_async from here.
    # ------------------------------------------------------------------
    async with open_task_queue(db_url):
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

    logger.info("EasyRepo API shutdown complete")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="EasyRepo AI Codebase Intelligence Platform",
    description=(
        "AI-powered codebase intelligence with graph-retrieval context "
        "expansion and citation grounding"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Attach the rate-limiter state and its 429 handler
app.state.limiter = _limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — origins from environment, open by default for local dev
_cors_origins = _parse_cors_origins()
_cors_wildcard = _cors_origins == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    # allow_credentials must be False when allow_origins=["*"]
    # (browser spec forbids credentials with wildcard origin)
    allow_credentials=not _cors_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("CORS origins: %s", _cors_origins)

# ---------------------------------------------------------------------------
# Routers
# /health and / are intentionally open — monitoring and load-balancer probes.
# /auth/* is open — registration needs no prior credentials.
# All repository/retrieval/ask routes enforce per-user auth when
# AUTH_ENABLED=true; when unset they remain open for dev/local use.
# ---------------------------------------------------------------------------

# Auth — open (no dependency), provides registration + token management
app.include_router(
    auth_router.router,
    prefix="/auth",
    tags=["auth"],
)

app.include_router(
    repositories_router.router,
    prefix="/repositories",
    tags=["repositories"],
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    retrieval_router.router,
    prefix="/repositories",
    tags=["retrieval"],
    dependencies=[Depends(verify_api_key)],
)
app.include_router(
    ask_router.router,
    prefix="/repositories",
    tags=["ask"],
    dependencies=[Depends(verify_api_key)],
)


@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Health check — open (no auth, no rate limit).

    Verifies database connectivity.  Suitable for load-balancer probes.
    """
    try:
        db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error("Health check failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection failed",
        )


@app.get("/")
async def root():
    """Root endpoint — open (no auth, no rate limit)."""
    return {
        "name": "EasyRepo AI Codebase Intelligence Platform",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
    }
