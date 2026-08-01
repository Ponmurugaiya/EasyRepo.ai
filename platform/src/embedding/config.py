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

Free tier: Voyage AI provides free starter credits on signup with no credit
card required. Rate limit is generous enough for indexing large repos quickly.

Environment variable: VOYAGE_API_KEY (required)
"""

import os

VOYAGE_API_KEY: str = os.environ.get("VOYAGE_API_KEY", "")
VOYAGE_MODEL: str = "voyage-code-3"
EMBEDDING_DIM: int = 1024
BATCH_SIZE: int = 128        # Voyage API accepts up to 128 texts per request
INPUT_TYPE: str = "document" # "document" for indexing, "query" for search queries

# Legacy — kept so existing imports of MODEL_NAME don't break at the module level
MODEL_NAME: str = VOYAGE_MODEL
