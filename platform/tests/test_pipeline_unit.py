"""Unit tests for the unified pipeline — no DB, no LLM calls.

All external dependencies (DB sessions, LLM client) are mocked so these
tests run instantly and work offline.

Coverage:
  - ShortTermMemory dataclass
  - QueryPlan parsing (good JSON, bad JSON, unknown values, low confidence)
  - Answer Agent JSON extraction and response shaping
  - Answer Agent safety rule (no "insufficient"/"rewrite_search" → "answered")
  - Targeted re-retrieval deduplication
  - LTM stale detection logic
  - Conversation history formatting
  - AskRequest schema backward compatibility
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ── STM ───────────────────────────────────────────────────────────────────────

from src.pipeline.memory import ShortTermMemory


class TestShortTermMemory:
    def test_defaults(self):
        stm = ShortTermMemory(goal="What does AuthService do?", repo_id="repo123")
        assert stm.intent == "query"
        assert stm.retrieval_strategy == "semantic_search"
        assert stm.search_query is None
        assert stm.session_id is None
        assert stm.conversation_id is None
        assert stm.visited_entity_ids == set()
        assert stm.retrieved_chunks == []
        assert stm.iteration_count == 0
        assert stm.answer_status == "pending"
        assert stm.answer_text is None

    def test_mutation(self):
        stm = ShortTermMemory(goal="test", repo_id="r1")
        stm.intent = "feature"
        stm.retrieval_strategy = "semantic_search_with_graph"
        stm.visited_entity_ids.add("py.auth.AuthService")
        stm.iteration_count += 1
        assert stm.intent == "feature"
        assert "py.auth.AuthService" in stm.visited_entity_ids
        assert stm.iteration_count == 1

    def test_independent_collections(self):
        """Two STMs must not share default mutable collections."""
        stm1 = ShortTermMemory(goal="a", repo_id="r")
        stm2 = ShortTermMemory(goal="b", repo_id="r")
        stm1.visited_entity_ids.add("some.entity")
        assert "some.entity" not in stm2.visited_entity_ids


# ── Query Planner ─────────────────────────────────────────────────────────────

from src.generation.query_planner import (
    QueryPlan,
    _default_plan,
    _parse_plan_response,
    _VALID_INTENTS,
    _VALID_STRATEGIES,
)


class TestQueryPlanner:
    def _make_json(self, **kwargs) -> str:
        defaults = {
            "intent": "feature",
            "retrieval_strategy": "semantic_search",
            "search_query": "auth service JWT",
            "confidence": 0.9,
        }
        return json.dumps({**defaults, **kwargs})

    def test_default_plan(self):
        plan = _default_plan("how does login work?")
        assert plan.intent == "query"
        assert plan.retrieval_strategy == "semantic_search"
        assert plan.search_query == "how does login work?"
        assert plan.confidence == 0.0

    def test_parse_good_json(self):
        raw = self._make_json()
        plan = _parse_plan_response(raw, "original")
        assert plan.intent == "feature"
        assert plan.retrieval_strategy == "semantic_search"
        assert plan.search_query == "auth service JWT"
        assert plan.confidence == 0.9

    def test_parse_bad_json_returns_default(self):
        plan = _parse_plan_response("not json at all", "original query")
        assert plan.intent == "query"
        assert plan.search_query == "original query"
        assert plan.confidence == 0.0

    def test_parse_malformed_json_returns_default(self):
        plan = _parse_plan_response("{bad: json}", "original query")
        assert plan.search_query == "original query"

    def test_unknown_intent_replaced(self):
        raw = self._make_json(intent="unknown_nonsense")
        plan = _parse_plan_response(raw, "original")
        assert plan.intent == "query"

    def test_unknown_strategy_replaced(self):
        raw = self._make_json(retrieval_strategy="magic_search")
        plan = _parse_plan_response(raw, "original")
        assert plan.retrieval_strategy == "semantic_search"

    def test_repository_walk_null_query(self):
        raw = self._make_json(
            intent="repository_overview",
            retrieval_strategy="repository_walk",
            search_query=None,
            confidence=0.85,
        )
        plan = _parse_plan_response(raw, "overview of the repo")
        assert plan.retrieval_strategy == "repository_walk"
        assert plan.search_query is None

    def test_low_confidence_repository_walk_falls_back(self):
        """Low confidence (<0.3) on repository_walk overrides to semantic_search."""
        raw = self._make_json(
            retrieval_strategy="repository_walk",
            search_query=None,
            confidence=0.1,
        )
        plan = _parse_plan_response(raw, "original query")
        assert plan.retrieval_strategy == "semantic_search"
        assert plan.search_query == "original query"

    def test_missing_search_query_filled_from_original(self):
        """When strategy is semantic_search but search_query is absent, use original."""
        raw = json.dumps({
            "intent": "feature",
            "retrieval_strategy": "semantic_search",
            "confidence": 0.8,
            # search_query intentionally absent
        })
        plan = _parse_plan_response(raw, "original query text")
        assert plan.search_query == "original query text"

    def test_json_embedded_in_prose(self):
        """Planner should extract JSON even if the model adds surrounding prose."""
        prose = (
            'Sure! Here is my classification:\n\n'
            + self._make_json(intent="dependency_flow", retrieval_strategy="semantic_search_with_graph")
            + '\n\nHope that helps!'
        )
        plan = _parse_plan_response(prose, "original")
        assert plan.intent == "dependency_flow"
        assert plan.retrieval_strategy == "semantic_search_with_graph"

    def test_plan_function_fallback_on_llm_error(self):
        """plan() returns default when the LLM call raises any exception."""
        # Patch smart_complete (the function query_planner now calls)
        with patch("src.generation.llm_client.smart_complete") as mock_llm:
            mock_llm.side_effect = RuntimeError("quota exceeded")
            from src.generation.query_planner import plan
            result = plan("what is the auth flow?", repo_id="repo123")
        assert result.retrieval_strategy == "semantic_search"
        assert result.search_query == "what is the auth flow?"
        assert result.confidence == 0.0


# ── Answer Agent ──────────────────────────────────────────────────────────────

from src.generation.answer_agent import (
    AgentResponse,
    _extract_answer_json,
    _build_augmented_system_prompt,
    run as agent_run,
)


class TestAnswerAgentExtraction:
    def _wrap(self, payload: dict, prose: str = "") -> str:
        return (prose + "\n<answer_json>\n" + json.dumps(payload) + "\n</answer_json>").strip()

    def test_extract_answered(self):
        payload = {"status": "answered", "answer": "Auth uses JWT."}
        parsed = _extract_answer_json(self._wrap(payload, "Auth uses JWT."))
        assert parsed["status"] == "answered"
        assert parsed["answer"] == "Auth uses JWT."

    def test_extract_insufficient(self):
        payload = {
            "status": "insufficient",
            "reason": "Missing JWTService",
            "missing": {"type": "feature", "entity": "JWTService"},
        }
        parsed = _extract_answer_json(self._wrap(payload))
        assert parsed["status"] == "insufficient"
        assert parsed["missing"]["entity"] == "JWTService"

    def test_extract_rewrite(self):
        payload = {
            "status": "rewrite_search",
            "reason": "Wrong context",
            "rewrite_query": "JWT token auth service",
        }
        parsed = _extract_answer_json(self._wrap(payload))
        assert parsed["status"] == "rewrite_search"
        assert parsed["rewrite_query"] == "JWT token auth service"

    def test_no_block_returns_none(self):
        assert _extract_answer_json("plain text, no JSON") is None

    def test_malformed_json_in_block_returns_none(self):
        assert _extract_answer_json("<answer_json>\n{bad json}\n</answer_json>") is None

    def test_case_insensitive_tag(self):
        payload = {"status": "answered", "answer": "Yes."}
        raw = "<ANSWER_JSON>\n" + json.dumps(payload) + "\n</ANSWER_JSON>"
        parsed = _extract_answer_json(raw)
        assert parsed is not None
        assert parsed["status"] == "answered"


class TestAnswerAgentRun:
    """Tests for the run() function with mocked LLM."""

    def _make_answered_response(self, answer: str = "It uses JWT.") -> str:
        payload = {
            "status": "answered",
            "answer": answer,
            "ltm_entry": {
                "feature_name": "Authentication",
                "confidence": "high",
                "exploration_status": "complete",
                "summary": "JWT-based auth.",
            },
        }
        return f"<answer_json>\n{json.dumps(payload)}\n</answer_json>"

    def _make_insufficient_response(self) -> str:
        payload = {
            "status": "insufficient",
            "reason": "Missing JWTService",
            "missing": {"type": "feature", "entity": "JWTService"},
            "partial_answer": "Auth exists but I couldn't trace JWT.",
        }
        return f"Partial context.\n<answer_json>\n{json.dumps(payload)}\n</answer_json>"

    def _make_rewrite_response(self) -> str:
        payload = {
            "status": "rewrite_search",
            "reason": "Context unrelated",
            "rewrite_query": "authentication service JWT validation",
        }
        return f"<answer_json>\n{json.dumps(payload)}\n</answer_json>"

    # answer_agent imports generate_answer_with_fallback lazily inside run(),
    # so we patch it at its source module.
    @patch("src.generation.llm_client.generate_answer_with_fallback")
    def test_answered_status(self, mock_llm):
        mock_llm.return_value = (self._make_answered_response(), "groq", 100, 50)
        resp = agent_run("How does auth work?", "context...", "sys prompt")
        assert resp.status == "answered"
        assert resp.answer == "It uses JWT."
        assert resp.ltm_entry is not None
        assert resp.ltm_entry["feature_name"] == "Authentication"
        assert resp.provider_used == "groq"

    @patch("src.generation.llm_client.generate_answer_with_fallback")
    def test_insufficient_status(self, mock_llm):
        mock_llm.return_value = (self._make_insufficient_response(), "groq", 100, 50)
        resp = agent_run("How does auth work?", "context...", "sys prompt")
        assert resp.status == "insufficient"
        assert resp.missing == {"type": "feature", "entity": "JWTService"}
        assert resp.partial_answer == "Auth exists but I couldn't trace JWT."
        assert resp.reason == "Missing JWTService"

    @patch("src.generation.llm_client.generate_answer_with_fallback")
    def test_rewrite_status(self, mock_llm):
        mock_llm.return_value = (self._make_rewrite_response(), "groq", 100, 50)
        resp = agent_run("Auth?", "context...", "sys prompt")
        assert resp.status == "rewrite_search"
        assert resp.rewrite_query == "authentication service JWT validation"

    @patch("src.generation.llm_client.generate_answer_with_fallback")
    def test_safety_rule_unknown_status(self, mock_llm):
        """Unknown status in JSON block must be treated as 'answered'."""
        payload = {"status": "hallucinated_status", "answer": "Some answer."}
        raw = f"<answer_json>\n{json.dumps(payload)}\n</answer_json>"
        mock_llm.return_value = (raw, "groq", 100, 50)
        resp = agent_run("query", "context", "sys")
        assert resp.status == "answered"

    @patch("src.generation.llm_client.generate_answer_with_fallback")
    def test_no_json_block_treated_as_answered(self, mock_llm):
        """If no <answer_json> block is found, treat the whole response as the answer."""
        mock_llm.return_value = ("The auth service uses HMAC-SHA256.", "gemini", 100, 50)
        resp = agent_run("query", "context", "sys")
        assert resp.status == "answered"
        assert resp.answer == "The auth service uses HMAC-SHA256."
        assert resp.provider_used == "gemini"

    @patch("src.generation.llm_client.generate_answer_with_fallback")
    def test_llm_error_returns_graceful_answer(self, mock_llm):
        """LLM provider error must not crash the pipeline — returns graceful answer."""
        from src.generation.llm_client import LLMProviderError
        mock_llm.side_effect = LLMProviderError("all providers exhausted")
        resp = agent_run("query", "context", "sys")
        assert resp.status == "answered"
        assert "unable to generate" in resp.answer.lower()

    def test_augmented_prompt_includes_addendum(self):
        from src.generation.prompt_templates import build_system_prompt
        base = build_system_prompt()
        augmented = _build_augmented_system_prompt(base)
        assert "<answer_json>" in augmented
        assert "status" in augmented

    def test_augmented_prompt_includes_history(self):
        from src.generation.prompt_templates import build_system_prompt
        base = build_system_prompt()
        augmented = _build_augmented_system_prompt(base, history_text="User: hi\nAssistant: hello")
        assert "<conversation_history>" in augmented
        assert "User: hi" in augmented

    def test_augmented_prompt_no_history_no_block(self):
        from src.generation.prompt_templates import build_system_prompt
        base = build_system_prompt()
        augmented = _build_augmented_system_prompt(base, history_text="")
        assert "<conversation_history>" not in augmented


# ── Targeted Re-retrieval ─────────────────────────────────────────────────────

from src.retrieval.targeted_retrieval import fetch as targeted_fetch


class TestTargetedRetrieval:
    def _make_stm(self, visited=None) -> ShortTermMemory:
        stm = ShortTermMemory(goal="test query", repo_id="repo123")
        stm.visited_entity_ids = set(visited or [])
        return stm

    def _make_agent_response(self, status, entity=None, rewrite=None) -> AgentResponse:
        resp = AgentResponse(status=status)
        if entity:
            resp.missing = {"type": "feature", "entity": entity}
        if rewrite:
            resp.rewrite_query = rewrite
        return resp

    def _make_retrieval_result(self, entity_id: str):
        from src.retrieval.models import RetrievalResult
        entity = MagicMock()
        entity.id = entity_id
        return RetrievalResult(entity_id=entity_id, entity=entity, score=0.9, rank=1)

    @patch("src.retrieval.targeted_retrieval.search")
    @patch("src.retrieval.targeted_retrieval.expand")
    def test_insufficient_searches_missing_entity(self, mock_expand, mock_search):
        stm = self._make_stm()
        agent_resp = self._make_agent_response("insufficient", entity="JWTService")

        mock_result = self._make_retrieval_result("py.auth.JWTService")
        mock_search.return_value = [mock_result]
        mock_expand.return_value = [MagicMock()]

        new_chunks = targeted_fetch(stm, agent_resp, "repo123", MagicMock())

        mock_search.assert_called_once()
        call_args = mock_search.call_args
        assert call_args.kwargs["query"] == "JWTService" or call_args.args[0] == "JWTService"
        assert len(new_chunks) == 1

    @patch("src.retrieval.targeted_retrieval.search")
    @patch("src.retrieval.targeted_retrieval.expand")
    def test_rewrite_uses_new_query(self, mock_expand, mock_search):
        stm = self._make_stm()
        agent_resp = self._make_agent_response("rewrite_search", rewrite="JWT token validation")

        mock_result = self._make_retrieval_result("py.auth.JWTService")
        mock_search.return_value = [mock_result]
        mock_expand.return_value = [MagicMock()]

        targeted_fetch(stm, agent_resp, "repo123", MagicMock())

        call_args = mock_search.call_args
        query = call_args.kwargs.get("query") or call_args.args[0]
        assert query == "JWT token validation"

    @patch("src.retrieval.targeted_retrieval.search")
    @patch("src.retrieval.targeted_retrieval.expand")
    def test_deduplication_skips_already_visited(self, mock_expand, mock_search):
        """Entities already in visited_entity_ids must not be re-expanded."""
        already_seen = "py.auth.AuthService"
        stm = self._make_stm(visited=[already_seen])
        agent_resp = self._make_agent_response("insufficient", entity="AuthService")

        mock_search.return_value = [self._make_retrieval_result(already_seen)]
        mock_expand.return_value = []

        new_chunks = targeted_fetch(stm, agent_resp, "repo123", MagicMock())

        # expand should not have been called — everything was already seen
        mock_expand.assert_not_called()
        assert new_chunks == []

    @patch("src.retrieval.targeted_retrieval.search")
    def test_search_failure_returns_empty(self, mock_search):
        mock_search.side_effect = Exception("DB down")
        stm = self._make_stm()
        agent_resp = self._make_agent_response("insufficient", entity="SomeEntity")
        result = targeted_fetch(stm, agent_resp, "repo123", MagicMock())
        assert result == []

    @patch("src.retrieval.targeted_retrieval.search")
    def test_no_results_returns_empty(self, mock_search):
        mock_search.return_value = []
        stm = self._make_stm()
        agent_resp = self._make_agent_response("insufficient", entity="Ghost")
        result = targeted_fetch(stm, agent_resp, "repo123", MagicMock())
        assert result == []

    @patch("src.retrieval.targeted_retrieval.search")
    @patch("src.retrieval.targeted_retrieval.expand")
    def test_visited_entity_ids_updated_after_fetch(self, mock_expand, mock_search):
        """visited_entity_ids on the STM must be updated after a successful fetch."""
        stm = self._make_stm()
        agent_resp = self._make_agent_response("insufficient", entity="NewEntity")

        new_id = "py.auth.NewEntity"
        mock_search.return_value = [self._make_retrieval_result(new_id)]
        mock_expand.return_value = [MagicMock()]

        targeted_fetch(stm, agent_resp, "repo123", MagicMock())
        assert new_id in stm.visited_entity_ids


# ── LTM Store — stale detection ───────────────────────────────────────────────

from src.storage.ltm_store import lookup, inject_ltm


class TestLTMStore:
    def _make_repo(self, indexed_at=None):
        repo = MagicMock()
        repo.indexed_at = indexed_at
        return repo

    def _make_ltm_entry(self, feature_name, repo_indexed_at=None):
        entry = MagicMock()
        entry.feature_name = feature_name
        entry.repo_indexed_at = repo_indexed_at
        entry.summary = "JWT-based auth."
        entry.confidence = "high"
        entry.exploration_status = "complete"
        return entry

    def test_no_session_id_returns_none(self):
        db = MagicMock()
        result = lookup("repo123", None, "feature", self._make_repo(), db)
        assert result is None
        db.query.assert_not_called()

    def test_no_db_entry_returns_none(self):
        db = MagicMock()
        db.query.return_value.filter_by.return_value.order_by.return_value.first.return_value = None
        result = lookup("repo123", "session-abc", "feature", self._make_repo(), db)
        assert result is None

    def test_fresh_entry_returned(self):
        """Entry written before re-index should be returned (repo_indexed_at matches)."""
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        repo = self._make_repo(indexed_at=ts)
        entry = self._make_ltm_entry("Authentication", repo_indexed_at=ts)

        db = MagicMock()
        db.query.return_value.filter_by.return_value.order_by.return_value.first.return_value = entry
        result = lookup("repo123", "session-abc", "feature", repo, db)
        assert result is entry

    def test_stale_entry_discarded(self):
        """Entry written before a re-index (repo.indexed_at > entry.repo_indexed_at) must be discarded."""
        entry_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        reindex_time = entry_time + timedelta(hours=1)  # repo was re-indexed after

        repo = self._make_repo(indexed_at=reindex_time)
        entry = self._make_ltm_entry("Authentication", repo_indexed_at=entry_time)

        db = MagicMock()
        db.query.return_value.filter_by.return_value.order_by.return_value.first.return_value = entry
        result = lookup("repo123", "session-abc", "feature", repo, db)
        assert result is None  # stale — must be discarded

    def test_inject_ltm_prepends_block(self):
        entry = self._make_ltm_entry("Authentication")
        original = "=== REPO CONTEXT ===\nsome context"
        result = inject_ltm(original, entry)
        # inject_ltm prepends with a leading newline — strip before asserting
        assert "=== LONG-TERM MEMORY" in result
        assert "JWT-based auth." in result
        assert "=== REPO CONTEXT ===" in result
        # The LTM block must come BEFORE the original content
        ltm_pos = result.index("=== LONG-TERM MEMORY")
        original_pos = result.index("=== REPO CONTEXT ===")
        assert ltm_pos < original_pos

    def test_lookup_db_exception_returns_none(self):
        """DB errors in lookup must be silently swallowed — pipeline must not crash."""
        db = MagicMock()
        db.query.side_effect = Exception("connection reset")
        result = lookup("repo123", "session-abc", "feature", self._make_repo(), db)
        assert result is None


# ── History Formatter ─────────────────────────────────────────────────────────

from src.pipeline.history_formatter import format_history, format_history_with_summary


class TestHistoryFormatter:
    def test_empty_returns_empty_string(self):
        assert format_history([]) == ""

    def test_single_user_turn(self):
        class T:
            role = "user"
            content = "How does login work?"
        result = format_history([T()])
        assert "User: How does login work?" in result

    def test_mixed_turns(self):
        class T:
            def __init__(self, r, c):
                self.role = r
                self.content = c
        result = format_history([
            T("user", "How does auth work?"),
            T("assistant", "It uses JWT."),
            T("user", "What about refresh tokens?"),
        ])
        assert "User: How does auth work?" in result
        assert "Assistant: It uses JWT." in result
        assert "User: What about refresh tokens?" in result

    def test_dict_turns(self):
        turns = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello"},
        ]
        result = format_history(turns)
        assert "User: Hi" in result
        assert "Assistant: Hello" in result

    def test_with_summary_only(self):
        result = format_history_with_summary("Prior context about auth.", [])
        assert "Prior context about auth." in result
        assert "[Conversation summary so far]" in result

    def test_with_recent_turns_only(self):
        turn = MagicMock()
        turn.role = "user"
        turn.content = "Follow up question"
        # recent_turns is intentionally ignored — only the summary is used.
        # With no summary, result is empty.
        result = format_history_with_summary(None, [turn])
        assert result == ""

    def test_with_both_summary_and_turns(self):
        turn = MagicMock()
        turn.role = "assistant"
        turn.content = "Answer to follow up"
        # recent_turns ignored — only summary injected into prompt
        result = format_history_with_summary("Summary of prior discussion.", [turn])
        assert "Summary of prior discussion." in result


# ── Schema backward compatibility ─────────────────────────────────────────────

from src.api.schemas import AskRequest, ConversationTurn


class TestAskRequestSchema:
    def test_minimal_request_backward_compatible(self):
        """Existing clients that send only 'query' must still work."""
        req = AskRequest(query="What does init_db do?")
        assert req.session_id is None
        assert req.conversation_id is None
        assert req.conversation_history == []
        assert req.top_k == 10
        assert req.model is None

    def test_full_request_with_all_fields(self):
        req = AskRequest(
            query="How does auth work?",
            top_k=5,
            session_id="sess-abc",
            conversation_id="conv-xyz",
            conversation_history=[
                ConversationTurn(role="user", content="What is AuthService?"),
                ConversationTurn(role="assistant", content="It handles auth."),
            ],
        )
        assert req.session_id == "sess-abc"
        assert req.conversation_id == "conv-xyz"
        assert len(req.conversation_history) == 2
        assert req.conversation_history[0].role == "user"
        assert req.conversation_history[1].content == "It handles auth."

    def test_conversation_turn_roles(self):
        t1 = ConversationTurn(role="user", content="test")
        t2 = ConversationTurn(role="assistant", content="response")
        assert t1.role == "user"
        assert t2.role == "assistant"
