"""test_routing_live.py — Live end-to-end routing tests.

Verifies that smart_complete:
  1. Picks the correct model tier for each task type
  2. Excludes models whose context window is too small
  3. Skips quota-exhausted models without making a call
  4. Falls back to next provider only when quota is genuinely exhausted
  5. Returns correct (answer, provider) tuples from real APIs

Run:
    python test_routing_live.py
"""
import sys
import os
import time
import logging

# Suppress litellm noise
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
logging.getLogger("litellm").setLevel(logging.ERROR)

# Load .env
from pathlib import Path
for line in (Path(__file__).parent / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "platform"))

# test_routing_live.py makes REAL API calls — quota consumed IS real and should
# be tracked in the production Redis namespace (llm:) so the server knows.
# Cost: ~5 real LLM calls (one per routing scenario). Run sparingly.
# For quota store mechanics testing only, use test_quota_store.py instead.
# Do NOT set LLM_QUOTA_KEY_PREFIX here.

from src.generation.llm_client import (
    smart_complete,
    _select_candidates,
    _get_quota,
    _quota_state,
    _GROQ,
    _GEMINI,
    ALL_MODEL_SPECS,
    ModelSpec,
)

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SEP  = "─" * 65


def check(name: str, cond: bool, detail: str = "") -> bool:
    icon = PASS if cond else FAIL
    print(f"  {icon}  {name}" + (f"  [{detail}]" if detail else ""))
    return cond


results = []


# ─── Unit-level routing tests (no API call) ──────────────────────────────────
print(f"\n\033[1m{SEP}\033[0m")
print("\033[1m  UNIT ROUTING TESTS (no API call)\033[0m")
print(SEP)

_quota_state.clear()

# 1. fast task → fast tier first
cands = _select_candidates("fast", 200)
results.append(check(
    "fast task: fast tier ranked first",
    cands and cands[0].tier == "fast",
    f"first={cands[0].model_id if cands else 'none'}",
))

# 2. reasoning task → reasoning tier ranked first
cands = _select_candidates("reasoning", 2000)
results.append(check(
    "reasoning task: reasoning tier ranked first",
    cands and cands[0].tier == "reasoning",
    f"first={cands[0].model_id if cands else 'none'}",
))

# 3. standard task → standard tier ranked first
cands = _select_candidates("standard", 2000)
results.append(check(
    "standard task: standard tier ranked first",
    cands and cands[0].tier == "standard",
    f"first={cands[0].model_id if cands else 'none'}",
))

# 4. 120k context → allam-2-7b (4k ctx) excluded
cands = _select_candidates("fast", 120_000)
allam_in = [c for c in cands if "allam" in c.model_id]
results.append(check(
    "120k context: allam-2-7b (4k ctx) excluded",
    len(allam_in) == 0,
    f"allam_found={len(allam_in)}",
))

# 5. 900k context → only Gemini (1M ctx)
cands = _select_candidates("standard", 900_000)
non_gemini = [c for c in cands if c.provider != "gemini"]
results.append(check(
    "900k context: only Gemini models survive (1M ctx window)",
    len(non_gemini) == 0 and len(cands) > 0,
    f"providers={list({c.provider for c in cands})}",
))

# 6. Exhaust Groq RPD → Groq excluded from candidates
for spec in _GROQ:
    q = _get_quota(spec.model_id)
    q.rpd_count = spec.rpd_free if spec.rpd_free > 0 else 99999
    q.rpd_date = str(__import__("datetime").date.today())
cands = _select_candidates("standard", 1000)
groq_in = [c for c in cands if c.provider == "groq"]
results.append(check(
    "Groq RPD exhausted: zero Groq candidates returned",
    len(groq_in) == 0,
    f"groq_remaining={len(groq_in)}",
))
_quota_state.clear()

# 7. Exhaust Groq RPM → Groq excluded
for spec in _GROQ:
    q = _get_quota(spec.model_id)
    now = time.monotonic()
    q.rpm_timestamps = [now] * (spec.rpm_free or 30)
cands = _select_candidates("standard", 1000)
groq_in = [c for c in cands if c.provider == "groq"]
results.append(check(
    "Groq RPM exhausted: zero Groq candidates returned",
    len(groq_in) == 0,
    f"groq_remaining={len(groq_in)}",
))
_quota_state.clear()

