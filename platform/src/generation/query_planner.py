"""Backward-compatibility shim.

QueryPlannerAgent has moved to src.agents.query_planner.
Import from there directly. This shim will be removed in a future cleanup.
"""
from src.agents.query_planner import (  # noqa: F401
    plan,
    QueryPlan,
    _default_plan,
    _parse_plan_response,
    _VALID_INTENTS,
    _VALID_STRATEGIES,
)

__all__ = [
    "plan", "QueryPlan", "_default_plan", "_parse_plan_response",
    "_VALID_INTENTS", "_VALID_STRATEGIES",
]
