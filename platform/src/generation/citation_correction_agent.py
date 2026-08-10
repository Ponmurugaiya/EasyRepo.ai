"""Backward-compatibility shim.

CitationCorrectionAgent has moved to src.agents.citation_correction_agent.
Import from there directly. This shim will be removed in a future cleanup.
"""
from src.agents.citation_correction_agent import run, CorrectionResult  # noqa: F401

__all__ = ["run", "CorrectionResult"]
