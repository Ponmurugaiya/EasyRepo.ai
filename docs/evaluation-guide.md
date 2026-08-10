# EasyRepo — Evaluation Guide

**Project:** AI Codebase Intelligence Platform  
**Reference:** `docs/evaluation-plan.md` (pillar definitions, metrics, pass criteria)  
**Purpose:** Operational step-by-step guide — what to run, how to run it, what to look for, and how to record results  
**Database:** Supabase PostgreSQL (cloud-hosted — see `.env → DATABASE_URL`)  
**Auth:** Developer account already registered (`AUTH_ENABLED=false` for current run)

---

## What Has Already Been Done

This project has been built with rigorous, evidence-first validation throughout. The following was verified in earlier development phases:

| What was verified | Where evidence lives | Outcome |
|---|---|---|
| Entity + relationship extraction against 62-entity / 87-relationship hand-built manifest | `scripts/validate_against_manifest.py` | 100% match |
| Storage layer: entity count, relationship counts, embedding dimensions, NULL-embedding check | `scripts/verify_storage.py` | All assertions passed |
| Vector search ranking quality (3 spot-check queries, disambiguation checks) | `scripts/verify_storage.py` + `scripts/analyze_q3_rankings.py` | Preferred entity ranks first in all 3 cases |
| Retrieval pipeline: 6 graph-expansion scenarios, 100% expansion-edges verified against real DB rows | `scripts/validate_retrieval.py` | All 6 scenarios passed |
| End-to-end citation validation: 4 canonical questions, 67 citations | `known-limitations.md §1` | 0.0% hallucination rate |
| Groq → Gemini 429 fallback | `known-limitations.md §1` | Real fallback confirmed (34 HTTP 429s) |
| Auth, rate-limiting, CORS hardening | `step7-hardening-report.md` | All 6 found issues fixed + documented |
| Async job queue (Procrastinate) | `known-limitations.md §2` | Closed with architecture proof |
| INSTANTIATES relationship type | `known-limitations.md §3` | Extracted, validated, cited |
| Collision-resistant repo_id + user/access system | `known-limitations.md §5` | 5 behavioural test cases passing |

**Why re-run now:** All of the above was validated informally. The goal of this guide is to re-run everything from a clean slate — fresh index, fresh DB — and produce documented result files with raw output, key numbers, and explicit pass/fail verdicts for every pillar.

---

## The Five Evaluation Pillars

```
GitHub URL / Local Path
        │
        ▼
┌───────────────────┐
│  Pillar 1         │  Extraction Quality
│  Tree-sitter      │  Does the parser faithfully represent code as entities + relationships?
│  EntityExtractor  │
└────────┬──────────┘
         │ entities + relationships
         ▼
┌───────────────────┐
│  Pillar 2         │  Storage & Embedding Integrity
│  PostgreSQL       │  Are all entities stored with correct counts and valid embeddings?
│  pgvector         │
└────────┬──────────┘
         │ vector index
         ▼
┌───────────────────┐
│  Pillar 3         │  Retrieval Quality
│  Vector Search    │  Does the system retrieve the right entities? Does graph expansion add value?
│  + Graph Expand   │
└────────┬──────────┘
         │ FinalContext
         ▼
┌───────────────────┐
│  Pillar 4         │  Answer Quality
│  LLM Generation   │  Is the generated answer correct, complete, and grounded?
│  (Groq / Gemini)  │
└────────┬──────────┘
         │ answer + raw citations
         ▼
┌───────────────────┐
│  Pillar 5         │  Citation Quality
│  Citation         │  Does the validator correctly classify all 3 citation types?
│  Validator        │  Is the hallucination rate stable?
└───────────────────┘
         │
         ▼
┌───────────────────┐
│  Pillar 6         │  Memory & Agentic System          ← added in this guide
│  Query Planner    │  Did the planner pick the right strategy?
│  Answer Agent     │  Did the agent converge without excessive retries?
│  STM / LTM        │  Does the cache work? Is stale data discarded?
└───────────────────┘
```

---

## Pre-Flight: Delete Existing Indexed Repos

Start from a clean DB so counts are not contaminated by data from an old extractor version.

> **Why this matters:** Pillars 1 and 2 compare exact counts against a hand-built manifest (62 entities, 87 relationships). If the repo was indexed before `variable` entities or `INSTANTIATES` were added, counts will be wrong and every check will fail.

Open the Supabase SQL Editor and run these in order:

```sql
-- 1. See what currently exists
SELECT id, name, status, indexed_at FROM repositories ORDER BY indexed_at DESC NULLS LAST;

-- 2. Wipe everything (CASCADE removes entities, relationships,
--    conversation_memory, conversations, conversation_turns automatically)
DELETE FROM repositories;

-- 3. Confirm clean state
SELECT COUNT(*) FROM repositories;   -- must be 0
SELECT COUNT(*) FROM entities;        -- must be 0
SELECT COUNT(*) FROM relationships;   -- must be 0

-- 4. Clear LTM cache (required for Pillar 6 memory tests)
TRUNCATE conversation_memory;
TRUNCATE conversations CASCADE;
```

