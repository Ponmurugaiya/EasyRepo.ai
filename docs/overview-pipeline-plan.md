# Overview Pipeline — Redesign Plan

## The Problem with the Current Approach

The current `repository_walk` does this:
- Fetches top-10 files by entry score
- Dumps their raw source into the context window
- Asks the Answer Agent to explain everything

**Why this fails for overview/detailed:**
1. A real repo has 20–200 files. Top-10 misses most of it.
2. Raw source of 10 files already fills the 8K token window.
3. The LLM never sees 90% of the codebase.
4. No citations are possible because the LLM is summarising
   from raw source it was shown, not from indexed entities.

---

## The Core Insight

The token window problem is solved by **hierarchical summarisation**:

```
File source (raw, too large)
    ↓  File Agent (1 LLM call per file)
File summary (~100 tokens each)
    ↓  Folder Agent (1 LLM call per folder)
Folder summary (~150 tokens each)
    ↓  Repo Agent (1 LLM call)
Final answer (with citations)
```

Each level compresses before the next level sees it.
The final Repo Agent sees only folder summaries — never raw source.
Total tokens at the Repo Agent: `num_folders × 150` tokens.
For a 50-file, 8-folder repo: ~1200 tokens of context → fits easily.

---

## STM & LTM Role

### STM (within this request)
The STM accumulates file summaries and folder summaries as they are
generated, passing them through the multi-agent chain:

```
stm.intermediate_summaries  ← file summaries appended here
stm.file_summaries          ← new field: dict[file_path → summary]
stm.folder_summaries        ← new field: dict[folder → summary]
stm.visited_entity_ids      ← all entity IDs cited in file summaries
```

This means if the pipeline needs to re-summarise (on token overflow),
it can reuse already-computed file summaries from STM without re-calling
the LLM for files already processed.

### LTM (across sessions)
Overview summaries are expensive (many LLM calls). LTM caches them
so the second time someone asks for an overview, the pipeline skips
all the per-file and per-folder agents and goes straight to the
cached repo summary.

**LTM keys for overview:**
- `feature_name = "repo_overview"` → full overview summary
- `feature_name = "repo_detailed"` → detailed overview summary
- `feature_name = "folder:{folder_path}"` → per-folder summary

**Stale detection** uses `repo_indexed_at` as always — if the repo
was re-indexed, all overview LTM entries are discarded.

**Partial cache hit:** If folder-level LTM entries exist but the
repo-level summary doesn't, the Repo Agent can use the cached
folder summaries instead of re-running all File Agents.

---

## Multi-Agent Architecture

### Agent 1 — File Summary Agent
**Input:** Source code of one file (the `source` field from `EntityModel`)
**Output:** 2–4 sentence summary with inline citations

Prompt instructs it to:
- Name the file's purpose in one sentence
- List key classes/functions with their roles
- Note key external dependencies (imports)
- Include inline citations: `[filename.py:start-end]` for each named entity

**Citation format preserved from existing system:**
`[auth/service.py:45-89]` → matched by the existing citation validator against
`EntityModel` rows, so citations link to real entities in the frontend.

**Model:** `llama-3.1-8b-instant` (fast, cheap — one call per file)

**Batching:** Files are processed in batches of 5 concurrently using
`asyncio.gather` to keep total latency under 10s for a 30-file repo.

**STM write:** Each file summary is appended to `stm.file_summaries[file_path]`
and the entity IDs mentioned are added to `stm.visited_entity_ids`.

---

### Agent 2 — Folder Summary Agent
**Input:** All file summaries for files within one folder
**Output:** 3–5 sentence folder summary with citations

Prompt instructs it to:
- Describe the folder's purpose and what domain it owns
- Name the most important files and their roles
- Describe key patterns across the folder (e.g. all files follow repository pattern)
- Include citations pointing to the key files/entities

**Model:** `llama-3.1-8b-instant`

**When invoked:** After all File Agents for that folder have completed.
One Folder Agent call per folder.

**STM write:** `stm.folder_summaries[folder_path]` = summary

**LTM write:** `feature_name = "folder:{folder_path}"` with
`exploration_status = "complete"` after the Folder Agent finishes.

---

### Agent 3 — Repo Summary Agent (Final Answer Agent)
**Input:** All folder summaries + architecture hint (entry points, file count, edges)
**Output:** The final answer with citations, structured as the user requested

**For `repository_overview`:**
- 3–5 paragraph high-level summary
- Describes purpose, main subsystems, key data flows
- Uses `[file:lines]` citations pointing to entry points and key classes

