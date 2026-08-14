"""Working Memory — per-conversation rolling summary and turn persistence.

STM tier: episodic at conversation granularity. Persisted to DB so it
survives page refresh, but bounded to one conversation thread. Compressed
eagerly after each Q&A pair — the LLM always sees a summary, never raw turns.

Three responsibilities:
  1. load_history        — load the rolling summary for a conversation
  2. save_turn           — upsert conversation row and append a new turn
  3. summarize_after_turn — compress the latest turns into the rolling summary
                            and extract LTM facts into the three semantic tiers

Design: every Q&A pair is summarised eagerly after it is saved.
The rolling summary is cumulative — each new summary incorporates the prior
summary plus the latest exchange.  load_history returns only the summary
string; no raw turns are ever forwarded to the Answer Agent.

Only called for authenticated users (user_id must be present).
Anonymous users send their history in the request body — nothing is persisted.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from src.storage.models import ConversationModel, ConversationTurnModel

logger = logging.getLogger(__name__)


def load_history(
    conversation_id: str,
    user_id: str,
    db: Session,
) -> tuple[Optional[str], list]:
    """Return (summary_text | None, []) for a conversation.

    The second element is always an empty list — raw turns are never passed to
    the LLM.  The signature retains the tuple shape so callers that destructure
    ``(summary, recent_turns)`` continue to work without modification.

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

        # Return only the rolling summary — no raw turns forwarded to the LLM.
        return conv.summary, []

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
                summarized_through_turn=0,
            )
            db.add(conv)
            db.flush()
            next_index = 0
        else:
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


