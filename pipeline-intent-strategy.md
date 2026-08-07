# Pipeline Intent & Strategy Reference

How the query planner classifies every user question and what the system
does differently for each case.

---

## Overview

Every request goes through the **Query Planner** before any retrieval.
The planner makes two decisions:

1. **Intent** — *what kind of question is this?*
2. **Strategy** — *how should the system retrieve and process context?*

The planner uses `groq/llama-3.1-8b-instant` (~200–400ms overhead) and
falls back to `gemini-2.5-flash-lite` if Groq is unavailable. On any failure
it defaults to `intent=query, strategy=semantic_search` so the pipeline
never stalls.

---

## Intents

### `feature`

The user asks about a specific class, function, module, or named feature.

**Example queries:**
- "How does the `AuthService` class work?"
- "What does `generate_embeddings()` do?"
- "Explain the `PaymentProcessor`"

**Routed to:** `semantic_search` or `semantic_search_with_graph`

---

### `dependency_flow`

The user wants to understand how components connect, call each other, or
how data flows through the system.

**Example queries:**
- "How does the login request flow through the system?"
- "What calls `validate_token()`?"
- "Trace the execution from `main.py` to the database"
- "How does `UserRepository` interact with `AuthService`?"

**Routed to:** `semantic_search_with_graph`

---

### `repository_overview`

The user wants a high-level architectural summary — what the project does
and how it is structured.

**Example queries:**
- "Give me an overview of this codebase"
- "How is this repo structured?"
- "What does this project do?"
- "Summarise the architecture"

**Routed to:** Hierarchical overview pipeline (see below)

---

### `repository_detailed`

The user wants a thorough, file-by-file or folder-by-folder walkthrough of
the entire codebase.

**Example queries:**
- "Walk me through the entire codebase"
- "Give me a detailed explanation of everything in this repo"
- "Explain the full project end-to-end"

**Routed to:** Hierarchical overview pipeline (same as `repository_overview`
but the Repo Summary Agent is instructed to produce section-per-folder depth)

---

### `specific_lookup`

The user is looking for an exact symbol, file, or code location.

**Example queries:**
- "Where is `CONFIG_PATH` defined?"
- "Show me the `database.py` file"
- "Find the `__init__` method of `APIClient`"

**Routed to:** `semantic_search`

---

### `query` *(fallback)*

The planner could not confidently classify the query, or the planner itself
failed.

**Routed to:** `semantic_search` with the original query unchanged.

---

## Strategies

### `semantic_search`

**Used for:** `feature`, `specific_lookup`, `query`

1. Planner rewrites the query to a keyword-dense phrase for embedding match
2. Voyage AI (`voyage-code-3`) generates the query embedding
3. pgvector cosine search returns the top-K entities (default K=10)
4. Basic graph expansion — immediate callers/callees only
5. Context assembled → Answer Agent

---

### `semantic_search_with_graph`

**Used for:** `dependency_flow`

1. Same vector search as `semantic_search`
2. Full **relationship expander** traverses:
   - **CALLS** / **CALLERS** — call graph in both directions
   - **INHERITS** — parent classes
   - **IMPLEMENTS** — implemented interfaces
   - **IMPORTS** — module dependencies
   - **CONTAINS** — child entities within the same file
3. All traversed entities deduplicated and ranked
4. Context window filled with the relationship-rich set
5. Answer Agent sees full call chains, not just isolated entities

---

### Hierarchical Overview Pipeline

**Used for:** `repository_overview`, `repository_detailed`

Bypasses vector search entirely. Uses three agents in sequence with LTM
caching at every level to avoid re-running on repeat requests.

#### Step-by-step

```
All files in DB
      │
      ▼
[3-LTM READ] full-repo cache check
      │ HIT → return cached answer immediately (no agents run)
      │ MISS ↓
      ▼
[3-LTM READ] per-folder cache check (one check per folder)
      │ HIT  → load cached folder summary, skip File Agents for that folder
      │ MISS ↓
      ▼
[FILE-AGENT] × N files  (async batches of 5, llama-3.1-8b-instant)
  Each file → 2-4 sentence summary with [file:start-end] citations
      │
      ▼
[FOLDER-AGENT] × M folders  (llama-3.1-8b-instant)
  Each folder → 3-5 sentence aggregate summary, citations preserved
      │
[5-LTM WRITE] folder summary cached per folder
      │
      ▼
[OVERVIEW] assembled: all folder summaries + architecture hint
      │
[5-DISPATCH] Repo Summary Agent (gemini-2.5-flash)
      │   Context = folder summaries only (~150 tokens each)
      │   Brief mode  → 4-6 paragraph overview with citations
      │   Detailed mode → section-per-folder with deep citations
      │
[5-LLM RESP]
      │
[7-LTM WRITE] full repo answer cached
```

#### Why hierarchical?

Raw source of all files never fits in a context window. The hierarchy
compresses at each level:

