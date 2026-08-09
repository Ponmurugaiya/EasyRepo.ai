"""Compatibility shim — re-exports from code_qa_agent.

The canonical module is ``src.generation.code_qa_agent``.
This shim keeps existing callers working without changes.
It will be removed in a future cleanup pass.
"""

from src.generation.code_qa_agent import run, QAResponse as AgentResponse, QAResponse

__all__ = ["run", "AgentResponse", "QAResponse"]
