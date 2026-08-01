# Known Limitations — AI Codebase Intelligence Platform

This file tracks things the platform intentionally does **not** yet do, or has
not yet **proven** it does, as of the end of Step 8 (API layer + regression suite).

The rule for this file: an item stays here until it has been closed with the
same standard of evidence used everywhere else in this project — raw output,
not a summary claim. Nothing gets marked "done" based on a plan to do it.

---

## 1. Live end-to-end citation validation through the API is unverified

**Status:** Closed (2026-08-01)

**Evidence — final run of `test_ask_endpoint.py` (after citation validator fix):**

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

**Evidence — real 429 fallback test (`test_429_fallback.py`):**

```
Groq requests fired: 35  (Groq-only, no fallback allowed)
Real HTTP 429s from Groq: 34
Final cascade request provider: GEMINI
VERDICT: Real 429 fallback CONFIRMED.
  - Groq returned genuine HTTP 429 rate-limit responses
  - Subsequent cascade request successfully routed to Gemini
  - This is a real fallback-on-429 event, not preference-based routing
```

**Two issues found and fixed during verification (not assumed away):**

1. **Citation validator only checked CALLS edges for non-definition citations.**
   All 4 original "unsupported" citations were backed by real IMPORTS edges in
   the database (import statements, constructor DI parameters, type annotations,
   class declarations referencing imported types). Fixed: validator now checks
   IMPORTS, INHERITS, IMPLEMENTS, and CONTAINS as valid backing before classifying
   a citation as unsupported. Hallucination rate: 7.0% → 2.7% → 0.0%.

2. **Validator only walked one level up the entity parent chain for IMPORTS.**
   IMPORTS edges live on the module entity, not on child class/method entities.
   A method (`save`) two levels deep missed the module-level IMPORTS edge.
   Fixed: validator now walks the full parent chain (up to 3 levels).

**What was verified:**
- `POST /repositories/sample-repo/ask` returns real LLM-generated answers
- `citations` object contains real 3-way classification with 0 true hallucinations
  across 67 citations on the 4-question canonical test set
- `provider` field correctly reports `"groq"` or `"gemini"`
- Groq → Gemini fallback triggers on real HTTP 429 responses (34 confirmed)
- Citation validator correctly handles all relationship types as backing evidence

---

## 2. No async/background job queue for repository ingestion

**Status:** Closed (2026-08-01) — upgraded to Procrastinate (Postgres-backed)

**What was implemented:** `POST /repositories` returns `202 Accepted`
immediately with `status: "pending"`. The job is persisted in a
`procrastinate_jobs` table in the existing Postgres database — durable across
API restarts, with automatic retry (3 attempts, 60 s wait).

**Architecture:**
- `src/jobs/queue.py` — `procrastinate.App` with `PsycopgConnector` (async,
  psycopg v3).  `ingest_repo_task` is registered as a sync task; procrastinate
  runs it in a thread-pool so the event loop is never blocked.
- `src/api/main.py` lifespan — opens the connector pool, applies the
  procrastinate DDL (idempotent), starts an in-process worker as an asyncio
  task on the `"ingestion"` queue, cancels it cleanly on shutdown.
- `src/api/routers/repositories.py` — `POST /repositories` calls
  `ingest_repo_task.defer_async(...)`.  No `BackgroundTasks` dependency.

**Polling flow (unchanged from consumer's perspective):**
1. `POST /repositories` → `202 { "repo_id": "...", "status": "pending" }`
2. `GET /repositories/{id}/status` → `{ "status": "indexing" }` (while running)
3. `GET /repositories/{id}/status` → `{ "status": "ready" }` or `{ "status": "failed" }`

**New dependencies:** `procrastinate>=3.9.0`, `psycopg[binary]>=3.3.0`

**Remaining trade-off:** Worker runs in-process with the API. To scale workers
independently, run `procrastinate --app=src.jobs.queue.task_queue worker`
as a separate process and remove `run_worker_async` from the lifespan.

---

## 3. INSTANTIATES is not a modeled relationship type

**Status:** Closed (2026-08-01)

**What was implemented:** Extended the relationship taxonomy to include
INSTANTIATES — a distinct edge type meaning "this function/method/module
creates an instance of that class", separate from CALLS (which tracks
function-to-function invocations).

**Files changed:**
- `src/extraction/models.py` — Added `"INSTANTIATES"` to the `Relationship.type`
  literal (type-safety; no schema migration needed since `relationships.type` is
  an unconstrained `VARCHAR(50)`).
- `src/resolution/instantiates_resolver.py` — New resolver. Scans function /
  method / module source for `ClassName(...)` (Python) and `new ClassName(...)`
  (TypeScript) patterns, resolves each `ClassName` to an in-repo class entity
  via the symbol table, and emits `INSTANTIATES` edges. Module-level bodies are
  stripped of child-entity lines before scanning to avoid double-attribution.
- `src/resolution/__init__.py` — Wired in as step 5 of `resolve_relationships()`.
- `src/retrieval/relationship_expander.py` — Outgoing BFS traversal now includes
  `"INSTANTIATES"` alongside `"CALLS"` so that classes being constructed appear
  in retrieval context.
- `src/generation/citation_validator.py` — Added `"INSTANTIATES"` to the
  backing-relationship check; citations backed by an instantiation edge are now
  classified as Category (a) DEFINITION rather than unsupported. Removed the
  "Known Limitation" docstring note.

---

## 4. CORS is permissive (local development only)

**Status:** Open — must be addressed before any real deployment

**What happens today:** CORS middleware is configured permissively to
simplify local development and testing.

**To close this item:** Before any deployment beyond local development,
restrict CORS to explicit allowed origins, and review other production
hardening basics (rate limiting, authentication on ingest/ask endpoints,
input size limits on repository ingestion).

---

## 5. Single-user, single-database assumption

**Status:** Open — architectural note, not yet a problem at MVP scale

**What happens today:** The platform assumes one shared Postgres instance
with no per-user isolation or multi-tenancy. Fine for a personal/internal
tool; not fine if this becomes a multi-user product.

**To close this item:** Not urgent — flag for design discussion if/when
multi-user support becomes a real requirement.

---

## 6. Language support limited to Python and TypeScript

**Status:** Open — by design, architecture supports extension

**What happens today:** Only Python and TypeScript have working
`LanguageAdapter` implementations (see Step 4's pluggable architecture
refactor). The resolver logic itself is language-agnostic, so adding a new
language should mean writing a new adapter, not touching core resolution
logic — but this has not actually been tested with a third language yet.
"Should be easy" is a design intention, not a proven fact.

**To close this item:** Pick a third language (Go is a good stress test,
since it has no classes — INHERITS/IMPLEMENTS semantics would need real
thought) and implement an adapter as a trial run of the pluggable
architecture, to confirm it holds up in practice.

---

## How to use this file

- When starting new work, check here first — some of these may be relevant
  to what you're building.
- When closing an item, replace "Status: Open" with "Status: Closed
  (YYYY-MM-DD)" and add a one-line pointer to the evidence (a test name, a
  script output, a specific verification) — not just "fixed."
- Don't delete closed items; keep them as a record of what was actually
  checked and when.