---

## Environment Setup

```bash
cd p:\EasyRepo\platform
.venv\Scripts\activate

# Confirm DB is reachable
python -c "from src.storage.db import get_session; print('DB OK')"
```

Expected: `DB OK`. If this fails, check `DATABASE_URL` in `.env` and confirm
your Supabase project is not paused (free tier pauses after 1 week of inactivity —
resume from the Supabase dashboard).

---

## Index the Sample Repository

All five pillars evaluate against `sample-repo` — the controlled test subject
with a hand-built ground-truth manifest.

**Terminal 1 — start the API:**

```bash
python run.py
```

Wait for: `Uvicorn running on http://0.0.0.0:8000`

**Terminal 2 — ingest sample-repo:**

```bash
curl -X POST http://localhost:8000/repositories ^
  -H "Content-Type: application/json" ^
  -d "{\"source\": \"../sample-repo\"}"
```

Note the `repo_id` from the response. Poll until ready (takes 2–5 min):

```bash
curl http://localhost:8000/repositories/<repo_id>/status
```

Confirm in Supabase:

```sql
SELECT id, name, status, indexed_at FROM repositories;
-- expect: 1 row, status = 'ready', indexed_at is not null
```

**Do not proceed until status is `ready`.**


---

## Pillar 1 — Extraction Quality

### What it measures
Whether the Tree-sitter extraction pipeline produces the correct set of entities and relationships when run against known source code.

### Metrics
| Metric | Formula | Target |
|---|---|---|
| Entity recall | matched / manifest_total × 100 | 100% |
| Entity precision | matched / extracted_total × 100 | 100% |
| Relationship recall per type | matched / manifest_total × 100 per type | 100% |
| Line range correctness | entities with correct start+end line / matched | 100% |
| Parent structure correctness | entities with correct parent_id / matched | 100% |
| Docstring flag correctness | entities with correct has_docstring / matched | 100% |

### Existing test infrastructure
- **`scripts/validate_against_manifest.py`** — runs extraction against `sample-repo`, compares against `sample-repo/test-manifest.json` (62 entities, 87 relationships), prints per-type match rates and mismatch details.
- **`platform/tests/test_extraction.py`** — 3 pytest unit tests: initialisation, module ID generation scheme, Python snippet extraction with structural assertions.

### How to run
```bash
cd platform

# Manifest validation
python scripts/validate_against_manifest.py

# Unit tests
python -m pytest tests/test_extraction.py -v
```

### Expected output
```
Entity recall:          62/62  (100.0%)
Entity precision:       62/62  (100.0%)
Relationship recall:    87/87  (100.0%)
  CONTAINS:    48/48  (100.0%)
  IMPORTS:     11/11  (100.0%)
  CALLS:       23/23  (100.0%)
  INHERITS:     2/2   (100.0%)
  IMPLEMENTS:   3/3   (100.0%)
  INSTANTIATES: N/N   (100.0%)
Line range mismatches:   0
Parent structure errors: 0
Docstring flag errors:   0
SUCCESS: 100% Match
```

### Pass criteria
- `validate_against_manifest.py`: exit code 0, `SUCCESS: 100% Match` in output
- All pytest tests: green
- Zero line-range mismatches, zero parent structure mismatches

### Failure diagnosis
| Symptom | Cause | Fix |
|---|---|---|
| Entity recall < 100% | Parser dropped entities | Check `python_adapter.py` for recent changes |
| Entity precision > 100% | Parser invented extra entities | Duplicate extraction — check node visitor logic |
| Any `MISMATCH` line | Wrong line ranges or parent IDs | Inspect extractor output for that entity type |
| Script crashes | `sample-repo` not found | Confirm `../sample-repo` path exists from `platform/` |

### Result recording location
`evaluation-results/pillar1-extraction.md`

---

## Pillar 2 — Storage & Embedding Integrity

### What it measures
Whether the database holds the correct number of entities and relationships, every entity has a non-null embedding of the correct dimension, and the vector similarity model produces semantically meaningful rankings.

### Metrics
| Metric | Target |
|---|---|
| Entity count in DB | Matches manifest (62 for sample-repo) |
| Relationship counts per type | Exact: CONTAINS=48, IMPORTS=11, CALLS=23, INHERITS=2, IMPLEMENTS=3, INSTANTIATES≥1 |
| NULL embeddings | 0 |
| Embedding dimension | 1024 (EMBEDDING_DIM config constant) |
| Q1 ranking check | auth/authenticate/user/token keywords → correct entities at top |
| Q2 ranking check | `AuthService.validate` ranks above `UserModel.validate` |
| Q3 ranking check | `format_audit_log` ranks above `format_user_record` |

