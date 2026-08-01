-- ============================================================================
-- Raw DDL Source of Truth for AI Codebase Intelligence Platform
-- ============================================================================

-- Enable pgvector extension for dense embedding storage and vector search
CREATE EXTENSION IF NOT EXISTS vector;

-- ----------------------------------------------------------------------------
-- 1. Repositories Table
-- Tracks indexed repositories and their current status
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repositories (
    id VARCHAR(255) PRIMARY KEY,
    url_or_path TEXT NOT NULL,
    -- Normalised canonical form of url_or_path (lowercase, no trailing slash,
    -- no .git suffix).  UNIQUE so two submissions of the same GitHub URL map
    -- to one shared indexed copy rather than racing to overwrite each other.
    canonical_url TEXT UNIQUE,
    name VARCHAR(255) NOT NULL,
    indexed_at TIMESTAMPTZ,
    status VARCHAR(50) NOT NULL DEFAULT 'pending',
    CONSTRAINT chk_repo_status CHECK (status IN ('pending', 'indexing', 'ready', 'failed'))
);

-- ----------------------------------------------------------------------------
-- 2. Entities Table
-- Stores code entities extracted from repositories alongside vector embeddings
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entities (
    id VARCHAR(512) PRIMARY KEY, -- Deterministic ID scheme (e.g. py.models.user.UserModel)
    repo_id VARCHAR(255) NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    type VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    parent_id VARCHAR(512) REFERENCES entities(id) ON DELETE SET NULL,
    language VARCHAR(50) NOT NULL,
    has_docstring BOOLEAN NOT NULL DEFAULT FALSE,
    source TEXT NOT NULL,
    embedding vector(768), -- 768 dimensions matching jinaai/jina-embeddings-v2-base-code
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ----------------------------------------------------------------------------
-- 3. Relationships Table
-- Graph edges connecting entities (CONTAINS, CALLS, IMPORTS, INHERITS, IMPLEMENTS)
-- 
-- TRADEOFF DECISION ON EXTERNAL TARGETS:
-- Uses a nullable target_id (FK -> entities.id) with an optional external_target_name.
-- Creating fake rows in the `entities` table for 3rd-party/library symbols would
-- pollute entity counts and vector search results. Nullable target_id keeps
-- `entities` strictly representing codebase entities.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS relationships (
    id SERIAL PRIMARY KEY,
    repo_id VARCHAR(255) NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    source_id VARCHAR(512) NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id VARCHAR(512) REFERENCES entities(id) ON DELETE CASCADE,
    external_target_name TEXT,
    type VARCHAR(50) NOT NULL,
    file_path TEXT NOT NULL,
    line INTEGER NOT NULL,
    CONSTRAINT chk_rel_target CHECK (target_id IS NOT NULL OR external_target_name IS NOT NULL)
);

-- ----------------------------------------------------------------------------
-- 4. Indexes
-- Optimized for retrieval hot-paths and vector similarity search
-- ----------------------------------------------------------------------------

-- HNSW Vector Index for fast cosine similarity search.
-- HNSW is preferred over IVFFlat because it does not require pre-populated centroid
-- training and handles dynamic repository insertions and updates gracefully over time.
CREATE INDEX IF NOT EXISTS idx_entities_embedding 
    ON entities USING hnsw (embedding vector_cosine_ops);

-- Hot-path relational indexes for entity filtering and graph expansion
CREATE INDEX IF NOT EXISTS idx_entities_repo_type 
    ON entities (repo_id, type);

CREATE INDEX IF NOT EXISTS idx_entities_parent_id 
    ON entities (parent_id);

CREATE INDEX IF NOT EXISTS idx_relationships_repo_source 
    ON relationships (repo_id, source_id);

CREATE INDEX IF NOT EXISTS idx_relationships_repo_target_type 
    ON relationships (repo_id, target_id, type);

-- ----------------------------------------------------------------------------
-- 5. Users Table
-- Identity records for API access.  external_id + provider identify an OAuth
-- subject (e.g. GitHub user ID).  api_token_hash is the bcrypt hash of the
-- user's personal API token — the plaintext token is never stored.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(64) PRIMARY KEY,          -- UUID hex (no hyphens)
    external_id VARCHAR(255),            -- OAuth subject identifier
    provider VARCHAR(50) NOT NULL DEFAULT 'local',
    email VARCHAR(255),
    api_token_hash TEXT,                 -- bcrypt hash of personal API token
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_users_external_provider UNIQUE (external_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- ----------------------------------------------------------------------------
-- 6. User–Repository Access Table
-- Controls which users may access which repository indexes and with what role.
--   owner  — query + re-index + grant/revoke access + delete index
--   viewer — query only
-- The first user to submit a repository URL is auto-granted owner.
-- Subsequent users submitting the same URL are auto-granted viewer on the
-- shared indexed copy.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_repos (
    user_id VARCHAR(64) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    repo_id VARCHAR(255) NOT NULL REFERENCES repositories(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'viewer',
    granted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, repo_id),
    CONSTRAINT chk_user_repo_role CHECK (role IN ('owner', 'viewer'))
);

CREATE INDEX IF NOT EXISTS idx_user_repos_repo_id ON user_repos (repo_id);
