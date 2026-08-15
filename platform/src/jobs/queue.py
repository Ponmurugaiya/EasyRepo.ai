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

import psycopg_pool
import procrastinate

from src.storage.db import get_session, make_psycopg_dsn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application object — the SAME connector instance must be used for the
# entire lifetime of the process.  task_queue is created with a real
# PsycopgConnector here; open_task_queue opens its pool at startup by
# passing a pre-built AsyncConnectionPool via App.open_async(pool=...).
#
# IMPORTANT: never replace task_queue.connector after decoration — the
# @task_queue.task decorator captures job_manager.connector at decoration
# time, so swapping connector post-decoration breaks defer_async.
# ---------------------------------------------------------------------------
task_queue = procrastinate.App(
    connector=procrastinate.PsycopgConnector(),
)


@asynccontextmanager
async def open_task_queue(db_url: str):
    """Async context manager that opens the Procrastinate connection pool.

    Builds a ``psycopg_pool.AsyncConnectionPool`` with the correct DSN and
    pool settings, then passes it into ``task_queue.open_async(pool=pool)``
    so the EXISTING connector (already captured by job_manager) gets its
    ``_async_pool`` set — without replacing the connector object.

    Pool settings tuned to survive Supabase's ~300s idle connection timeout:
    - max_idle=240s  : recycle connections idle > 4 min
    - max_lifetime=1800s : recycle connections older than 30 min
    - min_size=1     : keep one warm connection at all times
    - reconnect_timeout=60s : recover from transient failures

    Args:
        db_url: SQLAlchemy-style ``DATABASE_URL``.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    dsn = make_psycopg_dsn(db_url)
    logger.info(
        "Procrastinate: opening connector (remote=%s)",
        "localhost" not in dsn and "127.0.0.1" not in dsn,
    )

    # Build the pool manually so we control all settings.
    pool = psycopg_pool.AsyncConnectionPool(
        conninfo=dsn,
        open=False,               # we open it explicitly below
        min_size=1,
        max_size=5,
        max_idle=240.0,           # recycle before Supabase's 300s idle cutoff
        max_lifetime=1800.0,      # recycle connections older than 30 min
        reconnect_timeout=60.0,   # retry on transient failures
        check=psycopg_pool.AsyncConnectionPool.check_connection,
    )
    await pool.open(wait=True)

    # Pass the open pool into the existing connector via open_async(pool=...).
    # This sets connector._async_pool without touching connector identity,
    # so job_manager.connector (captured at @task_queue.task decoration) works.
    async with task_queue.open_async(pool=pool):
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


@task_queue.task(
    name="ask_pipeline",
    queue="ask",
    retry=procrastinate.RetryStrategy(max_attempts=1),
)
def ask_pipeline_task(
    job_id: str,
    repo_id: str,
    db_url: str,
    query: str,
    top_k: int,
    session_id: "str | None",
    conversation_id: "str | None",
    conversation_history: "list[dict]",
    user_id: "str | None",
    force_groq_model: "str | None",
    force_gemini_model: str,
    skip_groq: bool,
    skip_gemini: bool,
) -> None:
    """Procrastinate task that runs the full ask pipeline asynchronously.

    Runs the pipeline, citation validation, correction, and turn saving,
    then writes the serialised AskResponse into ``ask_jobs.result``.

    The ``POST /ask`` endpoint defers this task and immediately returns
    ``{job_id, status: "pending"}``.  The frontend polls
    ``GET /ask/{job_id}`` until status is ``done`` or ``failed``.
    """
    import asyncio
    import json as _json
    from datetime import datetime, timezone

    from src.storage.db import get_session as _get_session
    from src.storage.models import AskJobModel, RepositoryModel
    from src.generation.llm_client import LLMProviderError

    logger.info("ask_pipeline_task: starting job_id=%s repo=%s", job_id, repo_id)

    def _write_progress(db, jid: str, pipeline: str, stage: str,
                        files_done: int = 0, files_total: int = 0) -> None:
        """Write live progress to the job row. Non-fatal on any error."""
        try:
            j = db.query(AskJobModel).filter_by(id=jid).first()
            if j:
                j.progress = {
                    "pipeline": pipeline,
                    "stage": stage,
                    "files_done": files_done,
                    "files_total": files_total,
                }
                j.updated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as e:
            logger.warning("ask_pipeline_task: progress write failed: %s", e)
            try:
                db.rollback()
            except Exception:
                pass

    def _run():
        with _get_session(db_url) as db:
            # Mark as running + first stage: classifying
            job = db.query(AskJobModel).filter_by(id=job_id).first()
            if job:
                job.status = "running"
                job.progress = {
                    "pipeline": "semantic",   # updated after planner runs
                    "stage": "classifying",
                    "files_done": 0,
                    "files_total": 0,
                }
                job.updated_at = datetime.now(timezone.utc)
                db.commit()

            repo = db.query(RepositoryModel).filter_by(id=repo_id).first()
            if not repo:
                _fail(db, job_id, "Repository not found")
                return

            try:
                # Run the pipeline (async) inside a new event loop
                from src.pipeline.orchestrator import run_pipeline
                from src.api.schemas import (
                    AskResponse, ValidationReportSchema,
                    CitationMatchSchema, CitationMismatchSchema,
                    ConversationTurn,
                )
                from src.generation.citation_validator import (
                    validate_citations, collect_context_entities,
                    _normalise_citations, sanitize_citations, strip_leaked_labels,
                )

                history = [
                    ConversationTurn(role=t["role"], content=t["content"])
                    for t in (conversation_history or [])
                ]

                # Progress callback — called by orchestrator at key stages.
                # Runs in the worker thread, so direct DB writes are safe.
                def _on_progress(stage: str, pipeline: str = "semantic",
                                 files_done: int = 0, files_total: int = 0) -> None:
                    _write_progress(db, job_id, pipeline, stage, files_done, files_total)

                loop = asyncio.new_event_loop()
                try:
                    pipeline_result = loop.run_until_complete(run_pipeline(
                        query=query,
                        repo_id=repo_id,
                        repo=repo,
                        session_id=session_id,
                        conversation_id=conversation_id,
                        conversation_history=history,
                        user_id=user_id,
                        top_k=top_k,
                        db=db,
                        groq_model=force_groq_model,
                        groq_api_key=None,
                        gemini_model=force_gemini_model,
                        gemini_api_key=None,
                        skip_groq=skip_groq,
                        skip_gemini=skip_gemini,
                        on_progress=_on_progress,
                    ))
                finally:
                    loop.close()

                answer = pipeline_result.stm.answer_text or ""
                final_context = pipeline_result.final_context

                # Post-process citations
                answer = _normalise_citations(answer)
                answer = sanitize_citations(answer)
                answer = strip_leaked_labels(answer)

                # ── Overview intent: synthetic citation report ────────────────
                # For repository_overview / repository_detailed we don't use
                # inline [file:line] badges in the message body.  Instead we
                # build a ValidationReport that lists every file whose summary
                # was used as a verified definition citation, so the CitationPanel
                # can show "N files verified" with source-viewer links.
                # We also strip any inline citation tokens from the answer text
                # so the rendered message is clean prose.
                if pipeline_result.is_overview:
                    import re as _re
                    from src.generation.citation_validator import (
                        CitationMatch as _CitationMatch,
                        ValidationReport as _ValidationReport,
                    )
                    from src.storage.models import EntityModel as _EntityModel

                    # Signal: validating citations
                    _write_progress(db, job_id, "overview", "citations")

                    # Strip all [file:L-L] tokens from answer text
                    answer = _re.sub(
                        r"\[([^\[\]\s:`]+\.[a-zA-Z0-9]+):(\d+)(?:-(\d+))?\]",
                        "",
                        answer,
                    ).strip()

                    # Collect one module entity per file that was visited
                    visited_ids = pipeline_result.stm.visited_entity_ids
                    file_summaries_used = set(pipeline_result.stm.file_summaries.keys())

                    # Load module entities for files whose summaries were used
                    def_citations: list[_CitationMatch] = []
                    if file_summaries_used:
                        try:
                            module_entities = (
                                db.query(_EntityModel)
                                .filter(
                                    _EntityModel.repo_id == repo_id,
                                    _EntityModel.type == "module",
                                    _EntityModel.file_path.in_(file_summaries_used),
                                )
                                .all()
                            )
                            for ent in module_entities:
                                def_citations.append(_CitationMatch(
                                    raw=f"[{ent.file_path}:{ent.start_line}-{ent.end_line}]",
                                    file_path=ent.file_path,
                                    start_line=ent.start_line,
                                    end_line=ent.end_line,
                                    matched_entity_id=ent.id,
                                    matched_entity_name=ent.name,
                                    citation_type="definition",
                                ))
                        except Exception as exc:
                            logger.warning(
                                "ask_pipeline_task: overview citation build failed: %s", exc
                            )

                    report = _ValidationReport(
                        total_citations=len(def_citations),
                        definition_citations=def_citations,
                        call_site_citations=[],
                        unsupported_citations=[],
                    )

                    # Build serialisable response
                    result_dict = AskResponse(
                        answer=answer,
                        citations=ValidationReportSchema(
                            total_citations=report.total_citations,
                            definition_citations=[
                                CitationMatchSchema(
                                    raw=c.raw, file_path=c.file_path,
                                    start_line=c.start_line, end_line=c.end_line,
                                    matched_entity_id=c.matched_entity_id,
                                    matched_entity_name=c.matched_entity_name,
                                    citation_type=c.citation_type,
                                    caller_entity_name=c.caller_entity_name,
                                    callee_entity_name=c.callee_entity_name,
                                ) for c in report.definition_citations
                            ],
                            call_site_citations=[],
                            unsupported_citations=[],
                            hallucination_rate=0.0,
                        ),
                        context_entities=[e.id for e in collect_context_entities(final_context)],
                        provider=pipeline_result.provider_used,
                        is_overview=True,
                    ).model_dump()

                    job = db.query(AskJobModel).filter_by(id=job_id).first()
                    if job:
                        job.status = "done"
                        job.result = result_dict
                        job.progress = None
                        job.updated_at = datetime.now(timezone.utc)
                        db.commit()
                    logger.info("ask_pipeline_task: overview done job_id=%s files=%d",
                                job_id, len(def_citations))
                    return

                # ── Normal (non-overview) citation validation + correction ────
                # Signal citations stage
                _write_progress(db, job_id, "semantic", "citations")

                # Validate
                context_entities = collect_context_entities(final_context)
                if pipeline_result.validation_report is not None:
                    report = pipeline_result.validation_report
                else:
                    report = validate_citations(
                        answer=answer,
                        context_entities=context_entities,
                        final_context=final_context,
                        db_session=db,
                        repo_id=repo_id,
                    )

                # Correct
                if report.unsupported_citations:
                    try:
                        from src.agents.citation_correction_agent import run as correct_citations
                        correction = correct_citations(
                            answer=answer,
                            report=report,
                            context_entities=context_entities,
                            final_context=final_context,
                            db_session=db,
                        )
                        answer = correction.corrected_answer
                        report = correction.report
                    except Exception as exc:
                        logger.warning("ask_pipeline_task: citation correction failed: %s", exc)

                # Save turns
                if user_id and conversation_id:
                    try:
                        from src.memory.stm.working_memory import save_turn, summarize_after_turn
                        from src.generation import llm_client as _llm_client
                        save_turn(conversation_id=conversation_id, user_id=user_id,
                                  repo_id=repo_id, role="user", content=query, db=db)
                        save_turn(conversation_id=conversation_id, user_id=user_id,
                                  repo_id=repo_id, role="assistant", content=answer, db=db)
                        summarize_after_turn(
                            conversation_id=conversation_id, db=db,
                            llm_client=_llm_client, user_id=user_id, repo_id=repo_id,
                        )
                    except Exception as exc:
                        logger.warning("ask_pipeline_task: turn save failed: %s", exc)

                # Build serialisable response
                result_dict = AskResponse(
                    answer=answer,
                    citations=ValidationReportSchema(
                        total_citations=report.total_citations,
                        definition_citations=[
                            CitationMatchSchema(
                                raw=c.raw, file_path=c.file_path,
                                start_line=c.start_line, end_line=c.end_line,
                                matched_entity_id=c.matched_entity_id,
                                matched_entity_name=c.matched_entity_name,
                                citation_type=c.citation_type,
                                caller_entity_name=c.caller_entity_name,
                                callee_entity_name=c.callee_entity_name,
                            ) for c in report.definition_citations
                        ],
                        call_site_citations=[
                            CitationMatchSchema(
                                raw=c.raw, file_path=c.file_path,
                                start_line=c.start_line, end_line=c.end_line,
                                matched_entity_id=c.matched_entity_id,
                                matched_entity_name=c.matched_entity_name,
                                citation_type=c.citation_type,
                                caller_entity_name=c.caller_entity_name,
                                callee_entity_name=c.callee_entity_name,
                            ) for c in report.call_site_citations
                        ],
                        unsupported_citations=[
                            CitationMismatchSchema(
                                raw=c.raw, file_path=c.file_path,
                                start_line=c.start_line, end_line=c.end_line,
                                reason=c.reason,
                                nearest_entity=c.nearest_entity,
                                nearest_entity_id=c.nearest_entity_id,
                            ) for c in report.unsupported_citations
                        ],
                        hallucination_rate=report.hallucination_rate,
                    ),
                    context_entities=[e.id for e in context_entities],
                    provider=pipeline_result.provider_used,
                ).model_dump()

                # Write result
                job = db.query(AskJobModel).filter_by(id=job_id).first()
                if job:
                    job.status = "done"
                    job.result = result_dict
                    job.progress = None
                    job.updated_at = datetime.now(timezone.utc)
                    db.commit()
                logger.info("ask_pipeline_task: done job_id=%s", job_id)

            except Exception as exc:
                logger.error("ask_pipeline_task: failed job_id=%s: %s", job_id, exc, exc_info=True)
                _fail(db, job_id, str(exc))

    def _fail(db, jid: str, msg: str) -> None:
        try:
            j = db.query(AskJobModel).filter_by(id=jid).first()
            if j:
                j.status = "failed"
                j.error = msg[:2000]
                j.updated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as e:
            logger.warning("ask_pipeline_task: could not write failure: %s", e)

    _run()