def summarize_after_turn(
    conversation_id: str,
    db: Session,
    llm_client,
    trace=None,
    user_id: Optional[str] = None,
    repo_id: Optional[str] = None,
) -> None:
    """Eagerly compress all unsummarised turns AND extract long-term memories.

    Single LLM call that does two things at once:
      1. Produces a rolling conversation summary (injected on every future request)
      2. Extracts LTM facts into the three semantic memory tiers:
           - user_memory          : global user preferences/background (tier 4)
           - user_repo_preferences: how this user works with this specific repo (tier 5)
           - repo_memory          : facts about this repo learned in this conversation (tier 6)

    The rolling summary is cumulative:
      • Q1 → summarize+extract(Q1+A1)                   → summary_v1
      • Q2 → summarize+extract(summary_v1 + Q2+A2)      → summary_v2
      …

    IMPORTANT: Must be called with its own dedicated DB session — dispatched
    to a thread pool; SQLAlchemy sessions are not thread-safe.

    Parameters
    ----------
    conversation_id:
        Target conversation UUID.
    db:
        Dedicated database session (NOT the FastAPI request session).
    llm_client:
        The llm_client module (passed to avoid circular imports).
    trace:
        Optional PipelineTrace for structured logging.
    user_id:
        Authenticated user ID — required to persist LTM facts.
        If None, LTM extraction is skipped.
    repo_id:
        Repository ID — required to persist repo-scoped LTM facts.
        If None, repo-scoped LTM extraction is skipped.
    """
    import json as _json

    try:
        conv: Optional[ConversationModel] = (
            db.query(ConversationModel)
            .filter_by(id=conversation_id)
            .first()
        )
        if conv is None:
            return

        unsummarised: list[ConversationTurnModel] = (
            db.query(ConversationTurnModel)
            .filter(
                ConversationTurnModel.conversation_id == conversation_id,
                ConversationTurnModel.turn_index > conv.summarized_through_turn,
            )
            .order_by(ConversationTurnModel.turn_index.asc())
            .all()
        )

        if not unsummarised:
            return

        new_turns_text = "\n".join(
            f"{t.role.upper()}: {t.content}" for t in unsummarised
        )

        prior_summary = conv.summary or ""
        if prior_summary:
            condensation_input = (
                f"Prior summary:\n{prior_summary}\n\n"
                f"New exchange to incorporate:\n{new_turns_text}"
            )
        else:
            condensation_input = new_turns_text

        system_prompt = (
            "You are a memory manager for a codebase assistant. "
            "Given a conversation exchange, you must return a single JSON object with four keys:\n\n"
            "  \"summary\": A compact paragraph (max 200 words) summarizing topics discussed, "
            "decisions made, and code areas referenced. Do NOT reproduce raw code blocks.\n\n"
            "  \"user_memory\": A list of facts about the USER that are globally useful "
            "(preferences, background, working style, expertise level). "
            "Each item: {\"category\": \"preference|background|working_style\", \"fact\": \"...\"}.\n\n"
            "  \"user_repo_preferences\": A list of facts about how THIS USER works with "
            "THIS SPECIFIC REPO (their familiarity, focus areas, role in the project, "
            "stated experience with this codebase). "
            "Each item: {\"category\": \"familiarity|focus_area|role_in_project\", \"fact\": \"...\"}.\n\n"
            "  \"repo_memory\": A list of facts about the CODEBASE ITSELF discovered or "
            "confirmed in this conversation (architectural decisions, known bugs, confirmed "
            "behaviour, tech choices). "
            "Each item: {\"category\": \"codebase_fact|open_issue|architectural_decision|confirmed_behaviour\", \"fact\": \"...\"}.\n\n"
            "Return ONLY the raw JSON object — no markdown fences, no prose, no explanation. "
            "If a list has nothing to add, return an empty array []."
        )

        try:
            # Primary: Gemini Flash-Lite (15 RPM, 1000 RPD).
            # Deliberately avoids Groq to prevent RPM contention with
            # QueryPlanner and citation correction which run concurrently.
            try:
                raw_response, _, _, _ = llm_client.smart_complete(
                    query="Summarize and extract memories from this conversation",
                    context=condensation_input,
                    system_prompt=system_prompt,
                    task_type="fast",
                    force_provider="gemini",
                )
            except llm_client.LLMProviderError:
                # Gemini exhausted — fall back to NVIDIA NIM then Groq
                raw_response, _, _, _ = llm_client.smart_complete(
                    query="Summarize and extract memories from this conversation",
                    context=condensation_input,
                    system_prompt=system_prompt,
                    task_type="fast",
                    skip_providers={"openrouter", "cohere", "cloudflare", "cerebras"},
                )
        except Exception as llm_exc:
            logger.warning("summarize_after_turn: LLM call failed: %s", llm_exc)
            return

        parsed: Optional[dict] = None
        try:
            clean = raw_response.strip().strip("```json").strip("```").strip()
            parsed = _json.loads(clean)
        except _json.JSONDecodeError:
            import re as _re
            match = _re.search(r'\{.*\}', raw_response, _re.DOTALL)
            if match:
                try:
                    parsed = _json.loads(match.group(0))
                except _json.JSONDecodeError:
                    pass

        if not parsed:
            logger.warning(
                "summarize_after_turn: could not parse LLM JSON — raw: %.200s", raw_response
            )
            return

        new_summary = parsed.get("summary", "").strip()
        if not new_summary:
            logger.warning("summarize_after_turn: LLM returned empty summary")
            return

        last_turn_index = unsummarised[-1].turn_index
        conv.summary = new_summary
        conv.summarized_through_turn = last_turn_index
        db.commit()

        logger.info(
            "Conversation %s summarised through turn %d (%d new turns compressed)",
            conversation_id[:16],
            last_turn_index,
            len(unsummarised),
        )
        if trace:
            trace.step_summarise(
                conversation_id=conversation_id,
                turns_compressed=len(unsummarised),
                summarized_through=last_turn_index,
            )

        # Persist extracted facts to the three LTM semantic tiers.
        if user_id:
            from src.memory.ltm.user_memory import upsert_user_memory
            from src.memory.ltm.user_repo_preference import upsert_user_repo_preferences
            from src.memory.ltm.repo_user_memory import upsert_repo_user_memory

            user_facts = parsed.get("user_memory") or []
            if user_facts:
                upsert_user_memory(user_id, user_facts, db)

            if repo_id:
                repo_pref_facts = parsed.get("user_repo_preferences") or []
                if repo_pref_facts:
                    upsert_user_repo_preferences(user_id, repo_id, repo_pref_facts, db)

                repo_facts = parsed.get("repo_memory") or []
                if repo_facts:
                    upsert_repo_user_memory(user_id, repo_id, repo_facts, db)

    except Exception as exc:
        logger.warning("summarize_after_turn failed: %s", exc)
        db.rollback()


# Backward-compatibility alias
maybe_summarize = summarize_after_turn
