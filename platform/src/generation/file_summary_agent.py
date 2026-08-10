"""Backward-compatibility shim.

FileSummaryAgent has moved to src.agents.file_summary_agent.
Import from there directly. This shim will be removed in a future cleanup.
"""
from src.agents.file_summary_agent import summarize_file  # noqa: F401

__all__ = ["summarize_file"]
