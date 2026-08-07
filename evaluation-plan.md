# EasyRepo — Evaluation & Documentation Plan

**Project:** AI Codebase Intelligence Platform  
**Purpose:** Validate, evaluate, and formally document all pipeline components  
**Status of this document:** Implementation plan — tracks what to run, how to run it, and where to record results

---

## Context: What Has Already Been Done

This project has been built with rigorous, evidence-first validation throughout. The following work is already complete and verified:

| What was verified | Where evidence lives | Outcome |
|---|---|---|
| Entity + relationship extraction against 62-entity / 87-relationship hand-built manifest | `scripts/validate_against_manifest.py` | 100% match |
| Storage layer: entity count, relationship counts, embedding dimensions, NULL-embedding check | `scripts/verify_storage.py` | All assertions passed |
| Vector search ranking quality (3 spot-check queries, disambiguation checks) | `scripts/verify_storage.py` + `scripts/analyze_q3_rankings.py` | Preferred entity ranks first in all 3 cases |
| Retrieval pipeline: 6 graph-expansion scenarios, 100% expansion-edges verified against real DB rows | `scripts/validate_retrieval.py` | All 6 scenarios passed |
| End-to-end citation validation: 4 canonical questions, 67 citations | `known-limitations.md` §1 | 0.0% hallucination rate |
| Groq → Gemini 429 fallback | `known-limitations.md` §1 | Real fallback confirmed (34 HTTP 429s) |
| Auth, rate-limiting, CORS hardening | `step7-hardening-report.md` | All 6 found issues fixed + documented |
| Async job queue (Procrastinate) | `known-limitations.md` §2 | Closed with architecture proof |
| INSTANTIATES relationship type | `known-limitations.md` §3 | Extracted, validated, cited |
| Collision-resistant repo_id + user/access system | `known-limitations.md` §5 | 5 behavioural test cases passing |

**What is missing:** All of the above was validated informally with scripts and manual inspection. There is no structured evaluation framework, no pytest test suite for the pipeline components, no documented result tables, and no reproducible evaluation harness that can be re-run to catch regressions.

**Goal of this plan:** Build that framework, run it, and produce documented results with explanation for every component.

---

## Evaluation Pillars

The pipeline has five independently testable components. Each has its own metrics, test data, and pass criteria.

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
```

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

### What needs to be added
1. **Formal pytest wrapper for the manifest validation** — convert `validate_against_manifest.py` into a pytest test that asserts 100% match rates and fails on any deviation. Currently it exits with a code but is not part of a test suite.
2. **TypeScript extraction test** — `test_extraction.py` only has a Python snippet test. Need an equivalent for a `.tsx` file with a class, interface, and method.
3. **Variable entity test** — the `variable` entity type was added to the Python adapter later (known limitation §7). Need a dedicated test: a file with only module-level dict/list assignments, asserting `type="variable"` entities appear with correct names and line ranges.
4. **Edge case tests:**
   - File with multiple inheritance (`class Child(ParentA, ParentB)`)
   - TypeScript `interface` with method signatures (IMPLEMENTS edges)
   - `__init__.py` with re-exports (IMPORTS resolution)

### How to run
```bash
# Manifest validation (existing)
cd platform
python scripts/validate_against_manifest.py

# Unit tests (existing)
cd platform
python -m pytest tests/test_extraction.py -v

