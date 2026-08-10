# Free LLM Providers & Models for LiteLLM Integration

> Last updated: August 6, 2026  
> Focus: Permanently-free tiers (no expiring credits, no credit card required) that work via LiteLLM.

---

## Overview

LiteLLM uses a provider prefix in the model string: `groq/model-name`, `gemini/model-name`, `openrouter/org/model-name:free`, `cerebras/model-name`, etc.

Free tier = rate-limited but **$0, no billing required**.

---

## 1. Groq — `GROQ_API_KEY`

**LiteLLM prefix:** `groq/<model-id>`  
**Free tier:** No credit card. Rate-limited per model bucket (~30 RPM / 14,400 RPD at org level). Each model has its own TPM/TPD quota — rotating across models multiplies effective capacity.  
**Signup:** https://console.groq.com

### Text / Chat Models (Verified Live — August 2026)

| LiteLLM Model String | Model | Context | Notes |
|---|---|---|---|
| `groq/openai/gpt-oss-20b` | OpenAI GPT-OSS 20B | 131K | Fastest quality, ~1000 t/s |
| `groq/llama-3.3-70b-versatile` | Meta Llama 3.3 70B | 128K | Still live (deprecated Jun 2026 but responding) |
| `groq/groq/compound-mini` | Groq Compound Mini | 128K | Tool-use composite |
| `groq/llama-3.1-8b-instant` | Meta Llama 3.1 8B | 128K | Highest throughput quota |
| `groq/allam-2-7b` | SDAIA Allam 2 7B | 4K | Arabic-focused, separate quota |

### Excluded / Known Issues (Groq)
- **Decommissioned Jun 2026:** deepseek-r1-distill-*, qwen-qwq-32b, llama4-scout/maverick, llama-3.2-3b-preview, llama-3.3-70b-specdec, gemma2-9b-it, mistral-saba-24b
- `groq/openai/gpt-oss-120b` — returns empty content at standard max_tokens
- `groq/qwen/qwen3.6-27b` — emits `<think>…</think>` traces that corrupt citation regex parsing
- `groq/groq/compound` (full) — adds unsolicited agentic commentary

---

## 2. Google Gemini — `GEMINI_API_KEY`

**LiteLLM prefix:** `gemini/<model-id>`  
**Free tier (Google AI Studio key):** No credit card. Flash/Flash-Lite confirmed working.  
**Signup:** https://aistudio.google.com

| LiteLLM Model String | Status | Context | Free Limits |
|---|---|---|---|
| `gemini/gemini-2.5-flash` | ✅ Live | 1M | 10 RPM, 250 RPD |
| `gemini/gemini-2.5-flash-lite` | ✅ Live | 1M | 15 RPM, 1,000 RPD |
| `gemini/gemini-2.0-flash` | ⚠ Quota | 1M | Returns 429 on free AI Studio key |
| `gemini/gemini-2.0-flash-lite` | ⚠ Quota | 1M | Returns 429 on free AI Studio key |
| `gemini/gemini-2.5-pro` | ⚠ Quota | 1M | 5 RPM, 100 RPD, may require billing |

### Notes
- Flash-Lite is the highest free-quota model — best for high-volume fallback
- Pro is technically accessible without billing but Google may restrict without notice
- Use `gemini/gemini-2.5-flash-lite` as the second fallback after Groq exhaustion

---

## 3. OpenRouter — `OPENROUTER_API_KEY`

**LiteLLM prefix:** `openrouter/<org>/<model>:free`  
**Free tier:** No credit card. 20 RPM, 50 req/day (increases to 1,000 RPD after $10 purchase). Models tagged `:free` are always $0.  
**Signup:** https://openrouter.ai  
**Special router:** `openrouter/openrouter/free` — auto-picks best available free model

### Top Free Models (Verified — August 2026)

| LiteLLM Model String | Status | Context | Notes |
|---|---|---|---|
| `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | ✅ Live | 262K | NVIDIA 120B MoE |
| `openrouter/nvidia/nemotron-3-nano-30b-a3b:free` | ✅ Live | 256K | Fast 30B MoE |
| `openrouter/google/gemma-4-26b-a4b-it:free` | ✅ Live | 262K | Gemma 4 MoE |
| `openrouter/openai/gpt-oss-20b:free` | ✅ Live | 131K | May queue upstream |
| `openrouter/cohere/north-mini-code:free` | ✅ Live | 256K | Coding-optimised |
| `openrouter/openrouter/auto` | ✅ Live | varies | Auto-selects best free model |
| `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | ⚠ Unverified | 1M | Very large, may be slow |
| `openrouter/poolside/laguna-s-2.1:free` | ⚠ Unverified | 262K | Coding agent |

---

## 4. Cerebras — `CEREBRAS_API_KEY`

**LiteLLM prefix:** `cerebras/<model-id>`  
**⚠ NOT free as of August 2026** — All API calls return "Payment required" regardless of claimed free tier. Requires billing account.  
**Signup:** https://cloud.cerebras.ai

