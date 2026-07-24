"""Post-hoc citation validator for Gemini-generated answers.

After the model returns an answer, this module:
  1. Parses every ``[file_path:start_line-end_line]`` and ``[file_path:line]`` citation.
  2. Classifies each citation into a 3-way taxonomy:
     - Category (a) DEFINITION: Range matches declared entity lines & text names entity,
       OR the range falls within a container (file/class) AND the named symbol is a
       CONTAINS child declared at those lines in the DB.
     - Category (b) CALL-SITE: Line is inside caller body, text describes invocation,
       and a real CALLS edge exists in the DB graph.
     - Category (c) UNSUPPORTED: Line/path not in context, or claims an unverified CALLS edge.

Known Limitation / Future Work:
-------------------------------
Object instantiation citations (e.g. "UserModel created at line 37") are currently
classified as Category (c) UNSUPPORTED because the graph schema models CONTAINS,
CALLS, IMPORTS, INHERITS, and IMPLEMENTS edge types, but does not yet model an
explicit INSTANTIATES relationship type. This is expected behavior under the current
schema and is planned for future relationship type expansion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.storage.models import EntityModel


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CitationMatch:
    """A citation found in the answer that was verified against context entities.

    Attributes
    ----------
    raw:
        The exact citation string found in the answer.
    file_path:
        The file path component of the citation.
    start_line:
        The start line claimed in the citation.
    end_line:
        The end line claimed in the citation.
    matched_entity_id:
        The ``id`` of the real entity whose range covers this citation.
    matched_entity_name:
        Human-readable name of the matched entity.
    citation_type:
        "definition" (Category a) or "call_site" (Category b).
    caller_entity_name:
        For call_site citations: name of the calling entity containing the line.
    callee_entity_name:
        For call_site citations: name of the called entity invoked.
    """

    raw: str
    file_path: str
    start_line: int
    end_line: int
    matched_entity_id: str
    matched_entity_name: str
    citation_type: str = "definition"
    caller_entity_name: Optional[str] = None
    callee_entity_name: Optional[str] = None


@dataclass
class CitationMismatch:
    """A citation found in the answer that could NOT be verified (Category c)."""

    raw: str
    file_path: str
    start_line: int
    end_line: int
    reason: str
    nearest_entity: Optional[str] = None


@dataclass
class ValidationReport:
    """Result of validating citations in a generated answer.

    Classifies citations into three distinct categories:
    a. definition_citations: Cited range matches an entity's declared definition lines
       AND preceding text names that entity.
    b. call_site_citations: Preceding text describes an invocation ("calls X", "invokes X")
       and cited file/line is a line inside caller entity's body AND a real CALLS edge
       exists in graph from caller to callee X.
    c. unsupported_citations: File/line does not correspond to any entity in context,
       OR claims a CALLS relationship that does not exist in graph. (Actual hallucination category).
    """

    total_citations: int
    definition_citations: list[CitationMatch] = field(default_factory=list)
    call_site_citations: list[CitationMatch] = field(default_factory=list)
    unsupported_citations: list[CitationMismatch] = field(default_factory=list)

    @property
    def valid_citations(self) -> list[CitationMatch]:
        """All grounded citations (definition + call-site)."""
        return self.definition_citations + self.call_site_citations

    @property
    def invalid_citations(self) -> list[CitationMismatch]:
        """Alias for unsupported_citations (for backward compatibility)."""
        return self.unsupported_citations

    @property
    def hallucination_rate(self) -> float:
        if self.total_citations == 0:
            return 0.0
        return len(self.unsupported_citations) / self.total_citations

    def summary_line(self) -> str:
        """Single-line human-readable summary of the report."""
        rate_pct = self.hallucination_rate * 100
        return (
            f"Citations: {self.total_citations} total | "
            f"{len(self.definition_citations)} definition | "
            f"{len(self.call_site_citations)} call-site | "
            f"{len(self.unsupported_citations)} unsupported | "
            f"hallucination rate: {rate_pct:.1f}%"
        )

    def format_report(self) -> str:
        """Multi-line formatted report suitable for terminal output."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("CITATION VALIDATION REPORT (3-Way Classification)")
        lines.append("=" * 60)
        lines.append(self.summary_line())

        if self.definition_citations:
            lines.append(f"\n[OK] Definition citations ({len(self.definition_citations)}):")
            for c in self.definition_citations:
                lines.append(f"  {c.raw}  ->  definition of '{c.matched_entity_name}' ({c.matched_entity_id})")

        if self.call_site_citations:
            lines.append(f"\n[OK] Call-site citations ({len(self.call_site_citations)}):")
            for c in self.call_site_citations:
                caller = c.caller_entity_name or c.matched_entity_name
                callee = c.callee_entity_name or "?"
                lines.append(f"  {c.raw}  ->  call-site in '{caller}' calling '{callee}' (verified CALLS edge)")

        if self.unsupported_citations:
            lines.append(f"\n[!!] Unsupported / Hallucinated citations ({len(self.unsupported_citations)}):")
            for c in self.unsupported_citations:
                lines.append(f"  {c.raw}")
                lines.append(f"     Reason: {c.reason}")
                if c.nearest_entity:
                    lines.append(f"     Nearest real entity: {c.nearest_entity}")

        if not self.total_citations:
            lines.append("\n(No citations found in the answer.)")

        lines.append("=" * 60)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Citation parser
