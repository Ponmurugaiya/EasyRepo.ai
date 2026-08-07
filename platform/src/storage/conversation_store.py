"""Conversation history persistence service.

Three responsibilities:
  1. load_history   — load summary + recent unsummarised turns for a conversation
  2. save_turn      — upsert conversation row and append a new turn
  3. maybe_summarize — compress old turns into a rolling summary when threshold is hit

Only called for authenticated users (user_id must be present).
Anonymous users send their history in the request body — nothing is persisted.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.storage.models import ConversationModel, ConversationTurnModel

logger = logging.getLogger(__name__)

# Number of unsummarised turns that triggers summarisation.
# 20 turns = 10 user/assistant exchanges.
_SUMMARIZE_THRESHOLD = int(os.environ.get("CONVERSATION_SUMMARIZE_THRESHOLD", "20"))

# How many recent turns to keep unsummarised (passed raw to the Answer Agent).
_KEEP_RECENT = 6


def load_history(
    conversation_id: str,
    user_id: str,
    db: Session,
) -> tuple[Optional[str], list[ConversationTurnModel]]:
    """Return (summary_text | None, recent_unsummarised_turns).

    If no conversation row exists yet, returns (None, []).

    Parameters
    ----------
    conversation_id:
        Stable UUID identifying the conversation thread.
    user_id:
        Authenticated user's ID (used for ownership verification).
    db:
        Active database session.
    """
    try:
        conv: Optional[ConversationModel] = (
            db.query(ConversationModel)
            .filter_by(id=conversation_id, user_id=user_id)
            .first()
        )

        if conv is None:
            return None, []

        # Load turns AFTER the summarised range
        recent_turns: list[ConversationTurnModel] = (
            db.query(ConversationTurnModel)
            .filter(
                ConversationTurnModel.conversation_id == conversation_id,
                ConversationTurnModel.turn_index > conv.summarized_through_turn,
            )
            .order_by(ConversationTurnModel.turn_index.asc())
            .all()
        )

        # Return only the _KEEP_RECENT most recent turns to cap token overhead
        return conv.summary, recent_turns[-_KEEP_RECENT:]

    except Exception as exc:
        logger.warning("load_history failed: %s", exc)
        return None, []


def save_turn(
    conversation_id: str,
    user_id: str,
    repo_id: str,
    role: str,
    content: str,
    db: Session,
) -> None:
    """Upsert the conversation row and append a new turn.

    Safe to call multiple times for the same conversation_id — the first call
    creates the row, subsequent calls update it.

    Parameters
    ----------
    conversation_id:
        Stable UUID from the frontend.
    user_id:
        Authenticated user's ID.
    repo_id:
        Repository this conversation is about.
    role:
        "user" or "assistant".
    content:
        Message text.
    db:
        Active database session.
    """
    try:
        now = datetime.now(timezone.utc)

        conv: Optional[ConversationModel] = (
            db.query(ConversationModel)
            .filter_by(id=conversation_id)
            .first()
        )

        if conv is None:
            conv = ConversationModel(
                id=conversation_id,
                user_id=user_id,
                repo_id=repo_id,
                created_at=now,
                updated_at=now,
            )
            db.add(conv)
            db.flush()  # so we can use conv in the turn below
            next_index = 0
        else:
            # Get the highest turn_index already in this conversation
            from sqlalchemy import func as sa_func
            max_index = (
                db.query(sa_func.max(ConversationTurnModel.turn_index))
                .filter_by(conversation_id=conversation_id)
                .scalar()
            )
            next_index = (max_index or 0) + 1
            conv.updated_at = now

        turn = ConversationTurnModel(
            conversation_id=conversation_id,
            turn_index=next_index,
            role=role,
            content=content,
            created_at=now,
        )
        db.add(turn)
        db.commit()

    except Exception as exc:
        logger.warning("save_turn failed: %s", exc)
        db.rollback()


def maybe_summarize(
    conversation_id: str,
    db: Session,
    llm_client,
) -> None:
    """Compress old turns into a rolling summary when the threshold is exceeded.

    Runs synchronously after the response is returned so it never adds latency
    to the current request.  Any failure is caught and logged silently.

    Parameters
    ----------
    conversation_id:
        Target conversation UUID.
    db:
        Active database session.
    llm_client:
        The llm_client module (passed to avoid circular imports).
    """
    try:
        conv: Optional[ConversationModel] = (
            db.query(ConversationModel)
            .filter_by(id=conversation_id)
            .first()
        )
        if conv is None:
            return

        # Count unsummarised turns
        unsummarised: list[ConversationTurnModel] = (
            db.query(ConversationTurnModel)
            .filter(
                ConversationTurnModel.conversation_id == conversation_id,
                ConversationTurnModel.turn_index > conv.summarized_through_turn,
            )
            .order_by(ConversationTurnModel.turn_index.asc())
            .all()
        )

        if len(unsummarised) <= _SUMMARIZE_THRESHOLD:
            return  # not yet at threshold

        # Summarise all but the most recent _KEEP_RECENT turns
        turns_to_compress = unsummarised[:-_KEEP_RECENT]
        if not turns_to_compress:
            return

        # Build conversation text for condensation
        history_text = "\n".join(
            f"{t.role.upper()}: {t.content}" for t in turns_to_compress
        )

        prior_summary = conv.summary or ""
        if prior_summary:
            condensation_input = (
                f"Prior summary:\n{prior_summary}\n\n"
                f"New turns to incorporate:\n{history_text}"
            )
        else:
            condensation_input = history_text

        system_prompt = (
            "You are a conversation summarizer for a codebase assistant. "
            "Produce a compact paragraph (max 200 words) summarizing the topics discussed, "
            "decisions made, and code areas referenced. Do NOT reproduce raw code blocks. "
            "Focus on what was learned about the codebase."
        )

        try:
            new_summary, _ = llm_client.generate_answer_with_fallback(
                query="Summarize this conversation",
                context=condensation_input,
                system_prompt=system_prompt,
                groq_model="llama-3.1-8b-instant",
            )
        except Exception as llm_exc:
            logger.warning("maybe_summarize: LLM call failed: %s", llm_exc)
            return

        # Update the conversation row
        conv.summary = new_summary
        conv.summarized_through_turn = turns_to_compress[-1].turn_index
        db.commit()

        logger.debug(
            "Conversation %s summarised through turn %d",
            conversation_id,
            conv.summarized_through_turn,
        )

    except Exception as exc:
        logger.warning("maybe_summarize failed: %s", exc)
        db.rollback()
