"""Citation Correction Agent.

Fixes unsupported (hallucinated) citations in LLM-generated answers using a
two-pass approach:

  Pass 1 — Deterministic: for each unsupported citation with a known
    ``nearest_entity_id``, look up the real entity's file_path and line range,
    then do a direct string replacement in the answer text.

  Pass 2 — LLM (only when Pass 1 leaves uncorrected citations): for citations
    where no nearest entity was found (file path not in context at all), send
    the affected paragraph + valid entity list to a fast LLM for correction.

After both passes the answer is re-validated. If correction made things worse
(higher hallucination rate), the original answer is returned as a safety guard.

Public API
----------
run(answer, report, context_entities, final_context, db_session) -> CorrectionResult
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.generation.citation_validator import ValidationReport
    from src.storage.models import EntityModel

logger = logging.getLogger(__name__)

# Set to "false" to disable the correction agent entirely
_ENABLED = os.environ.get("CITATION_CORRECTION_ENABLED", "true").lower() not in ("false", "0", "no")

_CORRECTION_SYSTEM = """\
You are a citation correction assistant for a code intelligence system.
Citations use the format [file_path:start_line-end_line].

You receive a paragraph with INVALID citations marked with ⚠️, plus a list of
VALID entities you may use instead.

Your task: rewrite ONLY the marked citations. Either:
- Replace with the correct [file_path:start-end] from the valid entities list.
- Remove the citation entirely if no valid entity matches the claim.

