"""Repository Overview Pipeline.

Implements hierarchical summarisation for ``repository_overview`` and
``repository_detailed`` intents:

  1. Build full file graph (all files, not just top-10)
  2. LTM check — return cached answer if available
  3. File Agent (async batched) — summarise every file with citations
     - Skip files with cached folder-level LTM entries
  4. Folder Agent — aggregate file summaries per folder
     - Skip folders with cached LTM entries
  5. Repo Agent (Gemini) — synthesise folder summaries into final answer
  6. Write LTM entries (folder-level + repo-level)

Public API
----------
run(query, intent, repo_id, repo, session_id, db, trace, stm) -> str
    Returns the final answer text (with inline citations).
    Populates stm.file_summaries, stm.folder_summaries, stm.visited_entity_ids.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Optional, TYPE_CHECKING

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from src.storage.models import EntityModel, RepositoryModel
    from src.pipeline.memory import ShortTermMemory
    from src.pipeline.pipeline_logger import PipelineTrace

logger = logging.getLogger(__name__)

# Max concurrent LLM calls across file batches + folder agents combined
_SEMAPHORE_SIZE = int(os.environ.get("OVERVIEW_CONCURRENCY", "8"))


def _folder_for_file(file_path: str) -> str:
    """Return the folder path for a file (first directory component, or '.')."""
    parts = file_path.replace("\\", "/").split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else "."


async def _summarize_files_async(
    file_entity_map: "dict[str, tuple[str, list[EntityModel]]]",
    repo_id: str,
    stm: "ShortTermMemory",
    trace: Optional["PipelineTrace"],
    intent: str,
    semaphore: asyncio.Semaphore,
) -> None:
    """Model-first, adaptive bin-packing file summarizer.

    Flow:
      1. pick_model()      — select best available fast model (quota-aware, no call)
      2. build_batches()   — bin-pack uncached files into token-sized batches
      3. asyncio.gather()  — execute all batches concurrently (semaphore-limited)
      4. retry_batch()     — on failure, pick next model; re-split if window is smaller
      5. merge chunks      — join chunk summaries into one per file_path
      6. populate stm      — file_summaries + visited_entity_ids

    Populates stm.file_summaries and stm.visited_entity_ids in-place.
    Skips files already present in stm.file_summaries (loaded from LTM).
    """
    import src.generation.llm_client as _llm
    from src.agents.file_summary_agent import (
        build_batches,
        execute_batch,
        retry_batch,
        merge_chunk_summaries,
        _estimate_tokens,
    )

    uncached_map = {
        fp: v
        for fp, v in file_entity_map.items()
        if fp not in stm.file_summaries
    }
    if not uncached_map:
        return

    # Estimate worst-case single-call token cost to filter out models with
    # tiny context windows (e.g. allam-2-7b at 4096 ctx / 6000 TPM).
    # Apply 1.5x overhead factor for system prompt + entity lines + formatting.
    max_single_file_tokens = max(
        (_estimate_tokens(source) for source, _ in uncached_map.values()),
        default=0,
    )
    context_estimate = int(max_single_file_tokens * 1.5) + 400

    # Step 1 — pick model (quota-aware, no LLM call)
    # Hard-exclude openrouter for batch summarization: free tier throttles
    # concurrent calls badly (80s+ per batch). Use NVIDIA NIM / Cloudflare / Groq instead.
    _BATCH_SKIP = {"openrouter"}

    try:
        model = _llm.pick_model(
            task_type="fast",
            estimated_tokens=context_estimate,
            skip_providers=_BATCH_SKIP,
        )
        if model.model_id == "groq/allam-2-7b":
            model = _llm.pick_next_model(
                task_type="fast",
                exclude_model_ids={"groq/allam-2-7b"},
                estimated_tokens=context_estimate,
                skip_providers=_BATCH_SKIP,
            )
    except _llm.LLMQuotaExhaustedError:
        logger.warning("Overview: no fast models available for file summarization — skipping")
        return

    # Step 2 — bin-pack into batches sized for the selected model's context window
    batches = build_batches(uncached_map, model, intent)
    logger.info(
        "Overview: %d files → %d batches (model=%s, intent=%s)",
        len(uncached_map), len(batches), model.model_id, intent,
    )

    # Accumulate chunk results: file_path → [(chunk_index, total_chunks, summary)]
    chunk_results: dict[str, list[tuple[int, int, str]]] = {}
    chunk_lock = asyncio.Lock()

    async def _run_batch(batch) -> None:
        async with semaphore:
            t0 = time.monotonic()
            loop = asyncio.get_running_loop()
            tried = {batch.target_model.model_id}
            try:
                result = await loop.run_in_executor(
                    None, lambda b=batch: execute_batch(b, intent)
                )
            except _llm.LLMProviderError as exc:
                logger.warning(
                    "Overview: batch failed (%s) — retrying with next model: %s",
                    batch.target_model.model_id, exc,
                )
                try:
                    result = await loop.run_in_executor(
                        None, lambda b=batch: retry_batch(b, intent, tried)
                    )
                except Exception as retry_exc:
                    logger.warning("Overview: batch retry failed: %s", retry_exc)
                    result = {}
            except Exception as exc:
                logger.warning("Overview: batch unexpected error: %s", exc)
                result = {}

            elapsed = (time.monotonic() - t0) * 1000

            # Store chunk results
            async with chunk_lock:
                for item in batch.items:
                    summary_text = result.get(item.file_path, "")
                    if summary_text:
                        chunk_results.setdefault(item.file_path, []).append(
                            (item.chunk_index, item.total_chunks, summary_text)
                        )

                if trace:
                    for item in batch.items:
                        if item.file_path in result:
                            trace.step_file_agent(
                                file_path=item.file_path,
                                tokens=_estimate_tokens(item.text),
                                elapsed_ms=elapsed,
                                from_cache=False,
                                summary=result[item.file_path],
                            )

    # Step 3 — execute all batches concurrently
    await asyncio.gather(*[_run_batch(b) for b in batches])

    # Step 4 — merge chunk results into final per-file summaries
    for fp, chunks in chunk_results.items():
        sorted_chunks = [s for _, _, s in sorted(chunks, key=lambda x: x[0])]
        stm.file_summaries[fp] = merge_chunk_summaries(fp, sorted_chunks)
        # Mark entities as visited
        _, entities = uncached_map.get(fp, ("", []))
        for ent in entities:
            stm.visited_entity_ids.add(ent.id)


def _build_file_entity_map(
    repo_id: str,
    db: Session,
) -> "dict[str, tuple[str, list[EntityModel]]]":
    """Fetch all module source + child entities for every file in the repo.

    Returns dict mapping file_path → (source_text, child_entities).
    """
    from src.storage.models import EntityModel

    # All module (file-level) entities — carry the source text
    modules = (
        db.query(EntityModel)
        .filter_by(repo_id=repo_id, type="module")
        .all()
    )

    # All child entities (functions, classes, methods) for all files
    children = (
        db.query(EntityModel)
        .filter(
            EntityModel.repo_id == repo_id,
            EntityModel.type.in_(["function", "class", "method", "interface"]),
        )
        .all()
    )

    # Group children by file_path
    children_by_file: dict[str, list[EntityModel]] = defaultdict(list)
    for ent in children:
        children_by_file[ent.file_path].append(ent)

    result: dict[str, tuple[str, list[EntityModel]]] = {}
    for mod in modules:
        result[mod.file_path] = (
            mod.source or "",
            sorted(children_by_file.get(mod.file_path, []), key=lambda e: e.start_line),
        )

    return result


async def run(
    query: str,
    intent: str,
    repo_id: str,
    repo: "RepositoryModel",
    session_id: Optional[str],
    db: Session,
    trace: Optional["PipelineTrace"],
    stm: "ShortTermMemory",
) -> str:
    """Run the full hierarchical overview pipeline and return the final answer.

    Parameters
    ----------
    query:
        Original user query.
    intent:
        "repository_overview" or "repository_detailed".
    repo_id:
        Target repository ID.
    repo:
        RepositoryModel row (for LTM stale detection).
    session_id:
        Optional UUID for LTM reads/writes.
    db:
        Active SQLAlchemy session.
    trace:
        PipelineTrace for structured logging (may be None).
    stm:
        ShortTermMemory — populated in-place with file/folder summaries.

    Returns
    -------
    str
        Final answer text with inline citations.
    """
    from src.memory.ltm.session_knowledge import (
        lookup_by_feature,
        write_feature,
    )

    ltm_feature = "repo_overview" if intent == "repository_overview" else "repo_detailed"

    # ── Step 1: LTM full-repo cache check ────────────────────────────────────
    if session_id:
        cached = lookup_by_feature(repo_id, session_id, ltm_feature, repo, db)
        if cached:
            stm.overview_from_cache = True
            # Restore visited entity IDs from the LTM entry so that
            # _build_overview_context can do a targeted DB query instead of
            # loading all entities for the repo (avoids citation validation
            # marking every citation as unsupported on cache-hit responses).
            if cached.source_entity_ids:
                stm.visited_entity_ids.update(cached.source_entity_ids)
            if trace:
                trace.step_ltm_read(outcome="hit", feature_name=ltm_feature, step=3,
                                    ltm_summary=cached.summary)
            logger.info("Overview: LTM full-repo hit for %s", ltm_feature)
            return cached.summary
        if trace:
            trace.step_ltm_read(outcome="miss", feature_name=ltm_feature, step=3)

    # ── Step 2: Fetch all files from DB ──────────────────────────────────────
    file_entity_map = _build_file_entity_map(repo_id, db)

    if not file_entity_map:
        return "No files found in the repository index."

    # ── Step 3: Load cached folder summaries from LTM ────────────────────────
    folders: dict[str, list[str]] = defaultdict(list)  # folder → [file_paths]
    for fp in file_entity_map:
        folders[_folder_for_file(fp)].append(fp)

    if session_id:
        for folder in list(folders.keys()):
            folder_feature = f"folder:{folder}"
            cached_folder = lookup_by_feature(
                repo_id, session_id, folder_feature, repo, db
            )
            if cached_folder:
                stm.folder_summaries[folder] = cached_folder.summary
                # Mark all file paths in this folder as covered
                for fp in folders[folder]:
                    stm.file_summaries[fp] = f"(cached via folder summary)"
                if trace:
                    trace.step_folder_agent(
                        folder=folder,
                        file_count=len(folders[folder]),
                        elapsed_ms=0,
                        from_cache=True,
                        summary=cached_folder.summary,
                    )
                    trace.step_ltm_read(outcome="hit", feature_name=folder_feature, step=3,
                                        ltm_summary=cached_folder.summary)

    # ── Step 4: File Agents (model-first bin-packed, fully concurrent) ──────
    semaphore = asyncio.Semaphore(_SEMAPHORE_SIZE)
    await _summarize_files_async(file_entity_map, repo_id, stm, trace, intent, semaphore)

    # ── Step 5: Folder Agents (parallel, same semaphore) ─────────────────────
    from src.agents.folder_summary_agent import summarize_folder

    async def _summarize_one_folder(folder: str, file_paths: list) -> None:
        if folder in stm.folder_summaries:
            return  # already loaded from LTM

        folder_file_summaries = {
            fp: stm.file_summaries[fp]
            for fp in file_paths
            if fp in stm.file_summaries
        }
        if not folder_file_summaries:
            return

        async with semaphore:
            t0 = time.monotonic()
            loop = asyncio.get_running_loop()
            folder_result = await loop.run_in_executor(
                None,
                lambda f=folder, ffs=folder_file_summaries: summarize_folder(f, ffs, intent),
            )
        elapsed = (time.monotonic() - t0) * 1000
        folder_summary, folder_prompt_tokens, _ = folder_result
        stm.folder_summaries[folder] = folder_summary

        if trace:
            trace.step_folder_agent(
                folder=folder,
                file_count=len(folder_file_summaries),
                elapsed_ms=elapsed,
                from_cache=False,
                summary=folder_summary,
                input_tokens=folder_prompt_tokens,
            )

        # Write folder LTM entry
        if session_id:
            entity_ids = [
                ent.id
                for fp in file_paths
                for ent in file_entity_map.get(fp, (None, []))[1]
            ]
            write_feature(
                repo_id=repo_id,
                session_id=session_id,
                feature_name=f"folder:{folder}",
                summary=folder_summary,
                repo=repo,
                db=db,
                source_entity_ids=entity_ids[:50],
            )
            if trace:
                trace.step_ltm_write(
                    feature_name=f"folder:{folder}",
                    confidence="high",
                    exploration_status="complete",
                    step=5,
                    summary=folder_summary,
                )

    await asyncio.gather(*[
        _summarize_one_folder(folder, file_paths)
        for folder, file_paths in folders.items()
    ])

    if trace:
        trace.step_overview_assembled(
            file_count=len(stm.file_summaries),
            folder_count=len(stm.folder_summaries),
            visited_entities=len(stm.visited_entity_ids),
        )

    # ── Step 6: Repo Summary Agent ────────────────────────────────────────────
    from src.agents.repo_summary_agent import summarize_repo

    try:
        answer, provider = summarize_repo(
            repo_name=repo.name,
            folder_summaries=stm.folder_summaries,
            intent=intent,
            query=query,
            total_files=len(file_entity_map),
            file_paths=list(file_entity_map.keys()),
            trace=trace,
        )
        stm.answer_status = "answered"
    except Exception as exc:
        logger.error("Overview: Repo Summary Agent failed: %s", exc)
        # Best-effort fallback: concatenate folder summaries
        answer = (
            f"# {repo.name} — Repository Overview\n\n"
            + "\n\n".join(
                f"**{folder}**\n{summary}"
                for folder, summary in sorted(stm.folder_summaries.items())
            )
        )
        provider = "fallback"
        stm.answer_status = "answered"

    # ── Step 7: Write repo-level LTM entry ───────────────────────────────────
    if session_id:
        write_feature(
            repo_id=repo_id,
            session_id=session_id,
            feature_name=ltm_feature,
            summary=answer,
            repo=repo,
            db=db,
            # Persist all visited entity IDs so a future cache hit can restore
            # them into stm.visited_entity_ids for targeted citation validation.
            source_entity_ids=list(stm.visited_entity_ids)[:500],
        )
        if trace:
            trace.step_ltm_write(
                feature_name=ltm_feature,
                confidence="high",
                exploration_status="complete",
                step=7,
                summary=answer,
            )

    return answer
