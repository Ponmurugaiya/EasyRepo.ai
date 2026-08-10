"""Backward-compatibility shim.

CodeQAAgent has moved to src.agents.code_qa_agent.
answer_agent.py is a further alias kept for existing tests and callers.
Import from src.agents.code_qa_agent directly going forward.
"""
from src.agents.code_qa_agent import (  # noqa: F401
    run,
    QAResponse as AgentResponse,
    QAResponse,
    _extract_answer_json,
    _build_augmented_system_prompt,
)

__all__ = [
    "run", "AgentResponse", "QAResponse",
    "_extract_answer_json", "_build_augmented_system_prompt",
]
