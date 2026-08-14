"""File Summary Agent.

Produces per-file summaries with inline citations using a model-first,
adaptive bin-packing strategy:

  1. ``pick_model()``   — select best available fast model (quota-aware, no call)
  2. ``build_batches()`` — bin-pack files into batches sized to model.max_context
       - small files (< 30% of budget) packed together
       - normal files get their own call
       - large files split into token-sized chunks
  3. ``execute_batch()`` — single LLM call returning JSON array {file_path, summary}
  4. ``retry_batch()``   — on failure, pick next model; re-split if its window is smaller
  5. ``merge_chunk_summaries()`` — join chunk results into one summary per file

Two prompt modes controlled by ``intent``:
  "repository_overview"  → SHORT prompts  (2-3 sentences per file)
  "repository_detailed"  → DETAILED prompts (5-8 sentences per file)

Public API
----------
build_batches(file_entity_map, model, intent) -> list[FileBatch]
execute_batch(batch, intent) -> dict[str, str]
retry_batch(batch, intent, tried_model_ids) -> dict[str, str]
merge_chunk_summaries(chunks) -> str
summarize_file(file_path, source, entities, repo_id) -> tuple[str, int, int]
    Legacy single-file entry point (backwards compat).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.storage.models import EntityModel
    from src.generation.llm_client import ModelSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt constants
# ---------------------------------------------------------------------------

SHORT_SYSTEM_PROMPT = """\
You are a code documentation assistant.
For each file provided, write a SHORT summary of 2-3 sentences describing:
1. The file's purpose (what problem it solves or what it owns)
2. The most important class or function, with an inline citation
3. Its main external dependencies

Citation format: [file_path:start_line-end_line]
Example: "The `authenticate` function [auth/service.py:45-89] validates JWT tokens."

Rules:
- Use entity line numbers exactly as provided — do NOT guess.
- 2-3 sentences maximum per file.
- Do NOT reproduce source code blocks.
- Do NOT use markdown headers — inline prose only.
- Cite only the single most important entity per file.

When multiple files are provided, return a JSON array:
[{"file_path": "<exact path as given>", "summary": "<2-3 sentence prose>"}]
One object per file in the same order given. No extra keys.
"""

DETAILED_SYSTEM_PROMPT = """\
You are a code documentation assistant.
For each file provided, write a DETAILED summary of 5-8 sentences describing:
1. The file's purpose and the problem it solves
2. Every significant class and function with inline citations
3. Internal logic and key algorithms
4. External dependencies and what they are used for
5. Notable patterns (e.g. singleton, factory, middleware, event-driven)

Citation format: [file_path:start_line-end_line]
Example: "The `authenticate` function [auth/service.py:45-89] validates JWT tokens."

Rules:
- Use entity line numbers exactly as provided — do NOT guess.
- 5-8 sentences per file.
- Do NOT reproduce source code blocks.
- Do NOT use markdown headers — inline prose only.
- Cite every named entity you mention.