### Existing test infrastructure
- **`scripts/verify_storage.py`** — checks entity counts, relationship counts per type, embedding integrity (NULL check + dimension), and runs 3 spot-check vector similarity queries with ranking assertions.

### How to run
```bash
cd platform
python scripts/verify_storage.py
# Expect: "SUCCESS: All storage and embedding verifications passed perfectly!"
```

### Expected output
```
PASSED: Entity count matches manifest (62)
PASSED: CONTAINS=48, IMPORTS=11, CALLS=23, INHERITS=2, IMPLEMENTS=3, INSTANTIATES=N
PASSED: All entities have non-null vector embeddings
PASSED: All embeddings have correct dimension (1024)
RANKING CHECK PASSED (Q1): auth keywords → correct top results
RANKING CHECK PASSED (Q2): AuthService.validate ranks above UserModel.validate
RANKING CHECK PASSED (Q3): format_audit_log ranks above format_user_record
SUCCESS: All storage and embedding verifications passed perfectly!
```

### Pass criteria
- All count assertions pass with no `AssertionError`
- All three ranking checks: `RANKING CHECK PASSED` in output
- Log line: `PASSED: All entities have non-null vector embeddings`

### Failure diagnosis
| Symptom | Cause | Fix |
|---|---|---|
| `NULL embedding count: N` | Voyage AI call failed mid-ingestion | Check API key; re-index from clean slate |
| `Embedding dimension: 768` | Stale embeddings from old model | Full re-index required |
| `RANKING CHECK FAILED` | Semantic similarity not meaningful | Check `VOYAGE_API_KEY` in `.env`; confirm `voyage-code-3` model |
| Count mismatch | Ingestion incomplete | Check `logs/app.log` for errors; re-index |

### Result recording location
`evaluation-results/pillar2-storage.md`


---

## Pillar 3 — Retrieval Quality

### What it measures
Whether the combined vector search + graph expansion pipeline surfaces the right entities for a given natural language query, and whether every entity added by graph expansion is grounded in a real database relationship.

### Metrics
| Metric | Formula | Target |
|---|---|---|
| Expansion integrity | 100% of expanded edges verified against real DB rows | 100% |
| Scenario pass rate | scenarios passed / total scenarios | 6/6 |
| Method disambiguation | `AuthService.validate` at rank 1 among all `validate` entities | Rank 1 |
| Orphan file isolation | formatting.py entities have zero external expansions | 0 external expansions |
| Execution trace present | Multi-hop scenario reconstructs execution trace | Present |
| Inheritance completeness | All inherited entities in context for inheritance scenario | All present |

### Existing test infrastructure
- **`scripts/validate_retrieval.py`** — 6 named scenarios against `sample-repo/test-manifest.json`:
  1. `multi_hop_call_chain` — login flow end-to-end, expects execution trace
  2. `multi_level_inheritance` — AdminUser chain, expects all ancestor entities in context
  3. `interface_implementation` — AuthService implements Repository, expects both in context
  4. `method_disambiguation` — "validate a JWT token" must rank `AuthService.validate` at rank 1
  5. `textual_similarity_no_conflation` — "format an audit log entry" must rank `format_audit_log` first
  6. `orphan_file_isolation` — formatting.py has zero CALLS/IMPORTS edges; no external expansion allowed
- Each scenario runs `assert_all_expansions_backed_by_real_relationships()` which cross-checks every CONTAINS/CALLS/INHERITS/IMPLEMENTS expansion against the `relationships` table.
- **`scripts/analyze_q3_rankings.py`** — prints full ranked entity list for the Q3 disambiguation query so you can inspect the cosine similarity score gap.

### How to run
```bash
cd platform

# Replace <DB_URL> with DATABASE_URL from .env
# Replace <repo_id> with the ID from the ingest step
python scripts/validate_retrieval.py ^
  --db-url "<DB_URL>" ^
  --repo-id <repo_id> ^
  --manifest ../sample-repo/test-manifest.json

# Q3 ranking detail
python scripts/analyze_q3_rankings.py
```

### Expected output
```
[PASS] multi_hop_call_chain
[PASS] multi_level_inheritance
[PASS] interface_implementation
[PASS] method_disambiguation
[PASS] textual_similarity_no_conflation
[PASS] orphan_file_isolation
All N expansion edges verified against DB relationships table.
FINAL RESULT: ALL PASSED
```

### What needs to be added

The current `validate_retrieval.py` script verifies pass/fail per scenario and
checks expansion integrity, but does not compute numeric retrieval quality
metrics. These three additions would make the evaluation quantitatively rigorous:

1. **Precision@K and Recall@K computation** — the script prints ranked entity
   IDs but does not compute P@K or MRR numerically. For each scenario where
   `relevant_entity_ids` is defined in the manifest, compute these metrics
   using the implementation sketch below.

