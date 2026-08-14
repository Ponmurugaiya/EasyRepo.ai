"""Ask router — async job pattern over the unified pipeline orchestrator.

POST /{repo_id}/ask
    Validates auth + repo status, creates an AskJobModel row (status=pending),
    defers ask_pipeline_task to Procrastinate, and immediately returns
    {job_id, status: "pending"}.  Completes in < 1s, well within the 30s
    API Gateway timeout.

GET /{repo_id}/ask/{job_id}
    Polling endpoint.  Returns the job status and, when done, the full
    AskResponse.  Frontend polls every 2s until status is "done" or "failed".

Citation validation, correction, and turn saving all happen inside the
Procrastinate worker task (ask_pipeline_task in src/jobs/queue.py).
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from src.api.dependencies import get_db, get_accessible_repository, get_current_user
from src.api.schemas import (
    AskJobStatusResponse,
    AskJobSubmittedResponse,
    AskRequest,
    AskResponse,
)
from src.generation import GROQ_MODEL_NAMES
from src.storage.models import AskJobModel, UserModel
from src.jobs.queue import ask_pipeline_task

router = APIRouter()
logger = logging.getLogger(__name__)

_limiter = Limiter(key_func=get_remote_address)
_RATE_ASK = os.environ.get("RATE_LIMIT_ASK", "30/minute")


@router.post("/{repo_id}/ask", response_model=AskJobSubmittedResponse)
@_limiter.limit(_RATE_ASK)
async def ask_repository(
    request: Request,
    repo_id: str,
    body: AskRequest,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> AskJobSubmittedResponse:
    """Submit a query — returns a job_id immediately (< 1s).

    The pipeline runs asynchronously in the Procrastinate worker.
    Poll GET /{repo_id}/ask/{job_id} to retrieve the result.
    """
    repo = get_accessible_repository(repo_id, db, current_user)
    if repo.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": f"Repository '{repo_id}' is not ready (status: {repo.status})",
                "error_code": "repo_not_ready",
            },
        )

    # Parse model override flags (same logic as before)
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

    # Create job row
    job_id = uuid.uuid4().hex
    job = AskJobModel(
        id=job_id,
        repo_id=repo_id,
        user_id=current_user.id if current_user else None,
        status="pending",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()

    # Defer to Procrastinate worker
    db_url = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@127.0.0.1:5435/easyrepo",
    )
    try:
        await ask_pipeline_task.defer_async(
            job_id=job_id,
            repo_id=repo_id,
            db_url=db_url,
            query=body.query,
            top_k=body.top_k,
            session_id=body.session_id,
            conversation_id=body.conversation_id,
            conversation_history=[t.model_dump() for t in body.conversation_history],
            user_id=current_user.id if current_user else None,
            force_groq_model=force_groq_model,
            force_gemini_model=force_gemini_model,
            skip_groq=skip_groq,
            skip_gemini=skip_gemini,
        )
        logger.info("ask_repository: deferred job_id=%s repo=%s", job_id, repo_id)
    except Exception as exc:
        # If defer fails, mark job failed so the poll endpoint reports it
        job.status = "failed"
        job.error = f"Failed to queue job: {exc}"
        job.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.error("ask_repository: defer failed for job_id=%s: %s", job_id, exc)

    return AskJobSubmittedResponse(job_id=job_id, status=job.status)


@router.get("/{repo_id}/ask/{job_id}", response_model=AskJobStatusResponse)
async def get_ask_job(
    repo_id: str,
    job_id: str,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> AskJobStatusResponse:
    """Poll the result of an async ask job.

    Returns immediately with the current status.  When status is "done",
    the full AskResponse is included in the ``result`` field.
    """
    job = db.query(AskJobModel).filter_by(id=job_id, repo_id=repo_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"detail": "Job not found", "error_code": "job_not_found"},
        )

    # Ownership check — only the submitting user (or anonymous shared jobs) can read
    if job.user_id and current_user and job.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"detail": "Not authorised to access this job", "error_code": "forbidden"},
        )

    result: Optional[AskResponse] = None
    if job.status == "done" and job.result:
        result = AskResponse.model_validate(job.result)

    return AskJobStatusResponse(
        job_id=job.id,
        status=job.status,
        result=result,
        error=job.error,
    )


# ---------------------------------------------------------------------------
# Conversation history — restore turns for authenticated users
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BaseModel


class ConversationTurnResponse(_BaseModel):
    turn_index: int
    role: str
    content: str
    created_at: str


class ConversationResponse(_BaseModel):
    conversation_id: str
    repo_id: str
    turns: list[ConversationTurnResponse]
    created_at: str
    updated_at: str


@router.get("/{repo_id}/conversations", response_model=list[ConversationResponse])
@_limiter.limit(_RATE_ASK)
async def list_conversations(
    request: Request,
    repo_id: str,
    limit: int = 5,
    db: Session = Depends(get_db),
    current_user: Optional[UserModel] = Depends(get_current_user),
) -> list[ConversationResponse]:
    """Return the most recent conversations for an authenticated user in a repo."""
    if not current_user:
        return []

    limit = min(limit, 20)
    get_accessible_repository(repo_id, db, current_user)

    from src.storage.models import ConversationModel, ConversationTurnModel

    convs = (
        db.query(ConversationModel)
        .filter_by(user_id=current_user.id, repo_id=repo_id)
        .order_by(ConversationModel.updated_at.desc())
        .limit(limit)
        .all()
    )

    result = []
    for conv in convs:
        turns = (
            db.query(ConversationTurnModel)
            .filter_by(conversation_id=conv.id)
            .order_by(ConversationTurnModel.turn_index.asc())
            .all()
        )
        result.append(ConversationResponse(
            conversation_id=conv.id,
            repo_id=conv.repo_id,
            turns=[
                ConversationTurnResponse(
                    turn_index=t.turn_index,
                    role=t.role,
                    content=t.content,
                    created_at=t.created_at.isoformat(),
                )
                for t in turns
            ],
            created_at=conv.created_at.isoformat(),
            updated_at=conv.updated_at.isoformat(),
        ))

    return result

