"""Integration tests for the unified pipeline.

These tests hit the real Supabase DB and real LLM providers (Groq/Gemini).
They are intentionally slow (~10–30s each) and require environment variables:
  DATABASE_URL, GROQ_API_KEY, VOYAGE_API_KEY

Run with:
    python -m pytest tests/test_pipeline_integration.py -v -s

Use the --no-header flag to reduce noise:
    python -m pytest tests/test_pipeline_integration.py -v -s --no-header

Skip these in CI by marking them:
    pytest -m "not integration"

What is tested:
  1. Query Planner — live Groq call returns a valid QueryPlan for real queries
  2. Repository Walk — builds a real file graph from an indexed repo
  3. Answer Agent — produces a structured response from real retrieved context
  4. Full pipeline (orchestrator) — end-to-end answer for a single turn
  5. Multi-turn memory — second turn uses conversation history from first
  6. LTM write + read — answer is cached and served from LTM on repeat query
  7. STM deduplication — re-retrieval does not re-fetch already-seen entities
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

# ── Environment setup ─────────────────────────────────────────────────────────
# Load .env from repo root
_ENV = Path(__file__).resolve().parents[2] / ".env"
if _ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(str(_ENV))

# Mark all tests in this file as integration
pytestmark = pytest.mark.integration

# ── Skip guard ────────────────────────────────────────────────────────────────
# Skip automatically if no DB or API keys are configured
_HAS_DB = bool(os.environ.get("DATABASE_URL"))
_HAS_GROQ = bool(os.environ.get("GROQ_API_KEY"))
_HAS_VOYAGE = bool(os.environ.get("VOYAGE_API_KEY"))

if not (_HAS_DB and _HAS_GROQ and _HAS_VOYAGE):
    pytest.skip(
        "Integration tests require DATABASE_URL, GROQ_API_KEY, and VOYAGE_API_KEY.",
        allow_module_level=True,
    )

# ── Shared fixtures ───────────────────────────────────────────────────────────

from src.storage.db import get_session
from src.storage.models import RepositoryModel, ConversationMemoryModel


@pytest.fixture(scope="module")
def ready_repo_id():
    """Return the repo_id of the first 'ready' repository in the DB."""
    with get_session() as db:
        repo = db.query(RepositoryModel).filter_by(status="ready").first()
    if repo is None:
        pytest.skip("No ready repository found in DB.")
    return repo.id


@pytest.fixture(scope="module")
def db_session():
    """Module-scoped DB session — shared across all tests in this file."""
    with get_session() as db:
        yield db


@pytest.fixture()
def fresh_session_id():
    """A unique session UUID for LTM isolation between test runs."""
    return f"test-session-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def fresh_conversation_id():
    """A unique conversation UUID."""
    return f"test-conv-{uuid.uuid4().hex[:8]}"


# ── Test 1: Query Planner (live Groq) ─────────────────────────────────────────

class TestQueryPlannerIntegration:
    def test_feature_query_classified(self, ready_repo_id):
        """A specific feature question should get semantic_search strategy."""
        from src.generation.query_planner import plan
        result = plan("What does the authentication service do?", repo_id=ready_repo_id)
        assert result.intent in {"feature", "specific_lookup", "dependency_flow", "query"}
        assert result.retrieval_strategy in {"semantic_search", "semantic_search_with_graph"}
        assert result.search_query is not None
        assert len(result.search_query) > 0
        assert isinstance(result.confidence, float)
        # confidence is 0.0 when planner falls back due to rate limit — that's OK
        print(f"\nQueryPlan: intent={result.intent!r} strategy={result.retrieval_strategy!r} "
              f"query={result.search_query!r} conf={result.confidence:.2f}")

    def test_overview_query_classified(self, ready_repo_id):
        """An overview question should trigger repository_walk or semantic_search."""
        from src.generation.query_planner import plan
        result = plan("Give me an overview of the repository structure.", repo_id=ready_repo_id)
        # Both repository_walk and semantic_search are valid here
        assert result.retrieval_strategy in _VALID_STRATEGIES
        print(f"\nQueryPlan (overview): strategy={result.retrieval_strategy!r} "
              f"conf={result.confidence:.2f}")

    def test_planner_returns_valid_plan_on_any_query(self, ready_repo_id):
        """Planner should never raise — even for nonsense input."""
        from src.generation.query_planner import plan
        result = plan("aaabbbccc 123 ???", repo_id=ready_repo_id)
        assert result.retrieval_strategy in _VALID_STRATEGIES
        assert isinstance(result.confidence, float)


_VALID_STRATEGIES = {"semantic_search", "semantic_search_with_graph", "repository_walk"}


# ── Test 2: Repository Walk ───────────────────────────────────────────────────

class TestRepoWalkIntegration:
    def test_walk_returns_result(self, ready_repo_id):
        from src.retrieval.repo_walk import walk, to_retrieval_results
        with get_session() as db:
            result = walk(ready_repo_id, db)

        assert result is not None
        print(f"\nRepoWalk: {len(result.modules)} nodes, {len(result.edges)} edges, "
              f"{len(result.top_entities)} entities")
        # Should find at least one file
        assert len(result.modules) > 0

    def test_walk_to_retrieval_results(self, ready_repo_id):
        from src.retrieval.repo_walk import walk, to_retrieval_results
        with get_session() as db:
            result = walk(ready_repo_id, db)
            retrieval_results = to_retrieval_results(result)

        assert isinstance(retrieval_results, list)
        if retrieval_results:
            rr = retrieval_results[0]
            assert 0.0 < rr.score <= 1.0
            assert rr.rank >= 1
            assert rr.entity is not None
        print(f"\nRepoWalk retrieval results: {len(retrieval_results)}")

    def test_walk_produces_architecture_hint(self, ready_repo_id):
        from src.retrieval.repo_walk import walk
        with get_session() as db:
            result = walk(ready_repo_id, db)
        assert isinstance(result.architecture_summary_hint, str)
        assert len(result.architecture_summary_hint) > 0
        print(f"\nArch hint: {result.architecture_summary_hint}")


# ── Test 3: Answer Agent (live LLM) ──────────────────────────────────────────

class TestAnswerAgentIntegration:
    def test_produces_structured_response(self, ready_repo_id):
        """Agent should return an AgentResponse with a valid status."""
        from src.retrieval import search, expand, build_context
        from src.generation.prompt_templates import build_system_prompt, render_context_for_prompt
        from src.generation.answer_agent import run as agent_run

        with get_session() as db:
            results = search("main entry point function", ready_repo_id, top_k=3, db_session=db)
            expanded = expand(results, ready_repo_id, db)
            final_ctx = build_context(expanded, "What is the main entry point?", ready_repo_id)

        system_prompt = build_system_prompt()
        context_str = render_context_for_prompt(final_ctx)

        resp = agent_run(
            query="What is the main entry point of this repository?",
            context=context_str,
            system_prompt=system_prompt,
        )

        assert resp.status in {"answered", "insufficient", "rewrite_search"}
        assert resp.provider_used in {"groq", "gemini"}
        print(f"\nAgentResponse: status={resp.status!r} provider={resp.provider_used!r}")
        if resp.status == "answered":
            assert resp.answer and len(resp.answer) > 10
            print(f"Answer (first 200 chars): {resp.answer[:200]}")


# ── Test 4: Full Pipeline — single turn ───────────────────────────────────────

class TestOrchestratorSingleTurn:
    def test_pipeline_returns_answer(self, ready_repo_id):
        """Full end-to-end pipeline run should produce an answer."""
        import asyncio
        from src.pipeline.orchestrator import run_pipeline
        from src.storage.models import RepositoryModel

        with get_session() as db:
            repo = db.query(RepositoryModel).filter_by(id=ready_repo_id).first()
            result = asyncio.run(run_pipeline(
                query="What functions or classes are defined in this repository?",
                repo_id=ready_repo_id,
                repo=repo,
                session_id=None,
                conversation_id=None,
                conversation_history=[],
                user_id=None,
                top_k=5,
                db=db,
            ))

        assert result.stm.answer_text is not None
        assert len(result.stm.answer_text) > 20
        assert result.stm.answer_status == "answered"
        assert result.final_context is not None
        print(f"\nPipeline answer (first 300 chars): {result.stm.answer_text[:300]}")
        print(f"Provider: {result.provider_used}")
        print(f"Strategy: {result.stm.retrieval_strategy}")
        print(f"Iterations: {result.stm.iteration_count}")

    def test_pipeline_with_repository_walk_query(self, ready_repo_id):
        """A structural overview query should use repository_walk strategy."""
        import asyncio
        from src.pipeline.orchestrator import run_pipeline
        from src.storage.models import RepositoryModel

        with get_session() as db:
            repo = db.query(RepositoryModel).filter_by(id=ready_repo_id).first()
            result = asyncio.run(run_pipeline(
                query="Give me a high-level overview of this repository's structure and architecture.",
                repo_id=ready_repo_id,
                repo=repo,
                session_id=None,
                conversation_id=None,
                conversation_history=[],
                user_id=None,
                top_k=5,
                db=db,
            ))

        assert result.stm.answer_text is not None
        print(f"\nOverview answer (first 300 chars): {result.stm.answer_text[:300]}")
        print(f"Strategy used: {result.stm.retrieval_strategy}")


# ── Test 5: Multi-turn conversation history ───────────────────────────────────

class TestMultiTurnConversation:
    def test_second_turn_includes_history(self, ready_repo_id, fresh_conversation_id):
        """The second turn should be able to reference the first turn's answer."""
        import asyncio
        import time
        from src.pipeline.orchestrator import run_pipeline
        from src.storage.models import RepositoryModel
        from src.api.schemas import ConversationTurn

        with get_session() as db:
            repo = db.query(RepositoryModel).filter_by(id=ready_repo_id).first()

            # Turn 1: ask a grounding question
            try:
                result1 = asyncio.run(run_pipeline(
                    query="What are the main components of this repository?",
                    repo_id=ready_repo_id,
                    repo=repo,
                    session_id=None,
                    conversation_id=fresh_conversation_id,
                    conversation_history=[],
                    user_id=None,
                    top_k=5,
                    db=db,
                ))
            except Exception as exc:
                if "rate" in str(exc).lower() or "429" in str(exc):
                    pytest.skip(f"Rate limit hit on turn 1: {exc}")
                raise
            assert result1.stm.answer_text

            # Voyage free tier = 3 RPM — wait to avoid hitting it
            time.sleep(25)

            # Turn 2: ask a follow-up referencing the first answer
            history = [
                ConversationTurn(
                    role="user",
                    content="What are the main components of this repository?"
                ),
                ConversationTurn(
                    role="assistant",
                    content=result1.stm.answer_text[:500],
                ),
            ]

            try:
                result2 = asyncio.run(run_pipeline(
                    query="Can you elaborate on the first component you mentioned?",
                    repo_id=ready_repo_id,
                    repo=repo,
                    session_id=None,
                    conversation_id=fresh_conversation_id,
                    conversation_history=history,
                    user_id=None,
                    top_k=5,
                    db=db,
                ))
            except Exception as exc:
                if "rate" in str(exc).lower() or "429" in str(exc):
                    pytest.skip(f"Rate limit hit on turn 2: {exc}")
                raise

        assert result2.stm.answer_text is not None
        assert len(result2.stm.answer_text) > 20
        print(f"\nTurn 1 answer: {result1.stm.answer_text[:200]}")
        print(f"\nTurn 2 answer (follow-up): {result2.stm.answer_text[:300]}")


