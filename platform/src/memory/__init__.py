"""Memory package — all memory tiers for the EasyRepo pipeline.

Two top-level categories:

STM (Short-Term Memory) — volatile, bounded to a request or conversation.
  stm/short_term.py      — per-request scratch space (ShortTermMemory dataclass)
  stm/working_memory.py  — per-conversation rolling summary + turn persistence

LTM (Long-Term Memory) — persisted to DB, available across sessions.
  ltm/session_knowledge.py    — session-scoped feature cache (expires on re-index)
  ltm/user_memory.py          — global user preferences and background
  ltm/user_repo_preference.py — how this user works with a specific repo
  ltm/repo_user_memory.py     — hard facts about a repo discovered via this user
"""

from src.memory.stm import (
    ShortTermMemory,
    load_history,
    save_turn,
    summarize_after_turn,
)
from src.memory.ltm import (
    # session knowledge
    lookup,
    write,
    inject_ltm,
    lookup_by_feature,
    write_feature,
    # user memory
    load_user_memory,
    upsert_user_memory,
    # user-repo preference
    load_user_repo_preferences,
    upsert_user_repo_preferences,
    # repo-user memory
    load_repo_user_memory,
    upsert_repo_user_memory,
)

__all__ = [
    # STM
    "ShortTermMemory",
    "load_history",
    "save_turn",
    "summarize_after_turn",
    # LTM — session knowledge
    "lookup",
    "write",
    "inject_ltm",
    "lookup_by_feature",
    "write_feature",
    # LTM — user memory
    "load_user_memory",
    "upsert_user_memory",
    # LTM — user-repo preference
    "load_user_repo_preferences",
    "upsert_user_repo_preferences",
    # LTM — repo-user memory
    "load_repo_user_memory",
    "upsert_repo_user_memory",
]
