# Agent Design Review

## Current Agent Inventory

| Name | File | Role | Issues |
|---|---|---|---|
| Query Planner | `query_planner.py` | Classifies intent + strategy | Name is fine |
| Answer Agent | `answer_agent.py` | Generates cited answer for standard queries | Name is wrong, role is unclear |
| File Summary Agent | `file_summary_agent.py` | Summarises one file with citations | Name is fine |
| Folder Summary Agent | `folder_summary_agent.py` | Aggregates file summaries per folder | Name is fine |
| *(unnamed)* | `repo_overview.py` (inline) | Synthesises folder summaries into final answer | No agent module, no contract |
| Citation Correction Agent | `citation_correction_agent.py` | Fixes bad citations post-hoc | Symptom of a deeper design issue |

---

## Problem 1 — "Answer Agent" is the wrong name

**Current name:** `answer_agent`
**What it actually does:** Takes a user's natural-language question about code,
a retrieved + graph-expanded context block, and conversation history — then
produces a Markdown answer with inline `[file:line-line]` citations, a
structured JSON status block, and an LTM entry.

"Answer Agent" could mean anything. It's used only for `feature`, `dependency_flow`,
`specific_lookup`, and `query` intents — all cases where the user asks a targeted
question about specific code.

**Proposed rename:** `CodeQAAgent` / file: `code_qa_agent.py`

This name communicates:
- It's a Code intelligence agent (not a generic chatbot)
- It answers Questions about code (Q&A)
- It's distinct from the overview synthesis agents

---

## Problem 2 — The Repo Summary Agent doesn't exist as a proper agent

The final synthesis step in `repo_overview.py` is an inline `smart_complete()`
call. Compare to how File and Folder agents are structured:

```
file_summary_agent.py  → summarize_file(file_path, source, entities) -> str
folder_summary_agent.py → summarize_folder(folder, file_summaries) -> str
repo_overview.py       → inline _llm.smart_complete(...) ← NOT a proper agent
```

The Repo Summary Agent should be a named module `repo_summary_agent.py` with:
- A clear input contract: `(repo_name, folder_summaries, intent, query) -> str`
- Its own system prompt
- Its own fallback handling
- Testable in isolation

---

## Problem 3 — Overview path bypasses the Code QA Agent entirely (correct, but undocumented)

The overview path correctly does NOT use the Code QA Agent. Overview queries
don't need targeted Q&A — they need hierarchical summarisation. This is the
right design. The problem is it's undocumented and implied rather than explicit.

**Current flow:**
```
repository_overview intent
  → repo_overview.run()     (3-agent chain: File → Folder → Repo Summary)
  → returns answer directly
  → skips Code QA Agent entirely ✓ (correct)
  → skips LTM re-retrieval loop ✓ (correct, not needed)
  → skips standard LTM write ✗ (wrong — overview has its own LTM, but the
                                  standard pipeline's session_id-keyed entry
                                  for "repository_overview" intent is never
                                  written via the normal path)
```

The intent `"repository_overview"` as `feature_name` in the standard LTM
(via `ltm_store.lookup()`) is checked in the orchestrator at step 5, but the
overview pipeline writes its own LTM via `ltm_store.write_feature()` with
`feature_name="repo_overview"` — a different key. These are two separate LTM
namespaces that don't talk to each other.

---

## Problem 4 — Citation Correction Agent is a symptom, not a solution

The Correction Agent exists because the Code QA Agent generates citations
without knowing the exact entity line ranges from the DB — it infers them from
rendered context text and can get them wrong.

The File Summary Agent solves this correctly: it receives the entity list with
exact `[file_path:start-end]` tags and is instructed to use those exact tags.
The Code QA Agent doesn't get this — it gets rendered source text and has to
figure out line numbers itself.

**Root cause:** The Code QA Agent prompt doesn't include a structured entity
list with canonical citations. It sees source code with line numbers but no
guaranteed citation anchors.

