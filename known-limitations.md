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

**Status:** Closed (2026-08-01)

**What was implemented:** Four production hardening concerns addressed together.

**CORS — explicit origin allowlist**
CORS is now controlled by the `CORS_ALLOWED_ORIGINS` environment variable
(comma-separated origin list). Default remains `"*"` so local dev needs
no config change. For a real deployment, set e.g.:
`CORS_ALLOWED_ORIGINS=https://app.example.com,https://admin.example.com`
When origins are restricted, `allow_credentials` is automatically set to
`True`; it stays `False` with the wildcard (browser spec requirement).

**Rate limiting — slowapi + Redis**
`slowapi>=0.1.9` and `redis>=4.6.0` added to `pyproject.toml`. Rate-limit
counters are stored in Redis (`REDIS_URL` env var, default
`redis://localhost:6379`) so limits are shared across all API workers and
survive process restarts. If Redis is unreachable at startup the limiter
falls back to in-memory storage with a warning — local dev without a Redis
container continues to work, but limits won't be shared across workers.
Redis is defined in `docker-compose.yml` with `redis:7-alpine`, AOF
persistence enabled, and a `redisdata` volume.

**API key authentication**
A `verify_api_key` FastAPI dependency added to `dependencies.py`. When the
`API_KEY` environment variable is set, every request to a protected endpoint
must supply a matching `X-API-Key` header; wrong or missing key → 401.
When `API_KEY` is not set the check is a no-op — existing dev/test workflows
require zero config changes. Applied to all three routers via
`dependencies=[Depends(verify_api_key)]` on `include_router`.

**Input size limits + path traversal guard**
- `RepositoryCreateRequest.source` — `max_length=500`, validator rejects
  `..` path traversal segments.
- `AskRequest.query` and `QueryRequest.query` — `max_length=2000`.

**Files changed:**
- `platform/pyproject.toml` — added `slowapi>=0.1.9`, `redis>=4.6.0`
- `platform/docker-compose.yml` — added `redis:7-alpine` service with AOF persistence
- `src/api/main.py` — CORS from env, slowapi middleware + 429 handler,
  Redis storage configured at lifespan startup (in-memory fallback),
  `verify_api_key` dependency on all routers
- `src/api/dependencies.py` — `verify_api_key` function
- `src/api/schemas.py` — input size limits, path traversal validator
- `src/api/routers/repositories.py` — `request: Request` param + rate limit decorators
- `src/api/routers/ask.py` — `request: Request` param + rate limit decorator
- `src/api/routers/retrieval.py` — `request: Request` param + rate limit decorator

---

## 5. Single-user, single-database assumption

**Status:** Closed (Phase 1, 2026-08-01) — collision-resistant repo_id + deduplication

**What Phase 1 fixed:** The old `repo_id` was derived from the folder name
(`my-project`), so two different repos named `my-project` would collide —
the second ingestion would silently overwrite the first. With GitHub URLs this
was a constant hazard.

**What was implemented:**

New module `src/storage/repo_id.py` — single source of truth for ID derivation:
- `canonical_source(source)` — normalises URLs (lowercase, strip `.git`, strip
  trailing slash, `http://` → `https://` for GitHub/GitLab/Bitbucket, collapse
  duplicate slashes). Two submissions of the same GitHub URL in any casing or
  with/without `.git` produce identical canonical forms.
- `derive_repo_id(source)` — first 16 hex chars of SHA-256 of the canonical
  form. Deterministic, collision-resistant, opaque, URL-safe.
- `repo_name_from_source(source)` — human-readable name from last path component.

Verified by five behavioural test cases:
- All GitHub URL variants (HTTP/HTTPS, `.git`, trailing slash, mixed case) →
  same `repo_id`
- Same folder name, different org → different `repo_id`
- Local paths with and without trailing slash → same `repo_id`

New column `repositories.canonical_url TEXT UNIQUE` — stores the normalised
form for deduplication. The API router does a canonical_url lookup first so
existing rows (pre-migration, `canonical_url = NULL`) still resolve by
`repo_id`.

