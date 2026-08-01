"""Vector similarity search against entity embeddings in PostgreSQL/pgvector.

Embeds a natural language query using the same model as ingestion
(jinaai/jina-embeddings-v2-base-code) and executes cosine distance search.
"""

from __future__ import annotations

from typing import Optional
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.embedding.embedder import CodeEmbedder
from src.storage.models import EntityModel
from src.retrieval.models import RetrievalResult

# Global embedder instance singleton (lazy loaded internally by CodeEmbedder)
_embedder: Optional[CodeEmbedder] = None


def get_embedder() -> CodeEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = CodeEmbedder()
    return _embedder


def search(
    query: str,
    repo_id: str,
    top_k: int = 10,
    db_session: Session = None, # type: ignore
    embedder: Optional[CodeEmbedder] = None,
) -> list[RetrievalResult]:
    """Embed the query and execute top-k cosine similarity search.

    Parameters
    ----------
    query:
        Natural language query string.
    repo_id:
        Target repository ID.
    top_k:
        Number of entities to retrieve (default 10).
    db_session:
        Active SQLAlchemy session.
    embedder:
        Optional CodeEmbedder instance (uses default singleton if None).

    Returns
    -------
    list[RetrievalResult]
        Ranked list of entities with cosine similarity scores in [0, 1].
    """
    if db_session is None:
        raise ValueError("db_session is required for vector search.")

    emb_service = embedder or get_embedder()
    # Use input_type="query" for search queries — Voyage optimises differently
    # for queries vs indexed documents, improving retrieval accuracy.
    query_vector = emb_service.embed_query(query)

    # Cosine distance in pgvector is calculated with `<=>`.
    # Similarity score = 1 - cosine_distance.
    # Note: Vector binding in SQLAlchemy/pgvector: convert float list to string format '[1.0, 2.0, ...]'
    stmt = (
        select(
            EntityModel,
            (1 - EntityModel.embedding.cosine_distance(query_vector)).label("score"), # type: ignore
        )
        .where(
            EntityModel.repo_id == repo_id,
            EntityModel.embedding.is_not(None),
        )
        .order_by(EntityModel.embedding.cosine_distance(query_vector)) # type: ignore
        .limit(top_k)
    )

    results = db_session.execute(stmt).all()


    retrieved: list[RetrievalResult] = []
    for rank, (entity, score) in enumerate(results, start=1):
        retrieved.append(
            RetrievalResult(
                entity_id=entity.id,
                entity=entity,
                score=float(score),
                rank=rank,
            )
        )

    return retrieved
