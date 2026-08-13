"""Unified Query Pipeline Orchestrator.

Coordinates the full query lifecycle:

  1. Init STM (Short-Term Memory)
  2. Query Planner   — classify intent + select strategy
  3. Initial Retrieval — semantic search or repository walk
  4. Graph Expansion  — relationship expander + context builder
  5. LTM Check        — look up cached knowledge for this session
  6. Answer Agent loop (max 3 attempts: 1 initial + 2 re-retrieval)
     a. Answer Agent generates a structured response
     b. If "insufficient" or "rewrite_search" → targeted re-retrieval + retry
     c. If iteration cap hit → best-effort answer
  7. LTM Write        — persist Answer Agent knowledge (if session_id present)
  8. Return PipelineResult

The ask.py router calls ``run_pipeline()`` and handles citation validation
on the result — that step is intentionally NOT inside the orchestrator to
keep it a pure data-processing concern.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

from src.memory.stm.short_term import ShortTermMemory
from src.pipeline.history_formatter import format_history, format_history_with_summary
from src.pipeline.pipeline_logger import PipelineTrace
from src.retrieval.models import ExpandedContext, FinalContext
from src.retrieval import search, expand, build_context
from src.generation.prompt_templates import build_system_prompt, render_context_for_prompt
from src.generation import GROQ_MODELS
from src.generation.citation_validator import (
    validate_citations,
    collect_context_entities,
    ValidationReport,
)

if TYPE_CHECKING:
    from src.api.schemas import ConversationTurn
    from src.storage.models import RepositoryModel, UserModel

logger = logging.getLogger(__name__)

# Maximum re-retrieval iterations before forcing a best-effort answer.
# Total LLM calls = 1 initial + _MAX_ITERATIONS re-retrieval = 3 calls max.
_MAX_ITERATIONS = 2


def _build_overview_context(
    stm: "ShortTermMemory",
    repo_id: str,
    overview_answer: str,
    db: Session,
) -> "FinalContext":
    """Build a real FinalContext for overview answers so citation validation works.

    Queries the DB for all EntityModel rows whose id is in stm.visited_entity_ids,
    wraps each in a minimal ExpandedContext, and calls build_context() to produce
    a FinalContext with real entities.  Citation validation can then match citations
    against these entities instead of seeing an empty list.
    """
    from src.storage.models import EntityModel as _EntityModel
    from src.retrieval.models import RetrievalResult

    expanded: list[ExpandedContext] = []
    if stm.visited_entity_ids:
        try:
            entities = (
                db.query(_EntityModel)
                .filter(_EntityModel.id.in_(stm.visited_entity_ids))
                .all()
            )
            for i, ent in enumerate(entities):
                rr = RetrievalResult(
                    entity_id=ent.id,
                    entity=ent,
                    score=1.0,
                    rank=i + 1,
                )
                expanded.append(ExpandedContext(core=rr))
        except Exception as exc:
            logger.warning("_build_overview_context: DB query failed: %s", exc)

    if expanded:
        return build_context(
            expanded_contexts=expanded,
            query=stm.goal,
            repo_id=repo_id,
        )

    # Fallback: minimal context with empty expanded list but real rendered text
    from src.retrieval.models import FinalContext as _FC
    return _FC(
        query=stm.goal,
        repo_id=repo_id,
        expanded_contexts=[],
        rendered_text=overview_answer,
        total_tokens_est=len(overview_answer) // 4,
        truncated=False,
    )


@dataclass
class PipelineResult:
    """Output of a full pipeline run.

    Attributes
    ----------
    stm:
        The Short-Term Memory state at pipeline completion.
        ``stm.answer_text`` holds the final answer.
        ``stm.answer_status`` is "answered" for successful completions.
        ``stm.validation_report`` holds the citation report if validation ran
        inside the orchestrator (non-overview intents).
    final_context:
        The assembled FinalContext used for the last Answer Agent call.
        Passed to citation validation by the caller.
    validation_report:
        The ValidationReport produced by the orchestrator's inline citation
        validation pass.  ``None`` for overview intents (validation still runs
        in ask.py for those).  When set, ask.py skips re-validation and goes
        straight to correction.
    provider_used:
        Which LLM provider produced the final answer.
    trace:
        The PipelineTrace for this run.  The caller (ask.py) must call
        ``trace.finish()`` after citation validation so the log line
        contains the real citation count.
    """

    stm: ShortTermMemory
    final_context: FinalContext
    validation_report: "ValidationReport | None" = None
    provider_used: str = "unknown"
    trace: "PipelineTrace | None" = None


async def run_pipeline(
    query: str,
    repo_id: str,
    repo: "RepositoryModel",
    session_id: Optional[str],
    conversation_id: Optional[str],
    conversation_history: list["ConversationTurn"],
    user_id: Optional[str],
    top_k: int,
    db: Session,
    # LLM routing overrides (passed through from ask.py)
    groq_model: Optional[str] = None,
    groq_api_key: Optional[str] = None,
    gemini_model: str = "gemini-2.5-flash",
    gemini_api_key: Optional[str] = None,
    skip_groq: bool = False,
    skip_gemini: bool = False,
) -> PipelineResult:
    """Run the full unified query pipeline.

    Parameters
    ----------
    query:
        User's natural language question.
    repo_id:
        Target repository ID.
    repo:
        Repository ORM row (used for LTM stale detection).
    session_id:
        Optional client UUID for LTM scoping.
    conversation_id:
        Optional stable UUID identifying the conversation thread.
    conversation_history:
        Last N turns from the client (anonymous users) or empty list
        (authenticated users load history from DB inside this function).
    user_id:
        Authenticated user ID (None for anonymous).
    top_k:
        Number of entities to retrieve from vector search.
    db:
        Active SQLAlchemy session.
    groq_model / groq_api_key / gemini_model / gemini_api_key:
        LLM routing overrides forwarded from the request.
    skip_groq / skip_gemini:
        Force-skip provider flags.

    Returns
    -------
    PipelineResult
    """
    # ── 1. Init STM ──────────────────────────────────────────────────────────
    stm = ShortTermMemory(
        goal=query,
        repo_id=repo_id,
        session_id=session_id,
        conversation_id=conversation_id,
    )
    trace = PipelineTrace(query=query, repo_id=repo_id)
    trace.start()
    trace.step_stm("init", stm)

    # ── Conversation history loading ─────────────────────────────────────────
    history_text = ""
    if user_id and conversation_id:
        # Authenticated: load from DB (summary only — no raw turns)
        try:
            from src.memory.stm.working_memory import load_history
            from src.pipeline.history_formatter import format_history_with_summary
            summary, recent_turns = load_history(conversation_id, user_id, db)
            history_text = format_history_with_summary(summary, recent_turns)
            trace.step_history_load(
                source="db",
                turn_count=len(recent_turns),
                has_summary=bool(summary),
                conversation_id=conversation_id,
            )
        except Exception as exc:
            logger.warning("Failed to load conversation history: %s", exc)
            trace.step_history_load(source="none", turn_count=0, has_summary=False)
    elif conversation_history:
        # Anonymous: use what the client sent directly
        history_text = format_history(conversation_history)
        trace.step_history_load(
            source="client",
            turn_count=len(conversation_history),
            has_summary=False,
        )
    else:
        trace.step_history_load(source="none", turn_count=0, has_summary=False)

    if history_text:
        trace.step_history_debug(history_text)

    # ── Long-term memory loading ──────────────────────────────────────────────
    # Load all three memory tiers for authenticated users and inject them into
    # every Answer Agent call — even on Q1, so prior-session context is always
    # available.
    user_memory_facts: list[dict] = []
    user_repo_pref_facts: list[dict] = []
    repo_user_memory_facts: list[dict] = []
    if user_id:
        try:
            from src.memory.ltm.user_memory import load_user_memory
            from src.memory.ltm.user_repo_preference import load_user_repo_preferences
            from src.memory.ltm.repo_user_memory import load_repo_user_memory
            user_memory_facts = load_user_memory(user_id, db)
            user_repo_pref_facts = load_user_repo_preferences(user_id, repo_id, db)
            repo_user_memory_facts = load_repo_user_memory(user_id, repo_id, db)
            logger.debug(
                "LTM loaded: user=%d user_repo=%d repo=%d facts",
                len(user_memory_facts),
                len(user_repo_pref_facts),
                len(repo_user_memory_facts),
            )
        except Exception as exc:
            logger.warning("Failed to load long-term memory: %s", exc)

    # ── 2. Query Planner ─────────────────────────────────────────────────────
    try:
        from src.agents.query_planner import plan
        query_plan = plan(query, repo_id=repo_id)
        stm.intent = query_plan.intent
        stm.retrieval_strategy = query_plan.retrieval_strategy
        stm.search_query = query_plan.search_query or query
        trace.step_planner(stm.intent, stm.retrieval_strategy,
                           stm.search_query, query_plan.confidence)
    except Exception as exc:
        logger.warning("Query Planner failed, using defaults: %s", exc)
        stm.intent = "query"
        stm.retrieval_strategy = "semantic_search"
        stm.search_query = query
        trace.step_planner("query", "semantic_search", query, 0.0)
    trace.step_stm("post-plan", stm)

    # ── 3. Initial Retrieval ─────────────────────────────────────────────────
    results = []

    # Overview intents use the hierarchical summarisation pipeline — bypass
    # normal retrieval and go straight to repo_overview.run()
    if stm.intent in ("repository_overview", "repository_detailed"):
        try:
            from src.retrieval.repo_overview import run as run_overview
            # Log that retrieval is handled by the overview pipeline (not vector search)
            trace.step_retrieval("repository_overview", 0)
            overview_answer = await run_overview(
                query=query,
                intent=stm.intent,
                repo_id=repo_id,
                repo=repo,
                session_id=session_id,
                db=db,
                trace=trace,
                stm=stm,
            )
            stm.answer_text = overview_answer
            stm.answer_status = "answered"
            # Log expansion step — entities visited during file/folder agents
            trace.step_expansion(
                len(stm.visited_entity_ids),
                len(overview_answer) // 4,
                False,
            )
            trace.step_stm("post-expand", stm)
            trace.step_stm("final", stm)
            # Build a real FinalContext so ask.py can run citation validation
            # against the entities visited during the overview pipeline.
            real_context = _build_overview_context(stm, repo_id, overview_answer, db)
            return PipelineResult(
                stm=stm,
                final_context=real_context,
                provider_used="gemini",
                trace=trace,
            )
        except Exception as exc:
            logger.error("Overview pipeline failed, falling back to semantic search: %s", exc)
            # Reset intent/strategy so the standard pipeline runs correctly
            stm.intent = "query"
            stm.retrieval_strategy = "semantic_search"
            stm.search_query = query
            # Fall through to standard pipeline

    if stm.retrieval_strategy == "repository_walk":
        try:
            from src.retrieval.repo_walk import walk, to_retrieval_results
            walk_result = walk(repo_id, db)
            results = to_retrieval_results(walk_result)
            # Inject architecture hint into the query context
            if walk_result.architecture_summary_hint:
                stm.intermediate_summaries.append(walk_result.architecture_summary_hint)
        except Exception as exc:
            logger.warning("Repository walk failed, falling back to semantic search: %s", exc)
            results = search(stm.search_query, repo_id, top_k, db)
    else:
        results = search(stm.search_query, repo_id, top_k, db)

    # Track all entity IDs seen so far
    stm.visited_entity_ids = {r.entity_id for r in results}
    trace.step_retrieval(stm.retrieval_strategy, len(results))

    # ── 4. Graph Expansion ───────────────────────────────────────────────────
    expanded: list[ExpandedContext] = expand(
        retrieved_results=results,
        repo_id=repo_id,
        db_session=db,
    )
    stm.retrieved_chunks = expanded

    final_context = build_context(
        expanded_contexts=expanded,
        query=query,
        repo_id=repo_id,
    )

    # If we have an architecture summary from repo_walk, prepend it
    if stm.intermediate_summaries:
        arch_hint = stm.intermediate_summaries[0]
        final_context = FinalContext(
            query=final_context.query,
            repo_id=final_context.repo_id,
            expanded_contexts=final_context.expanded_contexts,
            rendered_text=(
                f"=== REPOSITORY ARCHITECTURE ===\n{arch_hint}\n\n"
                + final_context.rendered_text
            ),
            total_tokens_est=final_context.total_tokens_est,
            truncated=final_context.truncated,
        )

    trace.step_expansion(
        len(final_context.expanded_contexts),
        final_context.total_tokens_est,
        final_context.truncated,
    )
    trace.step_stm("post-expand", stm)

    # ── 5. LTM Check ────────────────────────────────────────────────────────
    ltm_hit = False
    if session_id:
        try:
            from src.memory.ltm.session_knowledge import lookup as ltm_lookup, inject_ltm
            ltm_entry = ltm_lookup(repo_id, session_id, stm.intent, repo, db)
            if ltm_entry:
                ltm_hit = True
                injected_text = inject_ltm(
                    final_context.rendered_text, ltm_entry
                )
                final_context = FinalContext(
                    query=final_context.query,
                    repo_id=final_context.repo_id,
                    expanded_contexts=final_context.expanded_contexts,
                    rendered_text=injected_text,
                    total_tokens_est=len(injected_text) // 4,
                    truncated=final_context.truncated,
                )
                trace.step_ltm(hit=True, feature_name=ltm_entry.feature_name,
                               ltm_summary=ltm_entry.summary)
            else:
                trace.step_ltm(hit=False)
        except Exception as exc:
            logger.warning("LTM lookup failed: %s", exc)
            trace.step_ltm(hit=False)
    else:
        trace.step_ltm(hit=False)

    # ── 6. Answer Agent loop ─────────────────────────────────────────────────
    system_prompt = build_system_prompt()

    from src.agents import code_qa_agent
    from src.retrieval.targeted_retrieval import fetch as targeted_fetch

    provider_used = "unknown"

    for attempt in range(_MAX_ITERATIONS + 1):
        context_str = render_context_for_prompt(final_context)
        context_tokens = len(context_str) // 4

        # Log dispatch (model selected, context size) BEFORE the LLM call
        trace.step_llm_dispatch(
            attempt=attempt,
            model=groq_model or gemini_model or "auto",
            provider="groq" if (groq_model and not skip_groq) else ("gemini" if not skip_gemini else "unknown"),
            context_tokens=context_tokens,
            task_type="answer",
        )

        # Log full prompt at DEBUG level
        trace.step_llm_prompt(system_prompt, context_str)

        import time as _time
        _llm_t0 = _time.monotonic()

        loop = asyncio.get_running_loop()
        agent_response = await loop.run_in_executor(
            None,
            lambda: code_qa_agent.run(
                query=query,
                context=context_str,
                system_prompt=system_prompt,
                groq_model=groq_model,
                groq_api_key=groq_api_key,
                gemini_model=gemini_model,
                gemini_api_key=gemini_api_key,
                skip_groq=skip_groq,
                skip_gemini=skip_gemini,
                history_text=history_text,
                iteration=attempt,
                user_memory=user_memory_facts,
                user_repo_preferences=user_repo_pref_facts,
                repo_user_memory=repo_user_memory_facts,
            )
        )

        _llm_ms = (_time.monotonic() - _llm_t0) * 1000
        provider_used = agent_response.provider_used
        stm.answer_status = agent_response.status

        # Store the raw LLM response in STM for post-run debugging
        raw_out = agent_response.raw_response or agent_response.answer or ""
        stm.raw_llm_responses.append(raw_out)

        # Log raw LLM response with real token counts from the agent response
        trace.step_llm_response(
            provider=provider_used,
            model=groq_model or gemini_model or "auto",
            answer_raw=raw_out,
            status=agent_response.status,
            elapsed_ms=_llm_ms,
            input_tokens=agent_response.prompt_tokens,
        )

        if agent_response.status == "answered":
            stm.answer_text = agent_response.answer

            # ── Inline citation validation ────────────────────────────────
            # Run validation immediately so the result is available in STM
            # for the re-retrieval decision and for ask.py (which skips
            # re-validation when stm.validation_report is already set).
            try:
                context_entities = collect_context_entities(final_context)
                report = validate_citations(
                    answer=stm.answer_text,
                    context_entities=context_entities,
                    final_context=final_context,
                    db_session=db,
                    repo_id=repo_id,
                )
                stm.validation_report = report
                stm.citation_hit_rate = (
                    1.0 - report.hallucination_rate
                    if report.total_citations > 0
                    else None  # no citations yet — not a confirmed 0
                )
                # Collect entity hints from unsupported citations for re-retrieval
                stm.unsupported_entity_hints = [
                    c.nearest_entity_id
                    for c in report.unsupported_citations
                    if c.nearest_entity_id
                ]
                trace.step_citation(
                    total=report.total_citations,
                    definition=len(report.definition_citations),
                    call_site=len(report.call_site_citations),
                    unsupported=len(report.unsupported_citations),
                    rate=report.hallucination_rate,
                )
            except Exception as cite_exc:
                logger.warning("Inline citation validation failed (non-fatal): %s", cite_exc)
                report = None

            # ── Citation-quality re-retrieval trigger ─────────────────────
            # If this is not the last attempt, and the answer has zero
            # citations despite entities being in context, treat it the same
            # as "insufficient" — fetch the hinted entities and retry once.
            _has_entities_in_context = bool(final_context.expanded_contexts)
            _zero_citations = report is not None and report.total_citations == 0
            _can_retry = attempt < _MAX_ITERATIONS

            if _zero_citations and _has_entities_in_context and _can_retry:
                logger.info(
                    "Inline citation check: 0 citations with %d entities in context "
                    "— triggering citation re-retrieval (attempt %d)",
                    len(final_context.expanded_contexts), attempt,
                )
                # Override status so the re-retrieval path below runs
                agent_response = type(agent_response)(
                    status="insufficient",
                    partial_answer=stm.answer_text,
                    reason="zero_citations",
                    missing={"type": "citation_retry", "entity": ""},
                    raw_response=agent_response.raw_response,
                    provider_used=agent_response.provider_used,
                    prompt_tokens=agent_response.prompt_tokens,
                    completion_tokens=agent_response.completion_tokens,
                )
                stm.answer_status = "insufficient"
                # Don't break — fall through to re-retrieval below
            else:
                # Write LTM if session scoped and agent provided an entry
                if session_id and agent_response.ltm_entry:
                    try:
                        from src.memory.ltm.session_knowledge import write as ltm_write
                        ltm_write(repo_id, session_id, agent_response.ltm_entry, repo, db)
                        trace.step_ltm_write(
                            feature_name=agent_response.ltm_entry.get("feature_name", "unknown"),
                            confidence=agent_response.ltm_entry.get("confidence", "medium"),
                            exploration_status=agent_response.ltm_entry.get("exploration_status", "partial"),
                            summary=agent_response.ltm_entry.get("summary"),
                        )
                    except Exception as exc:
                        logger.warning("LTM write failed: %s", exc)
                break

        # On last attempt, force a best-effort answer regardless of status
        if attempt >= _MAX_ITERATIONS:
            stm.answer_text = (
                agent_response.partial_answer
                or agent_response.answer
                or "I was unable to produce a complete answer from the available context."
            )
            stm.answer_status = "answered"
            logger.debug(
                "Answer Agent: iteration cap reached (%d) — using best-effort answer",
                attempt,
            )
            break

        # Re-retrieval pass — also inject entities from unsupported citation hints
        new_expanded = await loop.run_in_executor(
            None,
            lambda: targeted_fetch(stm, agent_response, repo_id, db)
        )

        # Supplement with any entities flagged by unsupported citation hints
        if stm.unsupported_entity_hints:
            try:
                from src.storage.models import EntityModel as _EntityModel
                from src.retrieval.models import RetrievalResult
                hint_entities = (
                    db.query(_EntityModel)
                    .filter(
                        _EntityModel.id.in_(stm.unsupported_entity_hints),
                        _EntityModel.id.notin_(stm.visited_entity_ids),
                    )
                    .limit(10)
                    .all()
                )
                if hint_entities:
                    hint_expanded = [
                        ExpandedContext(
                            core=RetrievalResult(
                                entity_id=e.id,
                                entity=e,
                                score=0.9,
                                rank=len(stm.retrieved_chunks) + i + 1,
                            )
                        )
                        for i, e in enumerate(hint_entities)
                    ]
                    new_expanded = (new_expanded or []) + hint_expanded
                    logger.info(
                        "Citation hints: fetched %d hinted entities for retry",
                        len(hint_entities),
                    )
                    stm.unsupported_entity_hints = []  # consumed
            except Exception as hint_exc:
                logger.warning("Failed to fetch citation hint entities: %s", hint_exc)

        new_count = len(new_expanded) if new_expanded else 0
        reason = agent_response.reason or agent_response.status
        trace.step_reretrieval(attempt + 1, new_count, reason)

        # Snapshot entity IDs for this iteration before updating
        stm.context_entity_ids_per_iteration.append(set(stm.visited_entity_ids))

        if new_expanded:
            stm.retrieved_chunks.extend(new_expanded)
            final_context = build_context(
                expanded_contexts=stm.retrieved_chunks,
                query=query,
                repo_id=repo_id,
            )
        else:
            logger.debug(
                "Targeted retrieval returned no new entities — forcing answer on next attempt"
            )

        stm.iteration_count += 1
        trace.step_stm(f"post-reretrieval-{stm.iteration_count}", stm)

    # Do NOT call trace.finish() here — citation validation runs in ask.py
    # after this returns.  ask.py calls trace.finish() with the real count.
    trace.step_stm("final", stm)
    return PipelineResult(
        stm=stm,
        final_context=final_context,
        validation_report=stm.validation_report,
        provider_used=provider_used,
        trace=trace,
    )