| LiteLLM Model String | Model | Context | Status |
|---|---|---|---|
| `cerebras/gpt-oss-120b` | OpenAI GPT-OSS 120B | 131K | ❌ Requires billing |
| `cerebras/gemma-4-31b` | Google Gemma 4 31B | 131K | ❌ Requires billing |

---

## 5. Together AI — `TOGETHER_API_KEY`

**LiteLLM prefix:** `together_ai/<model-id>`  
**Free tier:** $1 credit on signup (no ongoing free tier — credits expire). Listed here for completeness.  
**Signup:** https://api.together.xyz

| LiteLLM Model String | Notes |
|---|---|
| `together_ai/meta-llama/Llama-3-70b-chat-hf` | Requires active credits |
| `together_ai/mistralai/Mixtral-8x7B-Instruct-v0.1` | Requires active credits |

> **Note:** Not truly "free" — requires $1 credit top-up. Skip unless credits are available.

---

## 6. Cohere — `COHERE_API_KEY`

**LiteLLM prefix:** `cohere/<model-id>`  
**Free tier:** Trial key; 1,000 API calls/month, no credit card.  
**Signup:** https://dashboard.cohere.com

| LiteLLM Model String | Status | Context |
|---|---|---|
| `cohere/command-a-03-2025` | ✅ Live | 256K |
| `cohere/command-r-08-2024` | ✅ Live | 128K |
| `cohere/command-r-plus-08-2024` | ✅ Live | 128K |
| `cohere/command-r` | ❌ Removed Sep 2025 | — |
| `cohere/command-r-plus` | ❌ Removed Sep 2025 | — |
| `cohere/command-light` | ❌ Removed Sep 2025 | — |

---

## 7. Cloudflare Workers AI — `CLOUDFLARE_API_KEY` + `CLOUDFLARE_ACCOUNT_ID`

**LiteLLM prefix:** `cloudflare/<model-id>`  
**Free tier:** 10,000 neurons/day free (varies by model size). No credit card.  
**Signup:** https://workers.ai

| LiteLLM Model String | Status | Context |
|---|---|---|
| `cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast` | ✅ Live | 128K |
| `cloudflare/@cf/openai/gpt-oss-20b` | ✅ Live | 131K |
| `cloudflare/@cf/mistralai/mistral-small-3.1-24b-instruct` | ✅ Live | 128K |
| `cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct` | ✅ Live | 128K |
| `cloudflare/@cf/meta/llama-3.2-3b-instruct` | ✅ Live | 128K |
| `cloudflare/@cf/openai/gpt-oss-120b` | ❌ Empty response | — |
| `cloudflare/@cf/qwen/qwq-32b` | ⚠ `<think>` traces | — |
| `cloudflare/@cf/deepseek-ai/deepseek-r1-distill-qwen-32b` | ⚠ `<think>` traces | — |

---

## Summary: Recommended Provider Cascade for EasyRepo

Priority order for the `generate_answer_with_fallback` rotation:

```
1. Groq          — fastest (LPU), best free quotas, no CC required
   Models: llama-3.3-70b-versatile → compound-mini → llama-3.1-8b-instant
           → deepseek-r1-distill-llama-70b → qwen-qwq-32b → llama4-scout → allam-2-7b

2. Gemini        — large free quota on Flash-Lite, 1M context
   Models: gemini-2.5-flash → gemini-2.5-flash-lite

3. OpenRouter    — wide model variety, 50 req/day free
   Models: nvidia/nemotron-3-ultra:free → google/gemma-4-26b:free → openrouter/free

4. Cerebras      — second fast-inference option (WSE hardware)
   Models: llama-3.3-70b → qwen3-235b-a22b
```

---

## Required ENV Variables to Add

```dotenv
# Already configured:
GROQ_API_KEY=...
GEMINI_API_KEY=...

# New providers to add:
OPENROUTER_API_KEY=...   # https://openrouter.ai → Keys
CEREBRAS_API_KEY=...     # https://cloud.cerebras.ai → API Keys
COHERE_API_KEY=...       # https://dashboard.cohere.com → API Keys (optional)
```

---

## LiteLLM Fallback Configuration Reference

```python
# Full cascade example (what will be implemented in llm_client.py):
GROQ_MODELS = [
    "groq/llama-3.3-70b-versatile",
    "groq/groq/compound-mini",
    "groq/deepseek-r1-distill-llama-70b",
    "groq/qwen-qwq-32b",
    "groq/llama-3.1-8b-instant",
    "groq/llama4-scout-17b-16e-instruct",
    "groq/allam-2-7b",
]

GEMINI_MODELS = [
    "gemini/gemini-2.5-flash",
    "gemini/gemini-2.5-flash-lite",
]

CEREBRAS_MODELS = [
    "cerebras/llama-3.3-70b",
    "cerebras/qwen3-235b-a22b",
]

OPENROUTER_FREE_MODELS = [
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free",
    "openrouter/google/gemma-4-26b-a4b-it:free",
    "openrouter/openrouter/free",
]
```
