# Unified Query Pipeline — Implementation Plan

## Feasibility Review & Corrections

Before the plan, these are the gaps between the original design and the actual codebase, and why the plan below differs.

---

### Correction 1 — `repository_walk` does not exist yet

The original design listed `repository_walk` as a retrieval strategy the planner could select. The only retrieval mechanism in the codebase today is pgvector cosine search (`vector_search.py`). There is no "repository walk" path wired into the ask pipeline.

**What does exist:** `build_file_graph()`, `detect_entry_points()`, and BFS traversal in `graph/traversal.py`. These are used exclusively by the graph API endpoint — they are not connected to the ask pipeline at all.

**Fix:** `repository_walk` becomes a new module (`src/retrieval/repo_walk.py`) that wraps the existing file graph infrastructure. It is a real capability that needs to be built, not assumed.

---

### Correction 2 — The two-phase sequential design is replaced by strategy-driven dispatch

The original design read: "Phase 2 activates when Phase 1 cannot produce a complete answer." This creates a waterfall where every query goes through a full Phase 1 attempt before Phase 2 can even begin.

**Fix:** The planner decides the full agent combination upfront. All agents run as part of one unified pipeline — there are no phases. The planner routes directly to the right strategy. An `"insufficient"` response triggers targeted re-retrieval within the same pipeline pass, not a mode switch.

---

### Correction 3 — Feature Agent and Graph Agent should not be separate LLM calls

The original design described these agents as LLM-backed. The existing retrieval pipeline (`relationship_expander.py`) already does what they are described to do: it traverses CALLS, INHERITS, IMPORTS, and CONTAINS edges from the graph DB. Wrapping this in a separate LLM call adds cost and latency for no benefit, since graph traversal is deterministic.

**Fix:** Graph exploration and feature context building are **deterministic code modules** reusing the existing expansion infrastructure. Only the Query Planner and Answer/Summary Agent require LLM calls.

---

### Correction 4 — LTM requires a new DB table; the schema does not have one today

The schema today has: `repositories`, `entities`, `relationships`, `users`, `user_repos`. There is no session, conversation, or memory table.

**Fix:** A new `conversation_memory` table is added via an Alembic migration. The LTM read/write is gated behind the presence of a `session_id` in the request — the field is optional, so the change is fully backward compatible.

---

### Correction 5 — STM is a plain Python dataclass, not a LangGraph state

LangGraph is not installed and is not needed. The pipeline passes an STM dataclass through each stage as a plain Python object. This is idiomatic given the existing codebase style.

---

### Correction 6 — Structured LLM output needs JSON mode, not free-form

The Answer Agent in the design returns structured JSON (`"answered"`, `"insufficient"`, `"rewrite_search"`). Getting reliable structured output from an LLM requires either JSON mode or strong prompt constraints. Groq supports `response_format={"type": "json_object"}`. Gemini requires prompt-level enforcement. Both paths are handled in the implementation.

---

### Correction 7 — Re-retrieval loops must be capped

The `"insufficient"` branch in the Answer Agent can theoretically cycle indefinitely. The plan caps targeted re-retrieval at **2 iterations**. After that, the pipeline produces a best-effort answer from whatever is in STM.

---

## What Does Not Change

These parts of the codebase are untouched:

- Entire ingestion pipeline (`src/ingestion/pipeline.py`)
- pgvector search (`src/retrieval/vector_search.py`)
- Graph expansion (`src/retrieval/relationship_expander.py`)
- Context builder (`src/retrieval/context_builder.py`)
- Citation validator (`src/generation/citation_validator.py`)
- LLM client (`src/generation/llm_client.py`)
- Auth, rate limiting, all other routers
- The frontend (only the `session_id` field addition to `AskRequest`)

---

## Unified Pipeline Architecture

