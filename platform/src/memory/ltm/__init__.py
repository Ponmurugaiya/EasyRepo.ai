"""LTM — Long-Term Memory.

Persisted to the database. Available across sessions and process restarts.

session_knowledge.py    — session-scoped feature cache, keyed by
                          (repo_id, session_id, feature_name).
                          Expires (goes stale) when the repo is re-indexed.

user_memory.py          — global user facts: preferences, background, working
                          style. Apply everywhere regardless of repo.

user_repo_preference.py — user × repo preference facts: how this specific user
                          works with this specific repo. Never expire.

repo_user_memory.py     — hard facts about a codebase confirmed via this user's
                          conversations. Should be treated as stale after a
                          repo re-index (same staleness rule as session_knowledge).
"""

from src.memory.ltm.session_knowledge import (
    lookup,
    write,
    inject_ltm,
    lookup_by_feature,
    write_feature,
)
from src.memory.ltm.user_memory import load_user_memory, upsert_user_memory
from src.memory.ltm.user_repo_preference import (
    load_user_repo_preferences,
    upsert_user_repo_preferences,
)
from src.memory.ltm.repo_user_memory import load_repo_user_memory, upsert_repo_user_memory

__all__ = [
    # session knowledge
    "lookup",
    "write",
    "inject_ltm",
    "lookup_by_feature",
    "write_feature",
    # user memory
    "load_user_memory",
    "upsert_user_memory",
    # user-repo preference
    "load_user_repo_preferences",
    "upsert_user_repo_preferences",
    # repo-user memory
    "load_repo_user_memory",
    "upsert_repo_user_memory",
]
