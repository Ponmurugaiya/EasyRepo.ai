"""Code entity embedding via Jina AI jina-code-embeddings-1.5b API.

Replaces the voyageai SDK with a plain httpx POST to the Jina Embeddings REST
API. No SDK dependency — httpx is already in the project's requirements.

jina-code-embeddings-1.5b specifics:
- 1536 native dimensions, truncated to 1024 via Matryoshka (dimensions=1024)
- 32K token context window
- task="nl2code.passage" for indexing, "nl2code.query" for search queries
- No stated batch size limit on the API

Rate limiting strategy
----------------------
Texts are packed into token-aware batches (≤ JINA_MAX_TOKENS_REQUEST tokens
each). A sliding-window rate limiter tracks the last N request timestamps and
sleeps just long enough to stay within RPM_LIMIT before each call. This means:
  - No fixed sleeps — waits only as long as needed
  - Never exceeds the RPM or TPM budget
  - Adapts automatically to paid-tier limits via env vars

Environment overrides:
  JINA_RPM_LIMIT          max requests per minute      (default: 100 — free tier)
  JINA_MAX_TOKENS_REQUEST max tokens per request       (default: 8000)
  JINA_RETRY_BASE_DELAY   base backoff on 429 (secs)   (default: 2)

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

import httpx

from src.embedding.config import (
    EMBEDDING_DIM,
    JINA_API_KEY,
    JINA_MODEL,
    TASK_PASSAGE,
    TASK_QUERY,
)

logger = logging.getLogger(__name__)

_JINA_EMBED_URL = "https://api.jina.ai/v1/embeddings"


class CancellationRequestedError(RuntimeError):
    """Raised by embed_batch() when should_cancel() returns True between batches."""

# ── Rate-limit config (overridable via env vars) ──────────────────────────────
# Free tier:  100 RPM, 100K TPM  → default conservatively to free tier
# Paid tier:  top up via Stripe → 500 RPM, 2M TPM
#             set JINA_RPM_LIMIT=500
_RPM_LIMIT = int(os.environ.get("JINA_RPM_LIMIT", "100"))
_MAX_TOKENS_PER_REQUEST = int(os.environ.get("JINA_MAX_TOKENS_REQUEST", "50000"))

# Retry config — Jina returns 429 with a Retry-After header; use short backoff
_MAX_RETRIES = 6
_RETRY_BASE_DELAY = float(os.environ.get("JINA_RETRY_BASE_DELAY", "2.0"))

# Rough token estimator: ~4 chars/token for code (same conservative estimate).
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
    """Embedder for code entities and queries using Jina AI jina-code-embeddings-1.5b.

    Uses the Jina Embeddings REST API via httpx. Internally uses a token-aware
    batcher and a sliding-window rate limiter so it never exceeds the configured
    RPM/TPM limits regardless of repo size.

    Get a free API key at: https://jina.ai/?sui=apikey
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = JINA_MODEL,
    ) -> None:
        self.model = model
        key = api_key or JINA_API_KEY or os.environ.get("JINA_API_KEY", "")
        if not key:
            raise ValueError(
                "JINA_API_KEY is not set. "
                "Get a free API key at https://jina.ai/?sui=apikey and add it to your .env file."
            )
        self._headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._rate_limiter = _SlidingWindowRateLimiter(rpm_limit=_RPM_LIMIT)

    # ── Public API ────────────────────────────────────────────────────────────

    def embed(self, text: str, task: str = TASK_PASSAGE) -> list[float]:
        """Embed a single text string and return a float vector."""
        return self.embed_batch([text], task=task)[0]

    def embed_query(self, text: str) -> list[float]:
        """Embed a search query (uses task='nl2code.query' for better retrieval)."""
        return self.embed(text, task=TASK_QUERY)

    def embed_batch(
        self,
        texts: list[str],
        batch_size: int | None = None,  # ignored — token-aware batching takes over
        input_type: str | None = None,  # legacy param — ignored, use task instead
        task: str = TASK_PASSAGE,
        on_progress: Callable[[int, int], None] | None = None,
        on_wait: Callable[[float], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> list[list[float]]:
        """Embed a list of texts, returning one vector per text.

        Packs texts into token-aware batches (≤ JINA_MAX_TOKENS_REQUEST tokens
        each) and enforces the RPM limit via a sliding-window limiter.
        Retries on 429 with exponential backoff.

        Args:
            texts: Texts to embed.
            batch_size: Ignored — kept for API compatibility. Token-aware
                        batching is used instead.
            input_type: Ignored — kept for API compatibility. Use task instead.
            task: Jina task type. "nl2code.passage" for indexing,
                  "nl2code.query" for search queries.
            on_progress: Optional callback(done, total) called after each
                         batch completes successfully.
            on_wait: Optional callback(seconds) called just before the rate
                     limiter sleeps, so callers can show a user-facing
                     "Too many requests — waiting Xs, please wait…" message.
            should_cancel: Optional callable that returns True when the caller
                           wants to abort. Checked between batches so at most
                           one in-flight Jina call completes before stopping.
        """
        if not texts:
            return []

        total = len(texts)
        batches = _build_token_aware_batches(texts, _MAX_TOKENS_PER_REQUEST)
        logger.info(
            "Jina embed: %d texts → %d token-aware batches "
            "(max %d tokens/batch, %d RPM limit)",
            total, len(batches), _MAX_TOKENS_PER_REQUEST, _RPM_LIMIT,
        )

        all_embeddings: list[list[float]] = []
        done = 0

        for i, batch in enumerate(batches):
            # Check for cancellation before acquiring a rate-limit slot or
            # making any HTTP call — stops between batches, not mid-request.
            if should_cancel and should_cancel():
                logger.info("Jina embed: cancellation requested after %d/%d texts", done, total)
                raise CancellationRequestedError("Embedding cancelled by user")

            est_tokens = sum(_estimate_tokens(t) for t in batch)
            logger.info(
                "Jina embed: batch %d/%d — %d texts, ~%d tokens",
                i + 1, len(batches), len(batch), est_tokens,
            )

            # Acquire a rate-limit slot (sleeps only if needed, fires on_wait)
            self._rate_limiter.acquire(on_wait=on_wait)

            embeddings = self._embed_with_retry(batch, task)
            all_embeddings.extend(embeddings)
            done += len(batch)

            if on_progress:
                on_progress(done, total)

        return all_embeddings

    # ── Internal ──────────────────────────────────────────────────────────────

    def _embed_with_retry(
        self, texts: list[str], task: str
    ) -> list[list[float]]:
        """Call the Jina Embeddings API with exponential backoff on rate-limit errors."""
        delay = _RETRY_BASE_DELAY
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return self._call_api(texts, task)
            except httpx.HTTPStatusError as exc:
                is_rate_limit = exc.response.status_code == 429
                if is_rate_limit and attempt < _MAX_RETRIES:
                    # Honour Retry-After if present, else use exponential backoff
                    retry_after = exc.response.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else delay
                    logger.warning(
                        "Jina rate limit hit (attempt %d/%d), retrying in %.1fs…",
                        attempt, _MAX_RETRIES, wait,
                    )
                    time.sleep(wait)
                    delay *= 2
                else:
                    raise
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "Jina network error (attempt %d/%d): %s — retrying in %.1fs…",
                        attempt, _MAX_RETRIES, exc, delay,
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    raise
        # Should never reach here
        raise RuntimeError("Jina embed failed after max retries")

    def _call_api(self, texts: list[str], task: str) -> list[list[float]]:
        """Make a single POST request to the Jina Embeddings API."""
        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": EMBEDDING_DIM,
            "task": task,
            "truncate": True,   # silently truncate inputs that exceed context window
        }
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                _JINA_EMBED_URL,
                headers=self._headers,
                json=payload,
            )
            response.raise_for_status()

        data = response.json()
        # Jina response: {"data": [{"index": 0, "embedding": [...]}, ...], ...}
        items = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in items]
