# EasyRepo

**AI-powered codebase intelligence.** Point it at any GitHub repo or local path, ask questions in plain English, and get cited answers grounded in the actual source code — with verified `[file:line]` references and an interactive dependency graph.

---

## What it does

- **Understands your code** — parses every `.py` and `.ts/.tsx` file into a structural knowledge graph using Tree-sitter AST extraction
- **Answers questions** — retrieves relevant entities via pgvector similarity search, expands context through the call/inheritance graph, and generates answers with inline citations
- **Verifies citations** — every `[file.py:L-L]` reference is classified as a definition, a call-site, or flagged and auto-corrected if hallucinated
- **Remembers context** — rolling conversation summaries, session-scoped knowledge cache, and long-term per-user memory across sessions
- **Visualizes dependencies** — interactive file-level graph with entity drill-down

---

## Architecture overview

```
Browser (Next.js · AWS Amplify)
        │
        │  REST  (polling async job pattern)
        ▼
FastAPI  ·  Procrastinate job queue  ·  Uvicorn
        │
        ├─ Ingestion pipeline
        │    Tree-sitter → entity extraction → relationship resolution
        │    → Voyage AI embeddings → Postgres / pgvector
        │
        └─ Query pipeline
             Query Planner (llama-3.1-8b-instant)
             → pgvector search + BFS graph expansion
             → LLM Answer Agent (LiteLLM smart router)
             → Citation validator + correction agent
             → Rolling summary + LTM memory extraction

Databases:  Supabase Postgres + pgvector  ·  Redis Cloud (LLM quota tracking)
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.10+, FastAPI, Uvicorn, Procrastinate |
| Parsing | Tree-sitter (Python + TypeScript adapters) |
| Embeddings | Voyage AI `voyage-code-3` (1024-dim) |
| Vector DB | Supabase Postgres + pgvector |
| Job queue | Procrastinate (Postgres-backed, in-process worker) |
| LLM routing | LiteLLM — 23 models across 7 providers, quota-aware |
| Rate limiting | Redis Cloud (shared across workers) |
| Auth | HMAC-SHA256 API tokens + AWS Cognito (Google OAuth) |
| Frontend | Next.js 16 (App Router, static export), React 19, TypeScript 5 |
| Styling | Tailwind CSS v4 + shadcn/ui |
| State | Zustand + TanStack Query |
| Graph UI | ReactFlow + Dagre |

---

## Repo structure

```
EasyRepo/
├── platform/               # FastAPI backend
│   ├── src/
│   │   ├── api/            # FastAPI app, routers, auth, schemas
│   │   ├── agents/         # QueryPlanner, CodeQA, FileSummary, FolderSummary, RepoSummary, CitationCorrection
│   │   ├── extraction/     # Tree-sitter entity extractor
│   │   ├── generation/     # LLM smart router, prompt templates, citation validator
│   │   ├── ingestion/      # Full ingest pipeline (clone → extract → embed → persist)
│   │   ├── memory/
│   │   │   ├── stm/        # ShortTermMemory (per-request), WorkingMemory (rolling summary)
│   │   │   └── ltm/        # Session knowledge, user memory, user-repo prefs, repo-user memory
│   │   ├── pipeline/       # Orchestrator, history formatter, pipeline logger
│   │   ├── retrieval/      # Vector search, graph expander, context builder, repo overview
│   │   ├── resolution/     # CALLS / IMPORTS / INHERITS / IMPLEMENTS / INSTANTIATES resolvers
│   │   ├── storage/        # SQLAlchemy models, DB session
│   │   └── jobs/           # Procrastinate task definitions
│   ├── alembic/            # DB migrations
│   ├── docker-compose.yml  # Local Postgres + Redis
│   ├── run.py              # Dev server entrypoint
│   └── pyproject.toml
├── frontend/               # Next.js frontend
│   ├── app/                # App Router layout + root page
│   ├── components/         # chat/, graph/, sidebar/, auth/, ui/
│   ├── store/              # Zustand stores (chat, graph, auth)
│   ├── lib/                # API client, Cognito helpers, citation utils
│   └── package.json
├── evaluation/             # 6-pillar automated evaluation framework
├── sample-repo/            # Ground-truth fixture (62 entities, 87 relationships)
└── docs/                   # Architecture and design docs
```

---

## Local development setup

### Prerequisites

- Python ≥ 3.10
- Node.js 22 (use `nvm use` inside `frontend/` — `.nvmrc` is set to 22)
- Docker Desktop (for local Postgres + Redis)

---

### 1. Start local databases

```bash
cd platform
docker compose up -d
```

This starts:
- **Postgres** (pgvector/pgvector:pg16) on port `5435`
- **Redis** on port `6379`

---

### 2. Backend setup

```bash
cd platform