When multiple files are provided, return a JSON array:
[{"file_path": "<exact path as given>", "summary": "<5-8 sentence prose>"}]
One object per file in the same order given. No extra keys.
"""

# ---------------------------------------------------------------------------
# Token budget constants
# ---------------------------------------------------------------------------
# Output headroom reserved per call depending on summary depth.
_SHORT_OUTPUT_RESERVE = 512      # short summaries are compact
_DETAILED_OUTPUT_RESERVE = 2048  # detailed summaries are longer

# A file is a "small" packing candidate if its tokens are below this fraction
# of the total budget.
_SMALL_FILE_FRACTION = 0.30


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BatchItem:
    """One file (or one chunk of a large file) within a FileBatch."""
    file_path: str
    chunk_index: int              # 0-based; always 0 for non-split files
    total_chunks: int             # 1 for non-split files
    text: str                     # source text for this chunk
    entities: list                # EntityModel list; populated only on chunk_index==0


@dataclass
class FileBatch:
    """A group of BatchItems to be sent in a single LLM call."""
    items: list[BatchItem]
    token_estimate: int
    target_model: "ModelSpec"     # may be replaced on retry


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 4 chars ≈ 1 token."""
    return max(1, len(text) // 4)


def _chunk_source(source: str, max_tokens: int) -> list[str]:
    """Split source into chunks of at most max_tokens, splitting on line boundaries."""
    if _estimate_tokens(source) <= max_tokens:
        return [source]

    max_chars = max_tokens * 4
    chunks: list[str] = []
    lines = source.splitlines(keepends=True)
    current: list[str] = []
    current_chars = 0

    for line in lines:
        if current_chars + len(line) > max_chars and current:
            chunks.append("".join(current))
            current = [line]
            current_chars = len(line)
        else:
            current.append(line)
            current_chars += len(line)

    if current:
        chunks.append("".join(current))

    return chunks


def _entity_lines(file_path: str, entities: list) -> str:
    """Format entity list for the prompt."""
    lines = [
        f"  - {e.type} `{e.name}` [{file_path}:{e.start_line}-{e.end_line}]"
        for e in entities
        if e.type in ("function", "class", "method", "interface")
    ]
    return "\n".join(lines) if lines else "  (none)"


def _build_batch_prompt(batch: "FileBatch", intent: str) -> str:
    """Build the user message for a multi-file (or single-file) batch call."""
    parts: list[str] = []
    for i, item in enumerate(batch.items, 1):
        header = f"--- File {i}: {item.file_path}"
        if item.total_chunks > 1:
            header += f" (chunk {item.chunk_index + 1} of {item.total_chunks})"
        parts.append(header)

        if item.chunk_index == 0 and item.entities:
            parts.append(f"Entities:\n{_entity_lines(item.file_path, item.entities)}")

        # Cap displayed source to avoid blowing context in edge cases
        preview = item.text[:10000] + ("..." if len(item.text) > 10000 else "")
        parts.append(f"Source:\n```\n{preview}\n```")

    prompt_body = "\n\n".join(parts)

    if len(batch.items) == 1:
        item = batch.items[0]
        instruction = f'Summarise the file `{item.file_path}`'
        if item.total_chunks > 1:
            instruction += (
                f" (this is chunk {item.chunk_index + 1} of {item.total_chunks}"
                f" — focus on what is new in this chunk)"
            )
        instruction += ". Return a JSON array with one object."
    else:
        instruction = (
            f"Summarise each of the {len(batch.items)} files below. "
            "Return a JSON array with one object per file in the same order."
        )

    return f"{prompt_body}\n\n{instruction}"


def _parse_batch_response(raw: str, batch: "FileBatch") -> dict[str, str]:
    """Parse LLM response into {file_path: summary}.

    Matches by file_path key (not array index) so partial responses are safe.
    Returns only successfully parsed entries — missing ones trigger retry.
    """
    # Strip markdown fences if present
    clean = raw.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean)
    clean = re.sub(r"\s*```$", "", clean)

    # Try to find a JSON array
    match = re.search(r"\[.*\]", clean, re.DOTALL)
    if not match:
        # Single object fallback
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(0))
                fp = obj.get("file_path", "")
                summary = obj.get("summary", "").strip()
                if fp and summary:
                    return {fp: summary}
            except json.JSONDecodeError:
                pass
        logger.warning("file_summary_agent: no JSON found in batch response")
        return {}

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        logger.warning("file_summary_agent: JSON parse error: %s", exc)
        return {}

    if not isinstance(data, list):
        data = [data]

    results: dict[str, str] = {}
    for obj in data:
        if not isinstance(obj, dict):
            continue
        fp = obj.get("file_path", "").strip()
        summary = obj.get("summary", "").strip()
        if fp and summary:
            results[fp] = summary

    return results


# ---------------------------------------------------------------------------
# Public batch API
# ---------------------------------------------------------------------------