Deduplication logic in `POST /repositories`:
- Already `ready` → return existing data immediately, no re-queue.
- `pending` / `indexing` / `failed` → reset status and re-queue.

Alembic migration `alembic/versions/0001_add_canonical_url.py` — adds the
column and `uq_repositories_canonical_url` unique index. Existing rows are
not broken (column is nullable; they get `canonical_url` populated on their
next re-ingestion).

**Files changed:**
- `src/storage/repo_id.py` — new module
- `src/storage/models.py` — `canonical_url` column on `RepositoryModel`
- `src/storage/schema.sql` — DDL source of truth updated
- `src/ingestion/pipeline.py` — uses `derive_repo_id` / `repo_name_from_source`, writes `canonical_url`
- `src/api/routers/repositories.py` — deduplication check, uses `derive_repo_id`
- `alembic/versions/0001_add_canonical_url.py` — migration

**Phase 2 (users + access control) — Closed (2026-08-01):**

New tables: `users` and `user_repos`.

`users` — `id` (UUID hex), `external_id` + `provider` (OAuth identity),
`email`, `api_token_hash` (bcrypt, never the plaintext), `created_at`.

`user_repos` — `(user_id, repo_id)` composite PK, `role` = `owner|viewer`,
`granted_at`. Enforces access: owner can query + re-index + manage access;
viewer can query only.

**Token design** (`src/api/auth.py`) — opaque personal tokens:
`er_{16-char-user-id-prefix}.{32-byte-url-safe-secret}`. Only the bcrypt
hash is stored. The 16-char prefix allows fast DB lookup (one targeted query
per request, not a full table scan). Rotation via `POST /auth/token/rotate`
invalidates the previous token immediately.

**Auth dependency** (`src/api/dependencies.py`) — `get_current_user()`
resolves the token to a `UserModel` when `AUTH_ENABLED=true`; returns `None`
when disabled so all existing dev/test workflows need zero config changes.
`get_accessible_repository()` enforces per-user access: 404 if not found,
403 if found but no access grant. `require_owner()` additionally enforces
the owner role for write operations.

**Access grant logic** in `POST /repositories`:
- New repo: submitting user auto-granted `owner`.
- Repo already `ready` (shared index): submitting user auto-granted `viewer`
  on the shared copy — no redundant re-indexing.
- Never downgrades: an existing owner submitting again stays owner.

**New endpoints** (`src/api/routers/auth.py`):
- `POST /auth/register` — create local account, returns token once
- `POST /auth/token/rotate` — invalidate current token, issue new one
- `GET  /auth/me` — calling user's profile (anonymous placeholder if auth off)

**Access management endpoints** (`src/api/routers/repositories.py`):
- `GET  /repositories/{id}/access` — list all grants (owner only)
- `POST /repositories/{id}/access` — grant owner/viewer to a user (owner only)
- `DELETE /repositories/{id}/access/{user_id}` — revoke access (owner only,
  cannot revoke own access to prevent orphaned repos)

**Migration** `alembic/versions/0002_add_users_and_user_repos.py`.
To apply: `alembic upgrade head` from `platform/`.

**Files changed (Phase 2):**
- `platform/pyproject.toml` — added `passlib[bcrypt]>=1.7.4`
- `src/storage/models.py` — `UserModel`, `UserRepoModel`
- `src/storage/schema.sql` — DDL for `users` and `user_repos`
- `src/api/auth.py` — new module (token generation, hashing, verification, user CRUD)
- `src/api/dependencies.py` — `get_current_user`, `get_accessible_repository`,
  `require_owner`, `grant_repo_access`; `verify_api_key` kept as shim
- `src/api/main.py` — auth router registered; startup table check extended
- `src/api/routers/auth.py` — new router (`/auth/*`)
- `src/api/routers/repositories.py` — access grant on ingest; access management endpoints
- `src/api/routers/ask.py` — uses `get_accessible_repository` + `get_current_user`
- `src/api/routers/retrieval.py` — same
- `alembic/versions/0002_add_users_and_user_repos.py` — migration

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