# Create and activate virtual environment
python -m venv ../.venv
../.venv/Scripts/activate      # Windows
# source ../.venv/bin/activate # macOS/Linux

# Install dependencies
pip install -e ".[dev]"
```

Create `../.env` (at repo root) with the following keys — see [Environment variables](#environment-variables) below for details:

```bash
# Required
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5435/easyrepo
REDIS_URL=redis://localhost:6379
VOYAGE_API_KEY=your_voyage_key

# At least one LLM provider key (Groq recommended — free tier is generous)
GROQ_API_KEY=your_groq_key

# Auth (off by default in dev)
AUTH_ENABLED=false
```

Migrations run automatically on startup. To run them manually:

```bash
cd platform
alembic upgrade head
```

Start the backend:

```bash
# Windows
../.venv/Scripts/python.exe run.py

# macOS/Linux
python run.py
```

API available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

---

### 3. Frontend setup

```bash
cd frontend
nvm use          # switches to Node 22
npm ci
```

Create `frontend/.env.local`:

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000

# Cognito — only needed for Google OAuth. Leave as-is for dev token auth.
NEXT_PUBLIC_COGNITO_REGION=us-east-1
NEXT_PUBLIC_COGNITO_USER_POOL_ID=
NEXT_PUBLIC_COGNITO_CLIENT_ID=
NEXT_PUBLIC_COGNITO_DOMAIN=
NEXT_PUBLIC_COGNITO_REDIRECT_URI=http://localhost:3000
```

Start the dev server (run manually in your terminal — do not use a background process for this):

```bash
npm run dev
```

Frontend available at `http://localhost:3000`.

---

## Environment variables

### Required