def build_batches(
    file_entity_map: "dict[str, tuple[str, list]]",
    model: "ModelSpec",
    intent: str,
) -> "list[FileBatch]":
    """Bin-pack files into FileBatches sized for model.max_context.

    Strategy:
    - Large files (tokens > pack_budget): split into chunks, one batch per chunk
    - Small files (tokens < pack_budget * 0.3): pack greedily with others
    - Normal files: one batch each
    """
    output_reserve = (
        _DETAILED_OUTPUT_RESERVE
        if intent == "repository_detailed"
        else _SHORT_OUTPUT_RESERVE
    )
    pack_budget = max(1000, model.max_context - output_reserve)

    batches: list[FileBatch] = []
    # (file_path, token_count, source, entities)
    small_candidates: list[tuple[str, int, str, list]] = []

    for file_path, (source, entities) in file_entity_map.items():
        tokens = _estimate_tokens(source)

        if tokens > pack_budget:
            # Large file — split into chunks
            chunks = _chunk_source(source, pack_budget)
            total = len(chunks)
            for idx, chunk in enumerate(chunks):
                item = BatchItem(
                    file_path=file_path,
                    chunk_index=idx,
                    total_chunks=total,
                    text=chunk,
                    entities=entities if idx == 0 else [],
                )
                batches.append(FileBatch(
                    items=[item],
                    token_estimate=_estimate_tokens(chunk),
                    target_model=model,
                ))

        elif tokens < pack_budget * _SMALL_FILE_FRACTION:
            small_candidates.append((file_path, tokens, source, entities))

        else:
            # Normal file — own batch
            item = BatchItem(
                file_path=file_path,
                chunk_index=0,
                total_chunks=1,
                text=source,
                entities=entities,
            )
            batches.append(FileBatch(
                items=[item],
                token_estimate=tokens,
                target_model=model,
            ))

    # Bin-pack small files greedily
    current_items: list[BatchItem] = []
    current_tokens = 0

    for file_path, tokens, source, entities in small_candidates:
        item = BatchItem(
            file_path=file_path,
            chunk_index=0,
            total_chunks=1,
            text=source,
            entities=entities,
        )
        if current_tokens + tokens > pack_budget and current_items:
            batches.append(FileBatch(
                items=current_items,
                token_estimate=current_tokens,
                target_model=model,
            ))
            current_items = [item]
            current_tokens = tokens
        else:
            current_items.append(item)
            current_tokens += tokens

    if current_items:
        batches.append(FileBatch(
            items=current_items,
            token_estimate=current_tokens,
            target_model=model,
        ))

    return batches


def execute_batch(batch: "FileBatch", intent: str) -> dict[str, str]:
    """Call the target model with this batch. Returns {file_path: summary}.

    Raises LLMProviderError on failure so the caller can retry.
    """
    import src.generation.llm_client as _llm

    system_prompt = (
        DETAILED_SYSTEM_PROMPT
        if intent == "repository_detailed"
        else SHORT_SYSTEM_PROMPT
    )
    user_prompt = _build_batch_prompt(batch, intent)

    raw, _, _, _ = _llm.smart_complete(
        query="Summarise the provided file(s).",
        context=user_prompt,
        system_prompt=system_prompt,
        task_type="fast",
        force_model=batch.target_model.model_id,
    )

    return _parse_batch_response(raw, batch)


