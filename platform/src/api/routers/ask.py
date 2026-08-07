"""Ask router — thin shim over the unified pipeline orchestrator.

All retrieval + generation logic lives in ``src/pipeline/orchestrator.py``.
This router is responsible only for:
  1. Auth and repository status checks
  2. Parsing model override flags from the request body
  3. Calling ``run_pipeline()``
  4. Running citation validation on the result
  5. Building and returning ``AskResponse``
  6. Persisting conversation turns for authenticated users (fire-and-forget)
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.api.dependencies import get_db, get_gemini_api_key, get_accessible_repository, get_current_user
from src.api.schemas import (
    AskRequest,
    AskResponse,
    CitationMatchSchema,
    CitationMismatchSchema,
    ValidationReportSchema,
)
from src.generation import (
    GROQ_MODEL_NAMES,
    validate_citations,
)
from src.generation.citation_validator import collect_context_entities
from src.pipeline.orchestrator import run_pipeline
from src.storage.models import UserModel

router = APIRouter()
logger = logging.getLogger(__name__)

_limiter = Limiter(key_func=get_remote_address)
_RATE_ASK = os.environ.get("RATE_LIMIT_ASK", "30/minute")


@router.post("/{repo_id}/ask", response_model=AskResponse)
@_limiter.limit(_RATE_ASK)
async def ask_repository(
    request: Request,
    repo_id: str,
    body: AskRequest,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> AskResponse:
    """Run the unified query pipeline and return an answer with citations.

    Provider cascade: Groq (multi-model rotation) → Gemini fallback via LiteLLM.

    Pass ``model`` in the request body to pin a specific model:
    - ``"groq:llama3-70b-8192"`` → use that Groq model only
    - ``"gemini:gemini-2.5-flash"`` → use that Gemini model only
    - bare name (e.g. ``"llama3-70b-8192"``) → provider inferred from catalogue

    Conversation history:
    - ``conversation_id`` + ``conversation_history`` enable multi-turn context.
    - Authenticated users have turns persisted in the DB automatically.
    - Anonymous users manage history client-side and send it in every request.
    """
    # ── Repository status check ──────────────────────────────────────────────
    repo = get_accessible_repository(repo_id, db, current_user)
    if repo.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Repository '{repo_id}' is not ready for querying "
                f"(status: {repo.status})"
            ),
        )

    # ── API keys — LiteLLM reads env vars directly; we pass explicit keys only
    # when the dependency provides them (EASYREPO_MOCK_GEMINI support, etc.)
    gemini_key = get_gemini_api_key()
    groq_key = os.environ.get("GROQ_API_KEY", "")

    # ── Parse optional model override ────────────────────────────────────────
    force_groq_model: str | None = None
    force_gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    skip_groq = False
    skip_gemini = False

    if body.model:
        if body.model.startswith("groq:"):
            force_groq_model = body.model[len("groq:"):]
            skip_gemini = True
        elif body.model.startswith("gemini:"):
            force_gemini_model = body.model[len("gemini:"):]
            skip_groq = True
        elif body.model in GROQ_MODEL_NAMES:
            force_groq_model = body.model
        else:
            force_gemini_model = body.model
            skip_groq = True

    # ── Unified pipeline ─────────────────────────────────────────────────────
    try:
        pipeline_result = await run_pipeline(
            query=body.query,
            repo_id=repo_id,
            repo=repo,
            session_id=body.session_id,
            conversation_id=body.conversation_id,
            conversation_history=body.conversation_history,
            user_id=current_user.id if current_user else None,
            top_k=body.top_k,
            db=db,
            groq_model=force_groq_model,
            groq_api_key=groq_key or None,
            gemini_model=force_gemini_model,
            gemini_api_key=gemini_key or None,
            skip_groq=skip_groq,
            skip_gemini=skip_gemini,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pipeline failed: {e}",
        ) from e

    answer = pipeline_result.stm.answer_text or ""
    final_context = pipeline_result.final_context

    # ── Citation validation ───────────────────────────────────────────────────
    context_entities = collect_context_entities(final_context)
    report = validate_citations(
        answer=answer,
        context_entities=context_entities,
        final_context=final_context,
        db_session=db,
    )

    # Log citation summary via the structured trace (includes per-category breakdown)
    if pipeline_result.trace:
        pipeline_result.trace.step_citation(
            total=report.total_citations,
            definition=len(report.definition_citations),
            call_site=len(report.call_site_citations),
            unsupported=len(report.unsupported_citations),
            rate=report.hallucination_rate,
        )
    # Emit PIPELINE DONE with the real citation count now that validation is complete
    if pipeline_result.trace:
        pipeline_result.trace.finish(
            status=pipeline_result.stm.answer_status,
            provider=pipeline_result.provider_used,
            citation_count=report.total_citations,
            answer_chars=len(answer),
        )
    if not answer:
        logger.warning("PIPELINE: answer is empty — check LLM response and answer_agent extraction")

    definition_citations = [
        CitationMatchSchema(
            raw=c.raw,
            file_path=c.file_path,
            start_line=c.start_line,
            end_line=c.end_line,
            matched_entity_id=c.matched_entity_id,
            matched_entity_name=c.matched_entity_name,
            citation_type=c.citation_type,
            caller_entity_name=c.caller_entity_name,
            callee_entity_name=c.callee_entity_name,
        )
        for c in report.definition_citations
    ]
    call_site_citations = [
        CitationMatchSchema(
            raw=c.raw,
            file_path=c.file_path,
            start_line=c.start_line,
            end_line=c.end_line,
            matched_entity_id=c.matched_entity_id,
            matched_entity_name=c.matched_entity_name,
            citation_type=c.citation_type,
            caller_entity_name=c.caller_entity_name,
            callee_entity_name=c.callee_entity_name,
        )
        for c in report.call_site_citations
    ]
    unsupported_citations = [
        CitationMismatchSchema(
            raw=c.raw,
            file_path=c.file_path,
            start_line=c.start_line,
            end_line=c.end_line,
            reason=c.reason,
            nearest_entity=c.nearest_entity,
        )
        for c in report.unsupported_citations
    ]

    validation_report = ValidationReportSchema(
        total_citations=report.total_citations,
        definition_citations=definition_citations,
        call_site_citations=call_site_citations,
        unsupported_citations=unsupported_citations,
        hallucination_rate=report.hallucination_rate,
    )

    # ── Persist conversation turns (authenticated users only) ─────────────────
    if current_user and body.conversation_id:
        try:
            from src.storage import conversation_store
            from src.generation import llm_client as _llm_client
            _trace = pipeline_result.trace

            conversation_store.save_turn(
                conversation_id=body.conversation_id,
                user_id=current_user.id,
                repo_id=repo_id,
                role="user",
                content=body.query,
                db=db,
            )
            if _trace:
                _trace.step_turn_saved(role="user", turn_index=-1,
                                       conversation_id=body.conversation_id)

            conversation_store.save_turn(
                conversation_id=body.conversation_id,
                user_id=current_user.id,
                repo_id=repo_id,
                role="assistant",
                content=answer,
                db=db,
            )
            if _trace:
                _trace.step_turn_saved(role="assistant", turn_index=-1,
                                       conversation_id=body.conversation_id)

            conversation_store.maybe_summarize(
                conversation_id=body.conversation_id,
                db=db,
                llm_client=_llm_client,
                trace=_trace,
            )
        except Exception as exc:
            # Do not re-raise — persistence failure must not break the response
            logger.warning("Failed to persist conversation turn: %s", exc)

    return AskResponse(
        answer=answer,
        citations=validation_report,
        context_entities=[ent.id for ent in context_entities],
        provider=pipeline_result.provider_used,
    )
