# EasyRepo — Complete Query Pipeline Deep Dive

> Everything that happens from the moment a user types a question to the moment a cited answer
> lands on their screen — including what changes on the second query.

---

## Table of Contents

1. [The Big Picture](#1-the-big-picture)
2. [Entry Point — HTTP to Job Queue](#2-entry-point--http-to-job-queue)
3. [Pipeline Boot — STM Initialization](#3-pipeline-boot--stm-initialization)
4. [Conversation History Loading](#4-conversation-history-loading)
5. [LTM Loading (Authenticated Users)](#5-ltm-loading-authenticated-users)
6. [Query Planner](#6-query-planner)
7. [Retrieval — Three Strategies](#7-retrieval--three-strategies)
8. [Graph Expansion](#8-graph-expansion)
9. [LTM Session Knowledge Check](#9-ltm-session-knowledge-check)
10. [Answer Agent Loop](#10-answer-agent-loop)
11. [Citation Validation](#11-citation-validation)
12. [Citation Correction Agent](#12-citation-correction-agent)
13. [Overview Pipeline (repository_overview / repository_detailed)](#13-overview-pipeline)
    - [File Summary Agent](#131-file-summary-agent)
    - [Folder Summary Agent](#132-folder-summary-agent)
    - [Repo Summary Agent](#133-repo-summary-agent)
    - [Caching in the Overview Pipeline](#134-caching-in-the-overview-pipeline)
14. [Post-Pipeline — Saving Turns and Updating Memory](#14-post-pipeline--saving-turns-and-updating-memory)
15. [What Changes on the Second Query](#15-what-changes-on-the-second-query)
16. [Memory System — Complete Reference](#16-memory-system--complete-reference)
17. [End-to-End Timeline Summary](#17-end-to-end-timeline-summary)

---

## 1. The Big Picture

```
Browser (Next.js)
    │
    │  POST /repositories/{id}/ask  (returns job_id in < 1s)
    ▼
FastAPI Router (ask.py)
    │
    │  Creates AskJobModel row (status=pending)
    │  Defers ask_pipeline_task via Procrastinate
    ▼
Procrastinate Worker (same process, async task)
    │
    ├─ [STM init]
    ├─ [History loading]  ─────────────────────────── DB rolling summary  (authenticated)
    │                                                  Client turns        (anonymous)
    ├─ [LTM loading]      ─────────────────────────── user_memory
    │                                                  user_repo_preferences
    │                                                  repo_user_memory
    ├─ [Query Planner]    ─────────────────────────── llama-3.1-8b-instant (Groq fast tier)
    ├─ [Retrieval]        ─────────────────────────── pgvector cosine OR repo walk
    ├─ [Graph Expansion]  ─────────────────────────── BFS on CALLS/INHERITS/CONTAINS edges
    ├─ [LTM Session Check]────────────────────────── conversation_memory (Tier 3 cache)
    ├─ [Answer Agent Loop]────────────────────────── standard-tier LLM, max 3 attempts
    │     ├─ Inline citation validation
    │     └─ Targeted re-retrieval (if needed)
    ├─ [Overview Pipeline (if overview intent)]
    │     ├─ File Summary Agent  (batched, concurrent, LLM per file group)
    │     ├─ Folder Summary Agent (per folder)
    │     └─ Repo Summary Agent  (Gemini synthesis)
    ├─ [Citation Correction Agent]
    │     ├─ Pass 1: deterministic (DB entity lookup)
    │     └─ Pass 2: LLM (NVIDIA NIM → Gemini)
    ├─ [Save conversation turns]  ─────────────────── DB (authenticated only)
    └─ [summarize_after_turn]     ─────────────────── rolling summary + LTM extraction

    Job row: status=done, result=JSON
    │
    │  GET /repositories/{id}/ask/{job_id}  (frontend polls every 2s)
    ▼
Browser renders answer + citation panel
```

---

## 2. Entry Point — HTTP to Job Queue

**File:** `src/api/routers/ask.py`

### POST /repositories/{id}/ask

1. Auth check: `get_accessible_repository()` confirms the user can access this repo.
2. Repo status check: must be `"ready"` (ingestion complete).
3. Model override parsing: optional `body.model` field can force a specific provider:
   - `"groq:<model>"` → sets `force_groq_model`, skips Gemini
   - `"gemini:<model>"` → sets `force_gemini_model`, skips Groq
   - bare model name → matched against known Groq or Gemini names
4. Job row created: `AskJobModel(id=job_id, status="pending")` committed to DB.
5. `ask_pipeline_task.defer_async(...)` is called — this writes a Procrastinate job record
   into the `procrastinate_jobs` Postgres table.
6. Router returns `{job_id, status: "pending"}` immediately — well under the 30s API
   Gateway timeout.

### GET /repositories/{id}/ask/{job_id}  (polling)

Reads `AskJobModel` by job_id. Returns:
- `status: "pending"` / `"running"` / `"done"` / `"failed"`
- `progress: {pipeline, stage, files_done, files_total}` when running
- `result: AskResponse` (full JSON) when done

The frontend polls this every 2 seconds. The progress stages shown to the user are:
`classifying → searching → reading_files → insights → generating → citations`

---

## 3. Pipeline Boot — STM Initialization

**File:** `src/pipeline/orchestrator.py` → `run_pipeline()`

**File:** `src/memory/stm/short_term.py`

The very first thing `run_pipeline()` does is create a `ShortTermMemory` object:

```python
stm = ShortTermMemory(
    goal=query,          # original user question, never modified
    repo_id=repo_id,
    session_id=session_id,      # optional client UUID
    conversation_id=conversation_id,  # stable per-conversation UUID
)
```

The STM is a plain Python dataclass that acts as the **shared mutable blackboard** for the
entire pipeline. Every stage reads from it and writes back into it. It is **never persisted
to the database** — it lives only for the duration of this request.

Key fields and what populates them:

| STM Field | Populated by | Purpose |
|---|---|---|
| `goal` | init | Original question, passed to every LLM call |
| `intent` | Query Planner | e.g. `"feature"`, `"dependency_flow"`, `"repository_overview"` |
| `retrieval_strategy` | Query Planner | `"semantic_search"` / `"semantic_search_with_graph"` / `"repository_walk"` |
| `search_query` | Query Planner | Rewritten keyword-rich query for code embeddings |
| `visited_entity_ids` | Retrieval + Graph expansion | Deduplication across re-retrieval iterations |
| `retrieved_chunks` | Graph expansion | All `ExpandedContext` objects fetched so far |
| `answer_status` | Answer Agent | `"pending"` / `"answered"` / `"insufficient"` / `"rewrite_search"` |
| `answer_text` | Answer Agent | Final answer string |
| `validation_report` | Inline citation validator | `ValidationReport` from last validation pass |
| `citation_hit_rate` | Inline citation validator | Float 0–1, fraction of verified citations |
| `unsupported_entity_hints` | Inline citation validator | Entity IDs for re-retrieval targeting |
| `iteration_count` | Re-retrieval loop | How many extra fetches happened (max 2) |
| `raw_llm_responses` | Answer Agent | One raw string per LLM call, for debugging |
| `file_summaries` | Overview pipeline | `{file_path: summary}` populated by File Summary Agent |
| `folder_summaries` | Overview pipeline | `{folder: summary}` populated by Folder Summary Agent |
| `overview_from_cache` | Overview pipeline | `True` when full answer served from LTM |

---

## 4. Conversation History Loading

**File:** `src/memory/stm/working_memory.py` → `load_history()`  
**File:** `src/pipeline/history_formatter.py`

### Authenticated users — DB rolling summary

```python
summary, _ = load_history(conversation_id, user_id, db)
history_text = format_history_with_summary(summary, [])
```

`load_history()` queries `ConversationModel` by `(conversation_id, user_id)` and returns
only `conv.summary` — a **rolling LLM-generated summary** of all prior turns. Raw turns are
**never forwarded to the LLM**.

`format_history_with_summary()` wraps the summary:
```
[Conversation summary so far]
<summary text>
```

This text is later injected into the Answer Agent's system prompt as `<conversation_history>`.

On Q1 of a conversation, `summary` is `None` and `history_text` is empty.

### Anonymous users — client-sent turns

Anonymous users re-send their conversation history in every request body as
`conversation_history: [{role, content}, ...]`. `format_history()` formats them as raw
`User: ... / Assistant: ...` pairs. No compression or server-side persistence happens.

### Why only the summary is sent (not raw turns)

The rolling summary approach keeps the context window size **constant regardless of
conversation length**. By Q10 the prompt is no larger than Q2. This is the key design
decision that makes unbounded conversation depth practical without token overflow.

---

## 5. LTM Loading (Authenticated Users)

**Files:**
- `src/memory/ltm/user_memory.py`
- `src/memory/ltm/user_repo_preference.py`
- `src/memory/ltm/repo_user_memory.py`

For authenticated users, three LTM tiers are loaded upfront before any LLM call:

```python
user_memory_facts       = load_user_memory(user_id, db)
user_repo_pref_facts    = load_user_repo_preferences(user_id, repo_id, db)
repo_user_memory_facts  = load_repo_user_memory(user_id, repo_id, db)
```

| Tier | Table | Scope | What it stores | Example |
|---|---|---|---|---|
| **Tier 4** | `user_memory` | global (user-only) | User preferences, background, working style | `"Prefers functional style"`, `"Backend lead"` |
| **Tier 5** | `user_repo_preferences` | (user × repo) | How this user works with this specific codebase | `"Unfamiliar with the auth module"`, `"Focuses on API layer"` |
| **Tier 6** | `repo_user_memory` | (user × repo) | Hard facts about the codebase confirmed in past conversations | `"Auth uses JWT 24h expiry"`, `"Race condition in UserService.create()"` |

All three lists are passed into every Answer Agent call (see section 10). The Agent's
system prompt is augmented with three `## Long-term memory` sections. This means the
LLM knows your background and codebase facts **even on the first question of a new session**.

---

## 6. Query Planner

**File:** `src/agents/query_planner.py`

```
Input:  query string
Output: QueryPlan(intent, retrieval_strategy, search_query, confidence)
```

### Model

Primary: `groq/llama-3.1-8b-instant` (fastest, ~200–400ms overhead)  
Fallback 1: `gemini/gemini-2.5-flash-lite`  
Fallback 2: `nvidia_nim` fast tier  
Never uses `allam-2-7b` (poor JSON compliance for classification tasks)

### What it decides

**Intent** — what kind of question this is:

| Intent | Meaning |
|---|---|
| `feature` | About a specific class, function, or feature |
| `dependency_flow` | About connections, call chains, or how things interact |
| `repository_overview` | A summary/overview of the whole repo |
| `repository_detailed` | A full walkthrough of the entire codebase |
| `specific_lookup` | Find a specific symbol or exact code location |

**Retrieval strategy**:

| Strategy | When used | Mechanism |
|---|---|---|
| `semantic_search` | feature, specific_lookup | pgvector cosine similarity |
| `semantic_search_with_graph` | dependency_flow | pgvector + deep BFS graph expansion |
| `repository_walk` | repository_overview, repository_detailed | All files, hierarchical agent pipeline |

**Search query rewrite**: The planner also rewrites the user's question into a
keyword-rich phrase optimised for code embedding search. Filler words are removed
and relevant technical terms are added. E.g.:
- User: `"How does the login flow work?"`
- Rewritten: `"login flow authentication entry point"`

**Failure safe**: if the planner fails for any reason (timeout, quota, parse error),
the pipeline falls back to `intent="query", strategy="semantic_search",
search_query=original_query`. The pipeline is never blocked by a planner failure.

---

## 7. Retrieval — Three Strategies

**File:** `src/retrieval/vector_search.py`, `src/retrieval/repo_walk.py`

After the planner, retrieval branches into three paths:

### Path A — semantic_search / semantic_search_with_graph

```python
results = search(stm.search_query, repo_id, top_k, db)
```

The `search()` function:
1. Generates a query embedding using Voyage AI `voyage-code-3` (1024-dim)
2. Runs pgvector cosine similarity: `ORDER BY embedding <=> query_vec LIMIT top_k`
3. Returns `top_k` (default 10) `RetrievalResult` objects, each containing the full
   `EntityModel` row (with source code, line ranges, file path, type)

### Path B — repository_walk

```python
walk_result = walk(repo_id, db)
results = to_retrieval_results(walk_result)
```

Fetches a structural snapshot of the entire repo: entry point files, all file paths,
module entities, relationship counts. No embedding search. An architecture summary hint
is generated and stored in `stm.intermediate_summaries` to be prepended to the context.

### Path C — repository_overview / repository_detailed

These bypass retrieval entirely and jump straight to the **Overview Pipeline**
(Section 13). The orchestrator returns early from the normal flow.

### Tracking visited entities

After retrieval:
```python
stm.visited_entity_ids = {r.entity_id for r in results}
```

This deduplication set ensures that re-retrieval iterations in the Answer Agent loop
never fetch the same entity twice.

---

## 8. Graph Expansion

**File:** `src/retrieval/relationship_expander.py`, `src/retrieval/context_builder.py`

```python
expanded = expand(retrieved_results=results, repo_id=repo_id, db_session=db)
final_context = build_context(expanded_contexts=expanded, query=query, repo_id=repo_id)
```

### What expand() does

For each retrieved entity, BFS traversal follows relationship edges:

| Relationship | Direction | Depth |
|---|---|---|
| `CONTAINS` | parent → child | 3 |
| `CALLS` | both directions | 3 |
| `INHERITS` | both directions | 2 |
| `IMPLEMENTS` | both directions | 2 |
| `INSTANTIATES` | both directions | 2 |
| `IMPORTS` | both directions | 2 |

For `semantic_search_with_graph` the traversal is deeper (more hops) than for plain
`semantic_search`.

Each expanded `ExpandedContext` object wraps a `RetrievalResult` (core entity) plus all
related entities found via BFS. This is what lets the LLM understand call chains and
inheritance hierarchies, not just the single entity that was searched.

### What build_context() does

Renders all `ExpandedContext` objects into a structured text block for the LLM:
- Groups entities by file
- Shows source code + line ranges
- Respects a token budget (truncates if needed)
- If the repo_walk path produced an architecture summary, prepends it as
  `=== REPOSITORY ARCHITECTURE ===`

---

## 9. LTM Session Knowledge Check

**File:** `src/memory/ltm/session_knowledge.py`

```python
ltm_entry = ltm_lookup(repo_id, session_id, stm.intent, repo, db)
if ltm_entry:
    injected_text = inject_ltm(final_context.rendered_text, ltm_entry)
```

This is **Tier 3** of the memory system — a session-scoped feature cache.

`lookup()` searches `conversation_memory` for a row matching:
- `(repo_id, session_id, feature_name=stm.intent, exploration_status="complete")`

**Stale detection**: if `repo.indexed_at > entry.repo_indexed_at`, the entry was written
before the repo was last re-indexed. It is discarded (treated as a cache miss).

**On hit**: the cached summary is prepended to the context:
```
=== LONG-TERM MEMORY (from earlier in this session) ===
Feature: dependency_flow
Confidence: high  |  Exploration: complete
<summary text>
=== END LONG-TERM MEMORY ===
```

This means: if you asked about the auth flow in Q1 and the LLM learned something from it,
that knowledge is injected as extra context for Q3 when you ask a related question —
without re-doing the full retrieval.

**On miss**: pipeline proceeds normally. If the Answer Agent produces an LTM entry
at the end (see section 10), it will be written here for future use.

---

## 10. Answer Agent Loop

**File:** `src/pipeline/orchestrator.py` (loop),  `src/agents/code_qa_agent.py` (agent)

This is the core generation loop. It runs up to **3 times** (1 initial + 2 re-retrieval
retries) before producing a final answer.

### System prompt assembly

Before the first attempt, `_build_augmented_system_prompt()` in `code_qa_agent.py`
assembles the full system prompt by appending blocks in this order:

```
[Base system prompt from build_system_prompt()]
[# Long-term memory]
  ## User preferences & background       ← user_memory_facts (Tier 4)
  ## How this user works with this repo  ← user_repo_pref_facts (Tier 5)
  ## Known facts about this codebase     ← repo_user_memory_facts (Tier 6)
[# Conversation history]
  <conversation_history>
  [rolling summary or raw anonymous turns]
  </conversation_history>
[# REMINDER: Two-part response structure]
  ← tells LLM to produce prose + <answer_json> block
```

### Each attempt

```python
for attempt in range(_MAX_ITERATIONS + 1):  # 0, 1, 2
    context_str = render_context_for_prompt(final_context)
    agent_response = code_qa_agent.run(
        query=query,
        context=context_str,
        system_prompt=augmented_system,
        history_text=history_text,
        iteration=attempt,
        user_memory=...,
        user_repo_preferences=...,
        repo_user_memory=...,
    )
```

The LLM call goes through `generate_answer_with_fallback()` in `llm_client.py` using
`task_type="standard"` (or `"fast"` for very small contexts on first attempt).

### Response format

The LLM must produce two parts:

**Part 1** — Full Markdown prose with inline `[file.py:L-L]` citations.

**Part 2** — A `<answer_json>` block at the very end:

```json
// If answered:
{"status": "answered", "answer": "<1-sentence summary>",
 "ltm_entry": {"feature_name": "...", "confidence": "high",
               "exploration_status": "complete", "summary": "..."}}

// If insufficient context:
{"status": "insufficient", "reason": "...",
 "missing": {"type": "feature", "entity": "UserService"},
 "partial_answer": "..."}

// If wrong context retrieved:
{"status": "rewrite_search", "reason": "...", "rewrite_query": "..."}
```

### Status handling

**`answered`**: inline citation validation runs immediately (section 11). If citations
are good → LTM entry written → loop breaks. If 0 citations despite entities in context →
treated as `insufficient` and re-retrieval is triggered.

**`insufficient`**: `targeted_fetch(stm, agent_response, repo_id, db)` runs to fetch
the missing entity. Entity IDs from `stm.unsupported_entity_hints` (unsupported citation
hints) are also fetched. New entities are added to `stm.retrieved_chunks`, `build_context()`
is called again with the expanded set, and the next attempt begins.

**`rewrite_search`**: `stm.rewrite_query` is set and a new `search()` call runs with
the rewritten query before the next attempt.

**Iteration cap**: On attempt 2 (the last), if the status is still not `"answered"`,
`partial_answer` or whatever was returned is used as a best-effort answer.

### LTM write (on success)

After a successful `"answered"` status:
```python
if session_id and agent_response.ltm_entry:
    ltm_write(repo_id, session_id, agent_response.ltm_entry, repo, db)
```

This writes to `conversation_memory` with:
- `feature_name`: the topic identified by the LLM (e.g. `"authentication_flow"`)
- `confidence`: `"high"` / `"medium"` / `"low"`
- `exploration_status`: `"complete"` / `"partial"`
- `summary`: 1–2 sentence digest of what was learned
- `repo_indexed_at`: timestamp snapshot for future stale detection

---

## 11. Citation Validation

**File:** `src/generation/citation_validator.py`

Inline validation runs immediately after each `"answered"` response:

```python
context_entities = collect_context_entities(final_context)
report = validate_citations(
    answer=stm.answer_text,
    context_entities=context_entities,
    final_context=final_context,
    db_session=db,
    repo_id=repo_id,
)
```

### What it checks

For every `[file_path:L-L]` citation found in the answer:

1. **Definition citation**: the cited file/line range overlaps with an entity in
   `context_entities`. Classified as `"definition"`.

2. **Call-site citation**: a `CALLS` relationship in the DB confirms this is a
   valid call-site reference. Classified as `"call_site"`.

3. **Unsupported citation**: no entity in context matches and no DB relationship
   confirms it. Classified as `"unsupported"` (hallucinated).

For unsupported citations, the validator also finds the **nearest entity** by file path
similarity and stores its ID in `nearest_entity_id`. This is used by both:
- The re-retrieval loop (to fetch the hinted entity on the next attempt)
- The correction agent (to deterministically replace the bad tag)

Results are stored in `stm.validation_report` and `stm.citation_hit_rate`.

---

## 12. Citation Correction Agent

**File:** `src/agents/citation_correction_agent.py`

Runs in `ask_pipeline_task` (the worker), after the orchestrator returns.

```python
if report.unsupported_citations:
    correction = correct_citations(
        answer=answer,
        report=report,
        context_entities=context_entities,
        final_context=final_context,
        db_session=db,
    )
    answer = correction.corrected_answer
    report = correction.report
```

### Pass 1 — Deterministic

For each unsupported citation where `nearest_entity_id` is known:
1. DB query: `SELECT * FROM entities WHERE id = nearest_entity_id`
2. Construct correct tag: `[entity.file_path:entity.start_line-entity.end_line]`
3. Replace all occurrences of the bad tag in the answer with the correct one

No LLM call. Fast, reliable.

### Pass 2 — LLM (only for remaining citations with no nearest entity)

For citations where the file path wasn't in context at all:
1. Extract the paragraph containing the bad citation
2. Mark the bad citation with `⚠️`
3. Send to LLM: rewrite only the marked citation using the provided valid entity list
4. Primary model: NVIDIA NIM (40 RPM, no RPD cap — chosen to avoid Groq RPM contention)
5. Fallback: Gemini Flash-Lite → Groq

### Safety guard

After correction, the answer is **re-validated**. If the hallucination rate after
correction is higher than before (correction made things worse), the original answer
is returned unchanged.

---

## 13. Overview Pipeline

**File:** `src/retrieval/repo_overview.py`

Triggered when `stm.intent in ("repository_overview", "repository_detailed")`.
The orchestrator calls `run_overview()` and returns early — the normal
retrieval/answer-agent path is bypassed entirely.

```
repo_overview.run()
    │
    ├─ LTM full-repo cache check  ──────────── return cached answer if fresh
    ├─ _build_file_entity_map()   ──────────── fetch all modules + children from DB
    ├─ Load cached folder summaries from LTM
    ├─ _summarize_files_async()   ──────────── File Summary Agent (batched, parallel)
    ├─ _summarize_one_folder() × N ─────────── Folder Summary Agent (parallel)
    └─ summarize_repo()           ──────────── Repo Summary Agent (Gemini)
```

All agents run concurrently behind a `Semaphore(_SEMAPHORE_SIZE=8)` — max 8 LLM
calls in flight at any time.

### 13.1 File Summary Agent

**File:** `src/agents/file_summary_agent.py`

Produces per-file summaries with inline citations using a **model-first, adaptive
bin-packing** approach:

**Step 1 — pick_model()**: select the best available fast model (quota-aware, no LLM call).
Excludes OpenRouter (throttles concurrent calls to 80s+/batch).

**Step 2 — build_batches()**: bin-pack files into token-sized batches for the selected model:
- Large files (tokens > model context): split into line-boundary chunks, one batch per chunk
- Small files (< 30% of budget): packed greedily with other small files  
- Normal files: one file per batch

**Step 3 — execute_batch()**: single LLM call returning a JSON array:
```json
[{"file_path": "src/api/auth.py", "summary": "..."},
 {"file_path": "src/api/main.py", "summary": "..."}]
```

**Step 4 — retry_batch()**: if a call fails, picks the next available model. If the
next model has a smaller context window, re-splits the batch and recurses.

**Step 5 — merge_chunk_summaries()**: joins chunk summaries for large files with
`"(continued)"` transitions — no extra LLM call needed.

**Two prompt modes** based on intent:
- `repository_overview`: SHORT prompt (2–3 sentences per file)
- `repository_detailed`: DETAILED prompt (5–8 sentences per file)

Citations in file summaries use the real integer line numbers from entity metadata:
`[src/api/auth.py:45-89]`

### 13.2 Folder Summary Agent

**File:** `src/agents/folder_summary_agent.py`

Takes all file summaries within one folder and produces a folder-level prose summary.

- Scales sentence count with folder size (3–4 sentences for small folders, 5–6 for large)
- Uses `task_type="fast"`, skips OpenRouter
- Preserves inline citations verbatim from the file summaries (no re-invention)

After each folder summary is generated, it is immediately written to LTM:
```python
write_feature(
    repo_id=repo_id, session_id=session_id,
    feature_name=f"folder:{folder}",
    summary=folder_summary,
    ...
)
```

### 13.3 Repo Summary Agent

**File:** `src/agents/repo_summary_agent.py`

Synthesises all folder summaries into the final answer. Takes as input:
- All folder summaries (with citations preserved)
- Full project file tree (rendered as ASCII art by `_build_file_tree()`)
- Repository name, total file count

Primary model: `force_provider="gemini"` (best at long synthesis tasks)  
Fallbacks: Groq standard → NVIDIA NIM → Cloudflare → fast tier

Output format depends on intent:

**`repository_overview`** → `## What this project does` / `## Architecture` /
`## Key entry points` / `## Data flow`

**`repository_detailed`** → `## Overview` / `### folder/` sections per folder /
`## Key data flows`

After the Repo Summary Agent finishes, the full answer is written to LTM:
```python
write_feature(
    repo_id=repo_id, session_id=session_id,
    feature_name=ltm_feature,  # "repo_overview" or "repo_detailed"
    summary=answer,
    source_entity_ids=list(stm.visited_entity_ids)[:500],
    ...
)
```

### 13.4 Caching in the Overview Pipeline

There are three levels of LTM cache checks for overview queries:

**Level 1 — Full repo cache** (checked first):
```
feature_name = "repo_overview" | "repo_detailed"
```
If a fresh entry exists → return it immediately. No agents run. The full answer is served
from a single DB lookup.

**Level 2 — Folder-level cache** (per folder):
```
feature_name = "folder:src/api" | "folder:src/storage" | ...
```
Checked before the File Summary Agent runs. Folders with a cached summary skip file
summarization entirely. Only uncached folders go through the File Summary Agent.

**Level 3 — No cache (first time)** → all agents run → results cached at folder + repo level.

Cache invalidation: any entry written before `repo.indexed_at` is treated as stale (miss).
This means a repo re-index automatically invalidates all overview caches.

---

## 14. Post-Pipeline — Saving Turns and Updating Memory

**File:** `src/jobs/queue.py` → `ask_pipeline_task`, `src/memory/stm/working_memory.py`

After the pipeline and citation correction complete, for authenticated users:

### Step 1 — Save conversation turns

```python
save_turn(conversation_id, user_id, repo_id, role="user", content=query, db=db)
save_turn(conversation_id, user_id, repo_id, role="assistant", content=answer, db=db)
```

`save_turn()`:
- Upserts `ConversationModel` row (creates on first call, updates `updated_at` subsequently)
- Appends `ConversationTurnModel` with an auto-incremented `turn_index`
- Commits to DB

### Step 2 — summarize_after_turn() — the memory pipeline

This is the most important post-turn operation. A **single LLM call** does two things:

**1. Produces the rolling summary** (updates `conv.summary`):

The LLM receives:
```
Prior summary:
<summary_v(N-1)>   ← existing rolling summary

New exchange to incorporate:
USER: <query>
ASSISTANT: <answer>
```

And produces `summary_vN` — a compact paragraph (max 200 words) covering topics
discussed, decisions, and code areas referenced. No raw code blocks.

**Accumulation pattern:**
```
Q1 → summarize(Q1+A1)                    → summary_v1
Q2 → summarize(summary_v1 + Q2+A2)      → summary_v2
Q3 → summarize(summary_v2 + Q3+A3)      → summary_v3
...
```

On Q(N+1), the Answer Agent receives only `summary_vN`. This means the context window
stays bounded regardless of how many turns the conversation has.

**2. Extracts LTM facts** into three semantic tiers:

The same LLM call returns a JSON object:
```json
{
  "summary": "...",
  "user_memory": [
    {"category": "working_style", "fact": "Prefers functional style"}
  ],
  "user_repo_preferences": [
    {"category": "focus_area", "fact": "User focuses on the API layer"}
  ],
  "repo_memory": [
    {"category": "codebase_fact", "fact": "Auth uses JWT with 24h expiry"}
  ]
}
```

Each list is upserted into its corresponding table (deduplication: exact-match fact
strings are skipped):

- `user_memory` facts → `upsert_user_memory(user_id, facts, db)` → `user_memory` table
- `user_repo_preferences` facts → `upsert_user_repo_preferences(user_id, repo_id, ...)` → `user_repo_preferences` table
- `repo_memory` facts → `upsert_repo_user_memory(user_id, repo_id, ...)` → `repo_user_memory` table

**Model selection for summarize_after_turn()**: Primary: `force_provider="gemini"` 
(Gemini Flash-Lite, 15 RPM/1000 RPD). Deliberately avoids Groq to prevent RPM contention
with QueryPlanner and CitationCorrection which may run concurrently. Fallback: NVIDIA NIM 
→ Groq.

---

## 15. What Changes on the Second Query

This is the key question. Here is a concrete diff between Q1 and Q2:

### What is the same

- STM is created fresh (no state carried over)
- Query Planner runs afresh on the new question
- Vector search runs afresh
- Graph expansion runs afresh
- Answer Agent and citation validation run afresh

### What is different (accumulated context)

| Component | Q1 | Q2+ |
|---|---|---|
| **conversation history** | empty string | rolling summary of all prior turns |
| **LTM Tier 4 (user_memory)** | empty (Q1 is first ever) OR pre-loaded from past sessions | facts extracted from Q1 turn are now available |
| **LTM Tier 5 (user_repo_preferences)** | same — may already have facts from other sessions | facts extracted from Q1 are available |
| **LTM Tier 6 (repo_user_memory)** | same | codebase facts confirmed in Q1 are available |
| **LTM Tier 3 (session_knowledge)** | miss (nothing cached) | if Q1 produced an LTM entry with `exploration_status="complete"`, Q2 with the same intent gets a cache hit and the cached knowledge is injected into context |
| **Overview cache (folder/repo-level)** | miss — all agents run | hit — entire answer served from DB, zero agent calls |

### Concrete example: user asks about auth flow twice

**Q1: "How does the login flow work?"**

1. STM init. History = empty. LTM facts loaded (may be empty for new user).
2. Planner → `intent=dependency_flow, strategy=semantic_search_with_graph`
3. Vector search finds `AuthService`, `login()`, `JWT` entities
4. Graph expansion follows CALLS edges from `login()` to all callees
5. LTM check → miss
6. Answer Agent produces detailed answer about login flow with 8 citations
7. Inline validation: all 8 citations verified
8. LTM entry written: `{feature_name: "dependency_flow", confidence: "high",
   summary: "Login flow goes through AuthService.login() → JWT.sign() → token returned"}`
9. Turn saved + `summarize_after_turn()` runs
10. Rolling summary: `"User asked about login flow. Auth uses JWT. Key files: auth/service.py"`
11. LTM fact extracted: `repo_memory: "Auth uses JWT with 24h expiry"`

**Q2: "What about the logout flow?"**

1. STM init. History = `"User asked about login flow. Auth uses JWT. Key files: auth/service.py"`.
   LTM Tier 6 has: `"Auth uses JWT with 24h expiry"`. This is injected into the system prompt.
2. Planner → `intent=dependency_flow, strategy=semantic_search_with_graph`
3. Vector search finds `logout()`, `token_revocation`, `AuthService` entities
4. Graph expansion follows CALLS edges from `logout()`
5. LTM check → **partial hit** (intent matches `dependency_flow` and Q1's entry exists,
   but if `exploration_status="complete"` it's injected as extra context)
6. Answer Agent now knows from history that auth uses JWT, knows the key files, and has
   fresh retrieval context about logout. Produces answer about logout with the JWT context
   already pre-loaded.

The second answer is richer because it doesn't need to re-derive the JWT architecture — that
was already learned and is directly injected into the system prompt.

---

## 16. Memory System — Complete Reference

Six distinct memory stores, from shortest to longest-lived:

```
Scope ──────────────────────────────────────────────────────────────────────► Lifetime

[STM]                [Working Memory]     [Session Knowledge]   [LTM Tiers 4,5,6]
 │                         │                      │                      │
 └─ Single request         └─ One conversation    └─ One session         └─ Forever
    (in-memory only,           (DB-persisted,         (DB-persisted,         (DB-persisted,
     discarded after            bounded by             keyed by               cross-repo,
     response)                  rolling summary,       session_id,            cross-session,
                                never grows            invalidated on         never auto-
                                unboundedly)           repo re-index)         deleted)
```

### STM (Short-Term Memory) — `src/memory/stm/short_term.py`

- **Lifetime**: one request
- **Storage**: in-memory Python dataclass
- **Purpose**: blackboard for all pipeline stages to share state
- **Key data**: goal, intent, strategy, retrieved chunks, answer, citation report, iterations

### Working Memory (Rolling Summary) — `src/memory/stm/working_memory.py`

- **Lifetime**: one conversation thread (per `conversation_id`)
- **Storage**: `ConversationModel.summary` (Postgres)
- **Purpose**: keep conversation history compact and bounded
- **Written by**: `summarize_after_turn()` after every Q&A pair
- **Read by**: `load_history()` at the start of every pipeline run
- **Format**: LLM-generated paragraph, max 200 words, cumulative compression:
  `summary_vN = compress(summary_v(N-1) + Q(N) + A(N))`
- **Anonymous users**: NOT persisted. Raw turns sent in request body each time.

### Session Knowledge (LTM Tier 3) — `src/memory/ltm/session_knowledge.py`

- **Lifetime**: one session (keyed by `session_id`), invalidated on repo re-index
- **Storage**: `conversation_memory` table
- **Purpose**: cache what the agent learned about a feature/topic during this session
- **Written by**: Answer Agent on `"answered"` status (via `ltm_write()`)
- **Read by**: `ltm_lookup()` in orchestrator, before Answer Agent runs
- **Cache granularity**: `(repo_id, session_id, intent_as_feature_name)`
- **Special keys for overview**: `"folder:src/api"`, `"repo_overview"`, `"repo_detailed"`
- **Stale detection**: `repo.indexed_at > entry.repo_indexed_at` → treat as miss

### User Memory (LTM Tier 4) — `src/memory/ltm/user_memory.py`

- **Lifetime**: forever (never auto-deleted)
- **Scope**: global per user
- **Storage**: `user_memory` table
- **Written by**: `summarize_after_turn()` → `upsert_user_memory()`
- **Read by**: loaded at pipeline start, injected into Answer Agent system prompt
- **Categories**: `preference`, `background`, `working_style`
- **Deduplication**: exact-match fact strings skipped on insert

### User-Repo Preferences (LTM Tier 5) — `src/memory/ltm/user_repo_preference.py`

- **Lifetime**: forever
- **Scope**: per (user × repo)
- **Storage**: `user_repo_preferences` table
- **Written by**: `summarize_after_turn()` → `upsert_user_repo_preferences()`
- **Read by**: loaded at pipeline start
- **Categories**: `familiarity`, `focus_area`, `role_in_project`
- **Use case**: "User is unfamiliar with the GraphQL migration" — so the agent explains
  more carefully next time

### Repo-User Memory (LTM Tier 6) — `src/memory/ltm/repo_user_memory.py`

- **Lifetime**: forever (but should be treated as stale after re-index — not yet enforced)
- **Scope**: per (user × repo)
- **Storage**: `repo_user_memory` table
- **Written by**: `summarize_after_turn()` → `upsert_repo_user_memory()`
- **Read by**: loaded at pipeline start
- **Categories**: `codebase_fact`, `open_issue`, `architectural_decision`, `confirmed_behaviour`
- **Use case**: confirmed facts like `"Race condition in UserService.create()"` are
  always available to the Answer Agent without re-deriving them from code

---

## 17. End-to-End Timeline Summary

### Q1 — Semantic Query (authenticated user, new session)

```
t=0ms    POST /ask received → AskJobModel created → deferred → 200 OK
t=50ms   Worker picks up job
t=55ms   STM init
t=60ms   History: load_history() → (None, []) → history_text = ""
t=65ms   LTM: load all three tiers (all empty for new user)
t=70ms   [classifying] Query Planner → groq/llama-3.1-8b-instant
t=300ms  Plan: intent=feature, strategy=semantic_search
t=305ms  [searching] Vector search → Voyage AI embedding → pgvector → 10 entities
t=600ms  Graph expansion → BFS → expanded context assembled
t=610ms  LTM session check → miss
t=615ms  [generating] Answer Agent → standard-tier LLM call
t=3500ms Answer received with 6 inline citations
t=3510ms Inline citation validation → 5 definition, 1 unsupported
t=3520ms LTM entry written (session_knowledge)
t=3525ms [citations] Citation Correction (Pass 1 deterministic) → 1 fix
t=3530ms Re-validation → 6/6 verified
t=3535ms save_turn(user), save_turn(assistant)
t=3540ms summarize_after_turn() → Gemini → rolling summary + LTM facts extracted
t=4200ms Job status=done, result written to DB
---
Frontend polls at t=2000ms → "running" | t=4000ms → "running" | t=6000ms → "done"
```

### Q2 — Same session, follow-up question

```
t=0ms    POST /ask received → new job → deferred
t=50ms   Worker picks up job
t=55ms   STM init
t=60ms   History: load_history() → (summary_v1, []) → history_text = "[Conversation summary so far]\n..."
t=65ms   LTM: load all three tiers → Tier 6 has 2 facts from Q1
t=70ms   [classifying] Query Planner
t=300ms  Plan (may differ from Q1 depending on question)
t=305ms  [searching] Vector search
t=600ms  Graph expansion
t=610ms  LTM session check → possible HIT (if Q1 produced complete entry for same intent)
           → if hit: ltm_entry.summary injected at top of context
t=615ms  [generating] Answer Agent — now has: history summary + 3 LTM tiers + session cache + fresh retrieval
t=3500ms Answer received — richer because of accumulated context
...      (citation flow same as Q1)
t=4200ms summarize_after_turn() → summary_v2 (includes Q1+Q2 compressed)
```

### Overview Query — "Give me an overview of this repo"

**First time (no cache):**
```
t=0ms    POST /ask received
t=50ms   Worker
t=70ms   Query Planner → intent=repository_overview, strategy=repository_walk
t=300ms  [searching] → overview branch taken
t=305ms  LTM full-repo cache: MISS
t=310ms  _build_file_entity_map() → fetch all modules from DB
         (e.g. 40 files)
t=320ms  LTM folder checks → all miss (first time)
t=330ms  [reading_files 0/40] File Summary Agent: pick_model() → build_batches()
         → 8 batches dispatched concurrently (semaphore=8)
t=2000ms All file batches done → 40 file summaries in stm.file_summaries
         Each folder summary agent fires concurrently
t=3500ms All folder summaries done → stm.folder_summaries populated
         Each folder summary written to LTM (folder:xxx)
t=3510ms [insights] → [generating] Repo Summary Agent → Gemini
t=6000ms Final answer written
         Full repo-level answer written to LTM (repo_overview)
t=6010ms Job done
```

**Second time (cache hit):**
```
t=0ms    POST /ask received
t=50ms   Worker
t=70ms   Query Planner → intent=repository_overview
t=300ms  LTM full-repo cache: HIT
         → return cached answer immediately
t=305ms  Job done
```

Zero agent calls on a cache hit. The answer is served from a single Postgres `SELECT`.

---

*Document generated from source code: `src/pipeline/orchestrator.py`, `src/memory/stm/`,
`src/memory/ltm/`, `src/agents/`, `src/retrieval/repo_overview.py`, `src/jobs/queue.py`,
`src/api/routers/ask.py`*