**Better design:** Pass the entity list as citation anchors into the Code QA
Agent prompt (same pattern as File Summary Agent). Correction Agent becomes
a safety net for edge cases, not a primary fix path.

---

## Proposed Agent Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       QUERY PLANNER                             │
│  query_planner.py                                               │
│  Input: user query                                              │
│  Output: intent + strategy + rewritten search query            │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
              ┌────────────────────┴─────────────────────┐
              │ intent = feature/dependency/lookup/query  │  intent = overview/detailed
              ▼                                           ▼
┌─────────────────────────────┐     ┌──────────────────────────────────────────┐
│    CODE Q&A AGENT           │     │   OVERVIEW PIPELINE (3 agents)           │
│    code_qa_agent.py         │     │                                          │
│                             │     │  ┌──────────────────────────────────┐   │
│  Input:                     │     │  │  FILE SUMMARY AGENT              │   │
│  - user query               │     │  │  file_summary_agent.py           │   │
│  - retrieved context        │     │  │  Input: file_path, source, ents  │   │
│  - entity list (anchors)    │     │  │  Output: 2-4 sentence summary    │   │
│  - conversation history     │     │  │         with [file:line] cites   │   │
│  - iteration number         │     │  └──────────────────────────────────┘   │
│                             │     │                  │                        │
│  Output:                   │     │  ┌──────────────────────────────────┐   │
│  - answered/insufficient/   │     │  │  FOLDER SUMMARY AGENT           │   │
│    rewrite_search           │     │  │  folder_summary_agent.py        │   │
│  - Markdown answer with     │     │  │  Input: folder, file_summaries  │   │
│    [file:line] citations    │     │  │  Output: 3-5 sentence summary   │   │
│  - LTM entry (if answered)  │     │  │         with citations          │   │
│                             │     │  └──────────────────────────────────┘   │
│  Model: Groq → Gemini       │     │                  │                        │
└─────────────────────────────┘     │  ┌──────────────────────────────────┐   │
              │                     │  │  REPO SUMMARY AGENT  ← MISSING  │   │
              │                     │  │  repo_summary_agent.py           │   │
              ▼                     │  │  Input: repo_name, folder_sums,  │   │
┌─────────────────────────────┐     │  │         intent, query            │   │
│  CITATION CORRECTION AGENT  │◄────┼──│  Output: Final answer with       │   │
│  citation_correction_agent  │     │  │          citations + entity_ids  │   │
│                             │     │  │  Model: Gemini                   │   │
│  Input: answer + bad tags   │     │  └──────────────────────────────────┘   │
│  Output: corrected answer   │     └──────────────────────────────────────────┘
└─────────────────────────────┘
```

---

## Proposed Renames

| Old name | New name | New file |
|---|---|---|
| `answer_agent.py` | `code_qa_agent.py` | `src/generation/code_qa_agent.py` |
| `answer_agent.run()` | `code_qa_agent.run()` | same signature |
| `AgentResponse` | `QAResponse` | in `code_qa_agent.py` |
| *(inline in repo_overview.py)* | `repo_summary_agent.py` | `src/generation/repo_summary_agent.py` |

All callers (`orchestrator.py`) update the import. No other interface changes needed.

---

## Design Correctness Verdict

| Aspect | Correct? | Notes |
|---|---|---|
| Overview uses its own 3-agent chain | ✅ | Right design — hierarchical summarisation needed |
| Overview bypasses Code QA Agent | ✅ | Right — QA agent is for targeted questions, not structure exploration |
| Overview bypasses re-retrieval loop | ✅ | Right — no "insufficient" concept for hierarchical summarisation |
| File/Folder agents produce cited summaries | ✅ | Right pattern — entity list as citation anchors |
| Repo Summary Agent is inline code | ❌ | Should be a named module like the other two |
| Code QA Agent name | ❌ | "Answer Agent" is too generic |
| Overview LTM key (`"repo_overview"`) vs standard LTM key (intent string) | ❌ | Two separate namespaces, `lookup()` in orchestrator step 5 is redundant for overview |
| Citation Correction Agent as primary fix | ⚠️ | Acceptable as safety net, but Code QA Agent should produce better citations natively |
| Correction Agent runs on overview answers | ❌ | `expanded_contexts=[]` means all overview citations are flagged unsupported before correction even runs — correction can only make it worse |

---

## Bug Fix Backlog

Saved from the audit. Ordered by priority.

### P0 — Crash / Silent Total Failure

**B1 — `get_event_loop()` raises RuntimeError in Python 3.12+** (`repo_overview.py`)
```python
# Wrong:
loop = asyncio.get_event_loop()
summary = await loop.run_in_executor(...)

