"""test_quota_store.py — Verify Redis-backed quota store works end to end."""
import sys
import os
from pathlib import Path

# Load .env
for line in (Path(__file__).parent / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "platform"))

# Use a separate Redis namespace so tests never pollute production quota counters
os.environ["LLM_QUOTA_KEY_PREFIX"] = "test"

from src.generation.quota_store import QuotaStore, get_quota_store
from src.generation.llm_client import ALL_MODEL_SPECS, _quota_state, _is_quota_available, _record_request

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"


def check(label, cond, detail=""):
    icon = PASS if cond else FAIL
    print(f"  {icon}  {label}" + (f"  [{detail}]" if detail else ""))
    return cond


results = []
print("\n=== QuotaStore Tests ===\n")

store = get_quota_store()
results.append(check("Redis connection", store._redis_ok, f"url={store._redis_url[:30]}"))

# Use a test model spec (Groq gpt-oss-20b)
spec = next(s for s in ALL_MODEL_SPECS if "gpt-oss-20b" in s.model_id and s.provider == "groq")
model_id = spec.model_id

# Clean up any leftover test keys
import redis as redis_lib
r = redis_lib.from_url(os.environ["REDIS_URL"], decode_responses=True)
from src.generation.quota_store import _rpm_key, _rpd_key
r.delete(_rpm_key(model_id))
r.delete(_rpd_key(model_id))

# 1. Fresh model should be available
results.append(check("Fresh model is available", store.is_available(spec)))

# 2. Record 5 requests, check counters
for _ in range(5):
    store.record_request(model_id)
status = store.get_status(model_id)
results.append(check("record_request: RPM counter increments", status["rpm_last_60s"] == 5, f"rpm={status['rpm_last_60s']}"))
results.append(check("record_request: RPD counter increments", status["rpd_today"] == 5, f"rpd={status['rpd_today']}"))
results.append(check("RPD key has TTL set (midnight expiry)", status["rpd_key_ttl_s"] > 0, f"ttl={status['rpd_key_ttl_s']}s"))

# 3. Still available at 5 requests (well below 90% of 30 RPM = 27)
results.append(check("Available at 5/30 RPM (below 90% threshold)", store.is_available(spec)))

# 4. Mark exhausted and verify unavailable
r.delete(_rpm_key(model_id))
r.delete(_rpd_key(model_id))
store.mark_exhausted(spec)
status = store.get_status(model_id)
results.append(check("mark_exhausted: RPM key filled", status["rpm_last_60s"] >= spec.rpm_free, f"rpm={status['rpm_last_60s']}/{spec.rpm_free}"))
results.append(check("mark_exhausted: RPD set to limit", status["rpd_today"] == spec.rpd_free, f"rpd={status['rpd_today']}/{spec.rpd_free}"))
results.append(check("mark_exhausted: model now unavailable", not store.is_available(spec)))

# 5. Cleanup and verify redis-free fallback works
r.delete(_rpm_key(model_id))
r.delete(_rpd_key(model_id))
_quota_state.clear()

# 6. _is_quota_available delegates to Redis store when no test overrides
results.append(check("_is_quota_available delegates to Redis (no overrides)", _is_quota_available(spec)))

# 7. _quota_state test overrides still work for unit tests
_quota_state.clear()
from src.generation.llm_client import _ModelQuota
from datetime import date as _date
q = _ModelQuota()
q.rpd_count = spec.rpd_free          # exhaust daily count
q.rpd_date  = str(_date.today())     # must match today or _check_memory_quota resets it
_quota_state[model_id] = q
results.append(check("_quota_state test override honoured (in-memory exhaustion)", not _is_quota_available(spec)))
_quota_state.clear()

# 8. _record_request writes to both in-memory and Redis
_quota_state.clear()
r.delete(_rpm_key(model_id))
r.delete(_rpd_key(model_id))
_record_request(model_id)
redis_rpd = int(r.get(_rpd_key(model_id)) or 0)
mem_entry = _quota_state.get(model_id)
results.append(check("_record_request: Redis RPD incremented", redis_rpd == 1, f"redis_rpd={redis_rpd}"))
results.append(check("_record_request: in-memory RPD incremented", mem_entry is not None and mem_entry.rpd_count == 1, f"mem_rpd={mem_entry.rpd_count if mem_entry else 'none'}"))

# 9. Redis-down fallback: disconnect Redis and verify fail-open
bad_store = QuotaStore(redis_url="redis://localhost:9999")
result = bad_store.is_available(spec)
results.append(check("Redis-down: fail-open (returns True)", result is True, f"redis_ok={bad_store._redis_ok}"))

# Cleanup
r.delete(_rpm_key(model_id))
r.delete(_rpd_key(model_id))
_quota_state.clear()
r.close()

# Summary
print(f"\n{'─'*50}")
passed = sum(1 for r in results if r)
failed = sum(1 for r in results if not r)
print(f"  TOTAL: {passed}/{len(results)} passed  |  {failed} failed")
print(f"{'─'*50}\n")
sys.exit(0 if failed == 0 else 1)