```
User Query + session_id
        │
        ▼
┌─────────────────────────────┐
│       Query Planner         │  ← groq/llama-3.1-8b-instant (fast, cheap)
│  intent + strategy + query  │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│     Strategy Dispatcher     │  ← deterministic, no LLM
└──────┬──────────┬───────────┘
       │          │
       ▼          ▼
 Vector Search   Repo Walk
 (existing)      (new module)
       │          │
       └────┬─────┘
            │
            ▼
┌─────────────────────────────┐
│    Graph Expansion          │  ← existing expand() + build_context()
│    STM population           │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│       LTM Check             │  ← DB lookup by repo_id + session_id + feature_name
│  inject cached knowledge    │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────┐
│                  Answer Agent                       │  ← LLM (Groq → Gemini)
│  Returns: answered | insufficient | rewrite_search  │
└──────────┬──────────────┬──────────────────────────-┘
           │              │
           │     insufficient / rewrite_search
           │              │
           │              ▼
           │    Targeted Re-retrieval     ← max 2 iterations
           │    (new entities merged      
           │     into STM, retry loop)   
           │              │
           │              ▼
           │    Answer Agent (retry)
           │              │
           └──────────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │   Write to LTM        │  ← if session_id present
        └───────────┬───────────┘
                    │
                    ▼
        ┌───────────────────────┐
        │  Citation Validation  │  ← existing validate_citations()
        └───────────┬───────────┘
                    │
                    ▼
              AskResponse
```

---

## Implementation Tasks

### Task 1 — Short-Term Memory (STM) dataclass

**File:** `src/pipeline/memory.py` (new)  
**Risk:** None — pure data structure, zero side effects  
**Dependencies:** None

A plain Python dataclass that holds the entire reasoning state for one request. Created at the start of each request, passed through each pipeline stage, discarded when the response is sent.

```python
@dataclass
class ShortTermMemory:
    goal: str                              # original user query
    intent: str                            # planner output
    retrieval_strategy: str                # planner output
    search_query: str | None               # rewritten query (may differ from goal)
    session_id: str | None                 # for LTM scoping
    repo_id: str

    visited_entity_ids: set[str]           # all entity IDs seen so far
    pending_entity_ids: set[str]           # entities flagged for expansion
    retrieved_chunks: list[ExpandedContext] # accumulated context across iterations
    intermediate_summaries: list[str]      # summaries written by agents

    answer_status: str                     # "answered" | "insufficient" | "rewrite_search"
    answer_text: str | None                # final answer text
    missing: dict | None                   # {"type": "...", "entity": "..."}
    rewrite_query: str | None              # new query if status is rewrite_search
    iteration_count: int = 0               # re-retrieval iteration counter
```

---

### Task 2 — Long-Term Memory (LTM) schema

**Files:**
- `src/storage/models.py` — add `ConversationMemoryModel`
- `platform/alembic/versions/0005_add_conversation_memory.py` — migration

**Risk:** Low — additive schema change. No existing query is modified.

#### New table: `conversation_memory`

| Column | Type | Notes |
|---|---|---|
| `id` | `Integer PK autoincrement` | |
| `repo_id` | `String FK → repositories` | Cascade delete |
| `session_id` | `String(128)` | Client-generated UUID |
| `feature_name` | `String(255)` | e.g. `"Authentication"`, `"PaymentService"` |
| `summary` | `Text` | Structured knowledge written by Answer Agent |
| `source_entity_ids` | `JSON` | List of entity IDs used to derive this entry |
| `graph_paths` | `JSON` | Relationship paths traversed |
| `confidence` | `String(20)` | `"high"` \| `"medium"` \| `"low"` |
| `exploration_status` | `String(20)` | `"partial"` \| `"complete"` |
| `repo_indexed_at` | `DateTime` | Copy of `repo.indexed_at` at write time |
| `created_at` | `DateTime` | |

#### Stale detection

Before using an LTM entry, compare `ltm_entry.repo_indexed_at` with `repo.indexed_at`. If the repo was re-indexed after the LTM entry was written, the entry is discarded and treated as a cache miss. This prevents outdated knowledge from surviving a re-index.

#### LTM read/write rules

- **Read:** before dispatching to the Answer Agent. Look up `(repo_id, session_id, feature_name)` where `exploration_status = "complete"` and `repo_indexed_at` matches.
- **Write:** after the Answer Agent produces an `"answered"` response with `exploration_status` it determines. Only write if `session_id` is present.
- **Skip entirely** if no `session_id` is provided (backward compatible with all existing clients).

---

### Task 3 — Query Planner module

**File:** `src/generation/query_planner.py` (new)  
**Risk:** Low — runs before the main pipeline. Failure defaults to `semantic_search`.  
**LLM used:** `groq/llama-3.1-8b-instant` (already in the model catalogue)

