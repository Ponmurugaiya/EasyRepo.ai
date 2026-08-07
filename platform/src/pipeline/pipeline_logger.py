"""Structured pipeline logger.

Writes a detailed trace of every pipeline run to the application log.
Controlled by the environment variable:

  PIPELINE_LOG_LEVEL=DEBUG   — log everything (prompts, context, raw LLM output)
  PIPELINE_LOG_LEVEL=INFO    — log flow steps + model selection only (default)
  PIPELINE_LOG_LEVEL=OFF     — disable pipeline tracing entirely

File storage is controlled by main.py via LOG_TO_FILE / LOG_DIR / LOG_MAX_BYTES /
LOG_BACKUP_COUNT env vars — see main.py for details.

Usage:
    from src.pipeline.pipeline_logger import PipelineTrace
    trace = PipelineTrace(query="...", repo_id="...")
    trace.start()
    trace.step_planner(intent="feature", strategy="semantic_search", ...)
    trace.finish(status="answered", provider="groq", citation_count=3, answer_chars=512)
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Max chars to include in prompt/response previews at INFO level
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
    _steps: list[str] = field(default_factory=list, init=False)

    def _elapsed(self) -> str:
        return f"{(time.monotonic() - self._start)*1000:.0f}ms"

    # ── Pipeline flow steps ───────────────────────────────────────────────────

    def start(self) -> None:
        _log(logging.INFO,
             "PIPELINE START  repo=%s  query=%r",
             self.repo_id, self.query[:120])

    def step_planner(self, intent: str, strategy: str, search_query: Optional[str],
                     confidence: float) -> None:
        _log(logging.INFO,
             "PIPELINE [1-PLAN]  intent=%s  strategy=%s  confidence=%.2f  "
             "search_query=%r  elapsed=%s",
             intent, strategy, confidence, (search_query or "")[:80], self._elapsed())

    def step_retrieval(self, strategy: str, result_count: int) -> None:
        _log(logging.INFO,
             "PIPELINE [2-RETRIEVE]  strategy=%s  results=%d  elapsed=%s",
             strategy, result_count, self._elapsed())

    def step_expansion(self, entity_count: int, context_tokens: int,
                       truncated: bool) -> None:
        _log(logging.INFO,
             "PIPELINE [3-EXPAND]  entities=%d  tokens_est=%d  truncated=%s  elapsed=%s",
             entity_count, context_tokens, truncated, self._elapsed())

    def step_ltm(self, hit: bool, feature_name: Optional[str] = None) -> None:
        if hit:
            _log(logging.INFO,
                 "PIPELINE [4-LTM]  hit=True  feature=%s  elapsed=%s",
                 feature_name, self._elapsed())
        else:
            _log(logging.DEBUG, "PIPELINE [4-LTM]  hit=False  elapsed=%s", self._elapsed())

    def step_llm_dispatch(self, attempt: int, model: str, provider: str,
                          context_tokens: int, task_type: str) -> None:
        _log(logging.INFO,
             "PIPELINE [5-LLM]  attempt=%d  model=%s  provider=%s  "
             "ctx_tokens=%d  task=%s  elapsed=%s",
             attempt, model, provider, context_tokens, task_type, self._elapsed())

    def step_llm_prompt(self, system_prompt: str, context: str) -> None:
        """Log full prompts — only emitted at DEBUG level."""
        if _pipeline_level() is None or _pipeline_level() > logging.DEBUG:
            return
        sys_preview = system_prompt[:_DEBUG_PREVIEW]
        ctx_preview = context[:_DEBUG_PREVIEW]
        logger.debug(
            "PIPELINE [5-LLM PROMPT]\n"
            "── SYSTEM PROMPT (%d chars) ──────────────────────\n%s\n"
            "── USER CONTEXT (%d chars) ───────────────────────\n%s\n"
            "──────────────────────────────────────────────────",
            len(system_prompt), sys_preview, len(context), ctx_preview,
        )

    def step_llm_response(self, provider: str, model: str, answer_raw: str,
                          status: str, elapsed_ms: float) -> None:
        preview = answer_raw[:_INFO_PREVIEW].replace("\n", " ")
        _log(logging.INFO,
             "PIPELINE [5-LLM RESPONSE]  provider=%s  model=%s  "
             "status=%s  chars=%d  preview=%r  llm_ms=%.0f",
             provider, model, status, len(answer_raw), preview, elapsed_ms)
        # Full raw output at DEBUG
        if _pipeline_level() is not None and _pipeline_level() <= logging.DEBUG:
            logger.debug(
                "PIPELINE [5-LLM RAW OUTPUT] (%d chars)\n%s",
                len(answer_raw),
                answer_raw[:_DEBUG_PREVIEW],
            )

    def step_citation(self, total: int, definition: int,
                      call_site: int, unsupported: int, rate: float) -> None:
        _log(logging.INFO,
             "PIPELINE [6-CITE]  total=%d  definition=%d  call_site=%d  "
             "unsupported=%d  hallucination_rate=%.1f%%  elapsed=%s",
             total, definition, call_site, unsupported, rate * 100, self._elapsed())

    def step_reretrieval(self, attempt: int, new_entities: int,
                         reason: str) -> None:
        _log(logging.INFO,
             "PIPELINE [RE-RETRIEVE]  attempt=%d  new_entities=%d  reason=%r  elapsed=%s",
             attempt, new_entities, reason[:80], self._elapsed())

    def finish(self, status: str, provider: str,
               citation_count: int, answer_chars: int) -> None:
        total_ms = (time.monotonic() - self._start) * 1000
        _log(logging.INFO,
             "PIPELINE DONE  status=%s  provider=%s  "
             "citations=%d  answer_chars=%d  total_ms=%.0f",
             status, provider, citation_count, answer_chars, total_ms)
