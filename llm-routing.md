# LLM Routing — Models, Tiers, Quotas, and Proactive Selection

> Source of truth: `platform/src/generation/llm_client.py`  
> Last verified: August 2026

---

## Design Principle

The router **picks the right model before making any API call**. It never fires a request hoping it will succeed — it checks context size, task complexity, and quota state first, then dispatches exactly once to the best available model.

This keeps latency predictable and avoids 429 errors entirely under normal usage.

---

## Model Catalogue

Every model in the system is described by a `ModelSpec`:

| Field | Meaning |
|---|---|
| `model_id` | LiteLLM model string (includes provider prefix) |
| `provider` | Which API serves it |
| `tier` | `reasoning` / `standard` / `fast` |
| `max_context` | Hard input token limit |
| `max_output` | Max tokens the model will generate |
| `rpm_free` | Free-tier requests per minute |
| `rpd_free` | Free-tier requests per day (`0` = undocumented) |
| `tpm_free` | Free-tier tokens per minute (`0` = no explicit cap) |

---

## Provider Cascade Order

Default priority (best quality → broadest coverage):

```
Groq → Gemini → OpenRouter → Cohere → Cloudflare → Cerebras
```

Within each provider, models are further ordered by tier (see tier logic below).

---

## Full Model List

### Groq — `GROQ_API_KEY`

Free tier: ~30 RPM / 14,400 RPD per organisation. No credit card required.  
Each model has its own TPM quota bucket — rotating multiplies total capacity.

| LiteLLM Model String | Tier | Context | Max Out | RPM | RPD | TPM |
|---|---|---|---|---|---|---|
| `groq/openai/gpt-oss-20b` | standard | 131K | 8192 | 30 | 14,400 | 12,000 |
| `groq/llama-3.3-70b-versatile` | standard | 128K | 8192 | 30 | 14,400 | 12,000 |
| `groq/groq/compound-mini` | standard | 131K | 8192 | 30 | 14,400 | 6,000 |
| `groq/llama-3.1-8b-instant` | **fast** | 131K | 8192 | 30 | 14,400 | 20,000 |
| `groq/allam-2-7b` | **fast** | **4K** | 4096 | 30 | 5,000 | 5,000 |

> **Note — allam-2-7b context:** Only 4K tokens. The router automatically excludes it for any request > ~3.5K tokens.

**Excluded from catalogue (not used):**
- `gpt-oss-120b` — returns empty content at normal max_tokens
- `qwen3.6-27b` — emits `<think>…</think>` traces that break citation parsing
- All deprecated Jun 2026: deepseek-r1-*, qwq-32b, llama4-scout/maverick, llama-3.2-3b-preview, llama-3.3-70b-specdec, gemma2-9b-it, mistral-saba-24b

---

### Gemini — `GEMINI_API_KEY`

Free AI Studio key. Very large context window (1M tokens).

| LiteLLM Model String | Tier | Context | Max Out | RPM | RPD | TPM |
|---|---|---|---|---|---|---|
| `gemini/gemini-2.5-flash` | standard | 1,000K | 8192 | 10 | 250 | 250,000 |
| `gemini/gemini-2.5-flash-lite` | **fast** | 1,000K | 8192 | 15 | 1,000 | 250,000 |

> Flash-Lite has the highest daily quota (1,000 RPD) on the free key — it's the primary fallback when Groq is exhausted.

**Not active on this key (return 429):** gemini-2.0-flash, gemini-2.0-flash-lite, gemini-2.5-pro

---

### OpenRouter — `OPENROUTER_API_KEY`

Free account: 20 RPM / 50 RPD. After first $10 purchase: 1,000 RPD.

