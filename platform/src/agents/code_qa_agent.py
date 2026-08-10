"""Code Q&A Agent — answers targeted questions about a codebase.

Takes a user's natural-language question about code, a retrieved + graph-expanded
context block, and conversation history.  Returns a Markdown answer with inline
``[file:line-line]`` citations, a structured JSON status block, and an LTM entry.

Used for intents: feature, dependency_flow, specific_lookup, query.
NOT used for repository_overview / repository_detailed (those use the
hierarchical overview pipeline in src/retrieval/repo_overview.py).

Statuses:
  "answered"         — produced a complete answer
  "insufficient"     — context incomplete; requests targeted re-retrieval
  "rewrite_search"   — retrieved entities unrelated; requests query rewrite

Safety rule: unknown status is treated as "answered" to prevent the
re-retrieval loop from activating on a malformed response.

Public API
----------
run(query, context, system_prompt, ...) -> QAResponse
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

_STRUCTURED_OUTPUT_ADDENDUM = """

# REMINDER: Two-part response structure
Your response MUST have two parts:

PART 1 — Full Markdown answer with ALL inline citations [file:line-line].
PART 2 — A single <answer_json> block appended at the very end (after all prose).

If you can fully answer the question:
<answer_json>
{"status": "answered", "answer": "<brief 1-sentence summary only — full answer is in Part 1>", "ltm_entry": {"feature_name": "<topic>", "confidence": "high", "exploration_status": "complete", "summary": "<1-2 sentences>"}}
</answer_json>

If context is incomplete and you need more information:
<answer_json>
{"status": "insufficient", "reason": "<why>", "missing": {"type": "<dependency_flow|feature|specific_lookup>", "entity": "<missing entity name>"}, "partial_answer": "<what you can say so far>"}
</answer_json>

If retrieved entities are completely unrelated to the question:
<answer_json>
{"status": "rewrite_search", "reason": "<why>", "rewrite_query": "<better keyword-rich phrase>"}
</answer_json>

