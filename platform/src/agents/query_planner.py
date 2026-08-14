"""Query Planner module.

Classifies the user's query intent and selects the optimal retrieval strategy
before any retrieval occurs.  Uses ``groq/llama-3.1-8b-instant`` for speed and
low cost (~200–400ms latency overhead per request).

On any failure (timeout, quota, parse error), falls back to the safe default:
  intent="query", strategy="semantic_search", search_query=original_query

This ensures the pipeline is never blocked by a planner failure.

Public API
----------
plan(query, repo_id) -> QueryPlan
    The only function callers need.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Planner model — fast and cheap.  Each request adds ~200–400ms latency.
_PLANNER_MODEL = "llama-3.1-8b-instant"

# System prompt for the planner
_PLANNER_SYSTEM = """\
You are a query classifier for a codebase intelligence system.
Your job is to analyse a user query and produce a JSON object describing:
  1. The query intent
  2. The retrieval strategy to use
  3. A rewritten search query optimised for code embedding search

Respond with ONLY a JSON object — no explanation, no markdown code fences, no extra text.

Intent options:
  "feature"              — query about a specific class, function, or feature
  "dependency_flow"      — query about how things connect, call, or interact
  "repository_overview"  — query asking for an overview or summary of the repo structure
  "repository_detailed"  — query asking for a full walkthrough of the entire codebase
  "specific_lookup"      — query for a specific symbol name or exact code location

Strategy options:
  "semantic_search"               — use vector search (best for feature/specific_lookup)
  "semantic_search_with_graph"    — vector search + deep graph expansion (best for dependency_flow)
  "repository_walk"               — full repo traversal without query (best for overview/detailed)

Rules:
  - Use "repository_walk" ONLY when the user explicitly wants an overview or full walkthrough.
  - Use "semantic_search_with_graph" when the user asks about relationships, flows, or interactions.
  - Use "semantic_search" for everything else.
  - The "search_query" should be a keyword-rich phrase (not a sentence) optimised for code embeddings.
    Remove filler words. Add relevant technical terms (class names, function names if mentioned).
  - Set "search_query" to null when strategy is "repository_walk" (no query needed).
  - Set "confidence" between 0.0 and 1.0 reflecting how certain you are of the classification.

JSON schema:
{
  "intent": "<one of the intent options>",
  "retrieval_strategy": "<one of the strategy options>",
  "search_query": "<keyword-rich search phrase or null>",
  "confidence": <float 0.0-1.0>
}

Examples (use these to calibrate your classification):

Query: "Give me an overview of this repo"
{"intent": "repository_overview", "retrieval_strategy": "repository_walk", "search_query": null, "confidence": 0.97}

Query: "Give me a overview of this repo"
{"intent": "repository_overview", "retrieval_strategy": "repository_walk", "search_query": null, "confidence": 0.97}

Query: "What does this project do?"
{"intent": "repository_overview", "retrieval_strategy": "repository_walk", "search_query": null, "confidence": 0.95}

Query: "How is this codebase structured?"
{"intent": "repository_overview", "retrieval_strategy": "repository_walk", "search_query": null, "confidence": 0.95}

Query: "Summarise the architecture"
{"intent": "repository_overview", "retrieval_strategy": "repository_walk", "search_query": null, "confidence": 0.95}

Query: "Walk me through the entire codebase"
{"intent": "repository_detailed", "retrieval_strategy": "repository_walk", "search_query": null, "confidence": 0.96}

Query: "Give me a detailed explanation of everything in this repo"
{"intent": "repository_detailed", "retrieval_strategy": "repository_walk", "search_query": null, "confidence": 0.96}

Query: "How does the AuthService class work?"
{"intent": "feature", "retrieval_strategy": "semantic_search", "search_query": "AuthService class authentication", "confidence": 0.95}

Query: "How does the login flow work?"
{"intent": "dependency_flow", "retrieval_strategy": "semantic_search_with_graph", "search_query": "login flow authentication entry point", "confidence": 0.93}