Rules:
- Do NOT change any other text in the paragraph.
- Do NOT invent new citations or new file paths.
- Use ONLY file paths and line numbers from the valid entities list.
- If unsure, REMOVE the citation rather than guessing.
- Return ONLY the rewritten paragraph — no explanation, no extra text.
"""


@dataclass
class CorrectionResult:
    """Result of a citation correction pass."""

    corrected_answer: str
    original_unsupported: int
    remaining_unsupported: int
    corrections_made: int
    method: str  # "none" | "deterministic" | "llm" | "mixed"
    report: "ValidationReport"  # re-validated report on corrected answer


def _parse_nearest_entity_tag(nearest_entity: str) -> Optional[str]:
    """Extract the citation tag from a nearest_entity description string.

    Input:  "'authenticate' [auth/service.py:45-89]"
    Output: "[auth/service.py:45-89]"
    """
    match = re.search(r"\[([^\[\]]+:\d+-\d+)\]", nearest_entity)
    return f"[{match.group(1)}]" if match else None


def _deterministic_pass(
    answer: str,
    unsupported: list,
    db_session,
) -> tuple[str, int]:
    """Replace bad citations where nearest_entity_id is known.

    Returns (corrected_answer, count_of_replacements_made).
    """
    corrected = answer
    replacements = 0

    for mismatch in unsupported:
        if not mismatch.nearest_entity_id:
            continue

        # Look up the real entity to get canonical file_path and line range
        correct_tag: Optional[str] = None

        if db_session:
            try:
                from src.storage.models import EntityModel
                ent = db_session.query(EntityModel).filter_by(id=mismatch.nearest_entity_id).first()
                if ent:
                    correct_tag = f"[{ent.file_path}:{ent.start_line}-{ent.end_line}]"
            except Exception as exc:
                logger.debug("Correction: DB lookup failed for %s: %s",
                             mismatch.nearest_entity_id, exc)

        # Fallback: parse from nearest_entity string
        if not correct_tag and mismatch.nearest_entity:
            correct_tag = _parse_nearest_entity_tag(mismatch.nearest_entity)

        if correct_tag and mismatch.raw in corrected:
            # Replace ALL occurrences of the bad tag, not just the first
            corrected = corrected.replace(mismatch.raw, correct_tag)
            replacements += 1
            logger.debug(
                "Correction (deterministic): %s → %s",
                mismatch.raw, correct_tag,
            )

    return corrected, replacements


def _get_paragraph_containing(answer: str, citation_raw: str) -> str:
    """Extract the paragraph (or ±3 lines) containing citation_raw."""
    pos = answer.find(citation_raw)
    if pos == -1:
        return answer[:500]
    # Find paragraph boundaries (double newline)
    start = answer.rfind("\n\n", 0, pos)
    end = answer.find("\n\n", pos)
    start = start + 2 if start != -1 else 0
    end = end if end != -1 else len(answer)
    return answer[start:end].strip()


def _llm_correction_pass(
    answer: str,
    remaining_mismatches: list,
    context_entities: "list[EntityModel]",
) -> tuple[str, int]:
    """Use LLM to correct citations with no known nearest_entity_id.

    Returns (corrected_answer, count_of_replacements_made).
    """
    if not remaining_mismatches:
        return answer, 0

    import src.generation.llm_client as _llm

    # Build a lookup of available entities for quick reference
    entity_lines = "\n".join(
        f"  - {e.name} [{e.file_path}:{e.start_line}-{e.end_line}]"
        for e in context_entities[:40]  # cap at 40 to stay within context
        if e.type in ("function", "class", "method", "module", "interface")
    )

    corrected = answer
    replacements = 0

    for mismatch in remaining_mismatches:
        paragraph = _get_paragraph_containing(corrected, mismatch.raw)
        if not paragraph:
            continue

        # Mark the bad citation in the paragraph
        marked = paragraph.replace(mismatch.raw, f"⚠️{mismatch.raw}", 1)

        context = (
            f"Paragraph:\n{marked}\n\n"
            f"Invalid citation: {mismatch.raw}\n"
            f"Reason: {mismatch.reason}\n\n"
            f"Valid entities you may use:\n{entity_lines}"
        )

        try:
            # Primary: NVIDIA NIM — 40 RPM, no RPD cap.
            # Deliberately avoids Groq here to prevent RPM contention with
            # the QueryPlanner (which pins groq/llama-3.1-8b-instant).
            try:
                corrected_paragraph, _, _, _ = _llm.smart_complete(
                    query="Fix the invalid citation in this paragraph.",
                    context=context,
                    system_prompt=_CORRECTION_SYSTEM,
                    task_type="fast",
                    force_provider="nvidia_nim",
                )
            except _llm.LLMProviderError:
                # NIM unavailable — fall back to Gemini Flash-Lite then Groq
                corrected_paragraph, _, _, _ = _llm.smart_complete(
                    query="Fix the invalid citation in this paragraph.",
                    context=context,
                    system_prompt=_CORRECTION_SYSTEM,
                    task_type="fast",
                    skip_providers={"openrouter", "cohere", "cloudflare", "cerebras"},
                )
            corrected_paragraph = corrected_paragraph.strip()

            # Replace the original paragraph in the answer
            if paragraph in corrected:
                corrected = corrected.replace(paragraph, corrected_paragraph, 1)
                replacements += 1
                logger.debug(
                    "Correction (LLM): fixed citation %s in paragraph",
                    mismatch.raw,
                )
        except Exception as exc:
            logger.warning("Correction LLM pass failed for %s: %s", mismatch.raw, exc)

    return corrected, replacements


def run(
    answer: str,
    report: "ValidationReport",
    context_entities: "list[EntityModel]",
    final_context,
    db_session,
) -> CorrectionResult:
    """Correct unsupported citations in the answer.

    Parameters
    ----------
    answer:
        The raw answer text from the Answer Agent.
    report:
        ValidationReport from validate_citations() — contains unsupported list.
    context_entities:
        All EntityModel instances the LLM was shown (for LLM pass entity list).
    final_context:
        FinalContext — passed to re-validate after correction.
    db_session:
        Active SQLAlchemy session for entity lookups.

    Returns
    -------
    CorrectionResult
        Contains the corrected answer and a re-validated ValidationReport.
    """
    from src.generation.citation_validator import validate_citations

    if not _ENABLED:
        return CorrectionResult(
            corrected_answer=answer,
            original_unsupported=len(report.unsupported_citations),
            remaining_unsupported=len(report.unsupported_citations),
            corrections_made=0,
            method="none",
            report=report,
        )

    original_unsupported = len(report.unsupported_citations)

    if original_unsupported == 0:
        return CorrectionResult(
            corrected_answer=answer,
            original_unsupported=0,
            remaining_unsupported=0,
            corrections_made=0,
            method="none",
            report=report,
        )

    # ── Pass 1: Deterministic ────────────────────────────────────────────────
    corrected, det_replacements = _deterministic_pass(
        answer, report.unsupported_citations, db_session
    )

    # ── Pass 2: LLM for remaining no-nearest-entity citations ─────────────────
    remaining_mismatches = [
        m for m in report.unsupported_citations
        if not m.nearest_entity_id and m.raw in corrected
    ]
    llm_replacements = 0
    method = "none"

    if det_replacements > 0 or remaining_mismatches:
        if remaining_mismatches:
            corrected, llm_replacements = _llm_correction_pass(
                corrected, remaining_mismatches, context_entities
            )
            method = "mixed" if det_replacements > 0 else "llm"
        else:
            method = "deterministic"

    total_corrections = det_replacements + llm_replacements

    if total_corrections == 0:
        # Nothing changed — return original without re-validation cost
        return CorrectionResult(
            corrected_answer=answer,
            original_unsupported=original_unsupported,
            remaining_unsupported=original_unsupported,
            corrections_made=0,
            method="none",
            report=report,
        )

    # ── Re-validate corrected answer ─────────────────────────────────────────
    # Augment context_entities with any DB entities that were looked up during
    # the deterministic pass — they're now cited in the corrected answer and
    # must be present so re-validation can match them.
    augmented_entities = list(context_entities)
    if db_session:
        seen_ids = {e.id for e in augmented_entities}
        for mismatch in report.unsupported_citations:
            if mismatch.nearest_entity_id and mismatch.nearest_entity_id not in seen_ids:
                try:
                    from src.storage.models import EntityModel
                    ent = db_session.query(EntityModel).filter_by(id=mismatch.nearest_entity_id).first()
                    if ent:
                        augmented_entities.append(ent)
                        seen_ids.add(ent.id)
                except Exception:
                    pass

    new_report = validate_citations(
        answer=corrected,
        context_entities=augmented_entities,
        final_context=final_context,
        db_session=db_session,
    )

    # Safety guard: if correction made hallucination rate WORSE, revert
    if new_report.hallucination_rate > report.hallucination_rate:
        logger.warning(
            "Correction made hallucination rate worse (%.1f%% → %.1f%%) — reverting",
            report.hallucination_rate * 100,
            new_report.hallucination_rate * 100,
        )
        return CorrectionResult(
            corrected_answer=answer,
            original_unsupported=original_unsupported,
            remaining_unsupported=original_unsupported,
            corrections_made=0,
            method="reverted",
            report=report,
        )

    logger.info(
        "Citation correction: %d fixed, %d remaining (was %d) via %s",
        total_corrections,
        len(new_report.unsupported_citations),
        original_unsupported,
        method,
    )

    return CorrectionResult(
        corrected_answer=corrected,
        original_unsupported=original_unsupported,
        remaining_unsupported=len(new_report.unsupported_citations),
        corrections_made=total_corrections,
        method=method,
        report=new_report,
    )
