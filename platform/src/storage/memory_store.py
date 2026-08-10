"""Backward-compatibility shim.

The three semantic LTM tiers have moved:
  user_memory          → src.memory.ltm.user_memory
  user_repo_preference → src.memory.ltm.user_repo_preference
  repo_user_memory     → src.memory.ltm.repo_user_memory

Import from those modules directly. This shim will be removed in a future cleanup.
"""
from src.memory.ltm.user_memory import (  # noqa: F401
    load_user_memory,
    upsert_user_memory,
)
from src.memory.ltm.user_repo_preference import (  # noqa: F401
    load_user_repo_preferences,
    upsert_user_repo_preferences,
)
from src.memory.ltm.repo_user_memory import (  # noqa: F401
    load_repo_user_memory,
    upsert_repo_user_memory,
)

__all__ = [
    "load_user_memory",
    "upsert_user_memory",
    "load_user_repo_preferences",
    "upsert_user_repo_preferences",
    "load_repo_user_memory",
    "upsert_repo_user_memory",
]