#### Responsibilities

1. Classify query intent into one of: `feature`, `dependency_flow`, `repository_overview`, `repository_detailed`, `specific_lookup`
2. Rewrite the query for code embeddings when the original uses conversational language
3. Select retrieval strategy: `semantic_search`, `semantic_search_with_graph`, `repository_walk`

#### Output: `QueryPlan` dataclass

```python
@dataclass
class QueryPlan:
    intent: str
    retrieval_strategy: str   # "semantic_search" | "semantic_search_with_graph" | "repository_walk"
    search_query: str | None  # None for repository_walk
    confidence: float         # 0.0–1.0; low confidence → default to semantic_search
```

#### Failure handling

If the planner LLM call fails for any reason (timeout, quota, parse error):
- Log the error
- Return a default `QueryPlan` with `intent="Query"`, `strategy="semantic_search"`, `search_query=original_query`
- The pipeline continues without interruption

#### Latency

The planner adds ~200–400ms per request using `llama-3.1-8b-instant`. This is acceptable since the main generation step takes 2–5s.

#### Strategy selection logic (in the planner prompt)

| Query characteristics | Strategy |
|---|---|
| Asks about a specific class, function, or feature | `semantic_search` |
| Asks about how things connect, flow, or interact | `semantic_search_with_graph` |
| Asks "how is this repo structured", "give me an overview" | `repository_walk` |
| Asks for a full walkthrough of the entire codebase | `repository_walk` |

---

### Task 4 — Repository Walk module

**File:** `src/retrieval/repo_walk.py` (new)  
**Risk:** Low — uses only existing infrastructure, no new DB queries  
**Dependencies:** `src/graph/file_graph.py`, `src/graph/traversal.py`, `src/graph/entry_points.py`

#### Responsibilities

Produces an architecture-level view of the repository without relying on a user query for vector search. Used when the planner selects `repository_walk`.

#### Steps

1. Call `build_file_graph(repo_id, db)` — already exists
2. Call `detect_entry_points(repo_id, db)` — already exists
3. Call `get_full_graph(graph)` or `traverse(graph, root_id, max_depth=3)` — already exists
4. Fetch module-level entities for the top N files (by entry score + entity count)
5. Return a `RepoWalkResult` dataclass

#### Output: `RepoWalkResult`

```python
@dataclass
class RepoWalkResult:
    entry_points: list[EntryPointResult]
    modules: list[FileNode]               # all discovered file nodes
    edges: list[FileEdge]                 # cross-file relationships
    top_entities: list[EntityModel]       # module-level entities for context
    architecture_summary_hint: str        # text hint passed to Answer Agent
```

#### Conversion to retrieval format

`RepoWalkResult` is converted into a list of `RetrievalResult` objects (wrapping the top module entities) so the rest of the pipeline — `expand()`, `build_context()` — can proceed without modification.

---

### Task 5 — Answer Agent with structured output

**File:** `src/generation/answer_agent.py` (new)  
**Risk:** Medium — replaces the bare LLM call in `ask.py`. Requires reliable JSON output.  
**Dependencies:** existing `generate_answer_with_fallback()`, `build_system_prompt()`

#### Structured output contract

The Answer Agent is instructed to return a JSON object inside a `<answer_json>` block:

```json
{
  "status": "answered",
  "answer": "..."
}
```

```json
{
  "status": "insufficient",
  "reason": "Missing downstream dependencies for complete execution flow.",
  "missing": {
    "type": "dependency_flow",
    "entity": "JWTService"
  },
  "partial_answer": "..."
}
```

```json
{
  "status": "rewrite_search",
  "reason": "Retrieved entities are unrelated to the requested feature.",
  "rewrite_query": "authentication login AuthService JWT credentials"
}
```

#### JSON extraction

- **Groq:** use `response_format={"type": "json_object"}` via LiteLLM to get native JSON mode
- **Gemini:** parse the `<answer_json>` block from the response text using regex fallback

#### Safety rule

If the parsed JSON does not contain `"insufficient"` or `"rewrite_search"`, treat it as `"answered"`. This prevents the re-retrieval loop from activating on a malformed response.

#### LTM knowledge extraction

When status is `"answered"`, the Answer Agent also returns:

```json
{
  "status": "answered",
  "answer": "...",
  "ltm_entry": {
    "feature_name": "Authentication",
    "confidence": "high",
    "exploration_status": "complete",
    "summary": "..."
  }
}
```

This `ltm_entry` is passed to the LTM writer. If absent, no LTM write occurs.

---

### Task 6 — Targeted re-retrieval module

**File:** `src/retrieval/targeted_retrieval.py` (new)  
**Risk:** Low — thin wrapper around existing `search()` and `expand()`  
**Dependencies:** `src/retrieval/vector_search.py`, `src/retrieval/relationship_expander.py`

#### Behaviour

Called when the Answer Agent returns `"insufficient"`.

1. Extract the `missing.entity` name from the Answer Agent response
2. Run `search(query=missing_entity_name, repo_id=..., top_k=5)` — existing function
3. Run `expand(results, repo_id, db_session)` — existing function
4. Filter out entity IDs already in `stm.visited_entity_ids`
5. Merge new `ExpandedContext` objects into `stm.retrieved_chunks`
6. Update `stm.visited_entity_ids` with the new entity IDs
7. Increment `stm.iteration_count`

Called when status is `"rewrite_search"`:
1. Use `stm.rewrite_query` as the new search query
2. Same steps 2–7 as above

#### Iteration cap

If `stm.iteration_count >= 2`, skip re-retrieval and pass the STM as-is to the Answer Agent. The Answer Agent will produce a best-effort answer from the accumulated context, with a note that some context may be missing.

---

### Task 7 — Pipeline Orchestrator

**File:** `src/pipeline/orchestrator.py` (new)  
**Risk:** Medium — this is the wiring layer; all logic is delegated to existing modules  
**Dependencies:** all modules above + existing retrieval and generation modules

#### Full orchestration flow

```python
async def run_pipeline(
    query: str,
    repo_id: str,
    session_id: str | None,
    top_k: int,
    db: Session,
    model_override: str | None,
) -> PipelineResult:

    # 1. Init STM
    stm = ShortTermMemory(goal=query, repo_id=repo_id, session_id=session_id, ...)

    # 2. Query Planner
    plan = query_planner.plan(query)
    stm.intent = plan.intent
    stm.retrieval_strategy = plan.retrieval_strategy
    stm.search_query = plan.search_query or query

    # 3. Initial Retrieval
    if plan.retrieval_strategy == "repository_walk":
        walk_result = repo_walk.walk(repo_id, db)
        results = walk_result.to_retrieval_results()
    else:
        results = search(stm.search_query, repo_id, top_k, db)

    # 4. Graph Expansion
    expanded = expand(results, repo_id, db)
    final_context = build_context(expanded, query, repo_id)
    stm.retrieved_chunks = expanded
    stm.visited_entity_ids = {r.entity_id for r in results}

    # 5. LTM Check
    ltm_knowledge = ltm_store.lookup(repo_id, session_id, plan.intent, repo)
    if ltm_knowledge:
        final_context = inject_ltm(final_context, ltm_knowledge)

    # 6. Answer Agent loop (max 3 total attempts: 1 initial + 2 re-retrieval)
    for _ in range(3):
        agent_response = answer_agent.run(query, final_context, system_prompt, ...)
        stm.answer_status = agent_response.status

        if agent_response.status == "answered":
            stm.answer_text = agent_response.answer
            # Write LTM
            if session_id and agent_response.ltm_entry:
                ltm_store.write(repo_id, session_id, agent_response.ltm_entry, repo)
            break

        if stm.iteration_count >= 2:
            stm.answer_text = agent_response.partial_answer or agent_response.answer
            break

        # Re-retrieval
        new_expanded = targeted_retrieval.fetch(
            stm, agent_response, repo_id, db
        )
        stm.retrieved_chunks.extend(new_expanded)
        final_context = build_context(stm.retrieved_chunks, query, repo_id)
        stm.iteration_count += 1

    # 7. Return
    return PipelineResult(stm=stm, final_context=final_context)
```

#### `PipelineResult` dataclass

```python
@dataclass
class PipelineResult:
    stm: ShortTermMemory
    final_context: FinalContext
```

---

### Task 8 — Refactor `ask.py` router

**File:** `src/api/routers/ask.py`  
**Risk:** Low — the router becomes a thin shim; all logic moves to the orchestrator

