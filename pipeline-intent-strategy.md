# Pipeline Intent & Strategy Reference

How the query planner classifies every user question and what happens next.

---

## Overview

Every request goes through the **Query Planner** before any retrieval happens.
The planner makes two decisions:

1. **Intent** — *what kind of question is this?*
2. **Strategy** — *how should we retrieve context to answer it?*

The planner uses `groq/llama-3.1-8b-instant` (fast, ~200–400ms overhead) and
falls back to `gemini-2.5-flash-lite` if Groq is unavailable. On any failure it
defaults to `intent=query, strategy=semantic_search` so the pipeline never stalls.

---

## Intents

### `feature`
**What it means:** The user is asking about a specific class, function, module, or named feature.

**Example queries:**
- "How does the `AuthService` class work?"
- "What does `generate_embeddings()` do?"
- "Explain the `PaymentProcessor`"

**What happens:** Routed to `semantic_search` or `semantic_search_with_graph` depending on
whether the question involves relationships. Vector search finds the most semantically
similar entities to the query. The Answer Agent synthesises an explanation.

---

### `dependency_flow`
**What it means:** The user wants to understand how components connect, call each other,
or data flows through the system.

**Example queries:**
- "How does the login request flow through the system?"
- "What calls `validate_token()`?"
- "Trace the execution from `main.py` to the database"
- "How does `UserRepository` interact with `AuthService`?"

**What happens:** Routed to `semantic_search_with_graph`. After vector search finds seed
entities, the graph expander traverses CALLS, IMPORTS, INHERITS, and IMPLEMENTS edges to
pull in callers, callees, parents, and related entities. The LLM sees the full call chain,
not just the entry point.

---

### `repository_overview`
**What it means:** The user wants a high-level architectural summary of the repo.

**Example queries:**
- "Give me an overview of this codebase"
- "How is this repo structured?"
- "What does this project do?"
- "Summarise the architecture"

**What happens:** Routed to `repository_walk`. Bypasses vector search entirely —
instead does a BFS traversal of the file graph from the detected entry point,
ranks files by entry score, pulls module-level entities from the top 10 files,
and builds an architecture hint. The LLM sees the overall shape of the project.

---

### `repository_detailed`
**What it means:** The user wants a thorough walkthrough of the entire codebase, not
just an overview.

**Example queries:**
- "Walk me through the entire codebase"
- "Give me a detailed explanation of everything in this repo"
- "Explain the full project end-to-end"

**What happens:** Same as `repository_overview` — routed to `repository_walk` with
the same full-graph traversal. The planner distinguishes this intent for potential
future use (e.g. deeper traversal depth), but currently executes identically.

---

### `specific_lookup`
**What it means:** The user is looking for an exact symbol name, file, or specific
code location.

**Example queries:**
- "Where is `CONFIG_PATH` defined?"
- "Show me the `database.py` file"
- "Find the `__init__` method of `APIClient`"

**What happens:** Routed to `semantic_search`. The search query is tightened to the
exact symbol name. Vector search finds the closest matching entity. No graph expansion
needed since the question is about a specific location, not a relationship.

---

### `query` *(fallback)*
**What it means:** The planner couldn't confidently classify the query, or the planner
itself failed.

**Example queries:** Anything ambiguous, malformed, or where the planner returned an
invalid intent.

**What happens:** Routed to `semantic_search` with the original query unchanged. Safe
default that always produces a result.

---

## Strategies

### `semantic_search`
**Used for:** `feature`, `specific_lookup`, `query` (fallback)

**How it works:**
1. Takes the (possibly rewritten) search query
2. Generates a query embedding via Voyage AI (`voyage-code-3`)
3. Runs pgvector cosine similarity search against all indexed entity embeddings
4. Returns the top-K most semantically similar entities (default K=10)
5. Each entity goes through basic graph expansion (immediate callers/callees only)
6. Context is assembled and passed to the Answer Agent

