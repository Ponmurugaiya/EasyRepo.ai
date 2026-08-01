"""Ask router for full pipeline: retrieval -> generation -> citation validation."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.api.dependencies import get_db, get_gemini_api_key, get_repository
from src.api.schemas import (
    AskRequest,
    AskResponse,
    CitationMatchSchema,
    CitationMismatchSchema,
    ValidationReportSchema,
)
from src.generation import (
    GROQ_MODEL_NAMES,
    LLMProviderError,
    build_system_prompt,
    generate_answer_with_fallback,
    render_context_for_prompt,
    validate_citations,
)
from src.generation.citation_validator import collect_context_entities
from src.retrieval import build_context, expand, search

router = APIRouter()


@router.post("/{repo_id}/ask", response_model=AskResponse)
async def ask_repository(
    repo_id: str,
    request: AskRequest,
    db: Session = Depends(get_db),
) -> AskResponse:
    """Run the full pipeline: retrieval → LLM generation → citation validation.

    Provider cascade: Groq (multi-model rotation) → Gemini fallback via LiteLLM.

    Pass ``model`` in the request body to pin a specific model:
    - ``"groq:llama3-70b-8192"`` → use that Groq model only
    - ``"gemini:gemini-2.5-flash"`` → use that Gemini model only
    - bare name (e.g. ``"llama3-70b-8192"``) → provider inferred from catalogue
    """
    # ── Repository status check ──────────────────────────────────────────────
    repo = get_repository(repo_id, db)
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
    gemini_key = get_gemini_api_key()   # may be "mock-gemini-key" in tests
    groq_key = os.environ.get("GROQ_API_KEY", "")

    # ── Parse optional model override ────────────────────────────────────────
    force_groq_model: str | None = None
    force_gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    skip_groq = False
    skip_gemini = False

    if request.model:
        if request.model.startswith("groq:"):
            force_groq_model = request.model[len("groq:"):]
            skip_gemini = True
        elif request.model.startswith("gemini:"):
            force_gemini_model = request.model[len("gemini:"):]
            skip_groq = True
        elif request.model in GROQ_MODEL_NAMES:
            force_groq_model = request.model
        else:
            force_gemini_model = request.model
            skip_groq = True

    # ── Retrieval pipeline ───────────────────────────────────────────────────
    try:
        results = search(
            query=request.query,
            repo_id=repo_id,
            top_k=request.top_k,
            db_session=db,
        )
        expanded = expand(
            retrieved_results=results,
            repo_id=repo_id,
            db_session=db,
        )
        final_context = build_context(
            expanded_contexts=expanded,
            query=request.query,
            repo_id=repo_id,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retrieval pipeline failed: {e}",
        ) from e

    # ── Prompt assembly ──────────────────────────────────────────────────────
    system_prompt = build_system_prompt()
    user_prompt = render_context_for_prompt(final_context)

    # ── LLM generation via LiteLLM (Groq → Gemini) ───────────────────────────
    try:
        answer, provider_used = generate_answer_with_fallback(
            query=request.query,
            context=user_prompt,
            system_prompt=system_prompt,
            groq_model=force_groq_model,
            groq_api_key=groq_key or None,
            gemini_model=force_gemini_model,
            gemini_api_key=gemini_key or None,
            skip_groq=skip_groq,
            skip_gemini=skip_gemini,
        )
    except LLMProviderError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Generation failed: {e}",
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Generation failed: {e}",
        ) from e

    # ── Citation validation ───────────────────────────────────────────────────
    context_entities = collect_context_entities(final_context)
    report = validate_citations(
        answer=answer,
        context_entities=context_entities,
        final_context=final_context,
        db_session=db,
    )

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

    return AskResponse(
        answer=answer,
        citations=validation_report,
        context_entities=[ent.id for ent in context_entities],
        provider=provider_used,
    )