The router's responsibilities after this task:
1. Auth and repo status checks (unchanged)
2. Parse `session_id` from request body
3. Call `orchestrator.run_pipeline(...)`
4. Run citation validation on the result (unchanged)
5. Build and return `AskResponse` (unchanged)

The bulk of the current retrieval + LLM code in `ask.py` is removed and replaced by the single orchestrator call.

---

### Task 9 — Add `session_id` to request schema

**File:** `src/api/schemas.py`  
**Risk:** None — optional field with `None` default

```python
class AskRequest(BaseModel):
    query: str
    top_k: int = 10
    model: str | None = None
    session_id: str | None = None   # new optional field
```

No breaking change. Existing clients that omit `session_id` continue to work exactly as before; they simply do not get LTM reads/writes.

---

## Rollout Order

The tasks are ordered so each step is independently deployable and testable.

| Order | Task | Why this order |
|---|---|---|
| 1 | Task 1 — STM dataclass | Pure data structure, zero dependencies, no risk |
| 2 | Task 9 — `session_id` in schema | Optional field, backward compatible immediately |
| 3 | Task 2 — LTM schema + migration | Additive DB change, no behaviour change yet |
| 4 | Task 4 — Repo Walk module | New module, completely isolated from ask pipeline |
| 5 | Task 3 — Query Planner | Can be feature-flagged; failure defaults to existing path |
| 6 | Task 5 — Answer Agent | Replaces bare LLM call; backward compatible if it returns "answered" |
| 7 | Task 6 — Targeted re-retrieval | Only activates on "insufficient" responses |
| 8 | Task 7 — Orchestrator | Final wiring; ask.py becomes a shim |
| 9 | Task 8 — Refactor ask.py | Cleans up the router after orchestrator is validated |

---

## New Files Summary

```
src/
├── pipeline/
│   ├── __init__.py
│   ├── memory.py              # STM dataclass (Task 1)
│   └── orchestrator.py        # Pipeline orchestrator (Task 7)
├── generation/
│   ├── query_planner.py       # Query Planner LLM module (Task 3)
│   └── answer_agent.py        # Structured Answer Agent (Task 5)
├── retrieval/
│   ├── repo_walk.py           # Repository Walk module (Task 4)
│   └── targeted_retrieval.py  # Re-retrieval on insufficient (Task 6)
└── storage/
    └── ltm_store.py           # LTM read/write helper (Task 2 companion)

platform/alembic/versions/
└── 0005_add_conversation_memory.py   # LTM migration (Task 2)
```

---

## Modified Files Summary

| File | Change |
|---|---|
| `src/storage/models.py` | Add `ConversationMemoryModel` |
| `src/api/schemas.py` | Add optional `session_id` to `AskRequest` |
| `src/api/routers/ask.py` | Replace inline pipeline with `orchestrator.run_pipeline()` |

---

## Memory Growth Across Conversation

The LTM advantage compounds over turns within the same session.

| Turn | Query | Pipeline behaviour |
|---|---|---|
| 1 | "Explain Authentication" | Full exploration; LTM cache miss; writes entry on completion |
| 2 | "How does JWT work?" | LTM hit on Authentication; only JWT node needs new expansion |
| 3 | "Show the login flow" | Full LTM hit; Answer Agent answers from cache + minimal new retrieval |
| 4 | "How does Authentication interact with UserRepository?" | LTM hit for Authentication; targeted retrieval for the missing edge only |
| 5 | "Explain the Security module" | Most knowledge already in LTM; Answer Agent produces answer with near-zero new retrieval |

For a new session (or when `session_id` is absent), the pipeline behaves exactly as it does today — no regression.

---

## Conversation History — Design Extension

> **What the original plan does not cover:** the pipeline above handles codebase knowledge (LTM) but every request is still stateless from the dialogue perspective. A follow-up question like "what about the part you mentioned earlier?" has no prior turns to refer to. This section adds persistent conversation history for authenticated users and session-scoped history for anonymous users.

### Two user types, two storage strategies

```
Authenticated user (user_id present in JWT)
  → Turns stored in DB (conversations + conversation_turns tables)
  → Survives browser close, device switch, long gaps
  → Older turns summarised when turn count exceeds threshold
  → Summary injected into Answer Agent prompt instead of raw turns

Anonymous / temporary user (no user_id)
  → Turns stored in browser localStorage only
  → Frontend sends last N turns with every request as conversation_history[]
  → Backend is fully stateless for these users — no DB writes
  → Session ends when localStorage is cleared or conversation is reset
```

