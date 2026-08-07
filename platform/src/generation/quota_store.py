"""Persistent quota tracker for LLM model rate limits.

Stores request counters in Redis so quota state survives process restarts
and is shared across all API workers (important for multi-worker deployments).

Storage layout per model:
  llm:rpm:<model_id>   — Sorted set: members are "epoch_ms:uuid", score = epoch_ms.
                         Represents the sliding 60-second request window.
  llm:rpd:<model_id>   — String counter with TTL set to end of current UTC day.
                         Automatically resets at midnight UTC.

Quota check logic (same thresholds as in-memory version):
  RPM: skip model if recent-60s count ≥ rpm_free × 0.90
  RPD: skip model if today's count ≥ rpd_free × 0.90

Fallback behaviour:
  If Redis is unavailable (connection refused, timeout, any error), the store
  silently falls back to the in-memory _ModelQuota state in llm_client.py.
  This means the server keeps running — it just loses cross-worker quota sharing
  until Redis recovers.

Public API
----------
QuotaStore               — the main class; use get_quota_store() to obtain singleton
get_quota_store()        — returns the process-level singleton (lazy init)
is_available(spec)       — True if model has quota right now
record_request(model_id) — record one request just made
mark_exhausted(spec)     — immediately mark model exhausted (called on 429)
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.generation.llm_client import ModelSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redis key helpers
# ---------------------------------------------------------------------------
# Production keys use prefix "llm".
# Tests set LLM_QUOTA_KEY_PREFIX=test (or any other value) to use a separate
# namespace so test runs never pollute production quota counters.
# The test_quota_store.py and test_routing_live.py scripts set this automatically.

def _key_prefix() -> str:
    return os.environ.get("LLM_QUOTA_KEY_PREFIX", "llm")


def _rpm_key(model_id: str) -> str:
    safe = model_id.replace("/", ":").replace(" ", "_")
    return f"{_key_prefix()}:rpm:{safe}"


def _rpd_key(model_id: str) -> str:
    safe = model_id.replace("/", ":").replace(" ", "_")
    return f"{_key_prefix()}:rpd:{safe}"


def _seconds_until_midnight_utc() -> int:
    """Seconds from now until next UTC midnight (TTL for daily counter)."""
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(1, int((tomorrow - now).total_seconds()))


# ---------------------------------------------------------------------------
# QuotaStore
# ---------------------------------------------------------------------------

class QuotaStore:
    """Shared quota tracker backed by Redis with in-memory fallback.

    Thread-safe: each operation uses atomic Redis commands (ZADD, ZCARD,
    INCR, EXPIRE) so no application-level locking is needed.
    """

    def __init__(self, redis_url: Optional[str] = None) -> None:
        self._redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379")
        self._client = None
        self._redis_ok = True
        self._last_error_time: float = 0.0   # monotonic timestamp of last error
        self._retry_after: float = 30.0      # seconds before retrying after a failure

    # ── Redis connection ──────────────────────────────────────────────────────

    def _get_client(self):
        """Return a connected Redis client, or None if unavailable.

        After a failure, waits _retry_after seconds before attempting to
        reconnect. Transient timeouts don't permanently disable Redis.
        """
        import time as _t
        now = _t.monotonic()

        # If Redis was disabled, check if cooldown has passed before retrying
        if not self._redis_ok:
            if now - self._last_error_time < self._retry_after:
                return None
            # Cooldown passed — try to reconnect
            logger.info("QuotaStore: retrying Redis connection after %.0fs cooldown", self._retry_after)
            self._redis_ok = True
            self._client = None

        if self._client is not None:
            return self._client
        try:
            import redis
            self._client = redis.from_url(
                self._redis_url,
                socket_connect_timeout=2,
                socket_timeout=3,
                decode_responses=True,
            )
            self._client.ping()
            logger.info("QuotaStore: connected to Redis at %s", self._redis_url)
            return self._client
        except Exception as exc:
            logger.warning(
                "QuotaStore: Redis unavailable (%s) — falling back to in-memory (retry in %.0fs)",
                exc, self._retry_after,
            )
            self._redis_ok = False
            self._client = None
            self._last_error_time = _t.monotonic()
            return None

    def _redis_call(self, fn, *args, **kwargs):
        """Execute a Redis call; on any error, temporarily disable Redis."""
        import time as _t
        client = self._get_client()
        if client is None:
            return None
        try:
            return fn(client, *args, **kwargs)
        except Exception as exc:
            logger.warning(
                "QuotaStore: Redis error (%s) — switching to in-memory fallback for %.0fs",
                exc, self._retry_after,
            )
            self._redis_ok = False
            self._client = None
            self._last_error_time = _t.monotonic()
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    def is_available(self, spec: "ModelSpec") -> bool:
        """Return True if *spec* has quota available right now.

        Checks both RPM (sliding 60s window) and RPD (daily counter).
        Returns True if Redis is unreachable (fail-open: don't block requests).
        """
        result = self._redis_call(self._check_quota, spec)
        if result is None:
            # Redis unavailable — fail open (allow the call)
            return True
        return result

    def record_request(self, model_id: str) -> None:
        """Record that one request was just dispatched to *model_id*."""
        self._redis_call(self._do_record, model_id)

    def mark_exhausted(self, spec: "ModelSpec") -> None:
        """Immediately mark *model_id* as quota-exhausted.

        Called when the API returns a 429 / rate-limit error.
        Sets both RPM and RPD counters to their maximum values so
        _is_quota_available returns False for the rest of this period.
        """
        self._redis_call(self._do_mark_exhausted, spec)

    # ── Implementation ────────────────────────────────────────────────────────

    def _check_quota(self, client, spec: "ModelSpec") -> bool:
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - 60_000  # 60-second sliding window

        # ── RPM check ────────────────────────────────────────────────────────
        if spec.rpm_free > 0:
            rpm_key = _rpm_key(spec.model_id)
            # Remove entries older than 60s and count what's left — atomically
            pipe = client.pipeline()
            pipe.zremrangebyscore(rpm_key, "-inf", cutoff_ms)
            pipe.zcard(rpm_key)
            _, recent_count = pipe.execute()

            threshold = int(spec.rpm_free * 0.90)
            if recent_count >= threshold:
                logger.debug(
                    "QuotaStore: %s near RPM limit (%d/%d) — skipping",
                    spec.model_id, recent_count, spec.rpm_free,
                )
                return False

        # ── RPD check ────────────────────────────────────────────────────────
        if spec.rpd_free > 0:
            rpd_key = _rpd_key(spec.model_id)
            raw = client.get(rpd_key)
            daily_count = int(raw) if raw else 0

            threshold = int(spec.rpd_free * 0.90)
            if daily_count >= threshold:
                logger.debug(
                    "QuotaStore: %s near RPD limit (%d/%d) — skipping",
                    spec.model_id, daily_count, spec.rpd_free,
                )
                return False

        return True

    def _do_record(self, client, model_id: str) -> None:
        now_ms = int(time.time() * 1000)
        member = f"{now_ms}:{uuid.uuid4().hex[:8]}"  # unique even at same ms

        pipe = client.pipeline()

        # RPM: add to sorted set, set expiry to 70s (buffer beyond 60s window)
        rpm_key = _rpm_key(model_id)
        pipe.zadd(rpm_key, {member: now_ms})
        pipe.expire(rpm_key, 70)

        # RPD: increment counter, set TTL to end of UTC day if new key
        rpd_key = _rpd_key(model_id)
        pipe.incr(rpd_key)
        pipe.execute()

        # Set TTL on RPD key only if it was just created (to not reset mid-day)
        ttl = client.ttl(rpd_key)
        if ttl < 0:  # -1 = no TTL set, -2 = doesn't exist
            client.expire(rpd_key, _seconds_until_midnight_utc())

    def _do_mark_exhausted(self, client, spec: "ModelSpec") -> None:
        now_ms = int(time.time() * 1000)
        pipe = client.pipeline()

        # Fill RPM window: add rpm_free fake entries spanning the last 60s
        rpm_key = _rpm_key(spec.model_id)
        limit = spec.rpm_free or 30
        members = {f"exhaust:{i}:{uuid.uuid4().hex[:4]}": now_ms - i for i in range(limit)}
        pipe.zadd(rpm_key, members)
        pipe.expire(rpm_key, 70)

        # Set RPD to full daily limit so it stays exhausted until midnight
        rpd_key = _rpd_key(spec.model_id)
        daily_limit = spec.rpd_free if spec.rpd_free > 0 else 999_999
        pipe.set(rpd_key, daily_limit)
        ttl = _seconds_until_midnight_utc()
        pipe.expire(rpd_key, ttl)

        pipe.execute()
        logger.info(
            "QuotaStore: %s marked exhausted (RPM fill=%d, RPD=%d, TTL=%ds)",
            spec.model_id, limit, daily_limit, ttl,
        )

    # ── Introspection (for monitoring / debugging) ────────────────────────────

    def get_status(self, model_id: str) -> dict:
        """Return current quota counters for *model_id*. For monitoring only."""
        result = self._redis_call(self._do_get_status, model_id)
        if result is None:
            return {"redis": "unavailable", "model_id": model_id}
        return result

    def _do_get_status(self, client, model_id: str) -> dict:
        now_ms = int(time.time() * 1000)
        cutoff_ms = now_ms - 60_000

        rpm_key = _rpm_key(model_id)
        pipe = client.pipeline()
        pipe.zremrangebyscore(rpm_key, "-inf", cutoff_ms)
        pipe.zcard(rpm_key)
        pipe.ttl(rpm_key)
        _, rpm_count, rpm_ttl = pipe.execute()

        rpd_key = _rpd_key(model_id)
        rpd_raw = client.get(rpd_key)
        rpd_count = int(rpd_raw) if rpd_raw else 0
        rpd_ttl = client.ttl(rpd_key)

        return {
            "model_id": model_id,
            "rpm_last_60s": rpm_count,
            "rpd_today": rpd_count,
            "rpm_key_ttl_s": rpm_ttl,
            "rpd_key_ttl_s": rpd_ttl,
            "redis": "ok",
        }


# ---------------------------------------------------------------------------
# Process-level singleton
# ---------------------------------------------------------------------------

_store: Optional[QuotaStore] = None


def get_quota_store() -> QuotaStore:
    """Return the process-level QuotaStore singleton (lazy init)."""
    global _store
    if _store is None:
        _store = QuotaStore()
    return _store