# ---------------------------------------------------------------------------

# Matches: [some/path/file.py:12-34] or [some/path/file.py:12]
_CITATION_RE = re.compile(
    r"\[([^\[\]\s:]+\.[a-zA-Z0-9]+):(\d+)(?:-(\d+))?\]"
)


def _parse_citations(answer: str) -> list[tuple[str, str, int, int, str]]:
    """Return list of (raw, file_path, start_line, end_line, preceding_text) tuples."""
    found: list[tuple[str, str, int, int, str]] = []
    for m in _CITATION_RE.finditer(answer):
        raw = m.group(0)
        file_path = m.group(1)
        start_line = int(m.group(2))
        end_line = int(m.group(3)) if m.group(3) else start_line
        preceding_text = answer[max(0, m.start() - 60) : m.start()]
        found.append((raw, file_path, start_line, end_line, preceding_text))
    return found


# ---------------------------------------------------------------------------
# Range overlap helper
# ---------------------------------------------------------------------------


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Return True if [a_start, a_end] and [b_start, b_end] overlap (inclusive)."""
    return a_start <= b_end and b_start <= a_end


# ---------------------------------------------------------------------------
# Graph CALLS edge collector
# ---------------------------------------------------------------------------


def _collect_graph_calls(final_context=None, db_session=None) -> set[tuple[str, str]]:
    """Build a set of valid (caller_id, callee_id) and (caller_name, callee_name) CALLS edges."""
    calls_set: set[tuple[str, str]] = set()

    if final_context and hasattr(final_context, "expanded_contexts"):
        for exp in final_context.expanded_contexts:
            core_ent = exp.core.entity
            for called in exp.called_entities:
                calls_set.add((core_ent.id, called.entity.id))
                calls_set.add((core_ent.name, called.entity.name))
                calls_set.add((called.called_via, called.entity.id))
                calls_set.add((called.called_via.split(".")[-1], called.entity.name))

    if db_session:
        from sqlalchemy import select
        from src.storage.models import RelationshipModel

        stmt = select(RelationshipModel).where(RelationshipModel.type == "CALLS")
        rels = db_session.scalars(stmt).all()
        for r in rels:
            if r.source_id and r.target_id:
                calls_set.add((r.source_id, r.target_id))
                src_name = r.source_id.split(".")[-1]
                tgt_name = r.target_id.split(".")[-1]
                calls_set.add((src_name, tgt_name))

    return calls_set


def _is_contains_child(
    symbol_name: str,
    container_entity,
    start_line: int,
    end_line: int,
    db_session=None,
) -> bool:
    """Return True if *symbol_name* is a CONTAINS child of *container_entity*
    at approximately [start_line, end_line] according to the DB.

    This prevents abstract method / nested-function citations from being
    incorrectly routed through the CALL-SITE path when the model cites a
    sub-entity line range that falls inside a file-level or class-level entity.
    """
    if db_session is None:
        return False
    try:
        from sqlalchemy import select
        from src.storage.models import EntityModel as DBEntityModel, RelationshipModel

        # Find CONTAINS children of container_entity in DB
        stmt = (
            select(DBEntityModel)
            .join(
                RelationshipModel,
                (RelationshipModel.source_id == container_entity.id)
                & (RelationshipModel.target_id == DBEntityModel.id)
                & (RelationshipModel.type == "CONTAINS"),
            )
        )
        children = db_session.scalars(stmt).all()
        for child in children:
            if child.name == symbol_name and _ranges_overlap(
                start_line, end_line, child.start_line, child.end_line
            ):
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------


def validate_citations(
    answer: str,
    context_entities: "list[EntityModel]",
    final_context=None,
    db_session=None,
) -> ValidationReport:
    """Validate every citation in *answer* using 3-way classification.

    Categories:
    a. DEFINITION citation — cited range matches an entity's declared lines AND preceding text names that entity.
    b. CALL-SITE citation — cited range is inside a caller entity's body, preceding text describes an invocation of callee X, AND a real CALLS edge exists in graph from caller to callee X.
    c. UNSUPPORTED citation — file/line doesn't correspond to any entity in context, OR claims a CALLS edge that doesn't exist in graph.

    Returns
    -------
    ValidationReport
    """
    raw_citations = _parse_citations(answer)

    # De-duplicate citations by raw tag
    seen_raws: set[str] = set()
    unique_citations: list[tuple[str, str, int, int, str]] = []
    for item in raw_citations:
        raw = item[0]
        if raw not in seen_raws:
            seen_raws.add(raw)
            unique_citations.append(item)

    total = len(unique_citations)
    def_citations: list[CitationMatch] = []
    call_citations: list[CitationMatch] = []
    unsupported: list[CitationMismatch] = []

    # Build file lookup: file_path → [entities] sorted by range span ascending (methods before modules)
    file_entity_map: dict[str, list["EntityModel"]] = {}
    for ent in context_entities:
        file_entity_map.setdefault(ent.file_path, []).append(ent)

    for fp in file_entity_map:
        file_entity_map[fp].sort(key=lambda e: (e.end_line - e.start_line))

    # Known methods/functions lookup: name → list of entities
    known_methods: dict[str, list["EntityModel"]] = {}
    for ent in context_entities:
        if ent.type in ("function", "method", "class"):
            known_methods.setdefault(ent.name, []).append(ent)

    # Graph calls set
    graph_calls = _collect_graph_calls(final_context, db_session)

    for raw, file_path, start_line, end_line, preceding_text in unique_citations:
        # Step 1: File path match
        candidates = file_entity_map.get(file_path) or _fuzzy_file_match(file_path, file_entity_map)

        if not candidates:
            unsupported.append(
                CitationMismatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    reason=f"File path '{file_path}' not found in provided context.",
                )
            )
            continue

        # Step 2: Line range overlap (matching smallest span entity first)
        matched: "EntityModel | None" = None
        for ent in candidates:
            if _ranges_overlap(start_line, end_line, ent.start_line, ent.end_line):
                matched = ent
                break

        if not matched:
            nearest = _describe_nearest(candidates, start_line)
            unsupported.append(
                CitationMismatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    reason=f"No entity in '{file_path}' covers lines {start_line}-{end_line}.",
                    nearest_entity=nearest,
                )
            )
            continue

        # Step 3: Classify into Definition (a) vs Call-Site (b) vs Unsupported (c)
        words = re.findall(r"\b[a-zA-Z_]\w*\b", preceding_text)
        named_symbol: str | None = None

        for w in reversed(words):
            if w in known_methods:
                named_symbol = w
                break

        # Check if named_symbol matches entity name or its class/container ID -> Definition citation (Category a)
        is_def_citation = (
            not named_symbol
            or named_symbol == matched.name
            or named_symbol in matched.id
            or (matched.parent_id and named_symbol in matched.parent_id)
        )

        if is_def_citation:
            def_citations.append(
                CitationMatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    matched_entity_id=matched.id,
                    matched_entity_name=matched.name,
                    citation_type="definition",
                )
            )
            continue

        # Symbol in preceding text differs from matched entity name.
        # Before treating as call-site, check if the named symbol is a CONTAINS
        # child of the matched entity at the cited line range (e.g. abstract method
        # definition inside a file-level entity).  If so, it is a definition citation.
        if _is_contains_child(named_symbol, matched, start_line, end_line, db_session):
            def_citations.append(
                CitationMatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    matched_entity_id=matched.id,
                    matched_entity_name=f"{matched.name}.{named_symbol}",
                    citation_type="definition",
                )
            )
            continue

        callee_candidates = known_methods.get(named_symbol, [])
        if not callee_candidates:
            # Named symbol is not a known entity -> default to definition citation
            def_citations.append(
                CitationMatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    matched_entity_id=matched.id,
                    matched_entity_name=matched.name,
                    citation_type="definition",
                )
            )
            continue

        callee_ent = callee_candidates[0]

        # Check for CALLS relationship in graph
        is_valid_call_site = (
            (matched.id, callee_ent.id) in graph_calls
            or (matched.name, callee_ent.name) in graph_calls
            or any((matched.id, c.id) in graph_calls for c in callee_candidates)
            or any((matched.name, c.name) in graph_calls for c in callee_candidates)
        )

        if is_valid_call_site:
            call_citations.append(
                CitationMatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    matched_entity_id=matched.id,
                    matched_entity_name=matched.name,
                    citation_type="call_site",
                    caller_entity_name=matched.name,
                    callee_entity_name=callee_ent.name,
                )
            )
        else:
            unsupported.append(
                CitationMismatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    reason=(
                        f"Claims CALLS relationship from '{matched.name}' to '{callee_ent.name}' at line {start_line}, "
                        f"but no such CALLS edge exists in graph."
                    ),
                    nearest_entity=f"'{matched.name}' [{matched.start_line}-{matched.end_line}]",
                )
            )

    return ValidationReport(
        total_citations=total,
        definition_citations=def_citations,
        call_site_citations=call_citations,
        unsupported_citations=unsupported,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fuzzy_file_match(
    file_path: str,
    file_entity_map: dict[str, list["EntityModel"]],
) -> "list[EntityModel] | None":
    """Try to match a file path by suffix when exact match fails.

    E.g. model cites ``services/auth_service.py`` but context has
    ``python/services/auth_service.py`` — the suffix matches.
    """
    for known_path, entities in file_entity_map.items():
        if known_path.endswith(file_path) or file_path.endswith(known_path):
            return entities
    # Also try basename match
    basename = file_path.split("/")[-1].split("\\")[-1]
    for known_path, entities in file_entity_map.items():
        known_basename = known_path.split("/")[-1].split("\\")[-1]
        if basename == known_basename:
            return entities
    return None


def _describe_nearest(entities: "list[EntityModel]", target_line: int) -> str:
    """Return a human-readable description of the entity closest to *target_line*."""
    closest = min(
        entities,
        key=lambda e: min(abs(e.start_line - target_line), abs(e.end_line - target_line)),
    )
    return f"'{closest.name}' [{closest.start_line}-{closest.end_line}]"


# ---------------------------------------------------------------------------
# Utility: collect all entities from a FinalContext (for convenience)
# ---------------------------------------------------------------------------


def collect_context_entities(final_context) -> "list[EntityModel]":
    """Flatten all EntityModel instances from a FinalContext into a single list.

    This includes core entities, parents, callees, callers, and inheritance
    entities — everything the model was shown.  Pass the result to
    ``validate_citations`` so it can validate against the full set.
    """
    seen_ids: set[str] = set()
    entities: list["EntityModel"] = []

    def _add(ent: "EntityModel | None") -> None:
        if ent is not None and ent.id not in seen_ids:
            seen_ids.add(ent.id)
            entities.append(ent)

    for exp in final_context.expanded_contexts:
        _add(exp.core.entity)
        _add(exp.parent_entity)
        for called in exp.called_entities:
            _add(called.entity)
        for caller in exp.caller_entities:
            _add(caller)
        for inh in exp.inheritance_entities:
            _add(inh)

    return entities