| LiteLLM Model String | Tier | Context | Max Out | RPM | RPD |
|---|---|---|---|---|---|
| `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | **reasoning** | 262K | 8192 | 20 | 50 |
| `openrouter/nvidia/nemotron-3-nano-30b-a3b:free` | standard | 262K | 8192 | 20 | 50 |
| `openrouter/google/gemma-4-26b-a4b-it:free` | standard | 262K | 8192 | 20 | 50 |
| `openrouter/openai/gpt-oss-20b:free` | standard | 131K | 8192 | 20 | 50 |
| `openrouter/cohere/north-mini-code:free` | **fast** | 262K | 8192 | 20 | 50 |
| `openrouter/openrouter/auto` | standard | 200K | 8192 | 20 | 50 |

> **openrouter/auto** is an OpenRouter meta-router that picks the best available free model automatically. It sits last in the list as a wildcard.

**Excluded:** `nemotron-nano-9b-v2:free` — returns empty content

---

### Cohere — `COHERE_API_KEY`

Trial key: 1,000 API calls/month.

| LiteLLM Model String | Tier | Context | Max Out | RPM |
|---|---|---|---|---|
| `cohere/command-a-03-2025` | **reasoning** | 256K | 8192 | 10 |
| `cohere/command-r-08-2024` | standard | 128K | 4096 | 10 |
| `cohere/command-r-plus-08-2024` | standard | 128K | 4096 | 10 |

**Removed Sep 2025:** `command-r`, `command-r-plus` (bare), `command-light`

---

### Cloudflare Workers AI — `CLOUDFLARE_API_KEY`

Free: 10,000 neurons/day. Very high RPM (60/min) — good for burst traffic.

| LiteLLM Model String | Tier | Context | Max Out | RPM |
|---|---|---|---|---|
| `cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast` | standard | 128K | 8192 | 60 |
| `cloudflare/@cf/openai/gpt-oss-20b` | standard | 131K | 8192 | 60 |
| `cloudflare/@cf/mistralai/mistral-small-3.1-24b-instruct` | standard | 128K | 8192 | 60 |
| `cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct` | standard | 128K | 8192 | 60 |
| `cloudflare/@cf/meta/llama-3.2-3b-instruct` | **fast** | 128K | 4096 | 60 |

**Excluded:** `gpt-oss-120b` (empty responses), `qwq-32b` / `deepseek-r1-distill-qwen-32b` (`<think>` traces)  
**Fixed namespace:** `@cf/mistralai/` not `@cf/mistral/`

---

### Cerebras — `CEREBRAS_API_KEY`

⚠ Requires billing as of August 2026 — returns "Payment required" on free keys.  
Listed last in cascade; auto-skipped if payment error is returned.

| LiteLLM Model String | Tier | Context | Max Out | RPM |
|---|---|---|---|---|
| `cerebras/gpt-oss-120b` | **reasoning** | 131K | 8192 | 30 |
| `cerebras/gemma-4-31b` | standard | 131K | 4096 | 30 |

---

## Routing Logic

### Step 1 — Estimate context size

```
estimated_tokens ≈ len(system_prompt + context) / 4
```

One char ≈ 0.25 tokens. Good enough for routing without a tokeniser dependency.

---

### Step 2 — Infer task type

If `task_type` is not explicitly passed by the caller:

| Condition | Inferred type |
|---|---|
| `estimated_tokens < 500` | `fast` — planner-style, tiny prompts |
| `estimated_tokens ≥ 500` | `standard` |
| Explicitly passed | as given |

The query planner always passes `task_type="fast"`.  
The answer agent lets it auto-infer (typically `standard` for real code-QA contexts).

---

### Step 3 — Filter candidates

For every model in `ALL_MODEL_SPECS` (in catalogue order), the router checks:

```
1. API key configured?          (env var present and non-empty)
2. Provider not in skip set?    (skip_providers override)
3. Force-provider match?        (force_provider override, if set)
4. Force-model match?           (force_model override, if set)
5. Context fits?                estimated_tokens + 512 ≤ max_context
6. Quota available?             see quota check below
```

Models failing any check are silently dropped from the candidate list.

---

### Step 4 — Sort by tier priority

The filtered candidates are sorted by how well their tier matches the task:

| Task type | Tier priority order |
|---|---|
| `fast` | fast → standard → reasoning |
| `standard` | standard → reasoning → fast |
| `reasoning` | reasoning → standard → fast |

Within the same tier, **catalogue order is preserved** (Groq before Gemini before OpenRouter, etc.).

---

### Step 5 — Dispatch to first candidate

The router calls the **first** model in the sorted list. No trial calls. No guessing.

```
router picks candidate[0]  →  calls API once  →  returns (answer, provider)
```

---

### Quota Check (before every call)

The router tracks two counters per model in memory:

**RPM check:**
- Keeps a list of timestamps of the last 60 seconds of requests
- If `len(timestamps) ≥ rpm_free × 0.90` → skip this model (it's near the limit)
- Old timestamps (> 60s ago) are pruned before checking

**RPD check:**
- Keeps a daily request count, resets at midnight
- If `rpd_count ≥ rpd_free × 0.90` → skip this model for the rest of the day

The **90% threshold** means the router switches to the next model before hitting the hard wall. Under normal traffic, a 429 should never actually occur.

---

### What happens when a quota/rate-limit 429 does occur

If a model returns a 429 anyway (e.g. from a parallel worker that wasn't tracked):

```python
# Detected by matching any of:
# "rate limit" | "rate_limit" | "429" | "quota" | "per day"
# "daily" | "tokens per day" | "payment required"