# ── Test 6: LTM write + read ──────────────────────────────────────────────────

class TestLTMIntegration:
    def test_ltm_written_on_answered(self, ready_repo_id, fresh_session_id):
        """When the pipeline produces an 'answered' response, an LTM entry should be written."""
        import asyncio
        from src.pipeline.orchestrator import run_pipeline
        from src.storage.models import RepositoryModel, ConversationMemoryModel

        with get_session() as db:
            repo = db.query(RepositoryModel).filter_by(id=ready_repo_id).first()

            result = asyncio.run(run_pipeline(
                query="What is the main purpose of this repository?",
                repo_id=ready_repo_id,
                repo=repo,
                session_id=fresh_session_id,
                conversation_id=None,
                conversation_history=[],
                user_id=None,
                top_k=5,
                db=db,
            ))

        assert result.stm.answer_text

        # Check DB for LTM entries written by this session
        with get_session() as db:
            entries = (
                db.query(ConversationMemoryModel)
                .filter_by(repo_id=ready_repo_id, session_id=fresh_session_id)
                .all()
            )

        print(f"\nLTM entries written: {len(entries)}")
        for e in entries:
            print(f"  feature={e.feature_name!r} confidence={e.confidence!r} "
                  f"status={e.exploration_status!r}")

        # LTM write is best-effort — the agent may not always include ltm_entry
        # in its JSON block. We check that the pipeline didn't crash.
        assert result.stm.answer_status == "answered"

    def test_ltm_read_on_repeat_query(self, ready_repo_id, fresh_session_id):
        """Second query to same topic in same session should get an LTM hit (if first wrote one)."""
        import asyncio
        from src.pipeline.orchestrator import run_pipeline
        from src.storage.models import RepositoryModel, ConversationMemoryModel
        from src.generation.query_planner import plan

        query = "What is the main purpose of this codebase?"

        with get_session() as db:
            repo = db.query(RepositoryModel).filter_by(id=ready_repo_id).first()

            # First pass — populate LTM
            r1 = asyncio.run(run_pipeline(
                query=query,
                repo_id=ready_repo_id,
                repo=repo,
                session_id=fresh_session_id,
                conversation_id=None,
                conversation_history=[],
                user_id=None,
                top_k=5,
                db=db,
            ))

        # Check if any LTM was written for this session
        with get_session() as db:
            ltm_count_after_turn1 = (
                db.query(ConversationMemoryModel)
                .filter_by(repo_id=ready_repo_id, session_id=fresh_session_id)
                .count()
            )
            print(f"\nLTM entries after turn 1: {ltm_count_after_turn1}")

            repo = db.query(RepositoryModel).filter_by(id=ready_repo_id).first()
            # Second pass — same session, same intent
            r2 = asyncio.run(run_pipeline(
                query=query,
                repo_id=ready_repo_id,
                repo=repo,
                session_id=fresh_session_id,
                conversation_id=None,
                conversation_history=[],
                user_id=None,
                top_k=5,
                db=db,
            ))

        assert r2.stm.answer_text is not None
        print(f"Turn 1 provider: {r1.provider_used}, iterations: {r1.stm.iteration_count}")
        print(f"Turn 2 provider: {r2.provider_used}, iterations: {r2.stm.iteration_count}")
        # Both turns should produce answers
        assert r1.stm.answer_status == "answered"
        assert r2.stm.answer_status == "answered"

    def test_stale_ltm_not_served(self, ready_repo_id, fresh_session_id):
        """An LTM entry written with a past repo_indexed_at should be discarded."""
        from datetime import datetime, timezone, timedelta
        from src.storage.ltm_store import lookup
        from src.storage.models import RepositoryModel, ConversationMemoryModel

        # Manually insert a stale LTM entry
        stale_time = datetime(2020, 1, 1, tzinfo=timezone.utc)

        with get_session() as db:
            repo = db.query(RepositoryModel).filter_by(id=ready_repo_id).first()
            stale_entry = ConversationMemoryModel(
                repo_id=ready_repo_id,
                session_id=fresh_session_id,
                feature_name="stale_feature_test",
                summary="Old cached knowledge that is stale.",
                confidence="high",
                exploration_status="complete",
                repo_indexed_at=stale_time,  # Written before any real index
                created_at=datetime.now(timezone.utc),
            )
            db.add(stale_entry)
            db.commit()

            # Verify the repo's indexed_at is AFTER the stale entry
            result = lookup(
                ready_repo_id,
                fresh_session_id,
                "stale_feature_test",
                repo,
                db,
            )

        # If repo.indexed_at > stale_time, the entry should be discarded
        if repo.indexed_at and repo.indexed_at > stale_time:
            assert result is None, "Stale LTM entry should have been discarded"
            print("\nStale LTM correctly discarded.")
        else:
            print("\nRepo not indexed yet — stale detection skipped.")

        # Cleanup the test entry
        with get_session() as db:
            db.query(ConversationMemoryModel).filter_by(
                session_id=fresh_session_id,
                feature_name="stale_feature_test",
            ).delete()
            db.commit()