| Level | Input | Output per item |
|---|---|---|
| File Agent | ~300 tok raw source | ~80 tok summary + citations |
| Folder Agent | N × 80 tok file summaries | ~150 tok folder summary |
| Repo Agent | M × 150 tok folder summaries | Final answer |

A 50-file, 8-folder repo → ~1200 tokens at the Repo Agent level. Always fits.

#### LTM cache hierarchy

Second request for the same repo overview is nearly instant:

```
repo_overview LTM hit  → skip everything, return cached answer
      │ miss ↓
folder:X LTM hit       → skip File Agents for folder X
folder:Y LTM miss      → run File Agents for folder Y only
      │
Folder Agent for Y only
Repo Agent (uses mix of cached + fresh folder summaries)
```

Stale detection: if the repo was re-indexed, all LTM entries for it are
discarded automatically (via `repo_indexed_at` comparison).

#### Citations in overview answers

File Agents are explicitly prompted to use `[file_path:start-end]` format
matching `EntityModel.file_path` exactly. Citations flow up through Folder
Agents and into the Repo Agent's final answer unchanged. The standard citation
validator runs on the final answer — overview answers get real, clickable
source links in the frontend, identical to feature queries.

---

## Citation Correction Agent

Runs after every `validate_citations()` call when `unsupported_citations > 0`.

**Pass 1 — Deterministic (no LLM):**
- For each bad citation with a known `nearest_entity_id`, look up the real
  entity's file_path and line range in the DB
- Do a direct string replacement: `[wrong/path:99-110]` → `[auth/service.py:45-89]`
- Zero latency, zero API calls for line-range and path-prefix mistakes

**Pass 2 — LLM (only if Pass 1 leaves uncorrected citations):**
- For citations where the file path was not in context at all
- Sends the affected paragraph + valid entity list to `llama-3.1-8b-instant`
- Asks it to replace or remove each marked citation

**Safety guard:** If the corrected answer has a *higher* hallucination rate
than the original, the original is returned unchanged.

**Result:** Frontend always receives the corrected answer. `unsupported_citations`
in the response reflects the post-correction state.

---

## Re-retrieval Loop (standard queries only)

After the Answer Agent runs, it can trigger up to 2 re-retrieval passes:

### `insufficient`

Agent has partial context but is missing something specific.

```json
{
  "status": "insufficient",
  "reason": "Missing downstream call chain for JWTService",
  "missing": {"type": "dependency_flow", "entity": "JWTService"},
  "partial_answer": "..."
}
```

Pipeline: search for `missing.entity` → expand → merge → retry Agent.

### `rewrite_search`

Retrieved entities are completely unrelated to the question.

```json
{
  "status": "rewrite_search",
  "reason": "Entities are about logging, not authentication",
  "rewrite_query": "authentication JWT token user login credentials"
}
```

Pipeline: use `rewrite_query` for a fresh search → merge → retry Agent.

Both statuses cap at 2 iterations. After that the Agent is forced to produce
a best-effort answer from whatever is in the STM.

---

## Full Flow Examples

### Standard query — `dependency_flow`

```
User: "How does the login flow work?"
         │
[1-PLAN] intent=dependency_flow  strategy=semantic_search_with_graph
         │
[2-RETRIEVE] vector search → authenticate(), UserService, SessionManager
         │
[3-EXPAND] graph traversal → validate_token(), db.query_user(), JWT.sign()
         │
[4-LTM READ] miss → fresh context
         │
[5-DISPATCH] attempt=0  groq/llama-3.3-70b  ctx_tokens=3200
         │
[5-LLM RESP] status=insufficient  missing={entity: "TokenBlacklist"}
         │
[RE-RETRIEVE] search "TokenBlacklist" → 3 new entities
         │
[STM@post-reretrieval-1] visited=13  chunks=13
         │
[5-DISPATCH] attempt=1  groq/llama-3.3-70b  ctx_tokens=3800
         │
[5-LLM RESP] status=answered
         │
[4-LTM WRITE] feature=dependency_flow  confidence=high
         │
[STM@final]
         │
[6-CITE] total=7  definition=6  call_site=1  unsupported=0
         │
[7-TURN SAVE] role=user / role=assistant  (authenticated users)
         │
PIPELINE DONE  citations=7  total_ms=9800
```

### Overview query — `repository_overview`

