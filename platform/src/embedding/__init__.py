"""Embedding module exports.

Note: CodeEmbedder and format_entity_for_embedding are intentionally NOT
imported here at package load time. They pull in sentence-transformers and
torch, which are heavy ML dependencies. Importing this package from the DB
layer (models.py, alembic env.py) only needs EMBEDDING_DIM — it should not
trigger the full ML stack.

Any module that needs the embedder should import directly:
    from src.embedding.embedder import CodeEmbedder, format_entity_for_embedding
"""

from src.embedding.config import BATCH_SIZE, EMBEDDING_DIM, MODEL_NAME

__all__ = [
    "MODEL_NAME",
    "EMBEDDING_DIM",
    "BATCH_SIZE",
]