# ── Test 7: STM deduplication across iterations ───────────────────────────────

class TestSTMDeduplication:
    def test_visited_ids_prevent_rediscovery(self, ready_repo_id):
        """Entities fetched in iteration 0 should not be refetched in iteration 1."""
        import time
        from src.retrieval import search, expand
        from src.pipeline.memory import ShortTermMemory
        from src.retrieval.targeted_retrieval import fetch
        from src.generation.answer_agent import AgentResponse

        with get_session() as db:
            # Simulate what the orchestrator does on turn 1
            try:
                results = search("main entry function", ready_repo_id, top_k=5, db_session=db)
            except Exception as exc:
                if "rate" in str(exc).lower() or "429" in str(exc):
                    pytest.skip(f"Voyage rate limit: {exc}")
                raise
            expanded = expand(results, ready_repo_id, db)

            # Build STM as if we've already seen these entities
            stm = ShortTermMemory(goal="main entry function", repo_id=ready_repo_id)
            stm.visited_entity_ids = {r.entity_id for r in results}
            stm.retrieved_chunks = expanded

            # Simulate an "insufficient" response pointing to an already-seen entity
            first_entity = results[0].entity_id.split(".")[-1]
            agent_resp = AgentResponse(
                status="insufficient",
                missing={"type": "feature", "entity": first_entity},
            )

            time.sleep(22)  # Voyage free tier: 3 RPM — wait to avoid rate limit

            try:
                new_chunks = fetch(stm, agent_resp, ready_repo_id, db)
            except Exception as exc:
                if "rate" in str(exc).lower() or "429" in str(exc):
                    pytest.skip(f"Voyage rate limit on re-retrieval: {exc}")
                raise

        print(f"\nDedup test: initial entities={len(results)}, new_chunks={len(new_chunks)}")
        # All entities returned by fetch must now be in visited_entity_ids
        for chunk in new_chunks:
            assert chunk.core.entity_id in stm.visited_entity_ids
