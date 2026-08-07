"""Answer Agent with structured output.

Wraps the existing LLM generation with a structured JSON response contract.
The agent can return three statuses:

  "answered"         — produced a complete answer
  "insufficient"     — context is incomplete; requests targeted re-retrieval
  "rewrite_search"   — retrieved entities are unrelated; requests a query rewrite

Safety rule: if the parsed response does not contain "insufficient" or
"rewrite_search", it is treated as "answered" to prevent the re-retrieval loop
from activating on a malformed response.

Public API
----------
run(query, final_context, system_prompt, ...) -> AgentResponse
    The only function callers need.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Reinforcement appended to the system prompt — reminds the model of the
# two-part structure required. Placed AFTER the existing system prompt so
# citation rules are established first.
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
class AgentResponse:
    """Structured output from the Answer Agent.

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


def _extract_answer_json(text: str) -> Optional[dict]:
    """Extract the structured JSON block from the LLM response.

    Looks for content between <answer_json> and </answer_json> tags.
    Falls back to searching for any JSON object with a "status" key.
    """
    # Primary: extract from <answer_json> tags
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

    # Fallback: find any JSON object with a "status" key
    json_matches = re.finditer(r"\{[^{}]*\"status\"[^{}]*\}", text, re.DOTALL)
    for match in json_matches:
        try:
            data = json.loads(match.group(0))
            if "status" in data:
                return data
        except json.JSONDecodeError:
            continue

    return None


def _build_augmented_system_prompt(system_prompt: str, history_text: str = "") -> str:
    """Append the structured output addendum and optional conversation history."""
    parts = [system_prompt, _STRUCTURED_OUTPUT_ADDENDUM]
    if history_text:
        parts.append(
            f"\n# Conversation history\nUse this as context for follow-up questions:\n"
            f"<conversation_history>\n{history_text}\n</conversation_history>"
        )
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
) -> AgentResponse:
    """Run the Answer Agent and return a structured response.

    Parameters
    ----------
    query:
        The original user question.
    context:
        Rendered context string from render_context_for_prompt().
    system_prompt:
        Base system prompt from build_system_prompt().
    groq_model:
        Optional specific Groq model override.
    groq_api_key:
        Groq API key (falls back to env var).
    gemini_model:
        Gemini model name override.
    gemini_api_key:
        Gemini API key (falls back to env var).
    skip_groq:
        Force-skip Groq provider.
    skip_gemini:
        Force-skip Gemini fallback.
    history_text:
        Formatted conversation history to inject into the system prompt.
    iteration:
        Current re-retrieval iteration count (0 = first attempt).
        When > 0, the system prompt notes that this is a retry with additional context.

    Returns
    -------
    AgentResponse
    """
    from src.generation.llm_client import generate_answer_with_fallback, LLMProviderError

    augmented_system = _build_augmented_system_prompt(system_prompt, history_text)

    if iteration > 0:
        augmented_system += (
            f"\n\n[PIPELINE NOTE: This is retry #{iteration}. "
            "Additional context has been retrieved and added above. "
            "Please try to give a complete answer even if some gaps remain.]"
        )

    # Determine task type from context size
    estimated_tokens = len(context) // 4
    task_type = "standard"
    # For very short contexts (retries with narrow queries) use fast tier
    if estimated_tokens < 500:
        task_type = "fast"

    # Build skip set from legacy flags and pass as task_type hint
    try:
        raw_response, provider_used = generate_answer_with_fallback(
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
        logger.error("AnswerAgent: LLM call failed: %s", exc)
        # Return a graceful degradation rather than crashing the pipeline
        return AgentResponse(
            status="answered",
            answer="I was unable to generate an answer due to a provider error. Please try again.",
            raw_response=str(exc),
            provider_used="unknown",
        )

    # Parse the structured JSON block
    parsed = _extract_answer_json(raw_response)

    if parsed is None:
        # No structured block found — treat the entire response as the answer
        logger.debug(
            "AnswerAgent: no <answer_json> block found (iteration=%d) — treating as answered",
            iteration,
        )
        return AgentResponse(
            status="answered",
            answer=raw_response,
            raw_response=raw_response,
            provider_used=provider_used,
        )

    status = parsed.get("status", "answered")

    # Safety rule: treat unknown or missing status as "answered"
    if status not in ("answered", "insufficient", "rewrite_search"):
        logger.debug(
            "AnswerAgent: unknown status %r — treating as answered",
            status,
        )
        status = "answered"

    if status == "answered":
        # PRIMARY: extract prose BEFORE the <answer_json> tag.
        # The model writes the full answer with inline citations in the prose,
        # then appends the structured JSON block at the end. The JSON "answer"
        # field is often a condensed duplicate that loses citation tags.
        tag_pos = raw_response.lower().find("<answer_json>")
        prose_answer = raw_response[:tag_pos].strip() if tag_pos != -1 else ""

        # FALLBACK 1: use the JSON "answer" field if prose is empty
        json_answer = parsed.get("answer", "")

        # FALLBACK 2: use the entire raw response if both are empty
        answer_text = prose_answer or json_answer or raw_response.strip()

        if not prose_answer and json_answer:
            logger.debug(
                "AnswerAgent: prose before <answer_json> was empty — "
                "using JSON answer field (citations may be reduced)"
            )

        return AgentResponse(
            status="answered",
            answer=answer_text,
            ltm_entry=parsed.get("ltm_entry"),
            raw_response=raw_response,
            provider_used=provider_used,
        )

    if status == "insufficient":
        partial = parsed.get("partial_answer")
        if not partial:
            tag_pos = raw_response.lower().find("<answer_json>")
            partial = raw_response[:tag_pos].strip() if tag_pos != -1 else None

        return AgentResponse(
            status="insufficient",
            partial_answer=partial,
            reason=parsed.get("reason"),
            missing=parsed.get("missing"),
            raw_response=raw_response,
            provider_used=provider_used,
        )

    # status == "rewrite_search"
    return AgentResponse(
        status="rewrite_search",
        reason=parsed.get("reason"),
        rewrite_query=parsed.get("rewrite_query"),
        raw_response=raw_response,
        provider_used=provider_used,
    )
