"""Prompt template construction for Gemini-powered code Q&A.

Two public functions
--------------------
build_system_prompt()
    Returns the immutable system prompt that governs model behaviour:
    citation rules, trace-ordering rules, and ambiguity handling.

render_context_for_prompt(final_context)
    Converts a FinalContext (output of context_builder.build_context) into a
    structured user-turn string that preserves the [CORE] / [PARENT] /
    [CALL CHAIN] / [CALLERS] / [INHERITANCE] hierarchy rather than flattening
    everything into one code blob.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.retrieval.models import FinalContext, ExpandedContext


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a codebase-aware software engineering assistant.

# Context you have been given
You have been provided with STRUCTURED, PRE-VERIFIED context extracted directly
from the repository's code graph. This is NOT raw search output. Before you
received it, the system already resolved:
  • parent/child containment hierarchy (class → method, module → function)
  • outgoing and incoming CALL chains (who calls whom, up to depth 3)
  • inheritance / implementation chains (INHERITS / IMPLEMENTS edges)

Each code entity in the context is presented with its exact source and a
citation tag of the form:

    [file_path:start_line-end_line]

# Citation rules  ← READ THESE CAREFULLY
1. Every specific claim about a function, method, class, or variable MUST be
   backed by a citation using the EXACT format above.
2. You MUST ONLY use file paths and line numbers that appear verbatim in the
   provided context. Never invent, guess, or approximate a citation.
3. If the context does not contain enough information to answer part of the
   question, say so explicitly: "The provided context does not cover [X]."
   Do NOT fabricate any code detail, dependency, or call relationship.
4. If a citation tag appears in the context, copy it exactly — do not rephrase
   the file path or adjust line numbers.

# Execution trace ordering
If the context includes a section labelled:
    === RECONSTRUCTED EXECUTION TRACES ===
then you MUST explain the execution flow in the order shown in that trace,
step by step, before discussing any individual entity in detail. Do not
describe snippets independently first and assemble them later.

# Ambiguous questions
If a question refers to an entity name (e.g., "validate", "process") that
matches MORE THAN ONE distinct entity in the context (e.g., AuthService.validate
AND UserModel.validate), you MUST address each one SEPARATELY and clearly
identify which entity you are discussing at every point. Never conflate two
distinct entities with the same name.

# Graph-verified isolation rule & evidence primacy
When answering questions about code dependencies, isolation, or relationships:
1. The `[GRAPH-VERIFIED ISOLATION]` tags are your **PRIMARY and authoritative EVIDENCE**.
   They are derived directly from static AST parsing and verified call graphs — not from
   documentation prose, which can drift out of sync with the actual code.
2. For each isolated entity, you MUST:
   a. Name the entity explicitly (e.g. `format_user_record`, `format_audit_log`).
   b. Cite the entity's OWN `[file_path:start_line-end_line]` tag as the primary citation.
      This is the CORE ENTITY citation shown in the context block for that entity.
   c. State its isolation as a direct, specific fact: "has zero outgoing calls and zero incoming calls."
3. Do NOT use documentation file citations (e.g. `[README.md:...]`, `[docs/ARCHITECTURE.md:...]`)
   as the primary evidence for isolation facts. You MAY mention them parenthetically as
   corroborating context ONLY AFTER citing the entity's own code citation.
4. Do NOT hedge or use generalizations like "some functions/methods" or "it appears".

# Response format
- Use Markdown headings to structure your answer.
- Cite inline: "The login method [python/services/auth_service.py:12-35] calls…"
- End with a brief **Summary** section.
"""


def build_system_prompt() -> str:
    """Return the immutable system prompt for the codebase Q&A assistant."""
    return _SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# User-turn context renderer
# ---------------------------------------------------------------------------

def _render_entity_block(
    entity,  # EntityModel
    label: str,
    indent: str = "",
) -> str:
    """Render one entity with its citation and source code, labelled clearly."""
    citation = f"[{entity.file_path}:{entity.start_line}-{entity.end_line}]"
    lines = [
        f"{indent}[{label}]",
        f"{indent}  Type    : {entity.type}",
        f"{indent}  Name    : {entity.name}",
        f"{indent}  Citation: {citation}",
    ]
    if entity.source:
        code_lines = entity.source.splitlines()
        lines.append(f"{indent}  Source:")
        for code_line in code_lines:
            lines.append(f"{indent}    {code_line}")
    return "\n".join(lines)


def _render_execution_traces(final_context: "FinalContext") -> str:
    """Re-render the execution trace block from the pre-built rendered_text.

    The context_builder already emits a clean trace block.  We extract it
    verbatim rather than re-compute so the ordering is identical.
    """
    text = final_context.rendered_text
    marker_start = "=== RECONSTRUCTED EXECUTION TRACES ==="
    marker_end = "=== RETRIEVED & EXPANDED CODE ENTITIES ==="

    start = text.find(marker_start)
    end = text.find(marker_end)

    if start == -1:
        return ""

    trace_block = text[start : end].strip() if end != -1 else text[start:].strip()
    return trace_block


