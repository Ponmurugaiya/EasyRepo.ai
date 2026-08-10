# Pillar 4 & 5 — Answer, Citation, Memory & Agentic System Results

**Run date:**  
**Run by:**  
**Repo under test:** sample-repo (`../sample-repo`)  
**Repo ID in DB:**  
**Environment:**
- DATABASE_URL: postgresql://postgres:...@db.[ref].supabase.co:5432/postgres
- GROQ_API_KEY: gsk_... (prefix only)
- GEMINI_API_KEY: AQ... (prefix only)
- Generation models: groq/llama-3.3-70b-versatile → gemini/gemini-2.5-flash (fallback)
- Script: `platform/tests/test_ask_endpoint.py`

---

## Raw Output — test_ask_endpoint.py

[paste full terminal output here — unedited]

---

## Key Numbers — Answer & Citation Quality (Pillar 4 + 5)

| Metric | Value | Target | Pass? |
|---|---|---|---|
| Q1 total citations | | ≥ 5 | |
| Q1 unsupported citations | | 0 | |
| Q1 hallucination rate | | 0.0% | |
| Q2 total citations | | ≥ 5 | |
| Q2 unsupported citations | | 0 | |
| Q2 hallucination rate | | 0.0% | |
| Q3 total citations | | ≥ 3 | |
| Q3 unsupported citations | | 0 | |
| Q3 hallucination rate | | 0.0% | |
| Q4 total citations | | ≥ 3 | |
| Q4 unsupported citations | | 0 | |
| Q4 hallucination rate | | 0.0% | |
| **OVERALL total citations** | | ≥ 40 | |
| **OVERALL unsupported citations** | | 0 | |
| **OVERALL hallucination rate** | | **0.0%** | |
| Provider used | | groq or gemini | |

---

## Key Entities in Answers — Completeness Check (Pillar 4)

For each question, confirm the key entities named in `evaluation-plan.md` appear in the answer text.

**Q1** — login flow  
Expected: `login_user`, `AuthService.validate`, `UserModel`, `auth_service.py`

| Entity | Present in answer? |
|---|---|
| login_user | |
| AuthService.validate | |
| UserModel | |
| auth_service.py | |

**Q2** — AdminUser inheritance  
Expected: `AdminUser`, `UserModel`, `BaseModel`, `check_permission`

| Entity | Present in answer? |
|---|---|
| AdminUser | |
| UserModel | |
| BaseModel | |
| check_permission | |

**Q3** — functions with no dependencies  
Expected: `format_audit_log`, `format_user_record`, `truncate_text`

| Entity | Present in answer? |
|---|---|
| format_audit_log | |
| format_user_record | |
| truncate_text | |

**Q4** — validate method  
Expected: `AuthService.validate`, `UserModel.validate`, disambiguation present

| Entity | Present in answer? |
|---|---|
| AuthService.validate | |
| UserModel.validate | |
| Disambiguation (both methods mentioned) | |

---

## Agentic System — Query Planner Classifications

*(from `platform/logs/pipeline.log` — search `step=planner`)*

Paste the 4 raw log lines here, then fill the table.

```
[paste raw step=planner log lines]
```

| Question | Intent logged | Strategy logged | Confidence | Within expected range? |
|---|---|---|---|---|
| Q1 — login flow | | | | |
| Q2 — AdminUser inheritance | | | | |
| Q3 — no-dependency function | | | | |
| Q4 — validate method | | | | |

**Expected intents:** `feature`, `dependency_flow`, `specific_lookup`, `query`  
**Expected strategies:** `semantic_search`, `semantic_search_with_graph`  
**Failure signal:** `repository_walk` or `repository_overview` for any of Q1–Q4

---

## Agentic System — Answer Agent Loop (STM)

*(from `platform/logs/pipeline.log` — search `step=final` and `step=post-reretrieval`)*

Paste any `step=post-reretrieval` lines here (or "none" if absent):

```
[paste or write "none — all questions answered on first pass"]
```

| Question | iteration_count | Re-retrieval triggered? | answer_status |
|---|---|---|---|
| Q1 | | | |
| Q2 | | | |
| Q3 | | | |
| Q4 | | | |

**Pass:** `answer_status = answered` for all 4 (non-negotiable)  
**Target:** `iteration_count ≤ 1` for all 4

---

## Memory System — LTM Session Knowledge (Tier 3)

### Test 1 — Write + cache hit

**First curl call (session=eval-session-001):**  
Expected: `step=ltm hit=false` in log (cache miss — nothing written yet)

Log line found:
```
[paste step=ltm line from pipeline.log]
```

**Supabase SQL result — after first call:**

```sql
SELECT feature_name, confidence, exploration_status, repo_indexed_at, created_at
FROM conversation_memory
WHERE session_id = 'eval-session-001'
ORDER BY created_at DESC;
```

```
[paste query result here]
```

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| Row exists | Yes | | |
| exploration_status | complete | | |
| confidence | high or medium | | |
| repo_indexed_at matches repositories.indexed_at | Yes | | |

**Second curl call (same session, same question):**  
Expected: `step=ltm hit=true` in log (cache hit — LTM entry served)

Log line found:
```
[paste step=ltm line from pipeline.log]
```

### Test 2 — Stale detection after re-index

**Re-indexed at:** [paste new `indexed_at` timestamp from repositories table]

**Curl call after re-index (same session):**  
Expected: `step=ltm hit=false` (stale entry discarded)

Log line found:
```
[paste step=ltm line from pipeline.log]
```

**Supabase SQL — stale detection:**

```sql
SELECT
    cm.feature_name,
    cm.repo_indexed_at AS ltm_written_at,
    r.indexed_at AS repo_reindexed_at,
    CASE WHEN cm.repo_indexed_at < r.indexed_at THEN 'STALE' ELSE 'FRESH' END AS status
FROM conversation_memory cm
JOIN repositories r ON r.id = cm.repo_id
WHERE cm.session_id = 'eval-session-001';
```

```
[paste query result here]
```

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| Old entry status | STALE | | |
| New entry written after re-index call | Yes | | |

### LTM Tiers 1 & 2 (User & Repo Facts)

**Status:** Out of scope — `AUTH_ENABLED=false` in current dev setup.  
These tiers are dormant. No evaluation performed.  
See `evaluation-guide.md` → Area 4D for instructions if auth is enabled.

---

## Verdict

**PASS / FAIL / PARTIAL**

[One paragraph: what passed, what failed, and why. Cover answer quality,
citation quality, planner accuracy, agent loop efficiency, and memory system
separately.]

---

## Notes

[Anomalies, rate-limit events, provider fallback observations, anything unexpected.]