### Updated pipeline architecture (with conversation history)

```
User Query + conversation_id + conversation_history[]
        │
        ▼
┌──────────────────────────────────────┐
│  Conversation History Loader         │
│                                      │
│  if authenticated + conversation_id: │
│    load summary + recent turns (DB)  │
│  else:                               │
│    use conversation_history[] as-is  │
└──────────────────┬───────────────────┘
                   │  history_text injected into system prompt
                   ▼
          (rest of pipeline unchanged)
                   │
                   ▼
┌──────────────────────────────────────┐
│  Save Turn (authenticated only)      │
│  save user query + assistant answer  │
│  → maybe_summarize() if needed       │
└──────────────────────────────────────┘
```

---

### Task 10 — Conversation persistence schema

**Files:**
- `src/storage/models.py` — add `ConversationModel`, `ConversationTurnModel`
- `platform/alembic/versions/0006_add_conversations.py` — migration

**Risk:** Low — additive schema change, no existing query modified.

#### New table: `conversations`

| Column | Type | Notes |
|---|---|---|
| `id` | `String(128) PK` | UUID generated by the frontend, stable per conversation |
| `user_id` | `String(64) FK → users nullable` | `NULL` for anonymous (rows never written for anonymous) |
| `repo_id` | `String(255) FK → repositories` | Cascade delete |
| `summary` | `Text nullable` | Rolling summary of old turns, updated by summarisation job |
| `summarized_through_turn` | `Integer default 0` | Turn index up to which the summary covers |
| `created_at` | `DateTime` | |
| `updated_at` | `DateTime` | Updated on every new turn |

#### New table: `conversation_turns`

| Column | Type | Notes |
|---|---|---|
| `id` | `Integer PK autoincrement` | |
| `conversation_id` | `String(128) FK → conversations` | Cascade delete |
| `turn_index` | `Integer` | Sequential, 0-based, per conversation |
| `role` | `String(20)` | `"user"` \| `"assistant"` |
| `content` | `Text` | Message text |
| `created_at` | `DateTime` | |

Index on `(conversation_id, turn_index)` for ordered loading.

---

### Task 11 — Extend `AskRequest` with conversation fields

**File:** `src/api/schemas.py`  
**Risk:** None — both fields are optional with safe defaults.

```python
class ConversationTurn(BaseModel):
    role: str     # "user" | "assistant"
    content: str

class AskRequest(BaseModel):
    query: str
    top_k: int = 10
    model: str | None = None
    session_id: str | None = None               # existing (Task 9) — LTM scoping
    conversation_id: str | None = None          # NEW — stable UUID per conversation
    conversation_history: list[ConversationTurn] = []  # NEW — last N turns from client
```

- `conversation_id` is the UUID the frontend already generates for each `Conversation` object. The same value is sent on every turn of that conversation.
- `conversation_history` is populated by the frontend on every request — it is the authoritative history source for anonymous users and a client-side cache for authenticated users.
- Both fields default to safe no-ops. Existing callers that omit them continue to work exactly as before.

---

### Task 12 — Conversation store service

**File:** `src/storage/conversation_store.py` (new)  
**Risk:** Low — isolated service, no changes to existing modules.

#### Three responsibilities

**load_history** — called by the orchestrator before the Answer Agent

```python
def load_history(
    conversation_id: str,
    user_id: str,
    db: Session,
) -> tuple[str | None, list[ConversationTurnModel]]:
    """Return (summary_text | None, unsummarised_turns_after_summary)."""
```

Returns a summary paragraph (if one exists) plus the turns that follow it. These are formatted as a `<conversation_history>` block and injected into the Answer Agent's system prompt.

**save_turn** — called by `ask.py` after a successful response, only for authenticated users

```python
def save_turn(
    conversation_id: str,
    user_id: str,
    repo_id: str,
    role: str,
    content: str,
    db: Session,
) -> None:
    """Upsert conversation row; insert new turn with next turn_index."""
```

**maybe_summarize** — called after `save_turn`

