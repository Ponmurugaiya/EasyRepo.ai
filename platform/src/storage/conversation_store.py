"""Backward-compatibility shim.

Conversation working memory has moved to src.memory.stm.working_memory.
Import from there directly. This shim will be removed in a future cleanup.
"""
from src.memory.stm.working_memory import (  # noqa: F401
    load_history,
    save_turn,
    summarize_after_turn,
    maybe_summarize,
)

__all__ = ["load_history", "save_turn", "summarize_after_turn", "maybe_summarize"]
