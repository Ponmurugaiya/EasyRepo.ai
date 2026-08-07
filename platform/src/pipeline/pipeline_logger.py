"""Structured pipeline logger.

Writes a detailed trace of every pipeline run to the application log.
Controlled by the environment variable:

  PIPELINE_LOG_LEVEL=DEBUG   — log everything (prompts, context, raw LLM output,
                                STM snapshots, full conversation history)
  PIPELINE_LOG_LEVEL=INFO    — log all flow steps + model selection (default)
  PIPELINE_LOG_LEVEL=OFF     — disable pipeline tracing entirely

File storage is controlled by main.py via LOG_TO_FILE / LOG_DIR / LOG_MAX_BYTES /
LOG_BACKUP_COUNT env vars — see main.py for details.

Step reference
--------------
  [0-HISTORY]     Conversation history loaded (or skipped)
  [1-PLAN]        Query Planner output
  [2-RETRIEVE]    Initial retrieval results
  [3-EXPAND]      Graph expansion + context build
  [4-LTM READ]    LTM cache read (hit / miss / stale / skipped)
  [4-LTM WRITE]   LTM cache write after answered response
  [5-DISPATCH]    LLM dispatched (model, provider, context tokens) — before call
  [5-LLM PROMPT]  Full system prompt + context sent to LLM (DEBUG only)
  [5-LLM RESP]    LLM response (status, chars, preview)
  [5-LLM RAW]     Full raw LLM output (DEBUG only)
  [RE-RETRIEVE]   Targeted re-retrieval after insufficient/rewrite
  [6-CITE]        Citation validation results
  [7-TURN SAVE]   Conversation turn persisted to DB
  [7-SUMMARISE]   Rolling conversation summary triggered
  PIPELINE DONE   Final status, provider, citation count, timing
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.pipeline.memory import ShortTermMemory

logger = logging.getLogger(__name__)

# Max chars to include in previews at INFO level
_INFO_PREVIEW = 300
# Max chars at DEBUG level (full content)
_DEBUG_PREVIEW = 8000


def _pipeline_level() -> Optional[int]:
    """Read pipeline log level from env at call time (supports hot-reload)."""
    val = os.environ.get("PIPELINE_LOG_LEVEL", "INFO").upper()
    return {"DEBUG": logging.DEBUG, "INFO": logging.INFO, "OFF": None}.get(val, logging.INFO)


def _log(level: int, msg: str, *args) -> None:
    pl = _pipeline_level()
    if pl is None:
        return
    if level >= pl:
        logger.log(level, msg, *args)


@dataclass
class PipelineTrace:
    """Collects and emits structured logs for one pipeline request."""

    query: str
    repo_id: str
    _start: float = field(default_factory=time.monotonic, init=False)

    def _elapsed(self) -> str:
        return f"{(time.monotonic() - self._start)*1000:.0f}ms"

    # ── STM snapshots ─────────────────────────────────────────────────────────

    def step_stm(self, stage: str, stm: "ShortTermMemory") -> None:
        """Log a snapshot of STM state at a named pipeline stage.

        At INFO level: key scalar fields only (goal, intent, strategy, status,
        iteration count, entity/chunk counts).
        At DEBUG level: full STM dump including visited entity IDs and
        intermediate summaries.
        """
        _log(logging.INFO,
             "PIPELINE [STM@%s]  intent=%s  strategy=%s  search_query=%r  "
             "visited=%d  chunks=%d  iterations=%d  status=%s  answer_chars=%s",
             stage,
             stm.intent,
             stm.retrieval_strategy,
             (stm.search_query or "")[:60],
             len(stm.visited_entity_ids),
             len(stm.retrieved_chunks),
             stm.iteration_count,
             stm.answer_status,
             len(stm.answer_text or ""),
        )
        if _pipeline_level() is not None and _pipeline_level() <= logging.DEBUG:
            import json as _json
            try:
                summaries_preview = [s[:200] for s in stm.intermediate_summaries]
                visited_preview = sorted(stm.visited_entity_ids)[:20]
                detail = {
                    "goal": stm.goal[:200],
                    "intent": stm.intent,
                    "retrieval_strategy": stm.retrieval_strategy,
                    "search_query": stm.search_query,
                    "session_id": stm.session_id,
                    "conversation_id": stm.conversation_id,
                    "visited_entity_ids_sample": visited_preview,
                    "visited_count": len(stm.visited_entity_ids),
                    "pending_count": len(stm.pending_entity_ids),
                    "chunks_count": len(stm.retrieved_chunks),
                    "intermediate_summaries": summaries_preview,
                    "answer_status": stm.answer_status,
                    "answer_text_preview": (stm.answer_text or "")[:300],
                    "missing": stm.missing,
                    "rewrite_query": stm.rewrite_query,
                    "iteration_count": stm.iteration_count,
                }
                logger.debug(
                    "PIPELINE [STM@%s DETAIL]\n%s",
                    stage,
                    _json.dumps(detail, indent=2, default=str),
                )
            except Exception:
                pass  # never let logging break the pipeline



    def step_history_load(
        self,
        source: str,          # "db" | "client" | "none"
        turn_count: int,
        has_summary: bool,
        conversation_id: Optional[str] = None,
    ) -> None:
        """Log conversation history loading at the start of the pipeline."""
        if source == "none":
            _log(logging.DEBUG,
                 "PIPELINE [0-HISTORY]  source=none  elapsed=%s",
                 self._elapsed())
            return
        _log(logging.INFO,
             "PIPELINE [0-HISTORY]  source=%s  turns=%d  has_summary=%s"
             "  conv_id=%s  elapsed=%s",
             source, turn_count, has_summary,
             (conversation_id or "")[:16], self._elapsed())

    def step_history_debug(self, history_text: str) -> None:
        """Log full conversation history text at DEBUG level."""
        if _pipeline_level() is None or _pipeline_level() > logging.DEBUG:
            return
        logger.debug(
            "PIPELINE [0-HISTORY TEXT] (%d chars)\n%s",
            len(history_text),
            history_text[:_DEBUG_PREVIEW],
        )

    # ── Step 1 — Query Planner ────────────────────────────────────────────────

    def start(self) -> None:
        _log(logging.INFO,
             "PIPELINE START  repo=%s  query=%r",
             self.repo_id, self.query[:120])

    def step_planner(self, intent: str, strategy: str,
                     search_query: Optional[str], confidence: float) -> None:
        _log(logging.INFO,
             "PIPELINE [1-PLAN]  intent=%s  strategy=%s  confidence=%.2f  "
             "search_query=%r  elapsed=%s",
             intent, strategy, confidence,
             (search_query or "")[:80], self._elapsed())

    # ── Step 2 — Retrieval ────────────────────────────────────────────────────

    def step_retrieval(self, strategy: str, result_count: int) -> None:
        _log(logging.INFO,
             "PIPELINE [2-RETRIEVE]  strategy=%s  results=%d  elapsed=%s",
             strategy, result_count, self._elapsed())

    # ── Step 3 — Graph expansion ──────────────────────────────────────────────

    def step_expansion(self, entity_count: int, context_tokens: int,
                       truncated: bool) -> None:
        _log(logging.INFO,
             "PIPELINE [3-EXPAND]  entities=%d  tokens_est=%d  truncated=%s  elapsed=%s",
             entity_count, context_tokens, truncated, self._elapsed())

    # ── Step 4 — LTM read ─────────────────────────────────────────────────────

    def step_ltm_read(
        self,
        outcome: str,         # "hit" | "miss" | "stale" | "skipped"
        feature_name: Optional[str] = None,
        reason: Optional[str] = None,
        step: int = 4,        # pipeline step number for the log prefix
    ) -> None:
        """Unified LTM read log — INFO for hit/stale, DEBUG for miss/skipped."""
        label = f"{step}-LTM READ"
        if outcome == "hit":
            _log(logging.INFO,
                 "PIPELINE [%s]  outcome=hit  feature=%s  elapsed=%s",
                 label, feature_name, self._elapsed())
        elif outcome == "stale":
            _log(logging.INFO,
                 "PIPELINE [%s]  outcome=stale  feature=%s  reason=%s  elapsed=%s",
                 label, feature_name, reason or "", self._elapsed())
        else:
            _log(logging.DEBUG,
                 "PIPELINE [%s]  outcome=%s  elapsed=%s",
                 label, outcome, self._elapsed())

    # Keep old name as alias so existing orchestrator callers still work
    def step_ltm(self, hit: bool, feature_name: Optional[str] = None) -> None:
        self.step_ltm_read(
            outcome="hit" if hit else "miss",
            feature_name=feature_name,
        )

    def step_ltm_write(
        self,
        feature_name: str,
        confidence: str,
        exploration_status: str,
        step: int = 4,        # pipeline step number for the log prefix
    ) -> None:
        """Log when LTM knowledge is written after an answered response."""
        _log(logging.INFO,
             "PIPELINE [%d-LTM WRITE]  feature=%s  confidence=%s  status=%s  elapsed=%s",
             step, feature_name, confidence, exploration_status, self._elapsed())

    # ── Step 5 — LLM dispatch + response ─────────────────────────────────────

    def step_llm_dispatch(
        self,
        attempt: int,
        model: str,
        provider: str,
        context_tokens: int,
        task_type: str,
    ) -> None:
        """Log which model/provider was selected and with how many tokens BEFORE the call."""
        _log(logging.INFO,
             "PIPELINE [5-DISPATCH]  attempt=%d  model=%s  provider=%s  "
             "ctx_tokens=%d  task=%s  elapsed=%s",
             attempt, model, provider, context_tokens, task_type, self._elapsed())

    def step_llm_prompt(self, system_prompt: str, context: str) -> None:
        """Log full prompts — only emitted at DEBUG level."""
        if _pipeline_level() is None or _pipeline_level() > logging.DEBUG:
            return
        logger.debug(
            "PIPELINE [5-LLM PROMPT]\n"
            "── SYSTEM PROMPT (%d chars) ──────────────────────\n%s\n"
            "── USER CONTEXT (%d chars) ───────────────────────\n%s\n"
            "──────────────────────────────────────────────────",
            len(system_prompt), system_prompt[:_DEBUG_PREVIEW],
            len(context), context[:_DEBUG_PREVIEW],
        )

    def step_llm_response(self, provider: str, model: str, answer_raw: str,
                          status: str, elapsed_ms: float) -> None:
        preview = answer_raw[:_INFO_PREVIEW].replace("\n", " ")
        _log(logging.INFO,
             "PIPELINE [5-LLM RESP]  provider=%s  model=%s  "
             "status=%s  chars=%d  preview=%r  llm_ms=%.0f",
             provider, model, status, len(answer_raw), preview, elapsed_ms)
        if _pipeline_level() is not None and _pipeline_level() <= logging.DEBUG:
            logger.debug(
                "PIPELINE [5-LLM RAW] (%d chars)\n%s",
                len(answer_raw),
                answer_raw[:_DEBUG_PREVIEW],
            )

    # ── Re-retrieval ──────────────────────────────────────────────────────────

    def step_reretrieval(self, attempt: int, new_entities: int,
                         reason: str) -> None:
        _log(logging.INFO,
             "PIPELINE [RE-RETRIEVE]  attempt=%d  new_entities=%d  reason=%r  elapsed=%s",
             attempt, new_entities, reason[:80], self._elapsed())

    # ── Step 6 — Citation validation ─────────────────────────────────────────

    def step_citation(
        self,
        total: int,
        definition: int,
        call_site: int,
        unsupported: int,
        rate: float,
    ) -> None:
        _log(logging.INFO,
             "PIPELINE [6-CITE]  total=%d  definition=%d  call_site=%d  "
             "unsupported=%d  hallucination_rate=%.1f%%  elapsed=%s",
             total, definition, call_site, unsupported, rate * 100, self._elapsed())

    # ── Step 7 — Conversation persistence ────────────────────────────────────

    def step_turn_saved(
        self,
        role: str,
        turn_index: int,
        conversation_id: str,
    ) -> None:
        """Log when a conversation turn is persisted to the DB."""
        _log(logging.INFO,
             "PIPELINE [7-TURN SAVE]  role=%s  turn=%d  conv_id=%s  elapsed=%s",
             role, turn_index, conversation_id[:16], self._elapsed())

    def step_summarise(
        self,
        conversation_id: str,
        turns_compressed: int,
        summarized_through: int,
    ) -> None:
        """Log when a rolling conversation summary is generated."""
        _log(logging.INFO,
             "PIPELINE [7-SUMMARISE]  conv_id=%s  turns_compressed=%d  "
             "summarized_through=%d  elapsed=%s",
             conversation_id[:16], turns_compressed, summarized_through, self._elapsed())

    # ── Finish ────────────────────────────────────────────────────────────────

    def finish(self, status: str, provider: str,
               citation_count: int, answer_chars: int) -> None:
        total_ms = (time.monotonic() - self._start) * 1000
        _log(logging.INFO,
             "PIPELINE DONE  status=%s  provider=%s  "
             "citations=%d  answer_chars=%d  total_ms=%.0f",
             status, provider, citation_count, answer_chars, total_ms)

    # ── Overview pipeline steps ───────────────────────────────────────────────

    def step_file_agent(
        self,
        file_path: str,
        tokens: int,
        elapsed_ms: float,
        from_cache: bool = False,
    ) -> None:
        _log(logging.INFO,
             "PIPELINE [FILE-AGENT]  file=%s  tokens=%d  elapsed=%.0fms  cache=%s",
             file_path, tokens, elapsed_ms, from_cache)

    def step_folder_agent(
        self,
        folder: str,
        file_count: int,
        elapsed_ms: float,
        from_cache: bool = False,
    ) -> None:
        _log(logging.INFO,
             "PIPELINE [FOLDER-AGENT]  folder=%s  files=%d  elapsed=%.0fms  cache=%s",
             folder, file_count, elapsed_ms, from_cache)

    def step_overview_assembled(
        self,
        file_count: int,
        folder_count: int,
        visited_entities: int,
    ) -> None:
        _log(logging.INFO,
             "PIPELINE [OVERVIEW]  file_summaries=%d  folder_summaries=%d  visited=%d  elapsed=%s",
             file_count, folder_count, visited_entities, self._elapsed())

    # ── Citation correction step ──────────────────────────────────────────────

    def step_citation_correction(
        self,
        original_unsupported: int,
        corrections_made: int,
        remaining_unsupported: int,
        method: str = "deterministic",
    ) -> None:
        _log(logging.INFO,
             "PIPELINE [6-CORRECT]  original_unsupported=%d  corrections_made=%d  "
             "remaining=%d  method=%s  elapsed=%s",
             original_unsupported, corrections_made, remaining_unsupported,
             method, self._elapsed())
