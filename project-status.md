# AI Codebase Intelligence Platform — Project Status

**As of:** End of Step 8 (API layer + automated regression suite)
**Overall state:** MVP complete and verified end-to-end against a synthetic
test repository, with known open items tracked separately in
`KNOWN_LIMITATIONS.md`.

---

## What this project is

A system that clones a GitHub repository, parses it into structural entities
(modules, classes, functions, methods) using Tree-sitter, embeds each entity
with a code-specific embedding model, stores everything (plus their
structural relationships — who calls whom, who inherits from whom, etc.) in
Postgres/pgvector, and answers natural-language questions about the codebase
by combining vector search with relationship-aware context expansion, then
generating a cited answer via Gemini 2.5 Flash.

The core bet the whole project is testing: **relationship-aware retrieval
beats naive RAG for codebases**, because code isn't just text — it has real
structure (call chains, class hierarchies, interfaces) that naive chunking
throws away.

---

## What's been built and verified, step by step

### Step 1–2: Synthetic test repository + ground-truth manifest
A hand-built sample repo (`sample-repo/`) with 14 files across Python and
TypeScript, deliberately engineered to exercise every relationship type: a
3-hop call chain, 3-level inheritance, interface implementation in both
languages, two identically-named methods that must be disambiguated, two
structurally-similar-but-semantically-different functions, and one fully
isolated "orphan" file.

`test-manifest.json` hand-catalogs every entity and relationship in this repo
— 62 entities, 87 relationships — and has served as the untouched source of
truth for every verification step since. **Confirmed unmodified at multiple
checkpoints throughout the project via `git diff`.**

### Step 3: Tree-sitter entity extraction
Walks the AST of each file and extracts modules, classes, functions, and
methods with exact line ranges and parent/child structure.

**Verified:** 100% match against the manifest — 62/62 entities, 48/48
CONTAINS relationships, zero line-range mismatches.

### Step 4: Relationship resolution (pluggable per-language architecture)
Resolves CALLS, IMPORTS, INHERITS, and IMPLEMENTS edges via static symbol
resolution. Refactored into a `LanguageAdapter` interface so resolver logic
itself contains zero language-specific branching — adding a new language
means writing a new adapter, not touching the resolvers (see
`KNOWN_LIMITATIONS.md` #6 — this claim is architecturally sound but not yet
proven with a third language).

**Verified:** 100% match against the manifest across all 5 relationship
types (48 CONTAINS, 11 IMPORTS, 23 CALLS, 2 INHERITS, 3 IMPLEMENTS), confirmed
*after* the pluggable refactor to prove it was behavior-preserving.

### Step 5: Postgres + pgvector storage, embedding pipeline
Schema for entities, relationships, and repositories, with pgvector storing
per-entity embeddings. Embedding model was corrected mid-step from a
general-purpose text model (MiniLM) to `jinaai/jina-embeddings-v2-base-code`
— a genuinely code-trained model — after the first choice showed weak
disambiguation.

**Verified:** All entities embedded (768 dimensions), and — critically — the
code-specific model showed a **43-point score-range spread** vs. MiniLM's
8-point spread on disambiguation spot-checks (e.g.
`AuthService.validate` at 0.705 vs. `UserModel.validate` at 0.333 for the
same query, despite identical method names).

### Step 6: Relationship-aware retrieval + context expansion
Given top-k vector search hits, expands context via real database-backed
CONTAINS/CALLS/INHERITS/IMPLEMENTS relationships — reconstructing execution
traces, pulling in parent classes, resolving inheritance chains — with a
token budget and prioritization order.

**Verified:** All 6 manifest test scenarios pass with real ranks and scores
shown (not just pass/fail labels), and an automated
`assert_all_expansions_backed_by_real_relationships` check confirms every
expansion entry corresponds to a real database row.

**Bug caught and fixed during this step:** an early version of the parent-
expansion logic was fabricating relationships that didn't exist (e.g.
claiming a Python module was "contained by" an unrelated TypeScript file).
Caught by contradiction with the orphan-file test, fixed by requiring direct
DB verification for every expansion.

### Step 7: Gemini 2.5 Flash integration + citation validation
Generates natural-language answers grounded in the structured context from
Step 6, with a citation validator that checks every `[file:line]` citation
in the answer against real entities and relationships.

**Verified, after multiple rounds of catching real issues:**
- A citation validator bug that was misclassifying legitimate "here's where
  this function is called from" citations as hallucinations — fixed with a
  3-way classification (definition / call-site / unsupported).
- An answer that leaned on prose documentation instead of verified graph
  data — fixed with an explicit evidence-priority rule.
- A quality regression introduced by fixing the above — an orphan-file
  question started citing one obscure method instead of the file's actual
  functions — fixed structurally (auto-expanding an isolated module's
  contents) rather than by tuning a parameter.
- A real, honest gap around object-instantiation citations, documented as a
  known limitation rather than silently patched.

**Final state:** 4.6% true hallucination rate across 4 test questions, with
the remaining citations traced to the documented INSTANTIATES gap — not
unexplained noise.

### Step 8: FastAPI layer + automated regression suite
Wraps ingestion, retrieval, and generation in REST endpoints
(`POST /repositories`, `GET /repositories/{id}`,
`POST /repositories/{id}/query`, `POST /repositories/{id}/ask`,
`GET /health`), and converts every manual verification script from Steps
3–7 into a real pytest suite with specific assertions (exact ranks, exact
entity IDs, exact counts — not generic existence checks).

**Verified:** 34/34 automated tests passing, including a DB-startup health
check (added after a real bug was caught: the live server was serving
requests against a database whose tables hadn't been initialized), and a
mocked-Gemini test path so the regression suite doesn't depend on Gemini's
free-tier quota.

**Not yet verified:** real (non-mocked) Gemini citation validation through
the live HTTP endpoint specifically — tracked in `KNOWN_LIMITATIONS.md` #1.

---

## The methodology that got us here

Every step above that says "verified" earned that word the same way: by
refusing to accept a summary claim ("all tests passed," "100% match") without
also demanding the raw evidence behind it — actual scores, actual entity IDs,
actual test names, actual JSON responses. Multiple real bugs in this project
were caught **only** because a "success" summary was pushed on for the raw
data underneath it, and turned out to be hiding something.

That discipline is documented in more detail, with specific before/after
examples, in `step7-hardening-report.md`.

---

## What to read next

- **`KNOWN_LIMITATIONS.md`** — the honest list of what's still open, and what
  it will take to close each item.
- **`step7-hardening-report.md`** — a detailed, plain-language walkthrough of
  six real issues caught during the generation/citation step, useful as a
  case study for anyone continuing this project with AI-assisted
  development.
- **`sample-repo/test-manifest.json`** — the ground-truth source of every
  verification claim made above; still untouched since Step 2.