| Variable | Description |
|---|---|
| `DATABASE_URL` | Postgres connection string (direct, not pgbouncer). Format: `postgresql://user:pass@host:port/db` |
| `REDIS_URL` | Redis connection string. Format: `redis://default:pass@host:port` |
| `VOYAGE_API_KEY` | Voyage AI key for `voyage-code-3` embeddings. Get at [voyageai.com](https://www.voyageai.com) |

### LLM providers (at least one required)

The system uses a smart router that cascades across all configured providers. Groq is the
recommended starting point — its free tier is the most generous.

| Variable | Provider | Free tier |
|---|---|---|
| `GROQ_API_KEY` | Groq | 30 RPM, 500K tokens/day — [console.groq.com](https://console.groq.com) |
| `GEMINI_API_KEY` | Google AI Studio | 15 RPM, 1M tokens/day — [aistudio.google.com](https://aistudio.google.com) |
| `NVIDIA_API_KEY` | NVIDIA NIM | 40 RPM, no daily cap — [build.nvidia.com](https://build.nvidia.com) |
| `OPENROUTER_API_KEY` | OpenRouter | 50 req/day — [openrouter.ai](https://openrouter.ai) |
| `COHERE_API_KEY` | Cohere | 1,000 calls/month — [dashboard.cohere.com](https://dashboard.cohere.com) |
| `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID` | Cloudflare Workers AI | 10K neurons/day — [dash.cloudflare.com](https://dash.cloudflare.com) |
| `CEREBRAS_API_KEY` | Cerebras | 5 RPM, 1M tok/day — [cloud.cerebras.ai](https://cloud.cerebras.ai) |

### Optional tuning

| Variable | Default | Description |
|---|---|---|
| `AUTH_ENABLED` | `false` | Set `true` in production to enforce API key auth |
| `VOYAGE_BATCH_SIZE` | `4` | Embeddings per API call. Free tier: keep at 4. Paid tier: set to 128 |
| `VOYAGE_BATCH_DELAY_SECS` | `21` | Delay between batches. Free tier: 21s. Paid tier: 0 |
| `PIPELINE_LOG_LEVEL` | `INFO` | `DEBUG` \| `INFO` \| `OFF` — controls pipeline trace verbosity |
| `LOG_TO_FILE` | `false` | Write rotating log files to `LOG_DIR` |
| `LOG_DIR` | `logs` | Directory for log files when `LOG_TO_FILE=true` |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Override default Groq model |

> **Note on Voyage AI free tier:** 3 RPM means a mid-size repo (~200 entities) takes
> ~30 minutes to embed. Set `VOYAGE_BATCH_SIZE=128` and `VOYAGE_BATCH_DELAY_SECS=0`
> once you add a payment method.

---

## How it works

### Ingestion

When you submit a repo URL or path, the backend:

1. Git-clones the repo into a temp directory
2. Runs Tree-sitter AST extraction on all `.py` and `.ts/.tsx` files → extracts `module`, `class`, `interface`, `function`, `method`, `variable` entities with source and line ranges
3. Resolves cross-file relationships: `CALLS`, `IMPORTS`, `INHERITS`, `IMPLEMENTS`, `INSTANTIATES`
4. Embeds every entity with Voyage AI `voyage-code-3` → 1024-dimensional vectors
5. Bulk-inserts entities, relationships, and embeddings into Postgres

Progress is tracked live — the frontend polls `GET /repositories/{id}/status` and shows a percentage indicator.

### Query pipeline

When you ask a question:

1. **Query Planner** classifies intent (`feature` / `dependency_flow` / `repository_overview` / `specific_lookup`) and selects a retrieval strategy — runs on `llama-3.1-8b-instant` for low latency
2. **Retrieval** — pgvector cosine similarity search for the top-K most relevant entities
3. **Graph expansion** — BFS traversal of `CALLS`, `INHERITS`, `CONTAINS` edges to pull in related entities
4. **Answer Agent** — generates a cited Markdown answer using a standard-tier LLM
5. **Citation validation** — every `[file:L-L]` tag is verified against the entity DB; unsupported citations trigger targeted re-retrieval and up to 2 retries
6. **Citation correction** — deterministic pass (DB lookup) + LLM pass for any remaining hallucinated tags
7. **Memory** — conversation saved, rolling summary updated, long-term user/codebase facts extracted

For `repository_overview` and `repository_detailed` queries, a hierarchical pipeline runs instead:
File Summary Agent (per-file, batched + concurrent) → Folder Summary Agent → Repo Summary Agent (Gemini).
Results are cached in session-scoped LTM so repeat overview queries are served instantly from DB.

See [`docs/query-pipeline-deep-dive.md`](docs/query-pipeline-deep-dive.md) for the full end-to-end flow with timing and memory system details.

---

## API reference (key endpoints)

```
GET  /health                               Health check + DB ping

POST /auth/register                        Create user account
POST /auth/token                           Get / rotate API token
GET  /auth/me                              Current user info

GET  /repositories                         List your repos
POST /repositories                         Submit repo for ingestion (async)
GET  /repositories/{id}/status             Ingestion status + progress %

POST /repositories/{id}/ask                Submit a question → {job_id}
GET  /repositories/{id}/ask/{job_id}       Poll answer job

GET  /repositories/{id}/graph              File dependency graph
GET  /repositories/{id}/graph/{fid}/expand File node entity detail

GET  /repositories/{id}/entities/{eid}/source  Raw entity source code
GET  /repositories/{id}/conversations      Conversation history (authenticated)
```

Full interactive docs: `http://localhost:8000/docs`

---

## Authentication

**Development** (`AUTH_ENABLED=false`): all endpoints are open, no token required.

**Production** (`AUTH_ENABLED=true`): two auth modes are supported:

- **API token** — format `er_<16-char-prefix>.<32-byte-secret>`. Send as `X-API-Key` header. Only the HMAC-SHA256 hash is stored in the DB.
- **Cognito JWT** — Google OAuth via AWS Cognito Hosted UI. The frontend uses `aws-amplify` to get the id token; send as `Authorization: Bearer <jwt>`.

**Access control**: the first user to index a repo becomes its owner. Subsequent users who submit the same URL are granted viewer access automatically. Owners can manage access grants via `POST/DELETE /repositories/{id}/access`.

---

## Evaluation

A 6-pillar automated evaluation framework runs against the `sample-repo` fixture
(62 entities, 87 relationships, hand-built ground-truth manifest):

| Pillar | What it tests |
|---|---|
| 1 | Extraction quality — entity recall/precision vs manifest |
| 2 | Storage & embedding integrity — counts, NULL check, 1024-dim, semantic ranking |
| 3 | Retrieval quality — 6 scenarios (multi-hop chains, disambiguation, orphan isolation) |
| 4 | Answer quality — 4 canonical Q&A questions, hallucination rate |
| 5 | Citation quality — 3-way classification correctness |
| 6 | Memory & agents — query planner classification, STM iterations, LTM write/hit/stale |

**Current results:** 0.0% hallucination rate on 4 canonical questions (67 citations verified),
100% extraction match on the 62-entity manifest.

Run the full suite:

```bash
# From repo root — starts the API, ingests sample-repo, runs all pillars
python evaluation/run_evaluation.py

# Skip re-ingestion if the repo is already indexed
python evaluation/run_evaluation.py --skip-ingest --repo-id <id>
```

Results are written to `evaluation/evaluation-results/`.

---

## Supported languages

| Language | Entity types | Relationships |
|---|---|---|
| Python | `module`, `class`, `function`, `method`, `variable` | `CONTAINS`, `CALLS`, `IMPORTS`, `INHERITS`, `INSTANTIATES` |
| TypeScript / TSX | `module`, `class`, `interface`, `function`, `method`, `variable` | `CONTAINS`, `CALLS`, `IMPORTS`, `INHERITS`, `IMPLEMENTS`, `INSTANTIATES` |

Additional language support (Go, Rust, Java) is on the roadmap via the `LanguageAdapter` base class in `src/languages/`.

---

## Known limitations

- **Voyage AI free tier** — 3 RPM cap makes large repo ingestion slow (~30 min for 200 entities). Paid tier removes this.
- **Single-process worker** — Procrastinate runs inside the same Uvicorn process. Suitable for a single server; scale workers independently via `procrastinate worker` CLI if needed.
- **Python + TypeScript only** — other languages are not yet extracted or embedded.
- **No incremental re-index** — re-indexing a repo re-processes all files from scratch.

---

## Docs

| Doc | Description |
|---|---|
| [`docs/query-pipeline-deep-dive.md`](docs/query-pipeline-deep-dive.md) | Complete flow: Q1 vs Q2, agent orchestration, all memory tiers, timing |
| [`docs/llm-routing.md`](docs/llm-routing.md) | LLM smart router — provider cascade, quota tracking, model catalogue |
| [`docs/project-status.md`](docs/project-status.md) | Current feature status and roadmap |
| [`docs/known-limitations.md`](docs/known-limitations.md) | Known issues and workarounds |
| [`docs/evaluation-guide.md`](docs/evaluation-guide.md) | How to run and extend the evaluation suite |