```python
def maybe_summarize(
    conversation_id: str,
    db: Session,
    llm_client,
) -> None:
    """If unsummarised turn count > SUMMARIZE_THRESHOLD, compress to summary."""
```

#### Summarisation logic

- Threshold: `CONVERSATION_SUMMARIZE_THRESHOLD` env var, default `20` turns (10 user/assistant exchanges).
- When triggered, calls the fast model (`groq/llama-3.1-8b-instant`) with a condensation prompt.
- Prompt instructs the model to produce a compact summary paragraph of the topics discussed, decisions made, and code areas referenced — without reproducing raw code blocks.
- After writing `conversation.summary`, sets `summarized_through_turn` to the last compressed turn index.
- Keeps the most recent 6 turns (3 exchanges) unsummarised for recency — these are passed to the Answer Agent as raw turns alongside the summary.
- Runs synchronously after the response is returned to the user, so it never adds latency to the current request.

#### Token budget

What the Answer Agent sees per request:

```
<conversation_history>
[Summary paragraph — ~200 tokens]
User: ...  (turn N-2)
Assistant: ...  (turn N-2)
User: ...  (turn N-1)
Assistant: ...  (turn N-1)
User: ...  (turn N — current query, already in prompt)
</conversation_history>
```

Maximum overhead: ~800 tokens for the history block regardless of how long the conversation has been running.

---

### Task 13 — Inject conversation history into the orchestrator

**File:** `src/pipeline/orchestrator.py`  
**Risk:** Low — additive change to the orchestrator, no existing logic altered.

Add a history-loading step at the top of `run_pipeline()`:

```python
async def run_pipeline(
    query: str,
    repo_id: str,
    session_id: str | None,
    conversation_id: str | None,          # NEW
    conversation_history: list[ConversationTurn],  # NEW
    user_id: str | None,                  # NEW
    top_k: int,
    db: Session,
    model_override: str | None,
) -> PipelineResult:

    # ── Conversation history ─────────────────────────────────────────────────
    if user_id and conversation_id:
        # Authenticated: load from DB (summary + recent unsummarised turns)
        summary, recent_turns = conversation_store.load_history(
            conversation_id, user_id, db
        )
        history_text = format_history_with_summary(summary, recent_turns)
    elif conversation_history:
        # Anonymous: use what the client sent directly
        history_text = format_history(conversation_history)
    else:
        history_text = ""

    # history_text is appended to the system_prompt as a <conversation_history> block
    # before it is passed to the Answer Agent — no change to retrieval path
```

`format_history` and `format_history_with_summary` are small helpers in `src/pipeline/history_formatter.py` (new, ~30 lines).

---

### Task 14 — Persist turns after response (authenticated users)

**File:** `src/api/routers/ask.py`  
**Risk:** None — runs after the response is already assembled; any failure is caught and logged without affecting the returned `AskResponse`.

```python
# After pipeline_result is obtained and AskResponse is assembled:
if current_user and body.conversation_id:
    try:
        conversation_store.save_turn(
            conversation_id=body.conversation_id,
            user_id=current_user.id,
            repo_id=repo_id,
            role="user",
            content=body.query,
            db=db,
        )
        conversation_store.save_turn(
            conversation_id=body.conversation_id,
            user_id=current_user.id,
            repo_id=repo_id,
            role="assistant",
            content=pipeline_result.stm.answer_text or "",
            db=db,
        )
        conversation_store.maybe_summarize(body.conversation_id, db, llm_client)
    except Exception as exc:
        logger.warning("Failed to persist conversation turn: %s", exc)
        # Do not re-raise — persistence failure must not break the response
```

Anonymous users: nothing saved. Their `conversation_history` list travels in the request body and is managed entirely by the frontend.

---

### Task 15 — Frontend wiring

**Files:**
- `frontend/types/chat.ts` — add `sessionId` to `Conversation`
- `frontend/types/api.ts` — add `conversation_id` and `conversation_history` to `AskRequest`
- `frontend/store/chat-store.ts` — pass history on every `askRepository` call
- `frontend/lib/api.ts` — no changes needed (fields pass through transparently)

#### `Conversation` type change

```typescript
export interface Conversation {
  id: string;          // this is also used as conversation_id in AskRequest
  repoId: string;
  repoName: string;
  repoUrl: string;
  messages: ChatMessage[];
  createdAt: number;
  updatedAt: number;
}
// conversation_id sent to backend = conv.id — no new field needed
```

