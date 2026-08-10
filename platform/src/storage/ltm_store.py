"""Backward-compatibility shim.

All LTM session knowledge functions have moved to src.memory.ltm.session_knowledge.
Import from there directly. This shim will be removed in a future cleanup.
"""
from src.memory.ltm.session_knowledge import (  # noqa: F401
    lookup,
    write,
    inject_ltm,
    lookup_by_feature,
    write_feature,
)

__all__ = ["lookup", "write", "inject_ltm", "lookup_by_feature", "write_feature"]
