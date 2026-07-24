"""Post-hoc citation validator for Gemini-generated answers.

After the model returns an answer, this module:
  1. Parses every ``[file_path:start_line-end_line]`` citation in the answer text.
  2. Verifies each one against the actual entities in the provided context.
  3. Returns a ``ValidationReport`` with valid citations, hallucinated citations,
     and a hallucination rate.

Public API
----------
validate_citations(answer, context_entities) -> ValidationReport
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
    """A citation found in the answer that was verified against a real entity.

    Attributes
    ----------
    raw:
        The exact citation string found in the answer, e.g.
        ``[python/services/auth_service.py:12-35]``.
    file_path:
        The file path component of the citation.
    start_line:
        The start line claimed in the citation.
    end_line:
        The end line claimed in the citation.
    matched_entity_id:
        The ``id`` of the real entity whose range overlaps this citation.
    matched_entity_name:
        Human-readable name of the matched entity.
    """

    raw: str
    file_path: str
    start_line: int
    end_line: int
    matched_entity_id: str
    matched_entity_name: str


@dataclass
class CitationMismatch:
    """A citation found in the answer that could NOT be verified.

    Attributes
    ----------
    raw:
        The exact citation string as it appeared in the answer.
    file_path:
        The file path component claimed by the model.
    start_line:
        The start line claimed by the model.
    end_line:
        The end line claimed by the model.
    reason:
        Human-readable explanation of why it failed validation.
    nearest_entity:
        If the file path matched but the line range didn't, the nearest real
        entity in that file is reported here (aids debugging).
    """

    raw: str
    file_path: str
    start_line: int
    end_line: int
    reason: str
    nearest_entity: Optional[str] = None  # "<name> [start-end]" or None


@dataclass
class ValidationReport:
    """Result of validating all citations in a generated answer.

    Attributes
    ----------
    total_citations:
        Total number of ``[file_path:N-N]`` citations found in the answer.
    valid_citations:
        Citations that matched a real entity in the provided context.
    invalid_citations:
        Citations that could not be matched — either the file path doesn't
        exist in the context, or the line range doesn't overlap any entity.
    hallucination_rate:
        ``len(invalid_citations) / total_citations``.
        ``0.0`` when ``total_citations == 0`` (no citations → no hallucinations).
    """

    total_citations: int
    valid_citations: list[CitationMatch] = field(default_factory=list)
    invalid_citations: list[CitationMismatch] = field(default_factory=list)

    @property
    def hallucination_rate(self) -> float:
        if self.total_citations == 0:
            return 0.0
        return len(self.invalid_citations) / self.total_citations

    def summary_line(self) -> str:
        """Single-line human-readable summary of the report."""
        rate_pct = self.hallucination_rate * 100
        return (
            f"Citations: {self.total_citations} total | "
            f"{len(self.valid_citations)} valid | "
            f"{len(self.invalid_citations)} invalid | "
            f"hallucination rate: {rate_pct:.1f}%"
        )

    def format_report(self) -> str:
        """Multi-line formatted report suitable for terminal output."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append("CITATION VALIDATION REPORT")
        lines.append("=" * 60)
        lines.append(self.summary_line())

        if self.valid_citations:
            lines.append(f"\n[OK] Valid citations ({len(self.valid_citations)}):")
            for c in self.valid_citations:
                lines.append(f"  {c.raw}  ->  matched '{c.matched_entity_name}' ({c.matched_entity_id})")

        if self.invalid_citations:
            lines.append(f"\n[!!] Invalid / hallucinated citations ({len(self.invalid_citations)}):")
            for c in self.invalid_citations:
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

# Matches: [some/path/file.py:12-34]
# Groups: (file_path, start_line, end_line)
_CITATION_RE = re.compile(
    r"\[([^\[\]\s:]+\.[a-zA-Z0-9]+):(\d+)-(\d+)\]"
)


def _parse_citations(answer: str) -> list[tuple[str, str, int, int]]:
    """Return list of (raw, file_path, start_line, end_line) tuples."""
    found: list[tuple[str, str, int, int]] = []
    for m in _CITATION_RE.finditer(answer):
        raw = m.group(0)
        file_path = m.group(1)
        start_line = int(m.group(2))
        end_line = int(m.group(3))
        found.append((raw, file_path, start_line, end_line))
    return found


# ---------------------------------------------------------------------------
# Range overlap helper
# ---------------------------------------------------------------------------


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Return True if [a_start, a_end] and [b_start, b_end] overlap (inclusive)."""
    return a_start <= b_end and b_start <= a_end


# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------


def validate_citations(
    answer: str,
    context_entities: "list[EntityModel]",
) -> ValidationReport:
    """Validate every ``[file_path:start-end]`` citation in *answer*.

    For each citation:
    1. Check whether *file_path* matches any entity's ``file_path`` in the context.
    2. If yes, check whether ``[start_line, end_line]`` overlaps the entity's range.
    3. The first entity satisfying both conditions is the match.

    Parameters
    ----------
    answer:
        The raw text returned by Gemini.
    context_entities:
        All ``EntityModel`` instances that were part of the context provided to
        the model (not just the core retrievals — include callee, caller,
        parent, and inheritance entities as well).

    Returns
    -------
    ValidationReport
    """
    raw_citations = _parse_citations(answer)

    # De-duplicate citations so we report each unique [path:N-N] once.
    seen_raws: set[str] = set()
    unique_citations: list[tuple[str, str, int, int]] = []
    for item in raw_citations:
        raw = item[0]
        if raw not in seen_raws:
            seen_raws.add(raw)
            unique_citations.append(item)

    total = len(unique_citations)
    valid: list[CitationMatch] = []
    invalid: list[CitationMismatch] = []

    # Build a lookup: file_path → [entities with that file_path]
    file_entity_map: dict[str, list["EntityModel"]] = {}
    for ent in context_entities:
        fp = ent.file_path
        file_entity_map.setdefault(fp, []).append(ent)

    for raw, file_path, start_line, end_line in unique_citations:
        # Step 1: file path match — try exact, then suffix match for robustness
        candidates = file_entity_map.get(file_path)

        if candidates is None:
            # Try suffix match (e.g. model has "services/auth.py" but entity has "python/services/auth.py")
            candidates = _fuzzy_file_match(file_path, file_entity_map)

        if not candidates:
            invalid.append(
                CitationMismatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    reason=(
                        f"File path '{file_path}' not found in provided context. "
                        f"Known paths: {sorted(file_entity_map.keys())[:5]}…"
                    ),
                )
            )
            continue

        # Step 2: line range overlap
        matched: "EntityModel | None" = None
        for ent in candidates:
            if _ranges_overlap(start_line, end_line, ent.start_line, ent.end_line):
                matched = ent
                break

        if matched:
            valid.append(
                CitationMatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    matched_entity_id=matched.id,
                    matched_entity_name=matched.name,
                )
            )
        else:
            # File path matched but no entity covers those lines
            nearest = _describe_nearest(candidates, start_line)
            invalid.append(
                CitationMismatch(
                    raw=raw,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    reason=(
                        f"No entity in '{file_path}' covers lines {start_line}-{end_line}. "
                        f"Entities in this file span different ranges."
                    ),
                    nearest_entity=nearest,
                )
            )

    return ValidationReport(
        total_citations=total,
        valid_citations=valid,
        invalid_citations=invalid,
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
