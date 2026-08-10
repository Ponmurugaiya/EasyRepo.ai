"""Backward-compatibility shim.

FolderSummaryAgent has moved to src.agents.folder_summary_agent.
Import from there directly. This shim will be removed in a future cleanup.
"""
from src.agents.folder_summary_agent import summarize_folder  # noqa: F401

__all__ = ["summarize_folder"]
