"""Retrieval router for query-only endpoint (no LLM generation)."""

from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.api.dependencies import get_db, get_accessible_repository, get_current_user
from src.api.schemas import AskRequest, QueryResponse
from src.storage.models import UserModel
from src.retrieval import build_context, expand, search

router = APIRouter()

_limiter = Limiter(key_func=get_remote_address)
_RATE_DEFAULT = os.environ.get("RATE_LIMIT_DEFAULT", "60/minute")


@router.post("/{repo_id}/query", response_model=QueryResponse)
@_limiter.limit(_RATE_DEFAULT)
async def query_repository(
    request: Request,
    repo_id: str,
    body: AskRequest,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> QueryResponse:
    """Run the retrieval pipeline without LLM generation.

    Returns expanded entity context, execution traces, and citation anchors
    so the caller can inspect what context *would* be sent to the LLM.
    """
    repo = get_accessible_repository(repo_id, db, current_user)
    if repo.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Repository '{repo_id}' is not ready for querying "
                f"(status: {repo.status})"
            ),
        )

    try:
        results = search(
            query=body.query,
            repo_id=repo_id,
            top_k=body.top_k,
            db_session=db,
        )
        expanded = expand(
            retrieved_results=results,
            repo_id=repo_id,
            db_session=db,
        )
        final_context = build_context(
            expanded_contexts=expanded,
            query=body.query,
            repo_id=repo_id,
        )

        entities = [
            {
                "entity_id": exp.core.entity.id,
                "entity_name": exp.core.entity.name,
                "entity_type": exp.core.entity.type,
                "file_path": exp.core.entity.file_path,
                "start_line": exp.core.entity.start_line,
                "end_line": exp.core.entity.end_line,
                "score": exp.core.score,
                "parent_entity": exp.parent_entity.id if exp.parent_entity else None,
                "called_entities": [c.entity.id for c in exp.called_entities],
                "caller_entities": [c.id for c in exp.caller_entities],
                "inheritance_entities": [i.id for i in exp.inheritance_entities],
            }
            for exp in expanded
        ]

        execution_traces = []
        for exp in expanded:
            if exp.called_entities:
                trace = {
                    "from_entity": exp.core.entity.id,
                    "call_chain": [
                        {
                            "called_entity": c.entity.id,
                            "called_via": c.called_via,
                            "depth": c.depth,
                        }
                        for c in exp.called_entities
                    ],
                }
                execution_traces.append(trace)

        citations = [
            {
                "entity_id": exp.core.entity.id,
                "file_path": exp.core.entity.file_path,
                "start_line": exp.core.entity.start_line,
                "end_line": exp.core.entity.end_line,
            }
            for exp in expanded
        ]

        return QueryResponse(
            entities=entities,
            execution_traces=execution_traces,
            citations=citations,
            rendered_text=final_context.rendered_text,
            total_tokens_est=final_context.total_tokens_est,
            truncated=final_context.truncated,
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retrieval pipeline failed: {e}",
        ) from e