**When the planner rewrites the query:** For conversational queries ("how does auth
work?"), the planner rewrites to a keyword-dense phrase ("authentication flow JWT
token validation") to improve embedding match quality.

---

### `semantic_search_with_graph`
**Used for:** `dependency_flow`

**How it works:**
1. Same vector search as `semantic_search` to find seed entities
2. Passes results through the full **relationship expander** which traverses:
   - **CALLS** — functions/methods this entity calls
   - **CALLERS** — functions/methods that call this entity (reverse CALLS)
   - **INHERITS** — parent classes
   - **IMPLEMENTS** — implemented interfaces
   - **IMPORTS** — modules imported by this file
   - **CONTAINS** — child entities within the same file
3. All traversed entities are deduplicated and ranked
4. The context window is filled with this richer, relationship-aware set
5. The Answer Agent sees full call chains, not just isolated entities

**Why this matters:** A question like "how does auth work?" needs not just the
`authenticate()` function but also what it calls, what calls it, and which
classes it belongs to. `semantic_search_with_graph` surfaces all of that.

---

### `repository_walk`
**Used for:** `repository_overview`, `repository_detailed`

**How it works:**
1. Calls `build_file_graph(repo_id)` — builds the full file-level dependency graph
2. Detects entry points (files with high in-degree or explicit markers like `main.py`,
   `app.py`, `__init__.py` in root)
3. BFS-traverses from the top entry point (depth=3) to find the reachable file subgraph
4. Ranks files by entry score (how many other files import/call them)
5. Fetches module-level entities + top functions/classes from the 10 highest-ranked files
6. Builds an architecture summary hint: `"Repository has N files and M edges. Top entry
   points: ... Key files: ..."`
7. Converts everything into `RetrievalResult` objects with synthetic scores so the
   graph expander and context builder work unchanged

**No vector search.** The query is irrelevant for this strategy — the goal is to surface
the structural shape of the project, not semantically match a question.

---

## Re-retrieval Loop

After the Answer Agent runs, it can trigger additional retrieval passes (max 2) by
returning one of two statuses:

### `insufficient`
**What it means:** The agent has some relevant context but is missing a specific piece
to complete the answer.

**What the agent returns:**
```json
{
  "status": "insufficient",
  "reason": "Missing downstream call chain for JWTService",
  "missing": {"type": "dependency_flow", "entity": "JWTService"},
  "partial_answer": "..."
}
```

**What happens:**
- Extracts `missing.entity` (e.g. `"JWTService"`)
- Runs a new vector search for that entity name (top-K=5)
- Filters out entities already seen (deduplication via `stm.visited_entity_ids`)
- Runs graph expansion on new results
- Merges into the existing context
- Retries the Answer Agent with the enriched context

---

### `rewrite_search`
**What it means:** The retrieved entities are completely unrelated to the question —
the search landed in the wrong part of the codebase.

**What the agent returns:**
```json
{
  "status": "rewrite_search",
  "reason": "Entities are about logging, not authentication",
  "rewrite_query": "authentication JWT token user login credentials"
}
```

**What happens:**
- Takes `rewrite_query` as the new search term
- Runs a fresh vector search with the better query
- Filters out already-seen entities
- Merges and retries

---

## Full Pipeline Flow (with intent/strategy annotated)

```
User: "How does the login flow work?"
          │
          ▼
    Query Planner
    ├── intent: dependency_flow
    ├── strategy: semantic_search_with_graph
    └── search_query: "login flow authentication user credentials"
          │
          ▼
    Vector Search (top 10 results)
    → finds: authenticate(), UserService, SessionManager, ...
          │
          ▼
    Graph Expander (CALLS + CALLERS + INHERITS + IMPORTS)
    → adds: validate_token(), db.query_user(), JWT.sign(), ...
          │
          ▼
    LTM Check
    → hit? inject cached "Authentication" knowledge block
    → miss? continue with fresh context
          │
          ▼
    Answer Agent (attempt 0)
    → status: "insufficient" — missing: {entity: "TokenBlacklist"}
          │
          ▼
    Targeted Re-retrieval
    → search: "TokenBlacklist"
    → new entities: TokenBlacklist.is_revoked(), BlacklistStore
          │
          ▼
    Answer Agent (attempt 1)
    → status: "answered"
    → writes LTM: {feature: "login_flow", confidence: "high", ...}
          │
          ▼
    Citation Validation
    → 7 citations: 6 definition, 1 call-site, 0 unsupported
          │
          ▼
    Save turn to DB (authenticated users)
    → maybe_summarize() if > 20 unsummarised turns
          │
          ▼
    AskResponse → Frontend
```

---

## Planner Confidence & Safety Rules

| Situation | What happens |
|---|---|
| Confidence < 0.3 and strategy = `repository_walk` | Downgraded to `semantic_search` (safer default) |
| Planner returns unknown intent | Normalised to `"query"` |
| Planner returns unknown strategy | Normalised to `"semantic_search"` |
| Planner model call fails (timeout, quota) | Entire planner skipped, default plan used |
| Planner returns `"repository_walk"` as the *intent* | Normalised to `"repository_overview"` (small models confuse strategy/intent) |

---

## Log Reference

Every pipeline run emits these structured log lines (see `pipeline.log`):

```
PIPELINE START            repo=...  query='...'
PIPELINE [STM@init]       intent=query  strategy=semantic_search  visited=0  chunks=0
PIPELINE [0-HISTORY]      source=db|client|none  turns=N  has_summary=True|False
PIPELINE [1-PLAN]         intent=...  strategy=...  confidence=0.90  search_query='...'
PIPELINE [STM@post-plan]  intent=dependency_flow  strategy=semantic_search_with_graph  ...
PIPELINE [2-RETRIEVE]     strategy=...  results=10
PIPELINE [3-EXPAND]       entities=10  tokens_est=4082  truncated=False
PIPELINE [STM@post-expand] visited=10  chunks=10
PIPELINE [4-LTM READ]     outcome=hit|miss|stale|skipped  feature=...
PIPELINE [4-LTM WRITE]    feature=...  confidence=high  status=complete
PIPELINE [5-DISPATCH]     attempt=0  model=...  provider=groq  ctx_tokens=1200
PIPELINE [5-LLM RESP]     provider=groq  status=answered  chars=2490
PIPELINE [RE-RETRIEVE]    attempt=1  new_entities=3  reason='...'
PIPELINE [STM@post-reretrieval-1]  visited=13  chunks=13  iterations=1
PIPELINE [STM@final]      status=answered  answer_chars=2041
PIPELINE [6-CITE]         total=7  definition=6  call_site=1  unsupported=0
PIPELINE [7-TURN SAVE]    role=user  conv_id=...
PIPELINE [7-TURN SAVE]    role=assistant  conv_id=...
PIPELINE DONE             status=answered  provider=groq  citations=7  total_ms=8200
```

Set `PIPELINE_LOG_LEVEL=DEBUG` in `.env` to also see full prompts, raw LLM output,
complete STM field dumps, and conversation history text.
