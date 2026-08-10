# Free LLM Models Test Report

Generated: 2026-08-06 23:20:01

| | Count |
|---|---|
| ✅ Passed  | 5 |
| ❌ Failed  | 1 |
| ⏭ Skipped | 0 |
| **Total**  | **6** |

---

## Results by Provider

### GROQ  (1 pass / 0 fail / 0 skip)

| Model | Status | Latency | Response / Error |
|---|---|---|---|
| `groq/openai/gpt-oss-20b` | ✅ PASS | 906ms | OK |

### GEMINI  (1 pass / 0 fail / 0 skip)

| Model | Status | Latency | Response / Error |
|---|---|---|---|
| `gemini/gemini-2.5-flash` | ✅ PASS | 1016ms | OK |

### CEREBRAS  (0 pass / 1 fail / 0 skip)

| Model | Status | Latency | Response / Error |
|---|---|---|---|
| `cerebras/gpt-oss-120b` | ❌ FAIL | 4860ms | litellm.APIError: APIError: CerebrasException - Payment required to access this resource. Visit your |

### OPENROUTER  (1 pass / 0 fail / 0 skip)

| Model | Status | Latency | Response / Error |
|---|---|---|---|
| `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | ✅ PASS | 1718ms | OK |

### COHERE  (1 pass / 0 fail / 0 skip)

| Model | Status | Latency | Response / Error |
|---|---|---|---|
| `cohere/command-a-03-2025` | ✅ PASS | 797ms | OK |

### CLOUDFLARE  (1 pass / 0 fail / 0 skip)

| Model | Status | Latency | Response / Error |
|---|---|---|---|
| `cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast` | ✅ PASS | 984ms | OK |

---

## Failed Models — Full Errors

### `cerebras/gpt-oss-120b`
```
litellm.APIError: APIError: CerebrasException - Payment required to access this resource. Visit your billing tab.
```