# Fix:
loop = asyncio.get_running_loop()
summary = await loop.run_in_executor(...)
```

**B2 — Overview `expanded_contexts=[]` → all citations flagged unsupported** (`orchestrator.py` + `ask.py`)
```python
# Wrong (orchestrator overview path):
empty_context = _FC(expanded_contexts=[], ...)

# Fix: collect entities from stm.visited_entity_ids and build a real
# entity list so citation validation has something to match against.
# OR: skip citation validation for overview answers entirely (they have
# no graph-expanded context by design — use entity_ids from file agents).
```

**B3 — `_extract_answer_json` fallback regex never matches nested JSON** (`answer_agent.py`)
```python
# Wrong:
json_matches = re.finditer(r"\{[^{}]*\"status\"[^{}]*\}", text, re.DOTALL)

# Fix: use a recursive/permissive JSON extraction:
import json, re
def _extract_any_json_with_status(text):
    # Find all {...} candidates including nested braces
    for match in re.finditer(r'\{', text):
        try:
            obj, _ = json.JSONDecoder().raw_decode(text, match.start())
            if "status" in obj:
                return obj
        except json.JSONDecodeError:
            continue
    return None
```

**B4 — Sync LLM calls block async event loop** (`orchestrator.py`)
```python
# Wrong:
agent_response = answer_agent.run(...)

# Fix:
loop = asyncio.get_running_loop()
agent_response = await loop.run_in_executor(None, lambda: answer_agent.run(...))
```

### P1 — Wrong Results / Data Loss

**B5 — `db.commit()` inside shared session commits caller's partial state** (`ltm_store.py`)
```
Fix: Remove db.commit() from ltm_store.write() and write_feature().
The caller (orchestrator.py) owns the commit boundary.
Use db.flush() instead to write to the session without committing.
```

**B6 — `summarized_through_turn` not set on new ConversationModel rows** (`conversation_store.py`)
```python
# Fix: explicitly set it:
conv = ConversationModel(
    ...
    summarized_through_turn=0,   # ADD THIS
)
```

**B7 — `maybe_summarize` blocks the event loop** (`conversation_store.py` + `ask.py`)
```python
# Fix in ask.py: run in executor after returning the response,
# or make maybe_summarize async:
import asyncio
loop = asyncio.get_running_loop()
loop.run_in_executor(
    None,
    lambda: conversation_store.maybe_summarize(...)
)
```

**B8 — `RelationshipModel` loaded without `repo_id` filter** (`citation_validator.py`)
```python
# Wrong:
stmt = select(RelationshipModel)

# Fix:
from sqlalchemy import select
stmt = (
    select(RelationshipModel)
    .where(RelationshipModel.repo_id == repo_id)
)
# repo_id must be passed into _collect_graph_relationships()
```

### P2 — Quality Degradation

**B9 — `task_type="fast"` on re-retrieval iterations** (`answer_agent.py`)
```python
# Fix: don't downgrade on retries
task_type = "standard"
if estimated_tokens < 500 and iteration == 0:
    task_type = "fast"
```

**B10 — No Groq fallback in file/folder summary agents**
```python
# Fix: wrap in try/except with Gemini fallback (same pattern as query_planner.py)
try:
    summary, _ = _llm.smart_complete(force_model="groq/llama-3.1-8b-instant", ...)
except LLMProviderError:
    summary, _ = _llm.smart_complete(force_provider="gemini", ...)