**For `repository_detailed`:**
- Section-per-folder breakdown
- Each section: folder purpose, key files, main classes/functions with descriptions
- More citations, deeper explanation

**Model:** `gemini-2.5-flash` (best quality for the final synthesis step)

**Does NOT get raw source** — only folder summaries (~150 tokens each).

**LTM write:** After the Repo Summary Agent answers:
- `feature_name = "repo_overview"` or `"repo_detailed"`
- `summary` = the full final answer
- `exploration_status = "complete"`

---

## Citations — How They Work End-to-End

### File Agent produces citations
The File Agent is prompted to cite entities it mentions:
```
"The `authenticate()` function [auth/service.py:45-67] validates
the JWT token and calls `db.get_user()` [db/session.py:12-28]."
```

These citations use the same `[file_path:start-end]` format as the
existing citation validator.

### Folder Agent preserves citations
The Folder Agent is instructed to include citations from file summaries
it aggregates. It can also add its own when describing cross-file patterns.

### Repo Agent inherits citations
The Repo Agent's final answer contains citations carried forward through
the hierarchy. The raw answer is passed through `validate_citations()`
exactly as today — the validator matches `[file_path:start-end]` tags
against `EntityModel` rows in the DB.

### Frontend display
No frontend changes needed. The citation validator produces
`definition_citations` and `call_site_citations` — the same objects
the frontend already renders. File-level citations from the overview
answer appear as clickable source links exactly like feature queries.

**The key requirement for this to work:**
File Agents must use exact file paths as they appear in `EntityModel.file_path`
(e.g. `auth/service.py`, not `service.py` or `/repo/auth/service.py`).
The orchestrator passes the canonical `file_path` from the DB to each File Agent
so it cites correctly.

---

## Token Budget Analysis

| Repo size | Files | File summaries | Folders | Folder summaries | Repo Agent input |
|---|---|---|---|---|---|
| Small | 10 | 10 × 80 = 800 tok | 3 | 3 × 150 = 450 tok | ~600 tok |
| Medium | 30 | 30 × 80 = 2400 tok | 6 | 6 × 150 = 900 tok | ~1100 tok |
| Large | 80 | 80 × 80 = 6400 tok | 12 | 12 × 150 = 1800 tok | ~2000 tok |
| Very large | 200 | 200 × 80 = 16K tok | 20 | 20 × 150 = 3000 tok | ~3500 tok |

Even a 200-file repo stays comfortably within the 8K token window at the
Repo Agent level. File Agents receive single-file source (~200–500 tokens
each) well within the fast model's context limit.

---

## Latency Analysis

| Repo size | File Agent calls | Concurrency (batch=5) | Folder calls | Total latency |
|---|---|---|---|---|
| Small (10 files) | 10 | 2 batches × 400ms | 3 × 400ms | ~2s |
| Medium (30 files) | 30 | 6 batches × 400ms | 6 × 400ms | ~5s |
| Large (80 files) | 80 | 16 batches × 400ms | 12 × 400ms | ~11s |

With LTM cache hit (second request): skip File + Folder Agents entirely.
Latency = 1 LTM lookup + 1 Repo Agent call = ~1–2s total.

---

## LTM Cache Hierarchy (Partial Hits)

```
Request: "Give me an overview"
            │
            ▼
    LTM lookup: "repo_overview"
            │
     ┌──────┴──────┐
    HIT           MISS
     │              │
     ▼              ▼
  Return         LTM lookup: all "folder:{path}" entries
  cached           │
  answer      ┌────┴────┐
          ALL HIT    PARTIAL/MISS
              │           │
              ▼           ▼
         Skip File   Run File Agents only for
         Agents      folders with no LTM entry
              │           │
              └─────┬─────┘
                    ▼
             Folder Agents (only for new/missing folders)
                    ▼
             Repo Summary Agent
             (uses mix of cached + new folder summaries)
                    ▼
             Write new LTM entries
```

This means after the first full overview, any subsequent overview
is nearly instant. After a re-index, only the changed files need
re-summarising (future enhancement: file-level change detection).

---

## Changes Required to Existing Code

### New files
| File | Purpose |
|---|---|
| `src/generation/file_summary_agent.py` | File Agent — summarise one file |
| `src/generation/folder_summary_agent.py` | Folder Agent — summarise one folder |
| `src/retrieval/repo_overview.py` | Orchestrates the 3-agent chain for overview |