```
User: "Give me an overview of this repo"
         │
[1-PLAN] intent=repository_overview  strategy=repository_walk
         │
[2-RETRIEVE] strategy=repository_overview  results=0  (no vector search)
         │
[3-LTM READ] outcome=miss  feature=repo_overview
[3-LTM READ] outcome=hit   feature=folder:src/api     (cached)
[3-LTM READ] outcome=miss  feature=folder:src/generation
         │
[FILE-AGENT] src/generation/answer_agent.py  tokens=380  400ms
[FILE-AGENT] src/generation/query_planner.py  tokens=290  360ms
[FILE-AGENT] src/generation/llm_client.py  tokens=510  430ms
         │
[FOLDER-AGENT] folder=src/generation  files=3  390ms
[5-LTM WRITE] feature=folder:src/generation  step=5
         │
[OVERVIEW] file_summaries=12  folder_summaries=5  visited=47
         │
[5-DISPATCH] attempt=0  model=gemini-2.5-flash  ctx_tokens=1840  task=repo_summary
[5-LLM RESP] provider=gemini  status=answered  chars=4200
         │
[3-EXPAND]   entities=47  tokens_est=1050
[STM@post-expand]
         │
[7-LTM WRITE] feature=repo_overview  step=7
[STM@final]
         │
[6-CITE] total=12  definition=12  call_site=0  unsupported=0
[7-TURN SAVE] (authenticated users)
         │
PIPELINE DONE  citations=12  total_ms=6800
```

---

## Planner Safety Rules

| Situation | What happens |
|---|---|
| Confidence < 0.3 and strategy = `repository_walk` | Downgraded to `semantic_search` |
| Unknown intent returned | Normalised to `"query"` |
| Unknown strategy returned | Normalised to `"semantic_search"` |
| Planner LLM call fails entirely | Default plan used, pipeline continues |
| Model returns `"repository_walk"` as the intent field | Normalised to `"repository_overview"` |

---

## Complete Log Reference

### Standard query

```
PIPELINE START
PIPELINE [STM@init]                 intent=query  strategy=semantic_search  visited=0
PIPELINE [0-HISTORY]                source=db|client|none  turns=N  has_summary=True|False
PIPELINE [1-PLAN]                   intent=...  strategy=...  confidence=0.90
PIPELINE [STM@post-plan]
PIPELINE [2-RETRIEVE]               strategy=semantic_search_with_graph  results=10
PIPELINE [3-EXPAND]                 entities=10  tokens_est=4082  truncated=False
PIPELINE [STM@post-expand]
PIPELINE [4-LTM READ]               outcome=hit|miss|stale  feature=...
PIPELINE [4-LTM WRITE]              feature=...  confidence=high  status=complete
PIPELINE [5-DISPATCH]               attempt=0  model=...  provider=groq  ctx_tokens=3200
PIPELINE [5-LLM RESP]               provider=groq  status=answered  chars=2490
PIPELINE [RE-RETRIEVE]              attempt=1  new_entities=3  reason='...'
PIPELINE [STM@post-reretrieval-1]   visited=13  chunks=13  iterations=1
PIPELINE [STM@final]                status=answered  answer_chars=2041
PIPELINE [6-CITE]                   total=7  definition=6  call_site=1  unsupported=0
PIPELINE [6-CORRECT]                original_unsupported=1  corrections_made=1  remaining=0
PIPELINE [7-TURN SAVE]              role=user   conv_id=...
PIPELINE [7-TURN SAVE]              role=assistant  conv_id=...
PIPELINE DONE                       status=answered  provider=groq  citations=7  total_ms=8200
```

### Overview query

```
PIPELINE START
PIPELINE [STM@init]
PIPELINE [0-HISTORY]
PIPELINE [1-PLAN]                   intent=repository_overview  strategy=repository_walk
PIPELINE [STM@post-plan]
PIPELINE [2-RETRIEVE]               strategy=repository_overview  results=0
PIPELINE [3-LTM READ]               outcome=miss  feature=repo_overview
PIPELINE [3-LTM READ]               outcome=hit   feature=folder:src/api
PIPELINE [3-LTM READ]               outcome=miss  feature=folder:src/generation
PIPELINE [FILE-AGENT]               file=src/generation/answer_agent.py  tokens=380
PIPELINE [FILE-AGENT]               file=src/generation/query_planner.py  tokens=290
PIPELINE [FOLDER-AGENT]             folder=src/generation  files=3  cache=False
PIPELINE [5-LTM WRITE]              feature=folder:src/generation  step=5
PIPELINE [OVERVIEW]                 file_summaries=12  folder_summaries=5  visited=47
PIPELINE [5-DISPATCH]               attempt=0  model=gemini-2.5-flash  task=repo_summary
PIPELINE [5-LLM RESP]               provider=gemini  status=answered  chars=4200
PIPELINE [3-EXPAND]                 entities=47  tokens_est=1050
PIPELINE [STM@post-expand]
PIPELINE [7-LTM WRITE]              feature=repo_overview  step=7
PIPELINE [STM@final]                status=answered  answer_chars=4200
PIPELINE [6-CITE]                   total=12  definition=12  unsupported=0
PIPELINE [7-TURN SAVE]              role=user
PIPELINE [7-TURN SAVE]              role=assistant
PIPELINE DONE                       status=answered  provider=gemini  citations=12  total_ms=6800
```

Set `PIPELINE_LOG_LEVEL=DEBUG` in `.env` for full prompts, raw LLM output,
complete STM dumps, and conversation history text.
