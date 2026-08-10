"""Backward-compatibility shim.

CodeQAAgent has moved to src.agents.code_qa_agent.
Import from there directly. This shim will be removed in a future cleanup.
"""
from src.agents.code_qa_agent import run, QAResponse  # noqa: F401

__all__ = ["run", "QAResponse"]