```

**B11 — `named_symbol in matched.id` substring match too broad** (`citation_validator.py`)
```python
# Wrong:
or named_symbol in matched.id

# Fix: use word-boundary check on the last segment only
or named_symbol == matched.id.split(".")[-1]
```

**B12 — Duplicate citations: only first occurrence corrected** (`citation_correction_agent.py`)
```python
# Wrong:
corrected = corrected.replace(mismatch.raw, correct_tag, 1)

# Fix: replace ALL occurrences
corrected = corrected.replace(mismatch.raw, correct_tag)
```

**B13 — Stale LTM detection skipped when `repo_indexed_at` is NULL** (`ltm_store.py`)
```python
# Wrong (skips check entirely when either is None):
if repo.indexed_at and entry.repo_indexed_at:

# Fix: treat NULL repo_indexed_at as stale (unknown index time = unsafe)
if entry.repo_indexed_at is None:
    return None  # treat as stale — unknown index time
if repo.indexed_at and repo.indexed_at > entry.repo_indexed_at:
    return None
```

**B14 — Overview fallback leaves `stm.intent` stale** (`orchestrator.py`)
```python
except Exception as exc:
    logger.error("Overview pipeline failed, falling back: %s", exc)
    stm.intent = "query"                    # ADD: reset intent
    stm.retrieval_strategy = "semantic_search"  # ADD: reset strategy
    # fall through
```

### P3 — Observability / Correctness Gaps

**B15 — `_pipeline_level()` called twice in same conditional** (`pipeline_logger.py`)
```python
# Fix: cache it
level = _pipeline_level()
if level is not None and level <= logging.DEBUG:
```

**B16 — `turn_index=-1` logged for turn saves** (`ask.py`)
```
Fix: have save_turn() return the assigned turn_index so it can be logged.
```

**B17 — Race condition on new ConversationModel creation** (`conversation_store.py`)
```
Fix: use INSERT ... ON CONFLICT DO NOTHING or a DB-level unique constraint
enforcement with retry logic.
```

**B18 — `_entity_ids_to_check` defined inside citation loop with per-citation DB queries**
```
Fix: pre-build the parent chain lookup before the citation loop using a
single query to fetch all ancestors for all matched entities at once.
```

---

## Summary Checklist

Before next release, these MUST be fixed:
- [x] B1 `get_event_loop()` → `get_running_loop()`
- [x] B2 Overview citations all unsupported → `_build_overview_context` builds real FinalContext
- [x] B3 Nested JSON extraction in code_qa_agent fallback
- [x] B4 Sync calls blocking async event loop → `run_in_executor` in orchestrator
- [x] B5 `db.commit()` inside ltm_store → changed to `db.flush()`
- [x] B6 `summarized_through_turn` not initialised → set to 0 on create
- [x] B7 `maybe_summarize` blocks event loop → moved to `run_in_executor`
- [x] B8 RelationshipModel full table scan → `repo_id` filter added

Before next milestone:
- [x] Rename `answer_agent` → `code_qa_agent` (shim kept for compatibility)
- [x] Extract Repo Summary Agent into `repo_summary_agent.py`
- [x] B9 `task_type="fast"` on retry → only applies at `iteration == 0`
- [x] B10 No Groq fallback in file/folder summary agents → Gemini fallback added
- [x] B11 `named_symbol in matched.id` substring match → changed to last-segment equality
- [x] B12 Duplicate citations: only first corrected → `replace()` without count arg
- [x] B13 Stale LTM detection skipped on NULL → NULL treated as stale
- [x] B14 Overview fallback leaves intent stale → resets `stm.intent`, strategy, query

Nice to have (not yet fixed):
- [ ] B15 `_pipeline_level()` called twice → partially fixed in `step_stm`, `step_llm_response`
- [ ] B16 `turn_index=-1` logged for turn saves
- [ ] B17 Race condition on new ConversationModel creation
- [ ] B18 Per-citation DB queries in `_entity_ids_to_check`