→ Immediately mark that model exhausted in _quota_state:
     rpd_count  = rpd_free  (maxes out daily counter)
     rpm_timestamps = [now] × rpm_free  (fills minute window)

→ Continue to next candidate in the sorted list
```

This means a single 429 permanently excludes the model for the rest of that minute/day in this process — no retry storms.

---

### What happens on non-quota failures

For any other error (auth failure, connection error, model error, empty response):

```
→ Raise LLMProviderError immediately
→ Do NOT advance to next model
```

This prevents silently swallowing real configuration errors. If Groq returns 401 (bad API key), you see the error immediately rather than burning through all 23 models one by one.

---

## Routing Examples

### Example A — Short planner prompt (~150 tokens)

```
task_type inferred: fast  (< 500 tokens)

candidates after filter + sort:
  1. groq/llama-3.1-8b-instant   (fast,  131K ctx, 30 RPM)
  2. groq/allam-2-7b             (fast,  4K ctx,   30 RPM)   ← only if ≤ 3.5K tokens
  3. gemini/gemini-2.5-flash-lite (fast,  1M ctx,   15 RPM)
  4. openrouter/cohere/north-mini-code:free (fast, 262K ctx, 20 RPM)
  5. cloudflare/@cf/meta/llama-3.2-3b-instruct (fast, 128K ctx, 60 RPM)
  6. groq/openai/gpt-oss-20b     (standard, 131K)   ← standard tier, lower priority
  ...

dispatches to: groq/llama-3.1-8b-instant
```

### Example B — Code-QA with 5,000-token context

```
task_type inferred: standard  (≥ 500 tokens)

candidates after filter + sort:
  1. groq/openai/gpt-oss-20b          (standard, 131K ctx)
  2. groq/llama-3.3-70b-versatile     (standard, 128K ctx)
  3. groq/groq/compound-mini          (standard, 131K ctx)
  4. gemini/gemini-2.5-flash          (standard, 1M ctx)
  5. openrouter/nvidia/nemotron-3-nano (standard, 262K ctx)
  6. openrouter/google/gemma-4-26b    (standard, 262K ctx)
  ...

dispatches to: groq/openai/gpt-oss-20b
```

### Example C — Very long context (900,000 tokens)

```
task_type inferred: standard

context filter: 900,000 + 512 > max_context
  → all Groq models filtered out     (max 131K)
  → all OpenRouter models filtered    (max 262K)
  → all Cohere models filtered        (max 256K)
  → all Cloudflare models filtered    (max 131K)
  → all Cerebras models filtered      (max 131K)

surviving candidates:
  1. gemini/gemini-2.5-flash          (standard, 1M ctx)
  2. gemini/gemini-2.5-flash-lite     (fast,     1M ctx)

dispatches to: gemini/gemini-2.5-flash
```

### Example D — Groq RPD at 90% capacity

```
task_type: standard, 3,000 tokens

