"""Context builder module for assembling final structured context for LLM prompting.

Prioritization order (budget cap trimming):
1. Core retrieved entities
2. Direct CALLS neighbors (callees depth 1)
3. Caller entities (incoming depth 1)
4. Depth 2 CALLS neighbors (callees depth 2)
5. Parent (CONTAINS) entities
6. Inheritance (INHERITS / IMPLEMENTS) entities
"""

from __future__ import annotations

import re
from typing import Dict, List, Set, Tuple
from src.retrieval.models import ExpandedContext, FinalContext
from src.storage.models import EntityModel


def _estimate_tokens(text: str) -> int:
    """Rough character-to-token heuristic (4 chars per token)."""
    return len(text) // 4


def _extract_docstring(source: str, has_docstring: bool) -> str:
    if not has_docstring or not source:
        return ""

    py_match = re.search(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', source, re.DOTALL)
    if py_match:
        doc = py_match.group(1) if py_match.group(1) is not None else py_match.group(2)
        return (doc or "").strip()

    ts_match = re.search(r'/\*\*(.*?)\*/', source, re.DOTALL)
    if ts_match:
        lines = ts_match.group(1).split("\n")
        cleaned = [re.sub(r"^\s*\*?\s?", "", line) for line in lines]
        return "\n".join(cleaned).strip()

    return ""


def _render_entity_snippet(entity: EntityModel, role_label: str = "") -> str:
    header_role = f" ({role_label})" if role_label else ""
    doc = _extract_docstring(entity.source, entity.has_docstring)
    doc_block = f"Docstring:\n{doc}\n" if doc else ""

    return (
        f"--- Entity: {entity.type} {entity.name}{header_role} ---\n"
        f"Citation: [{entity.file_path}:{entity.start_line}-{entity.end_line}]\n"
        f"{doc_block}"
        f"Source Code:\n{entity.source}\n"
    )


def build_context(
    expanded_contexts: List[ExpandedContext],
    query: str,
    repo_id: str,
    token_budget: int = 6000,
) -> FinalContext:
    """Assemble list of ExpandedContext into a clean prompt-ready context object.

    Parameters
    ----------
    expanded_contexts:
        List of ExpandedContext instances.
    query:
        Original natural language query.
    repo_id:
        Repository ID.
    token_budget:
        Configurable token cap (default ~6000 tokens).

    Returns
    -------
    FinalContext
        Structured object containing prompt text, token estimates, and metadata.
    """
    lines: List[str] = []
    lines.append(f"=== QUERY CONTEXT FOR REPOSITORY: {repo_id} ===")
    lines.append(f"Query: \"{query}\"\n")

    # Global deduplication set for rendered entity bodies
    rendered_entity_ids: Set[str] = set()

    # Build execution path traces first if present
    trace_lines: List[str] = []
    for exp in expanded_contexts:
        core = exp.core.entity
        if exp.called_entities:
            # Group call chain
            for called in exp.called_entities:
                if called.depth == 1:
                    trace_lines.append(
                        f"  {core.name} [{core.file_path}:{core.start_line}] "
                        f"CALLS {called.entity.name} [{called.entity.file_path}:{called.entity.start_line}]"
                    )
                elif called.depth == 2:
                    trace_lines.append(
                        f"    ↳ via {called.called_via} CALLS {called.entity.name} [{called.entity.file_path}:{called.entity.start_line}]"
                    )

    if trace_lines:
        lines.append("=== RECONSTRUCTED EXECUTION TRACES ===")
        lines.extend(trace_lines)
        lines.append("")

    lines.append("=== RETRIEVED & EXPANDED CODE ENTITIES ===")

    # Priority Bucket Assembly
    # Items: (priority_rank, entity, role_label)
    # Priority: 1=Core, 2=Direct Callees, 3=Callers, 4=Depth2 Callees, 5=Parents, 6=Inheritance
    priority_items: List[Tuple[int, EntityModel, str]] = []

    for exp in expanded_contexts:
        core = exp.core.entity
        priority_items.append((1, core, f"Core Result #{exp.core.rank} (score: {exp.core.score:.3f})"))

        for called in exp.called_entities:
            if called.depth == 1:
                priority_items.append((2, called.entity, f"Callee of {core.name}"))
            elif called.depth == 2:
                priority_items.append((4, called.entity, f"Nested Callee via {called.called_via}"))

        for caller in exp.caller_entities:
            priority_items.append((3, caller, f"Caller of {core.name}"))

        if exp.parent_entity:
            priority_items.append((5, exp.parent_entity, f"Parent of {core.name}"))

        for inh in exp.inheritance_entities:
            priority_items.append((6, inh, f"Inherited/Implemented by {core.name}"))

    # Sort by priority rank
    priority_items.sort(key=lambda x: x[0])

    truncated = False
    current_text = "\n".join(lines) + "\n"

    for rank, ent, role in priority_items:
        if ent.id in rendered_entity_ids:
            continue

        snippet = _render_entity_snippet(ent, role) + "\n"
        projected_text = current_text + snippet
        projected_tokens = _estimate_tokens(projected_text)

        if projected_tokens > token_budget:
            truncated = True
            break

        current_text = projected_text
        rendered_entity_ids.add(ent.id)

    total_tokens = _estimate_tokens(current_text)

    return FinalContext(
        query=query,
        repo_id=repo_id,
        expanded_contexts=expanded_contexts,
        rendered_text=current_text,
        total_tokens_est=total_tokens,
        truncated=truncated,
    )
