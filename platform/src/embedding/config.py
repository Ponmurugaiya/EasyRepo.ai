"""Embedding configuration — Jina AI jina-code-embeddings-1.5b.

Model selection rationale
--------------------------
jina-code-embeddings-1.5b is Jina AI's code-specialized embedding model built
on the Qwen2.5-Coder-1.5B backbone. It is purpose-built for code retrieval
across NL→code, code→code, and code→NL tasks.

Key properties:
- 1024 dimensions output (Matryoshka — native 1536, truncated to 1024)
- 32K token context window — handles entire files without truncation
- Trained specifically for code retrieval across 300+ programming languages
- REST API — no SDK, no local model, uses httpx (already a project dependency)

Rate limiting
-------------
Batching and rate limiting are handled in embedder.py via a sliding-window
limiter. Configure via env vars:

  JINA_RPM_LIMIT           requests per minute  (default: 100 — free tier)
  JINA_MAX_TOKENS_REQUEST  tokens per request   (default: 50000)
  JINA_RETRY_BASE_DELAY    backoff base secs    (default: 2)

Free tier:   100 RPM, 100K TPM (10M tokens on signup, no payment required)
Paid tier:   top up via Stripe → 500 RPM, 2M TPM
             Set JINA_RPM_LIMIT=500 to use full paid-tier throughput.

Environment variable: JINA_API_KEY (required)
Get a free API key at: https://jina.ai/?sui=apikey
"""

import os

JINA_API_KEY: str = os.environ.get("JINA_API_KEY", "")
JINA_MODEL: str = "jina-code-embeddings-1.5b"
EMBEDDING_DIM: int = 1024
TASK_PASSAGE: str = "nl2code.passage"  # for indexing documents
TASK_QUERY: str = "nl2code.query"      # for search queries

# Legacy aliases — kept so any existing imports don't break
BATCH_SIZE: int = 128
MODEL_NAME: str = JINA_MODEL
INPUT_TYPE: str = TASK_PASSAGE
