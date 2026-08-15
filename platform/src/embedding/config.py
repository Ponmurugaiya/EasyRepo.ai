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
- API-based — no local model, no CPU bottleneck, ~100ms per batch vs minutes

Paid tier: 300 RPM, 1M TPM — no inter-batch delay needed.
Free tier:  3 RPM, 10K TPM — set VOYAGE_BATCH_SIZE=8 and
            VOYAGE_BATCH_DELAY_SECS=21 in your environment.

Environment variable: VOYAGE_API_KEY (required)
"""

import os

VOYAGE_API_KEY: str = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL: str = "voyage-code-3"
EMBEDDING_DIM: int = 1024
# Voyage AI paid tier supports up to 128 texts per request and 300 RPM.
# Default batch size is 128 (the API max). For the free tier (3 RPM, 10K TPM)
# set VOYAGE_BATCH_SIZE=8 and VOYAGE_BATCH_DELAY_SECS=21 in your environment.
BATCH_SIZE: int = int(os.environ.get("VOYAGE_BATCH_SIZE", "128"))
INPUT_TYPE: str = "document" # "document" for indexing, "query" for search queries

# Legacy — kept so existing imports of MODEL_NAME don't break at the module level
MODEL_NAME: str = VOYAGE_MODEL