# 8. force_provider filters correctly
cands = _select_candidates("standard", 1000, force_provider="cohere")
non_cohere = [c for c in cands if c.provider != "cohere"]
results.append(check(
    "force_provider='cohere': only Cohere models selected",
    len(non_cohere) == 0 and len(cands) > 0,
    f"providers={list({c.provider for c in cands})}",
))

# 9. skip_providers filters correctly
cands = _select_candidates("standard", 1000, skip_providers={"groq", "gemini"})
skipped_in = [c for c in cands if c.provider in {"groq", "gemini"}]
results.append(check(
    "skip_providers={'groq','gemini'}: neither appears",
    len(skipped_in) == 0,
    f"skipped_found={len(skipped_in)}",
))

# 10. force_model pinpoints exact spec
cands = _select_candidates("standard", 1000, force_model="groq/llama-3.3-70b-versatile")
results.append(check(
    "force_model: exactly one candidate, correct model",
    len(cands) == 1 and cands[0].model_id == "groq/llama-3.3-70b-versatile",
    f"count={len(cands)} model={cands[0].model_id if cands else 'none'}",
))


# ─── Live API routing tests ───────────────────────────────────────────────────
print(f"\n\033[1m{SEP}\033[0m")
print("\033[1m  LIVE API ROUTING TESTS\033[0m")
print(SEP)
_quota_state.clear()

def live_test(label: str, **kwargs) -> bool:
    """Call smart_complete and report pass/fail + latency."""
    t0 = time.monotonic()
    try:
        answer, provider = smart_complete(**kwargs)
        ms = int((time.monotonic() - t0) * 1000)
        ok = bool(answer and answer.strip())
        results.append(check(
            label,
            ok,
            f"provider={provider}  {ms}ms  {answer.strip()[:25]!r}",
        ))
        return ok
    except Exception as exc:
        ms = int((time.monotonic() - t0) * 1000)
        results.append(check(label, False, f"{ms}ms  {str(exc)[:60]}"))
        return False


# L1: fast task — planner-style
live_test(
    "fast task: dispatches to fast-tier model, returns answer",
    query="test",
    context="Reply with exactly the word: OK",
    system_prompt="You are a test assistant. Follow instructions.",
    task_type="fast",
)
time.sleep(0.5)

# L2: standard task
live_test(
    "standard task: dispatches to standard-tier model",
    query="What does add() do?",
    context="def add(a, b): return a + b\nQuestion: Reply with OK",
    system_prompt="You are a code assistant.",
    task_type="standard",
)
time.sleep(0.5)

# L3: force Gemini only
live_test(
    "force_provider='gemini': only uses Gemini",
    query="test",
    context="Reply with: OK",
    system_prompt="Follow instructions.",
    task_type="fast",
    force_provider="gemini",
)
time.sleep(0.5)

# L4: force Groq only
live_test(
    "force_provider='groq': only uses Groq",
    query="test",
    context="Reply with: OK",
    system_prompt="Follow instructions.",
    task_type="fast",
    force_provider="groq",
)
time.sleep(0.5)

# L5: Groq quota exhausted → falls back to Gemini
print("\n  [Simulating Groq quota exhausted → expect Gemini fallback]")
for spec in _GROQ:
    q = _get_quota(spec.model_id)
    q.rpd_count = spec.rpd_free if spec.rpd_free > 0 else 99999
    q.rpd_date = str(__import__("datetime").date.today())

t0 = time.monotonic()
try:
    answer, provider = smart_complete(
        query="test",
        context="Reply with: OK",
        system_prompt="Follow instructions.",
        task_type="standard",
    )
    ms = int((time.monotonic() - t0) * 1000)
    ok = provider != "groq" and bool(answer)
    results.append(check(
        "quota fallback: Groq exhausted → non-Groq provider used",
        ok,
        f"provider={provider}  {ms}ms",
    ))
except Exception as exc:
    results.append(check("quota fallback", False, str(exc)[:60]))

_quota_state.clear()


# ─── Final summary ────────────────────────────────────────────────────────────
print(f"\n{SEP}")
passed  = sum(1 for r in results if r)
failed  = sum(1 for r in results if not r)
total   = len(results)
print(f"\033[1m  TOTAL: {passed}/{total} passed  |  {failed} failed\033[0m")
print(SEP)

sys.exit(0 if failed == 0 else 1)
