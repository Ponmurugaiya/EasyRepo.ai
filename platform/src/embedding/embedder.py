"""Code entity embedding via Voyage AI voyage-code-3 API.

Replaces the local SentenceTransformers/torch stack with a lightweight HTTP
call to Voyage AI. No GPU/CPU model loading — each embed_batch() call sends
texts to the API and returns vectors.

voyage-code-3 specifics:
- 1024 dimensions
- 32K token context window
- input_type="document" for indexing, "query" for search
- Batch size up to 128 texts per request

Usage:
    embedder = CodeEmbedder()
    vectors = embedder.embed_batch(["def foo(): ...", "class Bar: ..."])
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Callable

import voyageai

from src.embedding.config import (
    BATCH_SIZE,
    EMBEDDING_DIM,
    INPUT_TYPE,
    VOYAGE_API_KEY,
    VOYAGE_MODEL,
)

logger = logging.getLogger(__name__)

# Number of retries on rate-limit (429) responses
_MAX_RETRIES = 5
# Base retry delay. On paid plans (300 RPM) a 5s backoff is plenty;
# free-tier users will hit 429s and back off further via exponential doubling.
_RETRY_BASE_DELAY = float(os.environ.get("VOYAGE_RETRY_BASE_DELAY", "5.0"))


def extract_docstring(source: str, has_docstring: bool) -> str:
    """Extract docstring text from entity source code if present."""
    if not has_docstring or not source:
        return ""

    # Python docstrings (triple double or triple single quotes)
    py_match = re.search(r'"""(.*?)"""|\'\'\'(.*?)\'\'\'', source, re.DOTALL)
    if py_match:
        doc = py_match.group(1) if py_match.group(1) is not None else py_match.group(2)
        return (doc or "").strip()

    # JS/TS JSDoc block comment /** ... */
    ts_match = re.search(r'/\*\*(.*?)\*/', source, re.DOTALL)
    if ts_match:
        lines = ts_match.group(1).split("\n")
        cleaned = [re.sub(r"^\s*\*?\s?", "", line) for line in lines]
        return "\n".join(cleaned).strip()

    return ""


def format_entity_for_embedding(entity: Any) -> str:
    """Format an entity into text for vector embedding generation.

    Template:
        Type: {type}
        Name: {name}
        Docstring: {docstring}

        {source}
    """
    if isinstance(entity, dict):
        ent_type = entity.get("type", "")
        name = entity.get("name", "")
        has_doc = entity.get("has_docstring", False)
        source = entity.get("source", "")
    else:
        ent_type = getattr(entity, "type", "")
        name = getattr(entity, "name", "")
        has_doc = getattr(entity, "has_docstring", False)
        source = getattr(entity, "source", "")

    docstring = extract_docstring(source, has_doc)
    return f"Type: {ent_type}\nName: {name}\nDocstring: {docstring}\n\n{source}"


class CodeEmbedder:
    """Embedder for code entities and queries using Voyage AI voyage-code-3."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = VOYAGE_MODEL,
    ) -> None:
        self.model = model
        key = api_key or VOYAGE_API_KEY or os.environ.get("VOYAGE_API_KEY", "")
        if not key:
            raise ValueError(
                "VOYAGE_API_KEY is not set. "
                "Sign up at https://www.voyageai.com and add it to your .env file."
            )
        self._client = voyageai.Client(api_key=key)

    # ── Public API ────────────────────────────────────────────────────────────

    def embed(self, text: str, input_type: str = INPUT_TYPE) -> list[float]:
        """Embed a single text string and return a float vector."""
        return self.embed_batch([text], input_type=input_type)[0]

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query (uses input_type='query' for better retrieval)."""
        return self.embed(text, input_type="query")

    def embed_batch(
        self,
        texts: list[str],
        batch_size: int = BATCH_SIZE,
        input_type: str = INPUT_TYPE,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[list[float]]:
        """Embed a list of texts in batches, returning one vector per text.

        Handles rate limiting with exponential backoff and splits large
        lists into API-sized chunks automatically.

        Args:
            texts: Texts to embed.
            batch_size: Number of texts per API call (max 128).
            input_type: "document" for indexing, "query" for search.
            on_progress: Optional callback(done, total) called after each batch.
        """
        if not texts:
            return []

        all_embeddings: list[list[float]] = []
        total = len(texts)
        # Inter-batch delay for free-tier rate limits (3 RPM = 1 req/20s).
        # Defaults to 0 (no delay) for paid plans.
        # Set VOYAGE_BATCH_DELAY_SECS=21 for the free tier.
        inter_batch_delay = float(os.environ.get("VOYAGE_BATCH_DELAY_SECS", "0"))

        for i, start in enumerate(range(0, total, batch_size)):
            chunk = texts[start : start + batch_size]
            end = start + len(chunk)
            logger.info(
                "Voyage embed: batch %d–%d / %d",
                start + 1, end, total,
            )
            # Pause between batches to stay under 3 RPM free-tier limit
            if i > 0 and inter_batch_delay > 0:
                logger.info(
                    "Voyage rate-limit pause: sleeping %.0fs between batches (%d/%d done)",
                    inter_batch_delay, start, total,
                )
                time.sleep(inter_batch_delay)
            embeddings = self._embed_with_retry(chunk, input_type)
            all_embeddings.extend(embeddings)
            if on_progress:
                on_progress(end, total)

        return all_embeddings

    # ── Internal ──────────────────────────────────────────────────────────────

    def _embed_with_retry(
        self, texts: list[str], input_type: str
    ) -> list[list[float]]:
        """Call the Voyage API with exponential backoff on rate-limit errors."""
        delay = _RETRY_BASE_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                result = self._client.embed(
                    texts,
                    model=self.model,
                    input_type=input_type,
                )
                return [vec for vec in result.embeddings]
            except Exception as exc:
                err = str(exc).lower()
                is_rate_limit = "429" in err or "rate limit" in err or "too many" in err
                if is_rate_limit and attempt < _MAX_RETRIES:
                    logger.warning(
                        "Voyage rate limit hit (attempt %d/%d), retrying in %.1fs...",
                        attempt, _MAX_RETRIES, delay,
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
        # Should never reach here
        raise RuntimeError("Voyage embed failed after max retries")
