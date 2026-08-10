"""Backward-compatibility shim.

ShortTermMemory has moved to src.memory.stm.short_term.
Import from there directly. This shim will be removed in a future cleanup.
"""
from src.memory.stm.short_term import ShortTermMemory  # noqa: F401

__all__ = ["ShortTermMemory"]
