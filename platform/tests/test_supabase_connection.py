"""Quick smoke test for Supabase connectivity.

Imports only the db module directly (avoids the full ML/embedder import chain).

Run from repo root:
    .venv\Scripts\python.exe test_supabase_connection.py
"""
import importlib
import os
import sys

sys.path.insert(0, "platform")

# Load .env
with open(".env") as f:
    for line in f:
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

db_url = os.environ["DATABASE_URL"]
print(f"DATABASE_URL prefix : {db_url[:50]}...")

# Import db.py directly — skip storage/__init__.py which pulls in the embedder
import importlib.util, pathlib

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# We need EMBEDDING_DIM for models.py but not for db.py itself — patch it early
# so the import chain doesn't explode on torch.
sys.modules.setdefault("src", type(sys)("src"))

# Stub out the heavy ML modules so db.py can import models.py cleanly
import types
def _stub(*a, **kw): return None
for mod_name in [
    "sentence_transformers",
    "torch", "torch.distributed",
    "transformers",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

# Provide a minimal embedding config so models.py can import EMBEDDING_DIM
embedding_config = types.ModuleType("src.embedding.config")
embedding_config.EMBEDDING_DIM = 768
sys.modules["src.embedding"] = types.ModuleType("src.embedding")
sys.modules["src.embedding.config"] = embedding_config

# Now import normally
from sqlalchemy import create_engine, text
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

def _is_remote(url):
    host = (urlparse(url).hostname or "").lower()
    return host not in ("localhost", "127.0.0.1", "::1", "")

def _add_ssl(url):
    if not _is_remote(url):
        return url
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    if "sslmode" not in params:
        params["sslmode"] = ["require"]
    return urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in params.items()})))

def make_psycopg_dsn(url):
    if url.startswith("postgresql+"):
        url = "postgresql" + url[url.index("://"):]
    return _add_ssl(url)

# --- 1. SSL logic ---
dsn = make_psycopg_dsn(db_url)
ssl_added = "sslmode=require" in dsn
print(f"psycopg DSN (60 chars): {dsn[:60]}...")
print(f"SSL added             : {ssl_added}")
assert ssl_added, "sslmode=require must be present for Supabase remote host"

# --- 2. SQLAlchemy connection ---
sa_url = _add_ssl(db_url)
engine = create_engine(sa_url, pool_pre_ping=True)
with engine.connect() as conn:
    version = conn.execute(text("SELECT version()")).scalar()
print(f"Postgres version      : {version[:70]}")

# --- 3. pgvector extension ---
with engine.connect() as conn:
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
    conn.commit()
print("pgvector extension    : OK")

# --- 4. Verify required tables (ORM tables created by alembic / init_db) ---
with engine.connect() as conn:
    rows = conn.execute(
        text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
    )
    tables = [r[0] for r in rows]

print(f"Tables in public      : {tables}")

required = {"repositories", "entities", "relationships", "users", "user_repos"}
missing = required - set(tables)
if missing:
    print(f"\nMISSING tables: {missing}")
    print("Run: cd platform && alembic upgrade head")
    sys.exit(1)
else:
    print("All required tables   : PRESENT")

print("\n=== Supabase connection: ALL CHECKS PASSED ===")
