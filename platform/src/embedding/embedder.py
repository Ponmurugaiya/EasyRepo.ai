"""Code entity embedding via Voyage AI voyage-code-3 API.

Replaces the local SentenceTransformers/torch stack with a lightweight HTTP
call to Voyage AI. No GPU/CPU model loading — each embed_batch() call sends
texts to the API and returns vectors.

voyage-code-3 specifics:
- 1024 dimensions
- 32K token context window
- input_type="document" for indexing, "query" for search
- Batch size up to 128 texts per request

Rate limiting strategy
----------------------
Texts are packed into token-aware batches (≤ MAX_TOKENS_PER_REQUEST tokens
each). A sliding-window rate limiter tracks the last N request timestamps and
sleeps just long enough to stay within RPM_LIMIT before each call. This means:
  - No fixed sleeps — waits only as long as needed
  - Never exceeds the RPM or TPM budget
  - Adapts automatically to paid-tier limits via env vars

Environment overrides:
  VOYAGE_RPM_LIMIT          max requests per minute      (default: 3)
  VOYAGE_MAX_TOKENS_REQUEST max tokens per request       (default: 9000)
  VOYAGE_RETRY_BASE_DELAY   base backoff on 429 (secs)   (default: 22)

Usage:
    embedder = CodeEmbedder()
    vectors = embedder.embed_batch(["def foo(): ...", "class Bar: ..."])
"""

from __future__ import annotations

import collections
import logging
import os
import re
import time
from typing import Any, Callable

import voyageai

from src.embedding.config import (
    EMBEDDING_DIM,
    INPUT_TYPE,
    VOYAGE_API_KEY,
    VOYAGE_MODEL,
)

logger = logging.getLogger(__name__)

# ── Rate-limit config (overridable via env vars) ──────────────────────────────
# Free tier:  3 RPM, 10K TPM  → set defaults conservatively
# Paid tier:  add payment method → 2000 RPM, 3M TPM
#             set VOYAGE_RPM_LIMIT=2000 (or leave headroom at e.g. 200)
_RPM_LIMIT = int(os.environ.get("VOYAGE_RPM_LIMIT", "3"))
_MAX_TOKENS_PER_REQUEST = int(os.environ.get("VOYAGE_MAX_TOKENS_REQUEST", "9000"))

# Retry config
_MAX_RETRIES = 6
_RETRY_BASE_DELAY = float(os.environ.get("VOYAGE_RETRY_BASE_DELAY", "22.0"))

# Rough token estimator: Voyage tokeniser is ~4 chars/token for code.
_CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    """Estimate token count for a text string (conservative over-estimate)."""
    return max(1, len(text) // _CHARS_PER_TOKEN)


def _build_token_aware_batches(
    texts: list[str], max_tokens: int
) -> list[list[str]]:
    """Pack texts into batches where each batch stays under max_tokens.

    Each text is placed in the current batch if it fits; otherwise a new
    batch is started. A single text that exceeds max_tokens gets its own
    batch (we can't split individual texts).
    """
    batches: list[list[str]] = []
    current_batch: list[str] = []
    current_tokens = 0

    for text in texts:
        tokens = _estimate_tokens(text)
        if current_batch and current_tokens + tokens > max_tokens:
            batches.append(current_batch)
            current_batch = []
            current_tokens = 0
        current_batch.append(text)
        current_tokens += tokens

    if current_batch:
        batches.append(current_batch)

    return batches


class _SlidingWindowRateLimiter:
    """Tracks request timestamps and sleeps just enough to honour RPM limit.

    Uses a deque of the last `rpm_limit` request timestamps. Before each
    request, if the oldest timestamp in the window is less than 60s ago,
    we sleep for the remaining gap. This keeps requests evenly spaced
    without ever wasting more sleep time than necessary.
    """

    def __init__(self, rpm_limit: int) -> None:
        self._rpm = rpm_limit
        self._window: collections.deque[float] = collections.deque()

    def acquire(self, on_wait: Callable[[float], None] | None = None) -> None:
        """Block until a request slot is available.

        Args:
            on_wait: Optional callback(seconds) called just before sleeping,
                     so callers can surface a user-facing "please wait" message.
        """
        now = time.monotonic()
        # Evict timestamps older than 60 seconds
        while self._window and now - self._window[0] >= 60.0:
            self._window.popleft()

        if len(self._window) >= self._rpm:
            # Window is full — sleep until the oldest request falls out
            sleep_for = 60.0 - (now - self._window[0])
            if sleep_for > 0:
                logger.info(
                    "Rate limiter: %d/%d requests used in last 60s — waiting %.1fs",
                    len(self._window), self._rpm, sleep_for,
                )
                if on_wait:
                    on_wait(sleep_for)
                time.sleep(sleep_for)
            # Evict again after sleeping
            now = time.monotonic()
            while self._window and now - self._window[0] >= 60.0:
                self._window.popleft()

        self._window.append(time.monotonic())


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
    """Embedder for code entities and queries using Voyage AI voyage-code-3.

    Internally uses a token-aware batcher and a sliding-window rate limiter
    so it never exceeds the configured RPM/TPM limits regardless of repo size.
    """

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
        self._rate_limiter = _SlidingWindowRateLimiter(rpm_limit=_RPM_LIMIT)

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
        batch_size: int | None = None,  # ignored — token-aware batching takes over
        input_type: str = INPUT_TYPE,
        on_progress: Callable[[int, int], None] | None = None,
        on_wait: Callable[[float], None] | None = None,
    ) -> list[list[float]]:
        """Embed a list of texts, returning one vector per text.

        Packs texts into token-aware batches (≤ VOYAGE_MAX_TOKENS_REQUEST
        tokens each) and enforces the RPM limit via a sliding-window limiter.
        Retries on 429 with exponential backoff.

        Args:
            texts: Texts to embed.
            batch_size: Ignored — kept for API compatibility. Token-aware
                        batching is used instead.
            input_type: "document" for indexing, "query" for search.
            on_progress: Optional callback(done, total) called after each
                         batch completes successfully.
            on_wait: Optional callback(seconds) called just before the rate
                     limiter sleeps, so callers can show a user-facing
                     "Too many requests — waiting Xs, please wait…" message.
        """
        if not texts:
            return []

        total = len(texts)
        batches = _build_token_aware_batches(texts, _MAX_TOKENS_PER_REQUEST)
        logger.info(
            "Voyage embed: %d texts → %d token-aware batches "
            "(max %d tokens/batch, %d RPM limit)",
            total, len(batches), _MAX_TOKENS_PER_REQUEST, _RPM_LIMIT,
        )

        all_embeddings: list[list[float]] = []
        done = 0

        for i, batch in enumerate(batches):
            est_tokens = sum(_estimate_tokens(t) for t in batch)
            logger.info(
                "Voyage embed: batch %d/%d — %d texts, ~%d tokens",
                i + 1, len(batches), len(batch), est_tokens,
            )

            # Acquire a rate-limit slot (sleeps only if needed, fires on_wait)
            self._rate_limiter.acquire(on_wait=on_wait)

            embeddings = self._embed_with_retry(batch, input_type)
            all_embeddings.extend(embeddings)
            done += len(batch)

            if on_progress:
                on_progress(done, total)

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
