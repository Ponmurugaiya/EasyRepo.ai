# AI Codebase Intelligence Platform — Project Status

**As of:** Graph feature + frontend completion
**Overall state:** Full-stack application — backend API + Next.js frontend + code graph visualization — working end-to-end.

---

## What this project is

A system that clones a GitHub repository, parses it into structural entities (modules, classes, functions, methods, variables) using Tree-sitter, embeds each entity with a code-specific embedding model, stores everything in Postgres/pgvector with structural relationships (CALLS, IMPORTS, INHERITS, IMPLEMENTS, INSTANTIATES, CONTAINS), answers natural-language questions about the codebase with cited answers, and visualizes the codebase as an interactive file-level graph.

---

## Backend — What's built and verified

### Extraction pipeline
- Tree-sitter AST parsing for Python and TypeScript via a pluggable `LanguageAdapter` architecture
- Extracts: modules, classes, interfaces, functions, methods, variables (module-level dict/list/call assignments added recently)
- Resolves: CALLS, IMPORTS, INHERITS, IMPLEMENTS, INSTANTIATES relationships via static symbol resolution
- 100% match against 62-entity / 87-relationship hand-built manifest (verified at Step 3–4)

### Storage
- Postgres + pgvector: `entities`, `relationships`, `repositories` tables
- Per-entity embeddings via Voyage AI `voyage-code-3` (1024 dimensions)
- Procrastinate (Postgres-backed) job queue for async ingestion — durable across restarts

### Retrieval + generation
- Vector search → graph expansion (CONTAINS/CALLS/INHERITS/IMPLEMENTS/INSTANTIATES)
- Groq (multi-model rotation) → Gemini 2.5 Flash fallback via LiteLLM
- 3-way citation classification: definition / call-site / unsupported
- 0.0% hallucination rate on 4-question canonical test set (67 citations verified)

### Auth + access control
- `AUTH_ENABLED` env flag (default false for local dev)
- Per-user bcrypt-hashed API tokens (`er_{prefix}.{secret}` format)
- `owner` / `viewer` roles, auto-granted on ingestion
- Collision-resistant `repo_id` via SHA-256 of canonical URL

### Code Graph API (new)
- `GET /repositories/{id}/graph` — file-level graph with entities embedded per node, BFS traversal from entry point, entry point auto-detected via filename heuristics + source patterns + zero-in-degree scoring
- `GET /repositories/{id}/graph/{file_id}/expand` — full entity detail + cross-file edges for a single file
- `GET /repositories/{id}/entities/{entity_id}/source` — raw source code for any entity

---

## Frontend — What's built

### Chat interface
- Next.js 16 + Tailwind + shadcn/ui
- Sidebar with persistent repo sessions (localStorage via Zustand persist)
- Chat window with citation panel (verified / call-site / unsupported badges)
- Citation code viewer — shows highlighted source code for any cited entity
- "Show in graph" button on each verified citation

### Code Graph panel
- React Flow + Dagre hierarchical layout (entry point at top)
- File nodes show language badge, entry point indicator (▶ ENTRY), full file path
- Click file header → expand/collapse to show entity list (functions, classes, methods, variables)
- Entity rows show type icon (ƒ function, ◆ class, → method, ≔ variable), name, line number
- Edges connect files with relationship type label (CALLS = solid blue, IMPORTS = dashed gray, INHERITS = purple)
- Hover edge → tooltip listing all entity-pair connections behind it
- Click entity row → source code viewer opens with exact line highlighting
- "Show in graph" from chat citation → opens panel, auto-expands file, highlights entity row in yellow
- Controls: entry point selector, depth slider, imports toggle, expand/collapse all, refresh

### Re-index flow
- Hover a repo in sidebar → refresh icon appears
- Click → confirms, POSTs same URL, polls status, refreshes graph panel when done

---

## Known gaps (current)

- `data_store.py`-style files (only module-level variables, no functions/classes) show 0 entities in graph until re-indexed with the new extraction code
- Re-indexing re-runs the full pipeline including Voyage AI embedding (~2–5 min, costs API credits)
- Language support limited to Python and TypeScript
- No pagination on graph endpoint for very large repos (>200 files)

---

## File structure

```
platform/
  src/
    api/         — FastAPI routers (repositories, ask, retrieval, graph, auth)
    graph/       — File graph builder, BFS traversal, entry point detection
    extraction/  — Tree-sitter entity extractor + language adapters
    resolution/  — CALLS/IMPORTS/INHERITS/IMPLEMENTS/INSTANTIATES resolvers
    embedding/   — Voyage AI embedder
    ingestion/   — Full pipeline orchestration
    retrieval/   — Vector search + graph expansion + context assembly
    generation/  — LLM client, prompt templates, citation validator
    storage/     — SQLAlchemy models, DB init, session management
    jobs/        — Procrastinate async job queue

frontend/
  app/           — Next.js app router (single page layout)
  components/
    chat/        — Chat window, messages, citation panel, code viewer
    graph/       — Graph panel, file node, edge components
    sidebar/     — Sidebar, repo item, add repo button
  store/         — Zustand stores (chat-store, graph-store)
  lib/           — API clients (api.ts, graph-api.ts), layout utils, citations
  types/         — TypeScript types mirroring backend schemas
```
  Qvb 