"""Unified LLM client with proactive smart routing via LiteLLM.

Design: SELECT the right model BEFORE calling it — never let a model fail
due to a predictable constraint. The router inspects task properties and
per-model capabilities before dispatching.

Routing dimensions (all known before the call):
  context_tokens  — estimated input token count
  task_type       — "reasoning" | "standard" | "fast" (affects model tier)
  provider_order  — caller-specified or default cascade

Per-model catalogue encodes:
  max_context     — hard context limit in tokens
  max_output      — max tokens the model will generate
  tier            — "reasoning" | "standard" | "fast"
  rpm_free        — free-tier requests per minute
  rpd_free        — free-tier requests per day
  tpm_free        — free-tier tokens per minute (0 = unknown/no explicit limit)

Runtime quota tracking (in-process, per-worker):
  Tracks RPM/RPD counters per model. If a model is near its limit, the router
  skips it immediately without making a call.

Provider cascade (default, best→fallback):
  Groq → Gemini → OpenRouter → Cohere → Cloudflare → Cerebras

Public API
----------
smart_complete(query, context, system_prompt, ...) -> tuple[str, str]
    Primary entry point. Selects the best model for the task, respects
    quotas, and falls through providers only when truly needed.

generate_answer_with_fallback(...)   — backwards-compat alias
generate_answer(...)                 — legacy Gemini-only shim

LLMProviderError
    Raised when every configured model is unavailable or exhausted.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model Capability Catalogue
# ---------------------------------------------------------------------------
# Each entry defines the hard constraints and free-tier quota for one model.
# These are used for PROACTIVE routing — the router picks before calling.
#
# tier:
#   "reasoning" — extended thinking/chain-of-thought; best quality, highest latency
#   "standard"  — general-purpose instruction-tuned; good quality/speed balance
#   "fast"      — small/optimised; lowest latency, lower quality ceiling
#
# Quota fields (free tier, per the providers' published docs, August 2026):
#   rpm_free  = requests per minute
#   rpd_free  = requests per day  (0 = not publicly documented)
#   tpm_free  = tokens per minute (0 = not explicitly capped)

@dataclass(frozen=True)
class ModelSpec:
    model_id: str          # LiteLLM model string (with prefix)
    provider: str          # "groq" | "gemini" | "openrouter" | "cohere" | "cloudflare" | "cerebras"
    tier: str              # "reasoning" | "standard" | "fast"
    max_context: int       # max input tokens the model accepts
    max_output: int        # max tokens the model will generate
    rpm_free: int          # free-tier requests-per-minute limit
    rpd_free: int          # free-tier requests-per-day limit (0=unknown)
    tpm_free: int          # free-tier tokens-per-minute limit (0=unknown)
    env_key: str           # env var name for the API key


# ─── Groq ───────────────────────────────────────────────────────────────────
# Verified live August 2026 via GET /openai/v1/models + live ping tests.
# Decommissioned: deepseek-r1-*, qwq-32b, llama4-*, specdec, gemma2-9b, mistral-saba
# Excluded:  gpt-oss-120b (empty response), qwen3.6-27b (<think> traces)
_GROQ = [
    ModelSpec("groq/openai/gpt-oss-20b",      "groq", "standard",  131072,  8192, 30, 14400, 12000, "GROQ_API_KEY"),
    ModelSpec("groq/llama-3.3-70b-versatile", "groq", "standard",  128000,  8192, 30, 14400, 12000, "GROQ_API_KEY"),
    ModelSpec("groq/groq/compound-mini",      "groq", "standard",  131072,  8192, 30, 14400,  6000, "GROQ_API_KEY"),
    ModelSpec("groq/llama-3.1-8b-instant",    "groq", "fast",      131072,  8192, 30, 14400, 20000, "GROQ_API_KEY"),
    ModelSpec("groq/allam-2-7b",              "groq", "fast",        3000,   4096, 30,  5000,  5000, "GROQ_API_KEY"),
]

# ─── Gemini ──────────────────────────────────────────────────────────────────
# Free AI Studio key. Gemini 2.5 Flash/Lite confirmed live.
# 2.0 models and 2.5-pro return 429 on this key — commented out.
_GEMINI = [
    ModelSpec("gemini/gemini-2.5-flash",      "gemini", "standard", 1000000, 8192, 10,   250, 250000, "GEMINI_API_KEY"),
    ModelSpec("gemini/gemini-2.5-flash-lite", "gemini", "fast",     1000000, 8192, 15,  1000, 250000, "GEMINI_API_KEY"),
    # ModelSpec("gemini/gemini-2.5-pro",     "gemini", "reasoning", 1000000, 8192,  5,   100, 250000, "GEMINI_API_KEY"),
    # ModelSpec("gemini/gemini-2.0-flash",   "gemini", "standard",  1000000, 8192, 10,  1000, 250000, "GEMINI_API_KEY"),
]

# ─── OpenRouter :free models ──────────────────────────────────────────────────
# 20 RPM, 50 RPD on free account. After first $10 purchase: 1000 RPD.
# nemotron-nano-9b excluded (empty responses). auto-router last as wildcard.
_OPENROUTER = [
    ModelSpec("openrouter/nvidia/nemotron-3-super-120b-a12b:free", "openrouter", "reasoning", 262144, 8192, 20, 50, 0, "OPENROUTER_API_KEY"),
    ModelSpec("openrouter/nvidia/nemotron-3-nano-30b-a3b:free",    "openrouter", "standard",  262144, 8192, 20, 50, 0, "OPENROUTER_API_KEY"),
    ModelSpec("openrouter/google/gemma-4-26b-a4b-it:free",         "openrouter", "standard",  262144, 8192, 20, 50, 0, "OPENROUTER_API_KEY"),
    ModelSpec("openrouter/openai/gpt-oss-20b:free",                "openrouter", "standard",  131072, 8192, 20, 50, 0, "OPENROUTER_API_KEY"),
    ModelSpec("openrouter/cohere/north-mini-code:free",            "openrouter", "fast",      262144, 8192, 20, 50, 0, "OPENROUTER_API_KEY"),
    ModelSpec("openrouter/openrouter/auto",                        "openrouter", "standard",  200000, 8192, 20, 50, 0, "OPENROUTER_API_KEY"),
]

# ─── Cohere ───────────────────────────────────────────────────────────────────
# Trial key: 1000 calls/month. command-r / command-light removed Sep 2025.
_COHERE = [
    ModelSpec("cohere/command-a-03-2025",      "cohere", "reasoning", 256000, 8192, 10, 0, 0, "COHERE_API_KEY"),
    ModelSpec("cohere/command-r-08-2024",      "cohere", "standard",  128000, 4096, 10, 0, 0, "COHERE_API_KEY"),
    ModelSpec("cohere/command-r-plus-08-2024", "cohere", "standard",  128000, 4096, 10, 0, 0, "COHERE_API_KEY"),
]

# ─── Cloudflare Workers AI ────────────────────────────────────────────────────
# 10,000 neurons/day. Excludes: gpt-oss-120b (empty), qwq-32b/<think>, deepseek-r1/<think>.
# mistral namespace fix: @cf/mistralai/ not @cf/mistral/
_CLOUDFLARE = [
    ModelSpec("cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast",     "cloudflare", "standard",  128000, 8192, 60, 0, 0, "CLOUDFLARE_API_KEY"),
    ModelSpec("cloudflare/@cf/openai/gpt-oss-20b",                        "cloudflare", "standard",  131072, 8192, 60, 0, 0, "CLOUDFLARE_API_KEY"),
    ModelSpec("cloudflare/@cf/mistralai/mistral-small-3.1-24b-instruct",  "cloudflare", "standard",  128000, 8192, 60, 0, 0, "CLOUDFLARE_API_KEY"),
    ModelSpec("cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct",       "cloudflare", "standard",  128000, 8192, 60, 0, 0, "CLOUDFLARE_API_KEY"),
    ModelSpec("cloudflare/@cf/meta/llama-3.2-3b-instruct",                "cloudflare", "fast",      128000, 4096, 60, 0, 0, "CLOUDFLARE_API_KEY"),
]

# ─── Cerebras ─────────────────────────────────────────────────────────────────
# Requires billing as of August 2026 — listed last, auto-skipped if key fails.
_CEREBRAS = [
    ModelSpec("cerebras/gpt-oss-120b", "cerebras", "reasoning", 131072, 8192, 30, 0, 0, "CEREBRAS_API_KEY"),
    ModelSpec("cerebras/gemma-4-31b",  "cerebras", "standard",  131072, 4096, 30, 0, 0, "CEREBRAS_API_KEY"),
]

# ─── NVIDIA NIM ───────────────────────────────────────────────────────────────
# Hosted at integrate.api.nvidia.com — OpenAI-compatible API.
# Free tier: 40 RPM hard cap per account (model-dependent credits on signup).
# Verified live August 2026 via direct API probe. 404/timeout models excluded.
# LiteLLM routes via openai/ prefix with a custom api_base.
# Quota: 40 RPM, RPD not publicly documented — rpm_free=40, rpd_free=0.
#
# EXCLUDED (404 or consistent timeout):
#   nvidia/llama-3.1-nemotron-ultra-253b-v1  → 404
#   nvidia/llama-3.1-nemotron-70b-instruct   → 404
#   meta/llama-3.3-70b-instruct              → timeout (overloaded)
#   nvidia/llama-3.1-nemotron-nano-8b-v1     → timeout
#   nvidia/llama-3.3-nemotron-super-49b-v1.5 → empty response
#   nvidia/llama-3.1-nemotron-51b-instruct   → 404
#   nvidia/nemotron-3.5-lightning-30b-a3b    → garbled output
_NVIDIA_NIM = [
    ModelSpec("nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b",        "nvidia_nim", "reasoning", 262144, 8192, 40, 0, 0, "NVIDIA_API_KEY"),
    ModelSpec("nvidia_nim/nvidia/nemotron-3-super-120b-a12b",        "nvidia_nim", "reasoning", 262144, 8192, 40, 0, 0, "NVIDIA_API_KEY"),
    ModelSpec("nvidia_nim/nvidia/llama-3.3-nemotron-super-49b-v1",   "nvidia_nim", "standard",  131072, 8192, 40, 0, 0, "NVIDIA_API_KEY"),
    ModelSpec("nvidia_nim/meta/llama-3.1-70b-instruct",              "nvidia_nim", "standard",  131072, 8192, 40, 0, 0, "NVIDIA_API_KEY"),
    ModelSpec("nvidia_nim/nvidia/nemotron-3-nano-30b-a3b",           "nvidia_nim", "standard",  262144, 8192, 40, 0, 0, "NVIDIA_API_KEY"),
    ModelSpec("nvidia_nim/meta/llama-3.1-8b-instruct",               "nvidia_nim", "fast",      131072, 8192, 40, 0, 0, "NVIDIA_API_KEY"),
    ModelSpec("nvidia_nim/nvidia/nemotron-mini-4b-instruct",         "nvidia_nim", "fast",       4096,  4096, 40, 0, 0, "NVIDIA_API_KEY"),
]

# ─── Master ordered catalogue ─────────────────────────────────────────────────
# Default cascade order: best-quality providers first within each tier.
# The router filters this list based on task requirements before picking.
ALL_MODEL_SPECS: list[ModelSpec] = _GROQ + _GEMINI + _OPENROUTER + _COHERE + _CLOUDFLARE + _CEREBRAS + _NVIDIA_NIM

# ─── Backwards-compat name lists (used by CLI + API routers) ─────────────────
GROQ_MODELS: list[str]            = [m.model_id for m in _GROQ]
GROQ_MODEL_NAMES: list[str]       = [m.model_id.split("/", 1)[1] for m in _GROQ]
GEMINI_MODELS: list[str]          = [m.model_id for m in _GEMINI]
CEREBRAS_MODELS: list[str]        = [m.model_id for m in _CEREBRAS]
OPENROUTER_FREE_MODELS: list[str] = [m.model_id for m in _OPENROUTER]
COHERE_FREE_MODELS: list[str]     = [m.model_id for m in _COHERE]
CLOUDFLARE_FREE_MODELS: list[str] = [m.model_id for m in _CLOUDFLARE]
NVIDIA_NIM_MODELS: list[str]      = [m.model_id for m in _NVIDIA_NIM]
ALL_FREE_MODELS: list[tuple[str, str]] = [(m.model_id, m.provider) for m in ALL_MODEL_SPECS]

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class LLMProviderError(RuntimeError):
    """Raised when every configured model is unavailable or quota-exhausted."""

    #: Machine-readable code forwarded to the HTTP layer as ``error_code``.
    error_code: str = "pipeline_error"


class LLMQuotaExhaustedError(LLMProviderError):
    """All configured LLM providers are over their quota / rate limit."""

    error_code = "llm_quota_exhausted"


class LLMRateLimitedError(LLMProviderError):
    """A transient rate-limit hit; the cascade may recover on the next try."""

    error_code = "llm_rate_limited"


class LLMAuthError(LLMProviderError):
    """API key invalid, missing, or rejected by the provider."""

    error_code = "llm_auth_error"


GeminiClientError = LLMProviderError   # backwards-compat alias
GroqClientError   = LLMProviderError   # backwards-compat alias

# ---------------------------------------------------------------------------
# Quota tracking — Redis-backed, in-memory fallback
# ---------------------------------------------------------------------------
# All quota state lives in quota_store.py which uses Redis as primary storage
# so counters survive restarts and are shared across all workers.
# The in-memory _ModelQuota / _quota_state below are kept ONLY to support
# unit tests that mock _quota_state directly; production code goes through
# get_quota_store().

@dataclass
class _ModelQuota:
    rpm_timestamps: list[float] = field(default_factory=list)
    rpd_count: int = 0
    rpd_date: str = ""

_quota_state: dict[str, _ModelQuota] = {}  # test-only fallback


def _get_quota(model_id: str) -> _ModelQuota:
    """Return in-memory quota object for model_id (used by tests only)."""
    if model_id not in _quota_state:
        _quota_state[model_id] = _ModelQuota()
    return _quota_state[model_id]


def _is_quota_available(spec: ModelSpec) -> bool:
    """Check quota via Redis-backed store; falls back to in-memory if needed.

    When _quota_state has entries for this model (test overrides), the
    in-memory path is used so unit tests remain isolated from Redis.
    Otherwise delegates to the Redis-backed QuotaStore.
    """
    # If this specific model has in-memory test overrides, use them
    if spec.model_id in _quota_state:
        return _check_memory_quota(spec)

    # Primary: Redis-backed shared store
    try:
        from src.generation.quota_store import get_quota_store
        return get_quota_store().is_available(spec)
    except Exception:
        pass
    # Fallback: in-memory (Redis-down scenario)
    return _check_memory_quota(spec)


def _check_memory_quota(spec: ModelSpec) -> bool:
    """In-memory quota check (fallback path, also used by tests)."""
    import time as _time
    from datetime import date

    now = _time.monotonic()
    today = str(date.today())
    quota = _get_quota(spec.model_id)

    if spec.rpd_free > 0:
        if quota.rpd_date != today:
            quota.rpd_date = today
            quota.rpd_count = 0
        if quota.rpd_count >= int(spec.rpd_free * 0.90):
            logger.debug("Router: %s near RPD limit (%d/%d) — skipping",
                        spec.model_id, quota.rpd_count, spec.rpd_free)
            return False

    if spec.rpm_free > 0:
        cutoff = now - 60.0
        quota.rpm_timestamps = [t for t in quota.rpm_timestamps if t > cutoff]
        if len(quota.rpm_timestamps) >= int(spec.rpm_free * 0.90):
            logger.debug("Router: %s near RPM limit (%d/%d) — skipping",
                        spec.model_id, len(quota.rpm_timestamps), spec.rpm_free)
            return False

    return True


def _record_request(model_id: str) -> None:
    """Record one request in both Redis (primary) and in-memory (fallback/tests)."""
    import time as _time
    # Always update in-memory (supports tests and fallback when Redis is down)
    quota = _get_quota(model_id)
    quota.rpm_timestamps.append(_time.monotonic())
    quota.rpd_count += 1
    # Best-effort Redis write — failure is silent, in-memory already updated
    try:
        from src.generation.quota_store import get_quota_store
        get_quota_store().record_request(model_id)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Smart router — picks the best model for the task before calling
# ---------------------------------------------------------------------------

def _count_tokens_approx(text: str) -> int:
    """Rough token estimate: ~4 chars per token (good enough for routing)."""
    return max(1, len(text) // 4)


def pick_model(
    task_type: str = "fast",
    estimated_tokens: int = 0,
    skip_providers: Optional[set[str]] = None,
) -> "ModelSpec":
    """Return the best available ModelSpec for the task without making an LLM call.

    Used by the bin-packing scheduler to know ``max_context`` before building
    batches.  Raises ``LLMQuotaExhaustedError`` if nothing is available.
    """
    candidates = _select_candidates(
        task_type=task_type,
        estimated_tokens=estimated_tokens,
        skip_providers=skip_providers or set(),
    )
    if not candidates:
        raise LLMQuotaExhaustedError(
            f"No models available for task_type={task_type!r}, "
            f"context={estimated_tokens} tokens. "
            "Check that API keys are set and quota is not exhausted."
        )
    return candidates[0]


def pick_next_model(
    task_type: str = "fast",
    exclude_model_ids: Optional[set[str]] = None,
    estimated_tokens: int = 0,
    skip_providers: Optional[set[str]] = None,
) -> "ModelSpec":
    """Return the next best available ModelSpec, excluding already-tried models.

    Used by retry logic in the batch summarizer to fall through to the next
    provider when the primary model fails or its context window is too small
    for a given batch.  Raises ``LLMQuotaExhaustedError`` if nothing remains.
    """
    exclude = exclude_model_ids or set()
    candidates = _select_candidates(
        task_type=task_type,
        estimated_tokens=estimated_tokens,
        skip_providers=skip_providers or set(),
    )
    remaining = [c for c in candidates if c.model_id not in exclude]
    if not remaining:
        raise LLMQuotaExhaustedError(
            f"No remaining models for task_type={task_type!r} after excluding "
            f"{exclude}. All quota exhausted."
        )
    return remaining[0]


def _select_candidates(
    task_type: str,          # "reasoning" | "standard" | "fast"
    estimated_tokens: int,   # total input tokens
    force_provider: Optional[str] = None,
    force_model: Optional[str] = None,
    skip_providers: Optional[set[str]] = None,
) -> list[ModelSpec]:
    """Return an ordered list of candidate ModelSpecs for this task.

    Filters:
    1. Provider has its env key configured.
    2. Model context window fits the request (with 512-token output headroom).
    3. Model tier matches task_type (or is better):
         reasoning task → reasoning tier first, then standard
         standard task  → standard tier first, then reasoning/fast
         fast task      → fast tier first, then standard
    4. Model has quota available.
    5. Respects force_provider / force_model / skip_providers overrides.
    """
    skip = skip_providers or set()
    candidates: list[ModelSpec] = []

    for spec in ALL_MODEL_SPECS:
        # Provider key configured?
        if not os.environ.get(spec.env_key, ""):
            continue
        # Provider skip override?
        if spec.provider in skip:
            continue
        # Force-provider override?
        if force_provider and spec.provider != force_provider:
            continue
        # Force-model override?
        if force_model and spec.model_id != force_model:
            continue
        # Context window fits?
        if estimated_tokens + 512 > spec.max_context:
            logger.debug(
                "Router: %s skipped — context %d > max %d",
                spec.model_id, estimated_tokens, spec.max_context,
            )
            continue
        # Quota available?
        if not _is_quota_available(spec):
            continue
        candidates.append(spec)

    # ── Tier-based ordering ──────────────────────────────────────────────────
    TIER_RANK = {"reasoning": 0, "standard": 1, "fast": 2}
    if task_type == "reasoning":
        # reasoning > standard > fast
        preferred = [0, 1, 2]
    elif task_type == "fast":
        # fast > standard > reasoning
        preferred = [2, 1, 0]
    else:  # "standard"
        # standard > reasoning > fast
        preferred = [1, 0, 2]

    def sort_key(spec: ModelSpec):
        tier_pos = preferred.index(TIER_RANK.get(spec.tier, 1))
        # Within same tier, preserve catalogue order (provider priority)
        catalogue_pos = ALL_MODEL_SPECS.index(spec)
        return (tier_pos, catalogue_pos)

    candidates.sort(key=sort_key)
    return candidates


def _infer_task_type(
    context: str,
    system_prompt: str,
    explicit_task_type: Optional[str],
) -> str:
    """Infer task type from content if not explicitly specified.

    Heuristics:
    - "fast"      → very short context (< 500 tokens), planner-style prompts
    - "reasoning" → explicit markers or long context (> 50k tokens)
    - "standard"  → everything else
    """
    if explicit_task_type in ("reasoning", "standard", "fast"):
        return explicit_task_type

    total_tokens = _count_tokens_approx(context + system_prompt)

    # Planner-style prompts are always fast
    if total_tokens < 500:
        return "fast"

    # Very long context → use a model with big context window (standard+ quality)
    if total_tokens > 50_000:
        return "standard"

    return "standard"

# ---------------------------------------------------------------------------
# LiteLLM import helper
# ---------------------------------------------------------------------------

def _import_litellm():
    try:
        import litellm
        return litellm
    except ImportError as exc:
        raise ImportError(
            "The 'litellm' package is required.\n"
            "Install it with:  pip install litellm"
        ) from exc


# ---------------------------------------------------------------------------
# Core single-model call
# ---------------------------------------------------------------------------

_NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"


def _call_model(
    litellm,
    model: str,
    system_prompt: str,
    context: str,
    api_key: Optional[str] = None,
    max_tokens: int = 8192,
) -> tuple[str, int, int]:
    """Make one LiteLLM completion call.

    Returns (answer_text, prompt_tokens, completion_tokens).
    Token counts come from the response usage object — exact values from the
    provider, not estimates.  Falls back to 0 if the provider omits usage.

    NVIDIA NIM models (prefix ``nvidia_nim/``) are routed via the OpenAI-
    compatible endpoint at integrate.api.nvidia.com using LiteLLM's
    ``openai/`` prefix with a custom ``api_base``.
    """
    # ── NVIDIA NIM: strip prefix, re-route via openai/ + api_base ────────────
    if model.startswith("nvidia_nim/"):
        nim_model = model[len("nvidia_nim/"):]  # e.g. "nvidia/llama-3.1-nemotron-ultra-253b-v1"
        kwargs: dict = dict(
            model=f"openai/{nim_model}",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": context},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
            api_base=_NVIDIA_NIM_BASE_URL,
            api_key=api_key,
        )
    else:
        kwargs = dict(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": context},
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )
        if api_key:
            kwargs["api_key"] = api_key

    response = litellm.completion(**kwargs)
    answer = response.choices[0].message.content
    if not answer:
        raise LLMProviderError(f"Model {model!r} returned an empty response.")

    # Extract real token counts from the response usage object
    usage = getattr(response, "usage", None)
    prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

    return answer, prompt_tokens, completion_tokens

# ---------------------------------------------------------------------------
# Primary public API: smart_complete
# ---------------------------------------------------------------------------

def smart_complete(
    query: str,
    context: str,
    system_prompt: str,
    # Task hints for routing
    task_type: Optional[str] = None,          # "reasoning" | "standard" | "fast" | None=auto
    # Routing overrides (backwards compat + API surface)
    force_provider: Optional[str] = None,     # lock to one provider
    force_model: Optional[str] = None,        # lock to one specific model string
    skip_providers: Optional[set[str]] = None,
    # Per-provider key overrides
    groq_api_key: Optional[str] = None,
    gemini_api_key: Optional[str] = None,
    # Legacy compat — specific model name shortcuts
    groq_model: Optional[str] = None,         # "groq:<name>" or bare name
    gemini_model: Optional[str] = None,
) -> tuple[str, str]:
    """Select the best model for this task and call it.

    Proactive routing: estimates context size and task complexity first, then
    filters the model catalogue to candidates that can handle the request
    without hitting quota limits. Only advances to the next candidate if
    the current one is near quota — not on API failure.

    Returns (answer_text, provider_used).
    """
    litellm = _import_litellm()
    litellm.success_callback = []
    litellm.failure_callback = []

    # ── Inject key overrides into env temporarily ─────────────────────────
    _key_overrides: dict[str, str] = {}
    if groq_api_key:
        _key_overrides["GROQ_API_KEY"] = groq_api_key
    if gemini_api_key:
        _key_overrides["GEMINI_API_KEY"] = gemini_api_key
    for k, v in _key_overrides.items():
        os.environ[k] = v

    # ── Resolve legacy model shortcut args ────────────────────────────────
    if groq_model and not force_model:
        gm = groq_model.removeprefix("groq:")
        # Find matching spec
        full = gm if gm.startswith("groq/") else f"groq/{gm}"
        match = next((s for s in _GROQ if s.model_id == full), None)
        if match:
            force_model = match.model_id
        else:
            force_provider = "groq"
    if gemini_model and not force_model:
        gm = gemini_model.removeprefix("gemini:")
        full = gm if gm.startswith("gemini/") else f"gemini/{gm}"
        match = next((s for s in _GEMINI if s.model_id == full), None)
        if match:
            force_model = match.model_id
        else:
            force_provider = "gemini"

    # ── Estimate context size ─────────────────────────────────────────────
    estimated_tokens = _count_tokens_approx(system_prompt + context)

    # ── Infer task type ───────────────────────────────────────────────────
    resolved_task = _infer_task_type(context, system_prompt, task_type)
    logger.debug(
        "Router: task_type=%s estimated_tokens=%d force_model=%s force_provider=%s",
        resolved_task, estimated_tokens, force_model, force_provider,
    )

    # ── Select candidates ─────────────────────────────────────────────────
    candidates = _select_candidates(
        task_type=resolved_task,
        estimated_tokens=estimated_tokens,
        force_provider=force_provider,
        force_model=force_model,
        skip_providers=skip_providers or set(),
    )

    if not candidates:
        raise LLMQuotaExhaustedError(
            f"No models available for task_type={resolved_task!r}, "
            f"context={estimated_tokens} tokens. "
            "Check that API keys are set and quota is not exhausted."
        )

    errors: list[str] = []

    for spec in candidates:
        api_key = os.environ.get(spec.env_key, "") or None
        try:
            logger.info(
                "Router: dispatching to %s (tier=%s ctx_est=%d)",
                spec.model_id, spec.tier, estimated_tokens,
            )
            _record_request(spec.model_id)
            answer, prompt_tokens, completion_tokens = _call_model(
                litellm,
                model=spec.model_id,
                system_prompt=system_prompt,
                context=context,
                api_key=api_key,
                max_tokens=min(spec.max_output, 8192),
            )
            logger.info(
                "Router: %s succeeded (%d chars, provider=%s, in=%d out=%d tok)",
                spec.model_id, len(answer), spec.provider,
                prompt_tokens, completion_tokens,
            )
            return answer, spec.provider, prompt_tokens, completion_tokens

        except Exception as exc:
            err_msg = str(exc).lower()
            errors.append(f"{spec.model_id}: {type(exc).__name__}: {exc}")

            # ── Case 1: Quota / rate-limit — mark exhausted, try next model ──
            if any(k in err_msg for k in (
                "rate limit", "rate_limit", "429", "quota", "per day",
                "daily", "tokens per day", "payment required",
            )):
                logger.warning(
                    "Router: %s quota/rate-limit hit — marking exhausted for this period",
                    spec.model_id,
                )
                try:
                    from src.generation.quota_store import get_quota_store
                    get_quota_store().mark_exhausted(spec)
                except Exception:
                    pass
                q = _get_quota(spec.model_id)
                if spec.rpd_free > 0:
                    q.rpd_count = spec.rpd_free
                else:
                    q.rpd_count = 999999
                import time as _t
                now = _t.monotonic()
                q.rpm_timestamps = [now] * (spec.rpm_free or 30)
                continue

            # ── Case 2: Request too large — context exceeds this model's limit.
            # Advance to next candidate (which may have a larger context window
            # or be a different provider). Do NOT stop the cascade.
            if any(k in err_msg for k in (
                "request entity too large", "request_too_large",
                "request too large", "context_length_exceeded",
                "maximum context length", "tokens per minute",
                "context window", "input is too long",
            )):
                logger.warning(
                    "Router: %s request too large (ctx_est=%d) — skipping to next model",
                    spec.model_id, estimated_tokens,
                )
                continue

            # ── Case 3: Hard failure — auth, connection, model removed, etc.
            # Stop the cascade; retrying other models won't fix these.
            logger.error(
                "Router: %s non-recoverable failure (%s) — NOT advancing cascade",
                spec.model_id, type(exc).__name__,
            )
            # Detect auth-specific errors for a more actionable error code
            is_auth_err = any(k in err_msg for k in (
                "401", "403", "unauthorized", "forbidden",
                "invalid api key", "authentication", "invalid_api_key",
            ))
            if is_auth_err:
                raise LLMAuthError(
                    f"API key for {spec.provider!r} is invalid or rejected: {exc}"
                ) from exc
            raise LLMProviderError(
                f"Model {spec.model_id} failed with a non-recoverable error: {exc}"
            ) from exc

    raise LLMQuotaExhaustedError(
        "All available models are quota-exhausted or unavailable.\n"
        + "\n".join(f"  {e}" for e in errors)
    )

# ---------------------------------------------------------------------------
# Backwards-compat wrapper: generate_answer_with_fallback
# ---------------------------------------------------------------------------

def generate_answer_with_fallback(
    query: str,
    context: str,
    system_prompt: str,
    # Legacy Groq options
    groq_model: Optional[str] = None,
    groq_api_key: Optional[str] = None,
    # Legacy Gemini options
    gemini_model: str = "gemini-2.5-flash",
    gemini_api_key: Optional[str] = None,
    # Legacy skip flags → translated to skip_providers
    skip_groq: bool = False,
    skip_gemini: bool = False,
    skip_cerebras: bool = False,
    skip_openrouter: bool = False,
    skip_cohere: bool = False,
    skip_cloudflare: bool = False,
    skip_nvidia_nim: bool = False,
    # Task type hint (new)
    task_type: Optional[str] = None,
) -> tuple[str, str]:
    """Backwards-compatible entry point. Delegates to smart_complete.

    Translates the old skip_* flags into the skip_providers set.
    """
    skip: set[str] = set()
    if skip_groq:       skip.add("groq")
    if skip_gemini:     skip.add("gemini")
    if skip_cerebras:   skip.add("cerebras")
    if skip_openrouter: skip.add("openrouter")
    if skip_cohere:     skip.add("cohere")
    if skip_cloudflare: skip.add("cloudflare")
    if skip_nvidia_nim: skip.add("nvidia_nim")

    # Determine force_provider from legacy single-provider skip pattern
    force_provider: Optional[str] = None
    all_providers = {"groq", "gemini", "cerebras", "openrouter", "cohere", "cloudflare", "nvidia_nim"}
    active = all_providers - skip
    if len(active) == 1:
        force_provider = next(iter(active))

    return smart_complete(
        query=query,
        context=context,
        system_prompt=system_prompt,
        task_type=task_type,
        force_provider=force_provider,
        skip_providers=skip,
        groq_api_key=groq_api_key,
        gemini_api_key=gemini_api_key,
        groq_model=groq_model,
        gemini_model=gemini_model if gemini_model != "gemini-2.5-flash" else None,
    )


# ---------------------------------------------------------------------------
# Legacy shim: generate_answer (Gemini-only)
# ---------------------------------------------------------------------------

def generate_answer(
    query: str,
    context: str,
    system_prompt: str,
    model: str = "gemini-2.5-flash",
    api_key: Optional[str] = None,
) -> str:
    """Gemini-only shim kept for backwards compatibility."""
    answer, _ = generate_answer_with_fallback(
        query=query,
        context=context,
        system_prompt=system_prompt,
        gemini_model=model,
        gemini_api_key=api_key,
        skip_groq=True,
        skip_cerebras=True,
        skip_openrouter=True,
        skip_cohere=True,
        skip_cloudflare=True,
    )
    return answer