IMPORTANT:
- The <answer_json> block must be the LAST thing in your response.
- Keep the JSON "answer" field short — the real answer with citations is in the prose above.
- Do NOT put your full answer inside the JSON "answer" field.
"""


@dataclass
class QAResponse:
    """Structured output from the Code Q&A Agent.

    Attributes
    ----------
    status:
        "answered" | "insufficient" | "rewrite_search"
    answer:
        The full answer text (set for "answered" status).
    partial_answer:
        Partial answer text available when status is "insufficient".
    reason:
        Why the status is "insufficient" or "rewrite_search".
    missing:
        For "insufficient": {"type": "...", "entity": "..."} identifying what's missing.
    rewrite_query:
        For "rewrite_search": the improved query string.
    ltm_entry:
        For "answered": structured entry to write to LTM.
        Contains: feature_name, confidence, exploration_status, summary.
    raw_response:
        The full unprocessed LLM response (for debugging).
    provider_used:
        Which LLM provider produced this response ("groq" or "gemini").
    """

    status: str
    answer: Optional[str] = None
    partial_answer: Optional[str] = None
    reason: Optional[str] = None
    missing: Optional[dict] = None
    rewrite_query: Optional[str] = None
    ltm_entry: Optional[dict] = None
    raw_response: str = ""
    provider_used: str = "unknown"
    prompt_tokens: int = 0
    completion_tokens: int = 0


def _extract_any_json_with_status(text: str) -> Optional[dict]:
    """Find the first JSON object with a 'status' key, handling nested braces."""
    for match in re.finditer(r'\{', text):
        try:
            obj, _ = json.JSONDecoder().raw_decode(text, match.start())
            if isinstance(obj, dict) and "status" in obj:
                return obj
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def _extract_answer_json(text: str) -> Optional[dict]:
    """Extract the structured JSON block from the LLM response."""
    tag_match = re.search(
        r"<answer_json>\s*(.*?)\s*</answer_json>",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if tag_match:
        try:
            return json.loads(tag_match.group(1))
        except json.JSONDecodeError:
            pass
    return _extract_any_json_with_status(text)


def _build_augmented_system_prompt(
    system_prompt: str,
    history_text: str = "",
    user_memory: list[dict] | None = None,
    user_repo_preferences: list[dict] | None = None,
    repo_user_memory: list[dict] | None = None,
) -> str:
    """Append memory blocks, conversation history, and structured output addendum."""
    parts = [system_prompt]

    # ── Long-term memory block ────────────────────────────────────────────────
    memory_sections = []

    if user_memory:
        lines = "\n".join(f"- [{m['category']}] {m['fact']}" for m in user_memory)
        memory_sections.append(f"## User preferences & background\n{lines}")

    if user_repo_preferences:
        lines = "\n".join(f"- [{m['category']}] {m['fact']}" for m in user_repo_preferences)
        memory_sections.append(f"## How this user works with this repo\n{lines}")

    if repo_user_memory:
        lines = "\n".join(f"- [{m['category']}] {m['fact']}" for m in repo_user_memory)
        memory_sections.append(f"## Known facts about this codebase\n{lines}")

    if memory_sections:
        parts.append(
            "\n# Long-term memory\n"
            "The following facts have been remembered from past conversations. "
            "Use them to personalize and improve your answer:\n\n"
            + "\n\n".join(memory_sections)
        )

    # ── Conversation history ──────────────────────────────────────────────────
    if history_text:
        parts.append(
            f"\n# Conversation history\nUse this as context for follow-up questions:\n"
            f"<conversation_history>\n{history_text}\n</conversation_history>"
        )

    parts.append(_STRUCTURED_OUTPUT_ADDENDUM)
    return "\n".join(parts)


def run(
    query: str,
    context: str,
    system_prompt: str,
    groq_model: Optional[str] = None,
    groq_api_key: Optional[str] = None,
    gemini_model: str = "gemini-2.5-flash",
    gemini_api_key: Optional[str] = None,
    skip_groq: bool = False,
    skip_gemini: bool = False,
    history_text: str = "",
    iteration: int = 0,
    user_memory: list[dict] | None = None,
    user_repo_preferences: list[dict] | None = None,
    repo_user_memory: list[dict] | None = None,
) -> QAResponse:
    """Run the Code Q&A Agent and return a structured response.

    Parameters
    ----------
    query:
        The original user question.
    context:
        Rendered context string from render_context_for_prompt().
    system_prompt:
        Base system prompt from build_system_prompt().
    groq_model / groq_api_key / gemini_model / gemini_api_key:
        LLM routing overrides.
    skip_groq / skip_gemini:
        Force-skip provider flags.
    history_text:
        Rolling conversation summary injected into the system prompt.
    iteration:
        Re-retrieval iteration count (0 = first attempt).
    user_memory:
        Global user facts (preferences, background) from long-term memory.
    user_repo_preferences:
        Facts about how this user works with this specific repo.
    repo_user_memory:
        Codebase facts learned through this user's past conversations.
    """
    from src.generation.llm_client import generate_answer_with_fallback, LLMProviderError  # noqa: PLC0415

    augmented_system = _build_augmented_system_prompt(
        system_prompt,
        history_text=history_text,
        user_memory=user_memory,
        user_repo_preferences=user_repo_preferences,
        repo_user_memory=repo_user_memory,
    )

    if iteration > 0:
        augmented_system += (
            f"\n\n[PIPELINE NOTE: This is retry #{iteration}. "
            "Additional context has been retrieved. "
            "Please give a complete answer even if some gaps remain.]"
        )

    estimated_tokens = len(context) // 4
    task_type = "standard"
    # Only downgrade to fast on the first attempt with very small context
    if estimated_tokens < 500 and iteration == 0:
        task_type = "fast"

    try:
        raw_response, provider_used, prompt_tokens, completion_tokens = generate_answer_with_fallback(
            query=query,
            context=context,
            system_prompt=augmented_system,
            groq_model=groq_model,
            groq_api_key=groq_api_key,
            gemini_model=gemini_model,
            gemini_api_key=gemini_api_key,
            skip_groq=skip_groq,
            skip_gemini=skip_gemini,
            task_type=task_type,
        )
    except LLMProviderError as exc:
        logger.error("CodeQAAgent: LLM call failed: %s", exc)
        return QAResponse(
            status="answered",
            answer="I was unable to generate an answer due to a provider error. Please try again.",
            raw_response=str(exc),
            provider_used="unknown",
        )

    parsed = _extract_answer_json(raw_response)

    if parsed is None:
        logger.debug(
            "CodeQAAgent: no <answer_json> block found (iteration=%d) — treating as answered",
            iteration,
        )
        return QAResponse(
            status="answered",
            answer=raw_response,
            raw_response=raw_response,
            provider_used=provider_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    status = parsed.get("status", "answered")
    if status not in ("answered", "insufficient", "rewrite_search"):
        logger.debug("CodeQAAgent: unknown status %r — treating as answered", status)
        status = "answered"

    if status == "answered":
        tag_pos = raw_response.lower().find("<answer_json>")
        prose_answer = raw_response[:tag_pos].strip() if tag_pos != -1 else ""
        json_answer = parsed.get("answer", "")
        answer_text = prose_answer or json_answer or raw_response.strip()
        if not prose_answer and json_answer:
            logger.debug(
                "CodeQAAgent: prose before <answer_json> empty — using JSON answer field"
            )
        return QAResponse(
            status="answered",
            answer=answer_text,
            ltm_entry=parsed.get("ltm_entry"),
            raw_response=raw_response,
            provider_used=provider_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    if status == "insufficient":
        partial = parsed.get("partial_answer")
        if not partial:
            tag_pos = raw_response.lower().find("<answer_json>")
            partial = raw_response[:tag_pos].strip() if tag_pos != -1 else None
        return QAResponse(
            status="insufficient",
            partial_answer=partial,
            reason=parsed.get("reason"),
            missing=parsed.get("missing"),
            raw_response=raw_response,
            provider_used=provider_used,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    return QAResponse(
        status="rewrite_search",
        reason=parsed.get("reason"),
        rewrite_query=parsed.get("rewrite_query"),
        raw_response=raw_response,
        provider_used=provider_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
