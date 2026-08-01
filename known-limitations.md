# Known Limitations — AI Codebase Intelligence Platform

This file tracks things the platform intentionally does **not** yet do, or has
not yet **proven** it does, as of the end of Step 8 (API layer + regression suite).

The rule for this file: an item stays here until it has been closed with the
same standard of evidence used everywhere else in this project — raw output,
not a summary claim. Nothing gets marked "done" based on a plan to do it.

---

## 1. Live end-to-end citation validation through the API is unverified

**Status:** Open

**What's verified:** Real Gemini calls + real 3-way citation classification
(definition / call-site / unsupported) were verified in Step 7, via the CLI
(`python -m src.cli ask`), with real hallucination-rate numbers (4.6% overall,
with a documented, understood cause).

**What's NOT yet verified:** The same real-Gemini, real-citation-validation
flow *through the `POST /repositories/{id}/ask` HTTP endpoint*. The API test
suite currently uses a mocked Gemini client (0 citations, fixed canned answer)
so that regression tests don't depend on Gemini's free-tier quota (20
requests/day).

**Why it matters:** The CLI and API call the same underlying pipeline code,
so this is very likely fine — but "very likely fine" is exactly the kind of
claim this project has repeatedly shown needs direct verification, not
assumption.

**To close this item:** Run one real (non-mocked) request against the live
`/ask` endpoint once Gemini quota allows, and confirm the response's
`citations` object shows real definition/call-site counts and a real
hallucination rate, matching the pattern already seen via the CLI.

---

## 2. No async/background job queue for repository ingestion

**Status:** Open — known MVP scope limitation, not a bug

**What happens today:** `POST /repositories` ingests synchronously — the
HTTP request blocks until extraction, relationship resolution, embedding, and
storage are all complete. This works fine for the ~62-entity sample repo
(seconds), but will not scale to real-world repositories with thousands of
files, where ingestion could take minutes and a blocking HTTP call is the
wrong shape.

**To close this item:** Add a background job queue (e.g. a simple task queue
or `BackgroundTasks` + polling, or a proper queue like Celery/RQ for a
production version), with `POST /repositories` returning immediately with a
`pending` status and `GET /repositories/{id}/status` used to poll for
completion.

---

## 3. INSTANTIATES is not a modeled relationship type

**Status:** Open — documented, deliberate scope boundary (see Step 7)

**What happens today:** The platform models five relationship types:
CONTAINS, CALLS, IMPORTS, INHERITS, IMPLEMENTS. Object construction
(`UserModel(...)`) is not tracked as its own relationship, so citations
describing "X creates an instance of Y" are correctly classified as
`unsupported` by the citation validator — this is honest behavior given the
current schema, not a bug.

**Why it matters:** "What creates instances of this class?" is a legitimate,
useful codebase question that the platform currently can't answer
structurally.

**To close this item:** Extend the relationship taxonomy to include
INSTANTIATES, updating the resolver (Step 4), the relationship expander
(Step 6), and the citation validator (Step 7) together — this is real,
deliberate scope, not a quick patch, and should be treated as its own
mini-project.

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