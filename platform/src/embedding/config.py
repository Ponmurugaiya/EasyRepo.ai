"""Embedding configuration — Voyage AI voyage-code-3.

Model selection rationale
--------------------------
voyage-code-3 is Voyage AI's code-specialized embedding model. It outperforms
OpenAI text-embedding-3-large by 13.8% and CodeSage-large by 16.81% across
238 code retrieval datasets.

Key properties:
- 1024 dimensions (vs 768 for jina-v2-base-code)
- 32K token context window — handles entire files without truncation
- Trained specifically for code retrieval across 300+ languages
- API-based — no local model, no CPU bottleneck

Rate limiting
-------------
Batching and rate limiting are handled in embedder.py via a sliding-window
limiter. Configure via env vars:

  VOYAGE_RPM_LIMIT           requests per minute  (default: 3  — free tier)
  VOYAGE_MAX_TOKENS_REQUEST  tokens per request   (default: 9000)
  VOYAGE_RETRY_BASE_DELAY    backoff base secs    (default: 22)

Free tier:  3 RPM, 10K TPM  (no payment method required for free tokens)
Paid tier:  add a payment method at dash.voyageai.com → 2000 RPM, 3M TPM
            Set VOYAGE_RPM_LIMIT=2000 to use full paid-tier throughput.

Environment variable: VOYAGE_API_KEY (required)
"""

import os

VOYAGE_API_KEY: str = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL: str = "voyage-code-3"
EMBEDDING_DIM: int = 1024
INPUT_TYPE: str = "document"  # "document" for indexing, "query" for search queries

# Legacy — kept so existing imports don't break
BATCH_SIZE: int = 128
MODEL_NAME: str = VOYAGE_MODEL