2. **Graph expansion noise metric** — the ratio of expansion-added entities
   that are in the manifest's `relevant_entity_ids` vs. those that are not.
   A high noise ratio means the graph expander is pulling in irrelevant context,
   inflating the token budget without adding useful signal.

3. **Token budget utilisation** — track `total_tokens_est / token_budget` and
   the `truncated` flag per scenario. A consistently truncated context is a
   signal that the budget is too tight or retrieval is too noisy.

### Precision@K and MRR — implementation sketch

Add this function to `validate_retrieval.py` and call it per scenario after
collecting `retrieved_ids` from the retrieval result:

```python
def compute_metrics(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> dict:
    hits = [1 if eid in relevant_ids else 0 for eid in retrieved_ids[:k]]
    precision_at_k = sum(hits) / k
    first_hit = next((i + 1 for i, h in enumerate(hits) if h), None)
    mrr = 1 / first_hit if first_hit else 0.0
    recall_at_k = sum(hits) / len(relevant_ids) if relevant_ids else 0.0
    return {"precision@k": precision_at_k, "recall@k": recall_at_k, "mrr": mrr}
```

Until this is implemented, these metrics are recorded as "not computed" in the
result file. The 6-scenario pass/fail and expansion integrity check are the
primary quality signals for this evaluation run.

### Extended metrics (once implemented)

| Metric | Formula | Target |
|---|---|---|
| Precision@10 | relevant hits in top 10 / 10 | ≥ 0.5 |
| Recall@10 | relevant hits in top 10 / total relevant | ≥ 0.7 |
| MRR | 1 / rank of first relevant hit | ≥ 0.7 |
| Graph expansion noise ratio | non-relevant expanded / total expanded | ≤ 0.3 |
| Token budget utilisation | total_tokens_est / token_budget | ≤ 0.9 |
| Truncated flag | any scenario where context was truncated | 0 scenarios |

### Pass criteria
- All 6 scenarios: `[PASS]`
- DB relationship audit: `All N expansion edges verified against DB relationships table.`
- `FINAL RESULT: ALL PASSED`
- `format_audit_log` at rank 1 in `analyze_q3_rankings.py` output
- Precision@K, Recall@K, MRR: record as "not computed" until script is updated

### Failure diagnosis
| Symptom | Cause | Fix |
|---|---|---|
| `[FAIL] method_disambiguation` | Vector similarity conflating two `validate` methods | Embedding quality issue — check if `voyage-code-3` was used |
| `[FAIL] orphan_file_isolation` | Expander following non-existent edges | Check `relationship_expander.py` edge type filtering |
| `N of M expansion edges NOT in DB` | Expander fabricated relationships — **critical** | Full re-index from clean slate; check resolver output |
| `[FAIL] multi_hop_call_chain` | Multi-hop traversal incomplete | Check BFS depth in `relationship_expander.py` |
| `--manifest file not found` | Wrong path | Run from `platform/`; path is `../sample-repo/test-manifest.json` |

### Result recording location
`evaluation-results/pillar3-retrieval.md`

---

## Pillar 4 — Answer Quality

