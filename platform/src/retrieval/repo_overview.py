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

# Max files to process per async batch (controls concurrency vs. rate-limit risk)
_BATCH_SIZE = int(os.environ.get("OVERVIEW_BATCH_SIZE", "5"))


def _folder_for_file(file_path: str) -> str:
    """Return the folder path for a file (first directory component, or '.')."""
    parts = file_path.replace("\\", "/").split("/")
    return "/".join(parts[:-1]) if len(parts) > 1 else "."


async def _summarize_files_async(
    file_entity_map: "dict[str, tuple[str, list[EntityModel]]]",
    repo_id: str,
    stm: "ShortTermMemory",
    trace: Optional["PipelineTrace"],
) -> None:
    """Run File Agents concurrently in batches.

    Populates stm.file_summaries and stm.visited_entity_ids in-place.
    Skips files already present in stm.file_summaries (loaded from LTM).
    """
    from src.agents.file_summary_agent import summarize_file

    files_to_process = [
        (fp, source, entities)
        for fp, (source, entities) in file_entity_map.items()
        if fp not in stm.file_summaries
    ]

    if not files_to_process:
        return

    async def _process_one(file_path: str, source: str, entities: "list[EntityModel]") -> None:
        t0 = time.monotonic()
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: summarize_file(file_path, source, entities, repo_id),
        )
        elapsed = (time.monotonic() - t0) * 1000
        summary, prompt_tokens, completion_tokens = result
        stm.file_summaries[file_path] = summary
        # Track entity IDs as visited
        for ent in entities:
            stm.visited_entity_ids.add(ent.id)
        if trace:
            trace.step_file_agent(
                file_path=file_path,
                tokens=prompt_tokens,
                elapsed_ms=elapsed,
                from_cache=False,
                summary=summary,
            )

    # Process in batches to limit concurrent API calls
    for i in range(0, len(files_to_process), _BATCH_SIZE):
        batch = files_to_process[i : i + _BATCH_SIZE]
        await asyncio.gather(*[_process_one(fp, src, ents) for fp, src, ents in batch])


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

    # ── Step 4: File Agents (async batched) for uncached files ───────────────
    await _summarize_files_async(file_entity_map, repo_id, stm, trace)

    # ── Step 5: Folder Agents for uncached folders ───────────────────────────
    from src.agents.folder_summary_agent import summarize_folder

    for folder, file_paths in folders.items():
        if folder in stm.folder_summaries:
            continue  # already loaded from LTM

        folder_file_summaries = {
            fp: stm.file_summaries[fp]
            for fp in file_paths
            if fp in stm.file_summaries
        }
        if not folder_file_summaries:
            continue

        t0 = time.monotonic()
        folder_result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda f=folder, ffs=folder_file_summaries: summarize_folder(f, ffs),
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