### Modified files
| File | Change |
|---|---|
| `src/pipeline/memory.py` | Add `file_summaries: dict[str, str]` and `folder_summaries: dict[str, str]` fields to STM |
| `src/pipeline/orchestrator.py` | Route `repository_overview` and `repository_detailed` to `repo_overview.run()` instead of `repo_walk.walk()` |
| `src/storage/ltm_store.py` | Add `lookup_folder_summaries()` and `write_folder_summary()` for folder-level LTM |
| `src/pipeline/pipeline_logger.py` | Add `step_file_agent()` and `step_folder_agent()` trace methods |

### No changes needed
- `src/generation/citation_validator.py` — citations work as-is
- `src/api/routers/ask.py` — response assembly unchanged
- Frontend — no changes at all

---

## New STM Fields

```python
@dataclass
class ShortTermMemory:
    # ... existing fields ...

    # Overview pipeline additions
    file_summaries: dict[str, str] = field(default_factory=dict)
    # key = file_path (canonical, as in EntityModel.file_path)
    # value = 2-4 sentence summary with inline citations

    folder_summaries: dict[str, str] = field(default_factory=dict)
    # key = folder path (e.g. "src/api", "src/storage", ".")
    # value = 3-5 sentence folder summary with citations

    overview_from_cache: bool = False
    # True when the final answer came entirely from LTM (no agents ran)
```

---

## New LTM Schema Usage

No schema changes needed — `ConversationMemoryModel` already has all
required fields. New `feature_name` conventions:

| feature_name | What it stores | exploration_status |
|---|---|---|
| `"repo_overview"` | Full overview answer | `"complete"` |
| `"repo_detailed"` | Full detailed walkthrough | `"complete"` |
| `"folder:src/api"` | Folder summary for `src/api` | `"complete"` |
| `"folder:src/storage"` | Folder summary for `src/storage` | `"complete"` |

The existing stale detection (`repo_indexed_at` comparison) handles
cache invalidation for all of these automatically.

---

## Pipeline Trace (New Log Lines)

```
PIPELINE START  repo=abc123  query='Give me an overview'
PIPELINE [STM@init]      intent=query  strategy=semantic_search  visited=0  chunks=0
PIPELINE [0-HISTORY]     source=none
PIPELINE [1-PLAN]        intent=repository_overview  strategy=repository_walk
PIPELINE [STM@post-plan] intent=repository_overview  strategy=repository_walk
PIPELINE [4-LTM READ]    outcome=miss  feature=repo_overview
PIPELINE [4-LTM READ]    outcome=hit   feature=folder:src/api     ← partial cache
PIPELINE [4-LTM READ]    outcome=hit   feature=folder:src/storage ← partial cache
PIPELINE [4-LTM READ]    outcome=miss  feature=folder:src/generation
PIPELINE [FILE-AGENT]    file=src/generation/answer_agent.py  tokens=380  elapsed=420ms
PIPELINE [FILE-AGENT]    file=src/generation/query_planner.py  tokens=290  elapsed=380ms
PIPELINE [FILE-AGENT]    file=src/generation/llm_client.py  tokens=510  elapsed=450ms
PIPELINE [FOLDER-AGENT]  folder=src/generation  files=3  elapsed=390ms
PIPELINE [4-LTM WRITE]   feature=folder:src/generation  confidence=high  status=complete
PIPELINE [STM@overview-assembled]  file_summaries=12  folder_summaries=5  visited=47
PIPELINE [5-DISPATCH]    attempt=0  model=gemini-2.5-flash  ctx_tokens=1840  task=answer
PIPELINE [5-LLM RESP]    provider=gemini  status=answered  chars=4200
PIPELINE [4-LTM WRITE]   feature=repo_overview  confidence=high  status=complete
PIPELINE [STM@final]     status=answered  answer_chars=4200
PIPELINE [6-CITE]        total=12  definition=12  call_site=0  unsupported=0
PIPELINE DONE            status=answered  provider=gemini  citations=12  total_ms=6800
```

---

## Implementation Order

| Step | Task | Risk |
|---|---|---|
| 1 | Add `file_summaries` + `folder_summaries` to STM | None — additive |
| 2 | Add `step_file_agent()` + `step_folder_agent()` to pipeline_logger | None |
| 3 | Build `file_summary_agent.py` | Low — single-file LLM call |
| 4 | Build `folder_summary_agent.py` | Low — depends on step 3 |
| 5 | Add `lookup_folder_summaries()` + `write_folder_summary()` to ltm_store | Low |
| 6 | Build `repo_overview.py` — the orchestration layer | Medium — wires 1–5 |
| 7 | Route overview intents to `repo_overview.run()` in orchestrator | Low — single if-branch |
| 8 | Update `pipeline-intent-strategy.md` with final behaviour | None |