Query: "Where is CONFIG_PATH defined?"
{"intent": "specific_lookup", "retrieval_strategy": "semantic_search", "search_query": "CONFIG_PATH definition", "confidence": 0.97}
"""

# Valid intent and strategy values — used for response validation
_VALID_INTENTS = {
    "feature",
    "dependency_flow",
    "repository_overview",
    "repository_detailed",
    "specific_lookup",
    "query",            # fallback value
    "repository_walk",  # small models sometimes return the strategy as the intent
}
_VALID_STRATEGIES = {
    "semantic_search",
    "semantic_search_with_graph",
    "repository_walk",
}


@dataclass
class QueryPlan:
    """Output of the Query Planner.

    Attributes
    ----------
    intent:
        Classified query intent.
    retrieval_strategy:
        Selected retrieval strategy.
    search_query:
        Rewritten query for code embeddings (None when strategy is repository_walk).
    confidence:
        Planner confidence in the classification [0.0, 1.0].
    """

    intent: str
    retrieval_strategy: str
    search_query: Optional[str]
    confidence: float


def _default_plan(query: str) -> QueryPlan:
    """Return a safe fallback plan that routes to semantic_search."""
    return QueryPlan(
        intent="query",
        retrieval_strategy="semantic_search",
        search_query=query,
        confidence=0.0,
    )


def _parse_plan_response(raw: str, original_query: str) -> QueryPlan:
    """Parse the planner LLM response into a QueryPlan.

    Handles both clean JSON responses and JSON embedded in prose.
    Returns the default plan on any parse failure.
    """
    # Try to extract a JSON object even if the model wrapped it in prose
    json_match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not json_match:
        logger.warning("QueryPlanner: no JSON object found in response — using default")
        return _default_plan(original_query)

    try:
        data = json.loads(json_match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("QueryPlanner: JSON parse error (%s) — using default", exc)
        return _default_plan(original_query)

    intent = data.get("intent", "query")
    strategy = data.get("retrieval_strategy", "semantic_search")
    search_query = data.get("search_query")
    confidence = float(data.get("confidence", 0.5))

    # Validate values against allowed sets; fall back on unknown values
    if intent not in _VALID_INTENTS:
        logger.warning("QueryPlanner: unknown intent %r — using 'query'", intent)
        intent = "query"

    # Normalize: small models sometimes return the strategy name as the intent
    if intent == "repository_walk":
        intent = "repository_overview"

    if strategy not in _VALID_STRATEGIES:
        logger.warning("QueryPlanner: unknown strategy %r — using 'semantic_search'", strategy)
        strategy = "semantic_search"

    # If search_query is absent (repository_walk) or empty, use original
    if strategy != "repository_walk" and not search_query:
        search_query = original_query

    # Low-confidence result: override to semantic_search as the safer choice
    if confidence < 0.3 and strategy == "repository_walk":
        logger.debug("QueryPlanner: low confidence %.2f — overriding to semantic_search", confidence)
        strategy = "semantic_search"
        search_query = search_query or original_query

    return QueryPlan(
        intent=intent,
        retrieval_strategy=strategy,
        search_query=search_query if strategy != "repository_walk" else None,
        confidence=confidence,
    )


def plan(query: str, repo_id: Optional[str] = None) -> QueryPlan:
    """Classify the query and return a QueryPlan.

    Uses the fast model tier via smart_complete. On any failure returns
    the safe default plan so the pipeline is never blocked.

    Planner model selection:
    - Primary:   groq/llama-3.1-8b-instant    (fastest, purpose-built for classification)
    - Fallback1: gemini/gemini-2.5-flash-lite  (high quota, reliable)
    - Fallback2: nvidia_nim fast tier          (no RPD cap, used when Groq+Gemini exhausted)
    - Never uses allam-2-7b (slow, unreliable for JSON classification tasks)
    """
    try:
        import src.generation.llm_client as _llm

        user_message = f'Query: "{query}"\n\nClassify this query and respond with the JSON object.'

        # Try primary: force fastest Groq model for minimal latency overhead
        try:
            raw_response, _, _, _ = _llm.smart_complete(
                query=query,
                context=user_message,
                system_prompt=_PLANNER_SYSTEM,
                task_type="fast",
                force_model="groq/llama-3.1-8b-instant",
            )
        except _llm.LLMProviderError:
            # Groq primary exhausted/unavailable — fall back to Gemini Flash-Lite.
            # Explicitly skip allam-2-7b (poor JSON compliance, very slow).
            # Skip other Groq models too — the planner needs reliable JSON output.
            try:
                raw_response, _, _, _ = _llm.smart_complete(
                    query=query,
                    context=user_message,
                    system_prompt=_PLANNER_SYSTEM,
                    task_type="fast",
                    skip_providers={"openrouter", "cohere", "cloudflare", "cerebras"},
                    force_provider="gemini",
                )
            except _llm.LLMProviderError:
                # Gemini also exhausted — fall back to NVIDIA NIM (no RPD cap).
                raw_response, _, _, _ = _llm.smart_complete(
                    query=query,
                    context=user_message,
                    system_prompt=_PLANNER_SYSTEM,
                    task_type="fast",
                    force_provider="nvidia_nim",
                )

        result = _parse_plan_response(raw_response, query)
        logger.debug(
            "QueryPlanner: repo=%s intent=%s strategy=%s confidence=%.2f",
            repo_id, result.intent, result.retrieval_strategy, result.confidence,
        )
        return result

    except Exception as exc:
        logger.warning("QueryPlanner failed (repo=%s): %s — using default plan", repo_id, exc)
        return _default_plan(query)