### What it measures
Whether the LLM-generated answer is factually correct about the codebase, complete (covers the key entities), and grounded (doesn't claim things beyond what the retrieved context supports).

### Metrics
| Metric | Description | How measured |
|---|---|---|
| Correctness | Is the answer factually right about the code? | Manual: human reads answer + code |
| Completeness | Did it mention all key entities for this question? | Check expected entity names appear in answer text |
| Groundedness | Does it only claim things the context supports? | Hallucination rate from citation validator |
| Provider used | Which LLM provider answered? | `provider` field in `AskResponse` |
| Response time | Wall-clock time for the full ask pipeline | Measured in script |

### Canonical question set (4 questions — regression baseline)
Every re-run must produce answers that meet or exceed the original hallucination rate of 0.0%.

| ID | Question | Key entities expected in answer |
|---|---|---|
| Q1 | Walk me through what happens when a user logs in, from entry point to completion | `login_user`, `AuthService.validate`, `UserModel`, `auth_service.py` |
| Q2 | What does AdminUser inherit and how does permission checking work? | `AdminUser`, `UserModel`, `BaseModel`, `check_permission` |
| Q3 | Is there any function in this codebase that has no dependencies on other code? | `format_audit_log`, `format_user_record`, `truncate_text` |
| Q4 | What does the validate method do? | `AuthService.validate`, `UserModel.validate`, disambiguation present |

### Existing test infrastructure
- **`tests/test_ask_endpoint.py`** — fires all 4 questions at `POST /repositories/<repo_id>/ask` via HTTP, collects `AskResponse` JSON, prints per-question and overall citation stats.
- **`known-limitations.md §1`** — raw output from the verified run: 67 total citations, 0.0% hallucination rate.

### How to run
```bash
cd platform

# API server must be running first (python run.py in another terminal)
python tests/test_ask_endpoint.py
```

### Expected output
```
Q    Provider     Time  Total   Def    CS   Bad  Hall%
----------------------------------------------------------------------
1    gemini      26.9s     25    25     0     0  0.0%
2    gemini      21.5s     18    18     0     0  0.0%
3    gemini      11.4s     18    18     0     0  0.0%
4    gemini       9.2s      6     6     0     0  0.0%
----------------------------------------------------------------------
OVERALL         total=67  unsupported=0  hallucination_rate=0.0%
ALL ASSERTIONS PASSED ✓
```

### Pass criteria
- `hallucination_rate == 0.0` for all 4 canonical questions
- All required entity names appear in answer text (see completeness check table in result file)
- `provider` is either `"groq"` or `"gemini"` (not `"unknown"`)
- `ALL ASSERTIONS PASSED ✓` in output

### Failure diagnosis
| Symptom | Cause | Fix |
|---|---|---|
| `Hall% > 0.0%` on any Q | LLM cited a non-existent or un-backed line | Read `UNSUPPORTED CITATIONS` block; increase `top_k` or check resolvers |
| `provider: unknown` | Both Groq and Gemini failed | Check API keys in `.env`; see `logs/app.log` for `LLMProviderError` |
| `total_citations: 0` for any Q | LLM answered in uncited prose | Check `code_qa_agent.py` structured output addendum |
| `HTTP 500` | Pipeline crashed | Check `logs/app.log` for traceback |

### Result recording location
`evaluation-results/pillar4-answers.md`


---

## Pillar 5 — Citation Quality

### What it measures
Whether the citation validator correctly classifies all three citation types, and whether the hallucination rate metric is trustworthy — not just returning 0.0% because the classifier is too lenient.

### The 3-way taxonomy
| Category | Label | Meaning |
|---|---|---|
| (a) | `definition` | Cited range overlaps a real entity's declared lines AND the entity name appears in preceding text, OR a non-CALLS relationship (IMPORTS / INHERITS / IMPLEMENTS / CONTAINS / INSTANTIATES) backs the claim |
| (b) | `call_site` | Preceding text describes an invocation AND a real CALLS edge exists in the DB from caller to callee at that line |
| (c) | `unsupported` | File/line not in context, OR no relationship of any type backs the claim — **true hallucination** |

### Metrics
| Metric | Target |
|---|---|
| Hallucination rate (unsupported / total) | 0.0% on canonical 4-question set |
| Definition citations correctly classified | All `def` column entries back a real entity declaration |
| Call-site citations correctly classified | All `CS` column entries have a real CALLS edge in DB |
| Parent-chain walking | IMPORTS edges on module entities correctly found for method-level citations |
| Fuzzy file path matching | Citations using short paths correctly matched to full-path context entities |

### Existing infrastructure
- **`src/generation/citation_validator.py`** — 3-way classification, fuzzy path matching, parent-chain walking (up to 3 levels), CONTAINS-child check.
- **`known-limitations.md §1`** — verified 67-citation run with 0.0% hallucination rate. Documents two bugs found and fixed during verification:
  - Validator originally only checked CALLS edges — missed IMPORTS/INHERITS/IMPLEMENTS → fixed to check all relationship types
  - Validator only walked 1 level up parent chain — missed module-level IMPORTS for method citations → fixed to walk up to 3 levels
- **`step7-hardening-report.md`** — full history: original false 21.9% hallucination rate (validator too strict), evolution to 0.0% after 3-way classification was introduced.

### How to run
Citation quality is measured through the same run as Pillar 4. The `Def`, `CS`, and `Bad` columns in `test_ask_endpoint.py` output are the citation quality metrics.

```bash
cd platform
python tests/test_ask_endpoint.py
# The Def / CS / Bad columns ARE the Pillar 5 output
```

To understand any unsupported citation, read the `UNSUPPORTED CITATIONS` block
printed for that question. Each line shows the raw citation and the reason
it was classified as unsupported.

### Pass criteria
- Hallucination rate on 4-question canonical set: `0.0%`
- Zero `unsupported_citations` across all 4 questions
- Every `definition` citation: entity name present in surrounding prose AND line range overlaps a real entity
- Every `call_site` citation: a real CALLS/IMPORTS/INHERITS edge in DB backs the claim

### Failure diagnosis — if hallucination rate > 0%
| Reason in output | Meaning | Fix |
|---|---|---|
| `"file path not in context"` | LLM cited a file that was not retrieved | Increase `top_k` to 20 |
| `"no relationship backing found"` | File/line in context but no DB edge backs it | Re-run Area 1 to confirm resolvers produced expected relationships |
| `"entity name mismatch"` | Fuzzy match failed to link citation to entity | Check `citation_validator.py` parent-chain walking depth |

### Result recording location
`evaluation-results/pillar5-citations.md`

> **Note:** Pillars 4 and 5 share a single test run (`test_ask_endpoint.py`).
> Pillar 4 = the answer text quality (correctness, completeness).
> Pillar 5 = the citation classification quality (Def/CS/Bad columns, hallucination rate).
> Record both in their respective result files from the same terminal output.


---

## Pillar 6 — Memory & Agentic System

### What it measures
Whether the Query Planner, Answer Agent loop, Short-Term Memory (STM), and Long-Term Memory (LTM) session cache all behave correctly — independently of answer quality, which is covered in Pillars 4 and 5.

The key principle: **agents are evaluated through their outputs, not their internals.** The iteration count, LTM hit rate, and planner strategy are the agent evaluation.

### Components under evaluation
| Component | Source file | What is checked |
|---|---|---|
| Query Planner | `src/agents/query_planner.py` | Correct intent + strategy classification for the 4 canonical questions |
| Answer Agent loop | `src/agents/code_qa_agent.py` | `iteration_count ≤ 1` for all 4 questions; `answer_status` always `answered` |
| STM deduplication | `src/pipeline/memory.py` | `visited_entity_ids` grows across iterations; re-retrieval never re-fetches already-seen entities |
| LTM session cache (Tier 3) | `src/memory/ltm/session_knowledge.py` | Write after answered turn; cache hit on repeat; stale entry discarded after re-index |
| LTM Tiers 1 & 2 | `src/memory/ltm/user_memory.py`, `user_repo_preference.py` | Out of scope — `AUTH_ENABLED=false` |

### Metrics
| Metric | Target |
|---|---|
| Planner: strategy for Q1–Q4 | `semantic_search` or `semantic_search_with_graph` (not `repository_walk`) |
| Planner: confidence | > 0.0 for all questions (0.0 = LLM fallback, planner call failed) |
| Agent: `answer_status` | `answered` on all 4 questions — non-negotiable |
| Agent: `iteration_count` | ≤ 1 on all 4 questions (0 = answered on first pass) |
| LTM write | 1 row in `conversation_memory` after first answered turn |
| LTM cache hit | `step=ltm hit=true` on second call with same `session_id` |
| LTM stale detection | `step=ltm hit=false` after re-index (old entry discarded) |

### How to run

**This pillar produces no new terminal commands.** Evidence comes from:

1. `platform/logs/pipeline.log` — generated automatically during the Pillar 4 run
2. Two manual curl commands
3. Two SQL queries in Supabase

---

#### Step 6A — Query Planner

After running `tests/test_ask_endpoint.py`, open `platform/logs/pipeline.log`
and search for `step=planner`. You will find one entry per question.

Expected log format:
```
step=planner  intent=feature  strategy=semantic_search  conf=0.87
```

Expected classifications for the 4 canonical questions:

| Question | Expected intent | Expected strategy |
|---|---|---|
| Q1 — login flow end-to-end | `feature` or `dependency_flow` | `semantic_search` or `semantic_search_with_graph` |
| Q2 — AdminUser inheritance | `feature` or `dependency_flow` | `semantic_search_with_graph` |
| Q3 — function with no dependencies | `specific_lookup` or `feature` | `semantic_search` |
| Q4 — what does validate do? | `specific_lookup` or `feature` | `semantic_search` |

**Failure signal:** `strategy=repository_walk` or `strategy=repository_overview`
for any of Q1–Q4 means the planner mis-classified a specific code question as a
repo overview. The pipeline will run BFS traversal instead of targeted vector
search, producing a generic answer with fewer precise citations.

---

#### Step 6B — Answer Agent Loop (STM)

In `pipeline.log`, search for `step=post-reretrieval`:

```
step=post-reretrieval-1  new_chunks=3  reason=insufficient
```

If this line **does not appear**: the agent answered on the first retrieval pass
for all 4 questions. This is the ideal outcome.

If it **does appear**: note which question triggered it.
- One re-retrieval is acceptable — means the first `top_k` retrieved entities were
  not enough, but the second pass filled the gap.
- `step=post-reretrieval-2` on the same question means the agent hit the
  `_MAX_ITERATIONS=2` cap — a signal that `top_k` is too small for that query.

Also search for `step=final` to read the STM state at pipeline completion:

```
step=final  answer_status=answered  iteration_count=0  strategy=semantic_search
```

`answer_status` must always be `answered` — the orchestrator forces best-effort
on the last iteration and never exits with `insufficient`.

---

#### Step 6C — LTM Session Cache (Tier 3)

**Test 1: Write + cache hit**

Run the same question twice with the same `session_id`:

```bash
# First call — cache miss expected
curl -X POST http://localhost:8000/repositories/<repo_id>/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"query\": \"Walk me through what happens when a user logs in\", \"session_id\": \"eval-session-001\", \"top_k\": 10}"
```

After the response, verify the LTM entry was written:

```sql
SELECT feature_name, confidence, exploration_status, repo_indexed_at, created_at
FROM conversation_memory
WHERE session_id = 'eval-session-001'
ORDER BY created_at DESC;
```

Expect: 1 row with `exploration_status = 'complete'`.

Then search `pipeline.log` for the two `step=ltm` entries:
```
# First call:
step=ltm  hit=false

# Second call (same command, same session_id):
step=ltm  hit=true  feature_name=feature  summary="..."
```

```bash
# Second call — cache hit expected (run the exact same command again)
curl -X POST http://localhost:8000/repositories/<repo_id>/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"query\": \"Walk me through what happens when a user logs in\", \"session_id\": \"eval-session-001\", \"top_k\": 10}"
```

**Test 2: Stale detection after re-index**

Re-index the same repo:

```bash
curl -X POST http://localhost:8000/repositories ^
  -H "Content-Type: application/json" ^
  -d "{\"source\": \"../sample-repo\"}"
```

Wait for `"status": "ready"`, then run the same question again:

```bash
curl -X POST http://localhost:8000/repositories/<repo_id>/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"query\": \"Walk me through what happens when a user logs in\", \"session_id\": \"eval-session-001\", \"top_k\": 10}"
```

Expect `step=ltm hit=false` in the log — the old entry was written before the
re-index and its `repo_indexed_at` is now older than `repositories.indexed_at`,
so it is discarded as stale.

Confirm with SQL:

```sql
SELECT
    cm.feature_name,
    cm.repo_indexed_at            AS ltm_written_at,
    r.indexed_at                  AS repo_reindexed_at,
    CASE
        WHEN cm.repo_indexed_at < r.indexed_at THEN 'STALE'
        ELSE 'FRESH'
    END                           AS status
FROM conversation_memory cm
JOIN repositories r ON r.id = cm.repo_id
WHERE cm.session_id = 'eval-session-001';
```

Expect: `status = 'STALE'` for the old entry.

---

#### Step 6D — LTM Tiers 1 & 2 (User & Repo Facts)

**Status: Out of scope — `AUTH_ENABLED=false` in current dev setup.**

These tiers store global user preferences and per-repo knowledge facts, and
are only active for authenticated users. They are additive (injected into the
LLM prompt but do not change the retrieval path), so their absence does not
affect Pillars 1–5.

To evaluate if auth is enabled: see `docs/evaluation-plan-v2.md` → Pillar 7D
for detailed steps.

### Pass criteria
| Check | Target |
|---|---|
| Planner: no `repository_walk` for Q1–Q4 | Pass |
| Planner: confidence > 0.0 | Pass |
| `answer_status = answered` all 4 Qs | Always |
| `iteration_count ≤ 1` all 4 Qs | Pass |
| LTM write after Q1 answered | 1 row in `conversation_memory` |
| LTM cache hit on second call | `hit=true` in log |
| LTM stale detection after re-index | `hit=false` in log, SQL shows `STALE` |
| LTM Tiers 1 & 2 | Out of scope (`AUTH_ENABLED=false`) |

### Result recording location
`evaluation-results/pillar6-memory-agents.md`


---

## Execution Order

Run these in sequence. Each pillar depends on the previous one being green.

### Phase 1 — Re-run existing scripts, capture raw output

| Step | Command | Output file |
|---|---|---|
| 0 | Clean DB (SQL: `DELETE FROM repositories`) | — |
| 0 | Index sample-repo via API | — |
| 1.1 | `python scripts/validate_against_manifest.py` | `evaluation-results/pillar1-extraction.md` |
| 1.2 | `python scripts/verify_storage.py` | `evaluation-results/pillar2-storage.md` |
| 1.3 | `python scripts/validate_retrieval.py --db-url <DB_URL> --repo-id <repo_id> --manifest ../sample-repo/test-manifest.json` | `evaluation-results/pillar3-retrieval.md` |
| 1.4 | `python scripts/analyze_q3_rankings.py` | append to `pillar3-retrieval.md` |
| 1.5 | `python tests/test_ask_endpoint.py` | `evaluation-results/pillar4-answers.md` and `pillar5-citations.md` |
| 1.6 | Read `pipeline.log`, run LTM curl commands, run SQL queries | `evaluation-results/pillar6-memory-agents.md` |

For each: paste the full terminal output into the result file, fill in the
key numbers table, write a verdict paragraph.

### Phase 2 — Checklist

- [ ] Supabase: `DELETE FROM repositories` + verify 0 rows
- [ ] Supabase: `TRUNCATE conversation_memory` + `TRUNCATE conversations CASCADE`
- [ ] `python run.py` — API running
- [ ] Ingest sample-repo, note `repo_id`, wait for `ready`
- [ ] `python scripts/validate_against_manifest.py` → paste output → Pillar 1
- [ ] `python scripts/verify_storage.py` → paste output → Pillar 2
- [ ] `python scripts/validate_retrieval.py ...` → paste output → Pillar 3
- [ ] `python scripts/analyze_q3_rankings.py` → append to Pillar 3
- [ ] **Stop if any Pillar 1/2/3 check fails** — fix before continuing
- [ ] `python tests/test_ask_endpoint.py` → paste output → Pillar 4 + 5
- [ ] Read `pipeline.log` for `step=planner`, `step=final`, `step=post-reretrieval`
- [ ] Run LTM curl ×2, run stale-detection curl + re-index
- [ ] Run SQL queries for LTM verification → Pillar 6
- [ ] Fill in all result file verdict sections

---

## Result File Template

Each file in `evaluation-results/` follows this structure exactly:

```markdown
# Pillar N — [Name] Evaluation Results

**Run date:** YYYY-MM-DD  
**Run by:** [name or "dev"]  
**Environment:**
- DB: postgresql://postgres:...@db.[ref].supabase.co:5432/postgres
- Embedding model: voyage-code-3 (1024 dimensions)
- Generation model: groq/llama-3.3-70b-versatile → gemini/gemini-2.5-flash (fallback)
- Repo: sample-repo (62 entities, 87 relationships)
- Script: `platform/scripts/[script_name].py`

---

## Raw Output

[paste full terminal output here — unedited]

---

## Key Numbers

| Metric | Value | Target | Pass? |
|---|---|---|---|
| ... | ... | ... | ✅ / ❌ |

---

## Verdict

**PASS** / **FAIL** / **PARTIAL**

[One paragraph explaining what passed, what failed, and why.]

---

## Notes

[Any anomalies, comparisons to prior runs, follow-up items.]
```

---

## Known Limitations (Open Items)

These are documented design boundaries, not evaluation failures. Reference them
in result files if they affected the run.

| # | Limitation | Impact on evaluation |
|---|---|---|
| 6 | Language support limited to Python + TypeScript | Extraction and retrieval tests only cover these two languages |
| 7 | Variable entities require re-indexing for existing repos | Starting from clean slate (Phase 0) ensures this is not an issue |
| 8 | Re-indexing required for new entity types | N/A for freshly-indexed sample-repo |
| 9 | No graph pagination for >200-file repos | sample-repo is small; not affected |
| — | LTM Tiers 1 & 2 require `AUTH_ENABLED=true` | Pillar 6D out of scope for dev setup |
| — | Voyage AI free tier: 3 RPM, ~1M tokens/month | Ingestion: 2–5 min; monthly cap may affect repeated runs |

---

## Quick Reference — All Commands

```bash
# Working directory
cd p:\EasyRepo\platform
.venv\Scripts\activate

# Start API server
python run.py

# Ingest (Windows CMD)
curl -X POST http://localhost:8000/repositories ^
  -H "Content-Type: application/json" ^
  -d "{\"source\": \"../sample-repo\"}"

# Poll status
curl http://localhost:8000/repositories/<repo_id>/status

# Pillar 1 — Extraction
python scripts/validate_against_manifest.py

# Pillar 2 — Storage
python scripts/verify_storage.py

# Pillar 3 — Retrieval
python scripts/validate_retrieval.py ^
  --db-url "postgresql://postgres:<PWD>@db.<REF>.supabase.co:5432/postgres" ^
  --repo-id <repo_id> ^
  --manifest ../sample-repo/test-manifest.json

# Pillar 3 — Q3 ranking detail
python scripts/analyze_q3_rankings.py

# Pillars 4 + 5 — Answer & Citation quality
python tests/test_ask_endpoint.py

# Pillar 6 — LTM first call
curl -X POST http://localhost:8000/repositories/<repo_id>/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"query\": \"Walk me through what happens when a user logs in\", \"session_id\": \"eval-session-001\", \"top_k\": 10}"

# (Run the same command a second time for the cache hit test)

# Supabase SQL — LTM entries
# SELECT feature_name, confidence, exploration_status, repo_indexed_at
# FROM conversation_memory WHERE session_id = 'eval-session-001';

# Supabase SQL — stale detection
# SELECT cm.feature_name, cm.repo_indexed_at, r.indexed_at,
#   CASE WHEN cm.repo_indexed_at < r.indexed_at THEN 'STALE' ELSE 'FRESH' END AS status
# FROM conversation_memory cm
# JOIN repositories r ON r.id = cm.repo_id
# WHERE cm.session_id = 'eval-session-001';

# Priority order if time is limited:
# Phase 1 first (scripts exist, zero new code)
# Then Pillar 5 citations — highest regression risk
# Then Pillar 6 LTM cache — only needs 2 curls + SQL
```

