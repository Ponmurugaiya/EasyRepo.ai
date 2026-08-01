"""Procrastinate task queue — Postgres-backed persistent job queue.

Architecture
------------
- One ``procrastinate.App`` instance (``task_queue``) is shared across the
  whole application.  It is created here with a deferred connector so that
  the actual database URL is injected at startup time from the environment,
  not hard-coded at import time.

- ``ingest_repo_task`` is the single task registered with the queue.  It is
  a *synchronous* function because ``ingest_repository`` (the core pipeline)
  is synchronous.  Procrastinate runs sync tasks in a thread-pool executor so
  the async event loop is never blocked.

- The FastAPI lifespan (``main.py``) is responsible for:
    1. Opening the connector (``task_queue.open_async()``).
    2. Applying the procrastinate schema once (idempotent DDL).
    3. Launching the in-process worker as an asyncio background task.
    4. Cancelling the worker on shutdown.

Worker note
-----------
Running the worker inside the API process is convenient for a single-server
deployment.  If you ever need to scale workers independently, switch to the
CLI approach::

    procrastinate --app=src.jobs.queue.task_queue worker

and remove ``run_worker_async`` from the lifespan.
"""

from __future__ import annotations

import logging

import procrastinate

from src.storage.db import get_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application object
# Connector is intentionally left un-opened here; ``main.py`` opens it inside
# the lifespan with the resolved DATABASE_URL.
# ---------------------------------------------------------------------------
task_queue = procrastinate.App(
    connector=procrastinate.PsycopgConnector(),
)


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
