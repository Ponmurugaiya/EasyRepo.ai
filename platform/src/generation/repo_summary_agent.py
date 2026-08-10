"""Backward-compatibility shim.

RepoSummaryAgent has moved to src.agents.repo_summary_agent.
Import from there directly. This shim will be removed in a future cleanup.
"""
from src.agents.repo_summary_agent import summarize_repo  # noqa: F401

__all__ = ["summarize_repo"]