# After adding new tests:
python -m pytest tests/ -v
```

### Pass criteria
- `validate_against_manifest.py`: exit code 0, "SUCCESS: 100% Match" in output
- All pytest tests: green
- Zero line-range mismatches, zero parent structure mismatches

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
| Relationship counts per type | Exact: CONTAINS=48, IMPORTS=11, CALLS=23, INHERITS=2, IMPLEMENTS=3 |
| NULL embeddings | 0 |
| Embedding dimension | 1024 (EMBEDDING_DIM config constant) |
| Q1 ranking check | auth/authenticate/user/token keywords in top results |
| Q2 ranking check | `AuthService.validate` ranks above `UserModel.validate` |
| Q3 ranking check | `format_audit_log` ranks above `format_user_record` |

### Existing test infrastructure
- **`scripts/verify_storage.py`** — full verification: ingests sample-repo, checks counts, embedding integrity, runs 3 spot-check queries with ranking assertions. Passes/fails with explicit log lines.

### What needs to be added
1. **Pytest wrapper for `verify_storage.py`** — same as Pillar 1: the script works but is not a pytest test. The count assertions and ranking checks should become proper `assert` statements in a pytest file so they participate in CI.
2. **Embedding dimension regression test** — if `EMBEDDING_DIM` config ever changes, all stored embeddings become invalid. A test should assert `len(entity.embedding) == EMBEDDING_DIM` for at least one entity per repo.
3. **INSTANTIATES relationship count** — `verify_storage.py` was written before INSTANTIATES was added. The expected counts need to be updated and `INSTANTIATES` added to the verification dict.

### How to run
```bash
cd platform
python scripts/verify_storage.py
# Expect: "SUCCESS: All storage and embedding verifications passed perfectly!"
```

### Pass criteria
- All count assertions pass with no `AssertionError`
- All three ranking checks: "RANKING CHECK PASSED" in output
- Log line: "PASSED: All entities have non-null vector embeddings"

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

### What needs to be added
1. **Pytest wrapper** — same pattern as Pillars 1 and 2.
2. **Precision@K and Recall@K computation** — the current script prints ranked entity IDs but does not compute P@K or MRR numerically. Add computation for each scenario where `relevant_entity_ids` is defined in the manifest.
3. **Graph expansion noise metric** — ratio of expansion-added entities that are in the manifest's `relevant_entity_ids` vs. those that are not. Currently not computed.
4. **Token budget utilisation** — track `total_tokens_est / token_budget` and `truncated` flag per scenario. A consistently truncated context is a signal that the budget is too tight or retrieval is too noisy.

### Precision@K and MRR — implementation sketch

```python
def compute_metrics(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> dict:
    hits = [1 if eid in relevant_ids else 0 for eid in retrieved_ids[:k]]
    precision_at_k = sum(hits) / k
    first_hit = next((i + 1 for i, h in enumerate(hits) if h), None)
    mrr = 1 / first_hit if first_hit else 0.0
    recall_at_k = sum(hits) / len(relevant_ids) if relevant_ids else 0.0
    return {"precision@k": precision_at_k, "recall@k": recall_at_k, "mrr": mrr}
```

### How to run
```bash
cd platform
python scripts/validate_retrieval.py \
  --db-url postgresql://postgres:postgres@127.0.0.1:5435/easyrepo \
  --repo-id sample-repo \
  --manifest ../sample-repo/test-manifest.json
# Expect: "FINAL RESULT: ALL PASSED"
```

### Pass criteria
- All 6 scenarios: `[PASS]`
- DB relationship audit: "All N expansion edges verified against DB relationships table."
- `FINAL RESULT: ALL PASSED`

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

### Canonical question set (4 questions — already validated)
These 4 questions form the regression baseline. Every future re-run must produce answers that meet or exceed the original hallucination rate of 0.0%.

| ID | Question | Key entities expected in answer |
|---|---|---|
| Q1 | Walk me through what happens when a user logs in, from entry point to completion | `login_user`, `AuthService.validate`, `UserModel`, `auth_service.py` |
| Q2 | What does AdminUser inherit and how does permission checking work? | `AdminUser`, `UserModel`, `BaseModel`, `check_permission` |
| Q3 | Is there any function in this codebase that has no dependencies on other code? | `format_audit_log`, `format_user_record`, `truncate_text` |
| Q4 | What does the validate method do? | `AuthService.validate`, `UserModel.validate`, disambiguation present |

### Existing test infrastructure
- **`scripts/run_validation_queries.py`** — runs all 4 questions via the CLI, prints full output including citation validation report. Does not compute any numeric scores automatically.
- **`known-limitations.md` §1** — raw output from a verified run: 67 total citations, 0.0% hallucination rate.

### What needs to be added
1. **Structured output capture** — modify `run_validation_queries.py` (or create a new script) to call the FastAPI `/ask` endpoint directly and capture the JSON `AskResponse`, not just the printed output. This makes the results machine-readable.
2. **Completeness check** — for each question, define a list of entity names that *must* appear in the answer text. Assert their presence. Example:
   ```python
   Q1_REQUIRED_MENTIONS = ["login_user", "validate", "AuthService"]
   assert all(name.lower() in answer.lower() for name in Q1_REQUIRED_MENTIONS)
   ```
3. **Regression guard** — add a pytest test that calls `/ask` for all 4 questions, asserts `hallucination_rate == 0.0`, and asserts `provider in ("groq", "gemini")`.
4. **Extended question set (15–20 questions)** — for more comprehensive coverage. Suggested additions:
   - "What is the difference between `UserModel` and `AdminUser`?"
   - "How does the ingestion pipeline handle a failed clone?"
   - "What happens if Groq rate-limits the API?"
   - "Which files does `main.py` import from?"
   - "Does this repo implement any caching?" (negative question — tests false-positive rate)

### How to run
```bash
# Current approach (prints to stdout)
cd platform
python scripts/run_validation_queries.py

# After adding structured evaluation:
python scripts/evaluate_answers.py \
  --api-url http://localhost:8000 \
  --repo-id sample-repo \
  --output evaluation-results/pillar4-answers.json
```

### Pass criteria
- `hallucination_rate == 0.0` for all 4 canonical questions
- All required entity names appear in answer text
- `provider` is either `"groq"` or `"gemini"` (not `"unknown"`)

### Result recording location
`evaluation-results/pillar4-answers.md`

---

## Pillar 5 — Citation Quality

### What it measures
Whether the citation validator correctly classifies all three citation types, and whether the hallucination rate metric is trustworthy (i.e., not just returning 0.0% because the classifier is too lenient).

### The 3-way taxonomy
| Category | Label | Meaning |
|---|---|---|
| (a) | `definition` | Cited range overlaps a real entity's declared lines AND the entity name appears in preceding text, OR a non-CALLS relationship (IMPORTS/INHERITS/IMPLEMENTS/CONTAINS/INSTANTIATES) backs the claim |
| (b) | `call_site` | Preceding text describes an invocation AND a real CALLS edge exists in the DB from caller to callee at that line |
| (c) | `unsupported` | File/line not in context, OR no relationship of any type backs the claim — true hallucination |

### Metrics
| Metric | Target |
|---|---|
| Hallucination rate (unsupported / total) | ≤ 0.0% on canonical 4-question set |
| Classifier precision (definition) | Correctly classified definition citations / total labeled as definition |
| Classifier precision (call_site) | Correctly classified call-site citations / total labeled as call_site |
| False negative rate | Citations labeled `definition` that are actually wrong about the code |
| Parent-chain walking | IMPORTS edges on module entities correctly found for method-level citations |
| Fuzzy file path matching | Citations using short paths correctly matched to full-path context entities |

### Existing infrastructure
- **`src/generation/citation_validator.py`** — full validator with 3-way classification, fuzzy path matching, parent-chain walking (up to 3 levels), CONTAINS-child check.
- **`known-limitations.md` §1** — verified 67-citation run with 0.0% hallucination rate and explanation of two bugs that were fixed during verification.
- **`step7-hardening-report.md`** — documents the full history: 6 issues found and fixed, including the original false "21.9% hallucination rate" caused by the validator being too strict, and the final 0.0% after the 3-way classification was introduced.

### What needs to be added
1. **Unit tests for `validate_citations()` in isolation** — this is the most critical gap. The validator has zero standalone unit tests. Its correctness has only been verified end-to-end. Needed test cases:

   ```python
   # Test: definition citation — entity name in preceding text, range overlaps
   def test_definition_citation_basic(): ...

   # Test: call-site citation — CALLS edge in DB, invocation language in preceding text
   def test_call_site_citation_verified_edge(): ...

   # Test: unsupported citation — file path not in context
   def test_unsupported_citation_unknown_file(): ...

   # Test: unsupported citation — CALLS claimed but no edge in DB
   def test_unsupported_citation_no_calls_edge(): ...

   # Test: IMPORTS edge on module entity found via parent-chain walk from method
   def test_imports_backed_via_parent_chain(): ...

   # Test: fuzzy file path matching (short path matches full path)
   def test_fuzzy_file_path_match(): ...

   # Test: CONTAINS child check prevents call-site misclassification
   def test_contains_child_classified_as_definition(): ...
   ```

2. **Labeled citation test set (50 examples)** — a JSON file with hand-labeled citation examples:
   ```json
   [
     {
       "answer_text": "The auth function is defined at [src/api/auth.py:45-67]",
       "expected_type": "definition",
       "context_entity_ids": ["py.api.auth.verify_api_key"]
     },
     ...
   ]
   ```
   Run `validate_citations()` on each and compare classifier output to label. This tests the classifier directly, independent of the LLM.

3. **Regression guard on hallucination rate** — a pytest test that re-runs the 4 canonical answers through the validator and asserts `hallucination_rate == 0.0`. This catches any validator regression even if the LLM answers don't change.

### How to run
```bash
# Unit tests (after writing them):
cd platform
python -m pytest tests/test_citation_validator.py -v

# End-to-end hallucination rate (existing, manual):
cd platform
python scripts/run_validation_queries.py
# Look for: "hallucination rate: 0.0%" in each Q output
```

### Pass criteria
- All unit tests green
- Hallucination rate on 4-question canonical set: `0.0%`
- Classifier precision on labeled test set: ≥ 95%

### Result recording location
`evaluation-results/pillar5-citations.md`

---

## Bonus: API & Integration Evaluation

### What it measures
End-to-end API behaviour: correct HTTP status codes, rate limiting, auth enforcement, progress polling, and graph endpoint correctness.

### What needs to be added
1. **API contract tests** — pytest + `httpx` or `requests` tests against a live dev server:
   ```python
   def test_health_check(): GET /health → 200, {"status": "healthy"}
   def test_ingest_returns_202(): POST /repositories → 202
   def test_status_polling(): GET /repositories/{id}/status → valid status string
   def test_ask_returns_answer(): POST /repositories/{id}/ask → answer + citations
   def test_graph_returns_nodes(): GET /repositories/{id}/graph → nodes + edges
   def test_rate_limit_enforced(): 31× POST /ask in 60s → 429 on 31st
   def test_auth_required_when_enabled(): with AUTH_ENABLED=true, no key → 401
   ```

2. **Graph structure assertions** — for `sample-repo`, assert:
   - Entry point is `py.api.main` (or `py.run`, whichever scores highest)
   - Known cross-file edges exist (e.g. `py.api.main` → `py.storage.db`)
   - Depth parameter is respected (depth=1 returns only 1-hop nodes from root)
   - `include_imports=false` removes IMPORTS edges

### Result recording location
`evaluation-results/bonus-api.md`

---

## Repository Structure for Evaluation Artifacts

```
EasyRepo/
  evaluation-plan.md                   ← this file
  evaluation-results/
    pillar1-extraction.md              ← raw output + analysis
    pillar2-storage.md
    pillar3-retrieval.md
    pillar4-answers.md
    pillar5-citations.md
    bonus-api.md
  platform/
    tests/
      test_extraction.py               ← exists (3 tests)
      test_extraction_typescript.py    ← to add
      test_citation_validator.py       ← to add (Pillar 5)
      test_retrieval_pipeline.py       ← to add (Pillar 3 pytest wrapper)
      test_api_contracts.py            ← to add (bonus)
    scripts/
      validate_against_manifest.py     ← exists
      verify_storage.py                ← exists
      validate_retrieval.py            ← exists
      run_validation_queries.py        ← exists
      analyze_q3_rankings.py           ← exists
      enrich_isolated_embeddings.py    ← exists
      evaluate_answers.py              ← to add (Pillar 4 structured output)
    evaluation/
      fixtures/
        citation_test_set.json         ← to add (50 labeled citations)
        retrieval_test_set.json        ← to add (query → expected entity IDs)
        answer_test_set.json           ← to add (question + reference answers)
```

---

## Execution Order

Run these in sequence. Each pillar depends on the previous one (you can't validate answers without a working retrieval pipeline).

### Phase 1 — Re-run existing scripts, capture raw output

These scripts already exist and work. The goal here is to re-run them cleanly and save their full output as the baseline record.

| Step | Command | Output file |
|---|---|---|
| 1.1 | `python scripts/validate_against_manifest.py` | `evaluation-results/pillar1-extraction.md` |
| 1.2 | `python scripts/verify_storage.py` | `evaluation-results/pillar2-storage.md` |
| 1.3 | `python scripts/validate_retrieval.py --manifest ../sample-repo/test-manifest.json` | `evaluation-results/pillar3-retrieval.md` |
| 1.4 | `python scripts/run_validation_queries.py` | `evaluation-results/pillar4-answers.md` |

For each: paste the full terminal output into the result file, then add a brief analysis section explaining what the numbers mean and whether they meet pass criteria.

### Phase 2 — Write missing unit tests

| Task | File to create | Pillar |
|---|---|---|
| TypeScript extraction test | `tests/test_extraction_typescript.py` | 1 |
| Variable entity extraction test | add to `test_extraction.py` | 1 |
| Citation validator unit tests (7 test cases listed in Pillar 5) | `tests/test_citation_validator.py` | 5 |
| Pytest wrapper for manifest validation | `tests/test_extraction_manifest.py` | 1 |

### Phase 3 — Build structured evaluation fixtures

| Task | File to create | Used by |
|---|---|---|
| 50 labeled citation examples | `evaluation/fixtures/citation_test_set.json` | Pillar 5 precision test |
| Query → expected entity IDs | `evaluation/fixtures/retrieval_test_set.json` | Pillar 3 P@K computation |
| Reference answers for 4 canonical questions | `evaluation/fixtures/answer_test_set.json` | Pillar 4 completeness check |

### Phase 4 — Document results

For each result file in `evaluation-results/`, write:
1. **What was run** — exact command, date, environment (DB URL, model used)
2. **Raw output** — full terminal output, unedited
3. **Key numbers** — extracted from the raw output in a table
4. **Pass/fail verdict** — explicit, with criteria stated
5. **Analysis** — what the numbers mean, any anomalies, comparison to prior runs

---

## Result File Template

Each file in `evaluation-results/` should follow this structure:

```markdown
# Pillar N — [Name] Evaluation Results

**Run date:** YYYY-MM-DD  
**Run by:** [name or "automated"]  
**Environment:**
- DB: postgresql://...@127.0.0.1:5435/easyrepo
- Model: voyage-code-3 (embeddings), groq/llama-3.3-70b-versatile or gemini/gemini-2.5-flash (generation)
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

These are not evaluation failures — they are documented design boundaries. Each result file should reference them if relevant.

| # | Limitation | Impact on evaluation |
|---|---|---|
| 6 | Language support limited to Python + TypeScript | Extraction and retrieval tests only cover these two languages |
| 7 | Variable entities require re-indexing for existing repos | Storage test expected counts may differ on old indexed repos |
| 8 | Re-indexing required for new entity types | N/A for evaluation against freshly-indexed sample-repo |
| 9 | No graph pagination for >200-file repos | Graph endpoint tests use small sample-repo; large-repo behaviour untested |

---

## Priority Order

If time is limited, tackle in this order:

1. **Phase 1** (re-run existing scripts, save output) — zero new code, highest immediate value
2. **Pillar 5 unit tests** (`test_citation_validator.py`) — the citation validator has zero standalone tests; one regression here is invisible
3. **Pillar 1 pytest wrappers** — convert the manifest script into a pytest test so it participates in `pytest tests/`
4. **Phase 3 fixtures** — labeled citation set and retrieval test set unlock numeric P@K and classifier precision
5. **Pillar 4 extended questions** — more questions stress-test generation quality beyond the 4-question baseline
6. **Bonus API tests** — important for production readiness but lower risk of regression than pipeline components

---

## Quick Reference — Existing Commands

```bash
# From platform/ directory

# Pillar 1 — Extraction
python scripts/validate_against_manifest.py

# Pillar 2 — Storage
python scripts/verify_storage.py

# Pillar 3 — Retrieval
python scripts/validate_retrieval.py \
  --db-url postgresql://postgres:postgres@127.0.0.1:5435/easyrepo \
  --repo-id sample-repo \
  --manifest ../sample-repo/test-manifest.json

# Pillar 3 — Q3 ranking detail
python scripts/analyze_q3_rankings.py

# Pillar 4 — Answer quality
python scripts/run_validation_queries.py

# Unit tests
python -m pytest tests/ -v

# Run all validation and save to file (from repo root)
python run_validate_to_file.py
```