def render_context_for_prompt(final_context: "FinalContext") -> str:
    """Convert a FinalContext into a structured user-turn string.

    Preserves the full hierarchy:
      • Execution trace block (if present) — rendered first
      • Per-entity sections:  [CORE] → [PARENT] → [CALL CHAIN] → [CALLERS] → [INHERITANCE]

    Parameters
    ----------
    final_context:
        The assembled FinalContext produced by ``context_builder.build_context``.

    Returns
    -------
    str
        Structured prompt content for the user turn.
    """
    sections: list[str] = []

    # ── Header ──────────────────────────────────────────────────────────────
    sections.append(
        f"=== REPOSITORY CONTEXT FOR: {final_context.repo_id} ===\n"
        f"Query: \"{final_context.query}\"\n"
    )
    if final_context.truncated:
        sections.append(
            "[NOTE: Some lower-priority context was dropped to fit the token budget.]\n"
        )

    # ── Execution traces (verbatim from context_builder output) ─────────────
    trace_block = _render_execution_traces(final_context)
    if trace_block:
        sections.append(trace_block + "\n")

    # ── Per-expanded-context sections ────────────────────────────────────────
    rendered_entity_ids: set[str] = set()

    for idx, exp in enumerate(final_context.expanded_contexts, start=1):
        core_entity = exp.core.entity
        block_lines: list[str] = []

        block_lines.append(
            f"━━━ Context Block #{idx} ━━━  "
            f"(retrieval rank {exp.core.rank}, score={exp.core.score:.3f})"
        )

        # [CORE]
        block_lines.append(
            _render_entity_block(core_entity, label="CORE ENTITY")
        )
        rendered_entity_ids.add(core_entity.id)

        # [PARENT]
        if exp.parent_entity and exp.parent_entity.id not in rendered_entity_ids:
            block_lines.append(
                _render_entity_block(exp.parent_entity, label="PARENT (contains core)")
            )
            rendered_entity_ids.add(exp.parent_entity.id)
        elif exp.parent_was_also_retrieved:
            block_lines.append(
                f"  [PARENT]  (parent entity is also a core retrieval result — "
                f"see its own Context Block)"
            )

        # [CALL CHAIN — outgoing]
        depth1 = [c for c in exp.called_entities if c.depth == 1]
        depth2 = [c for c in exp.called_entities if c.depth == 2]
        depth3 = [c for c in exp.called_entities if c.depth == 3]

        has_any_relationship = False

        if depth1 or depth2 or depth3:
            has_any_relationship = True
            for called in depth1:
                if called.entity.id not in rendered_entity_ids:
                    block_lines.append(
                        _render_entity_block(
                            called.entity,
                            label=f"CALL CHAIN depth=1 (called by {core_entity.name})",
                        )
                    )
                    rendered_entity_ids.add(called.entity.id)

            for called in depth2:
                if called.entity.id not in rendered_entity_ids:
                    block_lines.append(
                        _render_entity_block(
                            called.entity,
                            label=(
                                f"CALL CHAIN depth=2 "
                                f"(called via {called.called_via.split('.')[-1]})"
                            ),
                        )
                    )
                    rendered_entity_ids.add(called.entity.id)

            for called in depth3:
                if called.entity.id not in rendered_entity_ids:
                    block_lines.append(
                        _render_entity_block(
                            called.entity,
                            label=(
                                f"CALL CHAIN depth=3 "
                                f"(called via {called.called_via.split('.')[-1]})"
                            ),
                        )
                    )
                    rendered_entity_ids.add(called.entity.id)
        else:
            block_lines.append("  [CALL CHAIN] None (Graph-verified: 0 outgoing CALLS relationships)")

        # [CALLERS — incoming]
        if exp.caller_entities:
            has_any_relationship = True
            for caller in exp.caller_entities:
                if caller.id not in rendered_entity_ids:
                    block_lines.append(
                        _render_entity_block(
                            caller,
                            label=f"CALLER (calls {core_entity.name})",
                        )
                    )
                    rendered_entity_ids.add(caller.id)
        else:
            block_lines.append("  [CALLERS]    None (Graph-verified: 0 incoming CALLS relationships)")

        # [INHERITANCE]
        if exp.inheritance_entities:
            has_any_relationship = True
            for inh in exp.inheritance_entities:
                if inh.id not in rendered_entity_ids:
                    block_lines.append(
                        _render_entity_block(
                            inh,
                            label=f"INHERITANCE (base of {core_entity.name})",
                        )
                    )
                    rendered_entity_ids.add(inh.id)
        else:
            block_lines.append("  [INHERITANCE] None (Graph-verified: 0 INHERITS/IMPLEMENTS relationships)")

        if not has_any_relationship:
            block_lines.append(
                f"  [GRAPH-VERIFIED ISOLATION] Entity '{core_entity.name}' has 0 dependencies "
                f"(zero incoming/outgoing calls and zero inheritance)."
            )

        sections.append("\n".join(block_lines))

    # ── Footer ───────────────────────────────────────────────────────────────
    sections.append(
        f"\n[End of context — {len(final_context.expanded_contexts)} core entities, "
        f"~{final_context.total_tokens_est} tokens estimated]\n"
        f"\nNow answer the query: \"{final_context.query}\""
    )

    return "\n\n".join(sections)