def retry_batch(
    batch: "FileBatch",
    intent: str,
    tried_model_ids: set[str],
) -> dict[str, str]:
    """Retry a failed batch with the next available model.

    If the next model's context window fits the batch, re-submit as-is.
    If the window is smaller, re-split the batch items to fit, then execute
    each sub-batch independently and merge results.

    Recurses through all available models until one succeeds or all are
    exhausted — each failed model is added to tried_model_ids.
    """
    import src.generation.llm_client as _llm

    next_model = _llm.pick_next_model(
        task_type="fast",
        exclude_model_ids=tried_model_ids,
        estimated_tokens=batch.token_estimate,
    )
    tried_model_ids = tried_model_ids | {next_model.model_id}  # immutable copy for recursion

    output_reserve = (
        _DETAILED_OUTPUT_RESERVE
        if intent == "repository_detailed"
        else _SHORT_OUTPUT_RESERVE
    )
    new_budget = max(1000, next_model.max_context - output_reserve)

    if new_budget >= batch.token_estimate:
        # Window fits — re-submit as-is with the new model
        batch.target_model = next_model
        try:
            return execute_batch(batch, intent)
        except _llm.LLMProviderError:
            # This model also failed — keep trying
            logger.warning(
                "file_summary_agent: model %s also failed — trying next",
                next_model.model_id,
            )
            return retry_batch(batch, intent, tried_model_ids)

    # Window too small — re-split each item to fit the new budget
    logger.info(
        "file_summary_agent: re-splitting batch (%d tok) for smaller model %s (%d ctx)",
        batch.token_estimate, next_model.model_id, next_model.max_context,
    )
    results: dict[str, str] = {}
    for item in batch.items:
        if _estimate_tokens(item.text) <= new_budget:
            sub_batch = FileBatch(
                items=[item],
                token_estimate=_estimate_tokens(item.text),
                target_model=next_model,
            )
            try:
                results.update(execute_batch(sub_batch, intent))
            except _llm.LLMProviderError:
                # Sub-batch also failed — recurse with same tried set
                logger.warning(
                    "file_summary_agent: sub-batch failed for %s — retrying with next model",
                    item.file_path,
                )
                try:
                    results.update(retry_batch(sub_batch, intent, tried_model_ids))
                except Exception as exc:
                    logger.warning(
                        "file_summary_agent: all models exhausted for %s: %s",
                        item.file_path, exc,
                    )
            except Exception as exc:
                logger.warning(
                    "file_summary_agent: sub-batch failed for %s: %s", item.file_path, exc
                )
        else:
            # Need to further chunk this item for the smaller window
            chunks = _chunk_source(item.text, new_budget)
            total = len(chunks)
            for idx, chunk in enumerate(chunks):
                sub_item = BatchItem(
                    file_path=item.file_path,
                    chunk_index=item.chunk_index * total + idx,
                    total_chunks=item.total_chunks * total,
                    text=chunk,
                    entities=item.entities if idx == 0 else [],
                )
                sub_batch = FileBatch(
                    items=[sub_item],
                    token_estimate=_estimate_tokens(chunk),
                    target_model=next_model,
                )
                try:
                    results.update(execute_batch(sub_batch, intent))
                except _llm.LLMProviderError:
                    logger.warning(
                        "file_summary_agent: chunk sub-batch failed for %s chunk %d — retrying",
                        item.file_path, idx,
                    )
                    try:
                        results.update(retry_batch(sub_batch, intent, tried_model_ids))
                    except Exception as exc:
                        logger.warning(
                            "file_summary_agent: all models exhausted for %s chunk %d: %s",
                            item.file_path, idx, exc,
                        )
                except Exception as exc:
                    logger.warning(
                        "file_summary_agent: chunk sub-batch failed for %s chunk %d: %s",
                        item.file_path, idx, exc,
                    )

    return results


def merge_chunk_summaries(file_path: str, chunks: list[str]) -> str:
    """Join ordered chunk summaries into one cohesive file summary.

    No extra LLM call — chunk summaries are already prose so we join them
    with a light transition marker.
    """
    if len(chunks) == 1:
        return chunks[0]
    parts = [chunks[0]]
    for chunk in chunks[1:]:
        parts.append(f"(continued) {chunk}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Legacy single-file entry point (backwards compat)
# ---------------------------------------------------------------------------

def summarize_file(
    file_path: str,
    source: str,
    entities: "list",
    repo_id: str,
    intent: str = "repository_overview",
) -> "tuple[str, int, int]":
    """Summarise one source file with inline citations.

    Delegates to the batch infrastructure. Returns (summary, prompt_tokens, completion_tokens).
    Falls back to a placeholder string and (0, 0) on any LLM failure.
    """
    try:
        import src.generation.llm_client as _llm

        model = _llm.pick_model(task_type="fast", estimated_tokens=_estimate_tokens(source))
        batches = build_batches({file_path: (source, entities)}, model, intent)

        all_results: dict[str, list[tuple[int, str]]] = {}  # file_path → [(chunk_index, summary)]
        for batch in batches:
            tried = {model.model_id}
            try:
                result = execute_batch(batch, intent)
            except _llm.LLMProviderError:
                result = retry_batch(batch, intent, tried)

            for item in batch.items:
                fp = item.file_path
                summary_text = result.get(fp, "")
                if summary_text:
                    all_results.setdefault(fp, []).append((item.chunk_index, summary_text))

        if file_path not in all_results:
            raise RuntimeError("No summary returned for file")

        sorted_chunks = [s for _, s in sorted(all_results[file_path])]
        final_summary = merge_chunk_summaries(file_path, sorted_chunks)
        # Token counts not tracked in legacy path — return 0, 0
        return final_summary, 0, 0

    except Exception as exc:
        logger.warning("file_summary_agent: failed for %s: %s", file_path, exc)
        entity_names = ", ".join(
            e.name for e in entities[:5]
            if e.type in ("function", "class", "method")
        )
        fallback = (
            f"`{file_path}` — summary unavailable. "
            + (f"Contains: {entity_names}." if entity_names else "")
        )
        return fallback, 0, 0
