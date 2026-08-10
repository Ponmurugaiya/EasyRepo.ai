"""Conversation history formatting helpers.

Converts conversation history into a compact text block suitable for injection
into the Answer Agent's system prompt.

Design: the LLM always receives only a rolling summary of prior exchanges —
never raw turn-by-turn history.  This keeps the context window bounded and
predictable regardless of conversation length.

Flow
----
  Q1 answered  → summarize(Q1+A1)              → summary_v1
  Q2 arrives   → inject summary_v1 into prompt → answer Q2
  Q2 answered  → summarize(summary_v1 + Q2+A2) → summary_v2
  Q3 arrives   → inject summary_v2 into prompt → answer Q3
  …

Public API
----------
format_history(turns) -> str
    Format client-sent turns for anonymous users (no DB persistence).
    Used as a best-effort fallback; the full raw list is formatted because
    anonymous users have no server-side summary.

format_history_with_summary(summary, recent_turns) -> str
    Format DB-backed history for authenticated users.
    Only the rolling summary is included — ``recent_turns`` is accepted for
    signature compatibility but is intentionally ignored.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.storage.models import ConversationTurnModel


def format_history(turns: list) -> str:
    """Format a list of conversation turns from the client payload.

    Used for anonymous users who manage history client-side and re-send it
    on every request.  Raw turns are formatted as-is since there is no
    server-side summary available.

    Parameters
    ----------
    turns:
        List of objects with .role and .content attributes,
        or dicts with "role" and "content" keys.

    Returns
    -------
    str
        Formatted history text, or empty string if turns is empty.
    """
    if not turns:
        return ""

    lines = []
    for turn in turns:
        if hasattr(turn, "role"):
            role = turn.role
            content = turn.content
        else:
            role = turn.get("role", "user")
            content = turn.get("content", "")

        role_label = "User" if role == "user" else "Assistant"
        lines.append(f"{role_label}: {content}")

    return "\n".join(lines)


def format_history_with_summary(
    summary: Optional[str],
    recent_turns: "list[ConversationTurnModel]",  # accepted but not used
) -> str:
    """Format DB-backed conversation history for authenticated users.

    Only the rolling summary is injected into the prompt.  ``recent_turns``
    is accepted to preserve the call-site signature but is intentionally
    ignored — the rolling summary already captures everything those turns
    contained.

    Parameters
    ----------
    summary:
        Rolling LLM-generated summary of all prior exchanges (may be None
        on the very first message of a conversation).
    recent_turns:
        Ignored.  Kept in signature for backward compatibility.

    Returns
    -------
    str
        The summary string, or empty string if no summary exists yet.
    """
    if not summary:
        return ""

    return f"[Conversation summary so far]\n{summary}"