#### `AskRequest` type change

```typescript
export interface AskRequest {
  query: string;
  top_k?: number;
  model?: string;
  conversation_id?: string;
  conversation_history?: { role: string; content: string }[];
}
```

#### `chat-store.ts` — building history before each call

When `askRepository()` is called, serialize the current conversation messages into `conversation_history`:

```typescript
// Serialize last MAX_HISTORY_TURNS turns (user + assistant only, no errors/loading)
const MAX_HISTORY_TURNS = 20; // 10 exchanges — caps token overhead

const historyMessages = conv.messages
  .filter((m) => m.role === "user" || m.role === "assistant")
  .filter((m): m is UserMessage | AssistantMessage => !("loading" in m && m.loading))
  .slice(-MAX_HISTORY_TURNS)
  .map((m) => ({ role: m.role, content: m.content }));

// Passed as conversation_history in the AskRequest payload
```

On `clearConversation()` — a new `uid()` produces a new `conv.id`, which means a new `conversation_id` on the next request. Both LTM and conversation history start fresh automatically.

---

## Updated Rollout Order

Tasks 10–15 are independent of tasks 1–9 up to task 13 (which requires the orchestrator from task 7). Tasks 10–12 and 15 can be built in parallel with the pipeline tasks.

| Order | Task | Depends on | Risk |
|---|---|---|---|
| 1 | Task 1 — STM dataclass | — | None |
| 2 | Task 9 — `session_id` in schema | — | None |
| 3 | Task 2 — LTM schema + migration | — | Low |
| 4 | Task 10 — Conversation schema + migration | — | Low |
| 5 | Task 11 — Conversation fields in AskRequest | Task 9 | None |
| 6 | Task 12 — Conversation store service | Task 10 | Low |
| 7 | Task 4 — Repo Walk module | — | Low |
| 8 | Task 3 — Query Planner | — | Low |
| 9 | Task 5 — Answer Agent | Task 3 | Medium |
| 10 | Task 6 — Targeted re-retrieval | Task 5 | Low |
| 11 | Task 7 — Orchestrator | Tasks 1–6 | Medium |
| 12 | Task 13 — Inject history into orchestrator | Tasks 7, 12 | Low |
| 13 | Task 14 — Persist turns in ask.py | Tasks 8, 12 | None |
| 14 | Task 8 — Refactor ask.py | Task 7 | Low |
| 15 | Task 15 — Frontend wiring | Task 11 | Low |

---

## Updated New Files Summary

```
src/
├── pipeline/
│   ├── __init__.py
│   ├── memory.py                  # STM dataclass (Task 1)
│   ├── orchestrator.py            # Pipeline orchestrator (Task 7)
│   └── history_formatter.py       # format_history helpers (Task 13)
├── generation/
│   ├── query_planner.py           # Query Planner LLM module (Task 3)
│   └── answer_agent.py            # Structured Answer Agent (Task 5)
├── retrieval/
│   ├── repo_walk.py               # Repository Walk module (Task 4)
│   └── targeted_retrieval.py      # Re-retrieval on insufficient (Task 6)
└── storage/
    ├── ltm_store.py               # LTM read/write helper (Task 2 companion)
    └── conversation_store.py      # Conversation history service (Task 12)

platform/alembic/versions/
├── 0005_add_conversation_memory.py   # LTM migration (Task 2)
└── 0006_add_conversations.py         # Conversation history migration (Task 10)
```

## Updated Modified Files Summary

| File | Change |
|---|---|
| `src/storage/models.py` | Add `ConversationMemoryModel`, `ConversationModel`, `ConversationTurnModel` |
| `src/api/schemas.py` | Add `session_id`, `conversation_id`, `conversation_history` to `AskRequest` |
| `src/api/routers/ask.py` | Call orchestrator; persist turns after response |
| `src/pipeline/orchestrator.py` | Accept and inject conversation history |
| `frontend/types/chat.ts` | No structural change needed — `conv.id` reused as `conversation_id` |
| `frontend/types/api.ts` | Add `conversation_id`, `conversation_history` to `AskRequest` |
| `frontend/store/chat-store.ts` | Serialize and pass history on every ask call |
