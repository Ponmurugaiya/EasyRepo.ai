"""Conversation history formatting helpers.

Converts conversation turns into a compact text block suitable for injection
into the Answer Agent's system prompt.

The format is designed to be token-efficient:
  User: <message>
  Assistant: <message>
  ...

When a rolling summary is present (authenticated users), it appears first
followed by the recent unsummarised turns.

Public API
----------
format_history(turns) -> str
    Format a list of raw ConversationTurn dicts/objects.

format_history_with_summary(summary, recent_turns) -> str
    Format DB-backed summary + recent ConversationTurnModel objects.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.storage.models import ConversationTurnModel


def format_history(turns: list) -> str:
    """Format a list of conversation turns from the client payload.

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
    recent_turns: list["ConversationTurnModel"],
) -> str:
    """Format DB-backed conversation history (summary + recent turns).

    Parameters
    ----------
    summary:
        Rolling LLM-generated summary of older turns (may be None).
    recent_turns:
        Most recent unsummarised ConversationTurnModel rows.

    Returns
    -------
    str
        Formatted history text, or empty string if both inputs are empty/None.
    """
    parts = []

    if summary:
        parts.append(f"[Previous conversation summary]\n{summary}")

    if recent_turns:
        turn_lines = []
        for turn in recent_turns:
            role_label = "User" if turn.role == "user" else "Assistant"
            turn_lines.append(f"{role_label}: {turn.content}")
        parts.append("\n".join(turn_lines))

    return "\n\n".join(parts)