quota check for each Groq model:
  groq/openai/gpt-oss-20b:    rpd_count=13,200 / 14,400 = 91.7% → SKIP
  groq/llama-3.3-70b:         rpd_count=13,200 / 14,400 = 91.7% → SKIP
  groq/groq/compound-mini:    rpd_count=13,200 / 14,400 = 91.7% → SKIP
  groq/llama-3.1-8b-instant:  rpd_count=13,200 / 14,400 = 91.7% → SKIP
  groq/allam-2-7b:            rpd_count=4,600  /  5,000 = 92.0% → SKIP

first surviving candidate:
  gemini/gemini-2.5-flash  (standard, 1M ctx, 10 RPM, 250 RPD)

dispatches to: gemini/gemini-2.5-flash  ← zero 429s, no wasted calls
```

---

## Routing Overrides (API / CLI)

| Parameter | Effect |
|---|---|
| `task_type="reasoning"` | Prioritises reasoning-tier models first |
| `task_type="fast"` | Prioritises fast-tier models first |
| `force_provider="groq"` | Only considers Groq models |
| `force_model="groq/llama-3.3-70b-versatile"` | Locks to exactly this one model |
| `skip_providers={"groq","gemini"}` | Excludes those providers entirely |

Via HTTP API (body field `model`):
- `"groq:llama-3.3-70b-versatile"` → force Groq, specific model
- `"gemini:gemini-2.5-flash"` → force Gemini, specific model
- bare name in GROQ_MODEL_NAMES → inferred as Groq

---

## Quota State — Redis-Backed, Shared Across Workers

Quota counters live in **Redis**, not in-process memory. This means:

- State **survives process restarts** (server deploy, crash, reload)
- State is **shared across all API workers** — a 429 absorbed by worker 1 immediately prevents workers 2, 3, 4 from also hitting it
- Counters **auto-expire**: RPM keys expire after 70s, RPD keys expire at UTC midnight

### Redis Keys Per Model

```
llm:rpm:<model_id>   — Sorted set (score = epoch_ms timestamp per request)
                       Sliding 60-second window. Expires after 70s.

llm:rpd:<model_id>   — String counter (integer).
                       TTL set to seconds-until-UTC-midnight on first write.
                       Auto-resets to 0 at midnight via key expiry.
```

### RPM Check (per-minute rate)

```
1. ZREMRANGEBYSCORE  remove entries older than (now - 60 000ms)
2. ZCARD             count entries remaining in last 60s
3. if count ≥ rpm_free × 0.90  →  skip this model
```

All in one Redis pipeline (atomic).

### RPD Check (per-day rate)

```
1. GET rpd_key       read today's request count
2. if count ≥ rpd_free × 0.90  →  skip this model
```

### Recording a Request

```
ZADD rpm_key  {epoch_ms:uuid → epoch_ms}   (add timestamped entry)
EXPIRE rpm_key 70                           (prune after 70s)
INCR  rpd_key                               (increment daily counter)
EXPIRE rpd_key <seconds-until-midnight>     (only if TTL not set yet)
```

### Marking Exhausted (on 429)

```
ZADD  rpm_key  {exhaust:N → now_ms} × rpm_free   (fill RPM window)
EXPIRE rpm_key 70
SET   rpd_key  rpd_free                           (set daily count to limit)
EXPIRE rpd_key <seconds-until-midnight>
```

After this, `_is_quota_available` returns `False` immediately for all subsequent
calls until the RPM key expires (next minute) or the RPD key expires (midnight UTC).

### Fallback — Redis Unavailable

If Redis is unreachable at any point (connection refused, timeout, network error):

- `QuotaStore` logs a warning once and sets `_redis_ok = False`
- All subsequent quota checks **fail-open** (return `True` = allow the call)
- In-memory per-process counters (`_quota_state`) continue to track state locally
- Multi-worker sharing is lost until Redis recovers, but requests still flow

Redis reconnection is attempted lazily on the next request after a restart.

### Multi-worker sharing

Each API worker process has its own `_quota_state` dict in memory, but all workers share the same Redis keys. This means a 429 hit by worker 1 immediately prevents workers 2, 3, 4 from trying the same model. State also survives Uvicorn reloads, crashes, and deploys. Daily counters reset at UTC midnight automatically via Redis TTL expiry.

---

## Test Script Quota Rules

| Script | Real LLM calls? | Redis namespace | Notes |
|---|---|---|---|
| `test_quota_store.py` | ❌ No | `test:` prefix | Writes fake counters to test store mechanics. Isolated from production. |
| `test_free_models.py` | ✅ Yes | `llm:` (production) | Real calls, real quota consumed. Use `--fast` (6 calls) during development. |
| `test_routing_live.py` | ✅ Yes (~5 calls) | `llm:` (production) | Real calls, real quota consumed. |

**Why `test_quota_store.py` uses a separate namespace:**
It writes fake values like `record_request()` 5 times and `mark_exhausted()` to test the Redis logic — no actual API calls happen. If it wrote to `llm:`, the router would think those models were exhausted even though no real quota was consumed. The `test:` namespace keeps those fake writes isolated.

**Why the other scripts use production namespace:**
`test_free_models.py` and `test_routing_live.py` make actual API calls, consuming real quota. Writing that to `llm:` is correct — the router needs to know those calls happened to avoid hitting 429s in subsequent requests.

**The API quota is always shared:**
The actual limit (e.g., 14,400 RPD on Groq) is enforced by Groq's servers, not by Redis. Redis only tells the *router* how much of that quota has been used. Running test scripts burns the same daily budget as production requests.

### Quota State Lifecycle

```
Process starts
    └─ QuotaStore singleton lazy-connects to Redis

