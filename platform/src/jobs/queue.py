"""Procrastinate task queue — Postgres-backed persistent job queue.

Architecture
------------
- ``task_queue`` is a module-level placeholder ``procrastinate.App``.  It is
  NOT opened here.  The actual connector (with the resolved DATABASE_URL) is
  created and opened inside the FastAPI lifespan via ``open_task_queue()``.

- ``ingest_repo_task`` is the single task registered with the queue.  It is
  a *synchronous* function because ``ingest_repository`` (the core pipeline)
  is synchronous.  Procrastinate runs sync tasks in a thread-pool executor so
  the async event loop is never blocked.

- The FastAPI lifespan (``main.py``) is responsible for:
    1. Calling ``open_task_queue(db_url)`` which builds a fresh
       ``PsycopgConnector`` with the correct DSN (including SSL for remote
       hosts) and enters ``task_queue.open_async()``.
    2. Applying the procrastinate schema once (idempotent DDL).
    3. Launching the in-process worker as an asyncio background task.
    4. Cancelling the worker on shutdown.

Supabase / remote Postgres
--------------------------
``PsycopgConnector`` passes all constructor kwargs directly to
``psycopg_pool.AsyncConnectionPool``.  The first positional kwarg of
``AsyncConnectionPool`` is ``conninfo`` — the psycopg v3 connection string.
``make_psycopg_dsn()`` strips any SQLAlchemy driver suffix and adds
``sslmode=require`` for non-localhost hosts.

Worker note
-----------
Running the worker inside the API process is convenient for a single-server
deployment.  If you ever need to scale workers independently, switch to the
CLI approach::

    procrastinate --app=src.jobs.queue.task_queue worker

and remove ``run_worker_async`` from the lifespan.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager

import procrastinate

from src.storage.db import get_session, make_psycopg_dsn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application object — connector replaced at startup by open_task_queue().
# ---------------------------------------------------------------------------
task_queue = procrastinate.App(
    connector=procrastinate.PsycopgConnector(),
)


@asynccontextmanager
async def open_task_queue(db_url: str):
    """Async context manager that opens the Procrastinate connector."""
    # psycopg v3 async requires SelectorEventLoop on Windows.
    # Set it here so the connector's connection pool uses the right loop
    # regardless of how the server was started.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    """Async context manager that opens the Procrastinate connector.

    Builds a new ``PsycopgConnector`` with the correct ``conninfo`` DSN
    derived from *db_url* (SSL added for remote hosts), swaps it into
    ``task_queue``, and opens the pool.

    Usage in FastAPI lifespan::

        async with open_task_queue(db_url):
            await task_queue.schema_manager.apply_schema_async()
            worker = asyncio.create_task(task_queue.run_worker_async(...))
            yield
            worker.cancel()

    Args:
        db_url: SQLAlchemy-style ``DATABASE_URL`` (read from environment).
    """
    dsn = make_psycopg_dsn(db_url)
    logger.info(
        "Procrastinate: opening connector (remote=%s)",
        "localhost" not in dsn and "127.0.0.1" not in dsn,
    )

    # Replace the placeholder connector with one that has the real conninfo.
    # PsycopgConnector forwards **kwargs to AsyncConnectionPool, whose first
    # kwarg is `conninfo`.
    task_queue.connector = procrastinate.PsycopgConnector(conninfo=dsn)

    async with task_queue.open_async():
        yield


# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------

@task_queue.task(
    name="ingest_repository",
    queue="ingestion",
    retry=procrastinate.RetryStrategy(max_attempts=3, wait=60),
)
def ingest_repo_task(
    repo_path_or_url: str,
    db_url: str,
    repo_id: str,
    repo_name: str,
) -> None:
    """Procrastinate task that runs the full ingestion pipeline.

    Procrastinate executes sync tasks in a thread-pool executor, so this
    never blocks the event loop.

    Args:
        repo_path_or_url: Local path or Git URL to ingest.
        db_url: SQLAlchemy-compatible database URL for opening a session.
        repo_id: Slug identifier for the repository row.
        repo_name: Human-readable repository name.
    """
    # Import here to avoid heavy ML imports at module load time
    from src.ingestion.pipeline import ingest_repository  # noqa: PLC0415

    logger.info(
        "procrastinate worker: starting ingestion job repo_id=%s path=%s",
        repo_id,
        repo_path_or_url,
    )
    with get_session(db_url) as session:
        ingest_repository(
            repo_path_or_url=repo_path_or_url,
            db_session=session,
            repo_id=repo_id,
            repo_name=repo_name,
        )
    logger.info("procrastinate worker: ingestion complete repo_id=%s", repo_id)