First request to model X
    └─ record_request(X):
         ZADD llm:rpm:X  {ts:uuid → ts}   EXPIRE 70
         INCR llm:rpd:X                    EXPIRE until-midnight (if no TTL)

Each candidate selection (_select_candidates):
    └─ QuotaStore.is_available(X):
         ZREMRANGEBYSCORE + ZCARD  → rpm_count
         GET llm:rpd:X             → rpd_count
         if rpm_count ≥ rpm_free×0.90  →  skip
         if rpd_count ≥ rpd_free×0.90  →  skip

On 429 from API:
    └─ QuotaStore.mark_exhausted(X):
         ZADD fills RPM window with rpm_free fake entries
         SET  rpd_key = rpd_free
         → model excluded for remainder of minute + day

At UTC midnight:
    └─ rpd key TTL expires automatically
    → model eligible again, rpd_count resets to 0

On process restart:
    └─ Redis state intact — quota counters preserved
    → _quota_state (in-memory fallback) starts empty
    → Redis picks up immediately on first request
```

---

## ENV Variables Required

```dotenv
# Required (minimum one must be set)
GROQ_API_KEY=...          # https://console.groq.com
GEMINI_API_KEY=...        # https://aistudio.google.com

# Optional (each adds more models to the cascade)
OPENROUTER_API_KEY=...    # https://openrouter.ai → Keys
COHERE_API_KEY=...        # https://dashboard.cohere.com → API Keys
CLOUDFLARE_API_KEY=...    # https://dash.cloudflare.com → AI → Workers AI
CLOUDFLARE_ACCOUNT_ID=... # required alongside CLOUDFLARE_API_KEY
CEREBRAS_API_KEY=...      # https://cloud.cerebras.ai (billing required)
```

Any provider whose key is absent is silently excluded from the candidate list.

---

## Total Verified Live Models

| Provider | Models | Tiers available | Verified |
|---|---|---|---|
| Groq | 5 | standard, fast | ✅ Aug 2026 |
| Gemini | 2 | standard, fast | ✅ Aug 2026 |
| OpenRouter | 6 | reasoning, standard, fast | ✅ Aug 2026 |
| Cohere | 3 | reasoning, standard | ✅ Aug 2026 |
| Cloudflare | 5 | standard, fast | ✅ Aug 2026 |
| Cerebras | 2 | reasoning, standard | ⚠ billing required |
| **Total** | **23** | | |
