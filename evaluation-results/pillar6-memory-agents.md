# Pillar 6 — Memory & Agentic System Results

**Run date:**  
**Run by:**  
**Repo under test:** sample-repo (`../sample-repo`)  
**Repo ID in DB:**  
**Environment:**
- DATABASE_URL: postgresql://postgres:...@db.[ref].supabase.co:5432/postgres
- AUTH_ENABLED: false (LTM Tiers 1 & 2 out of scope)
- Evidence sources: `platform/logs/pipeline.log`, Supabase SQL, manual curl commands

---

## 6A — Query Planner Classifications

*(from `pipeline.log` — search `step=planner`, one line per question)*

**Raw log lines:**

```
[paste 4 step=planner lines here]
```

**Classification table:**

| Question | Intent logged | Strategy logged | Confidence | Within expected range? |
|---|---|---|---|---|
| Q1 — login flow | | | | |
| Q2 — AdminUser inheritance | | | | |
| Q3 — no-dependency function | | | | |
| Q4 — validate method | | | | |

**Expected intents:** `feature`, `dependency_flow`, `specific_lookup`, `query`  
**Expected strategies:** `semantic_search`, `semantic_search_with_graph`  
**Failure signal:** `repository_walk` or `repository_overview` for any Q1–Q4

**Verdict:** PASS / FAIL

---

## 6B — Answer Agent Loop (STM)

*(from `pipeline.log` — search `step=final` and `step=post-reretrieval`)*

**`step=post-reretrieval` lines found:**

```
[paste lines here, or write "none — all questions answered on first pass"]
```

**`step=final` lines (one per question):**

```
[paste 4 step=final lines here]
```

**Iteration count table:**

| Question | iteration_count | Re-retrieval triggered? | answer_status |
|---|---|---|---|
| Q1 | | | |
| Q2 | | | |
| Q3 | | | |
| Q4 | | | |

**Pass criteria:**
- `answer_status = answered` for all 4 (non-negotiable)
- `iteration_count ≤ 1` for all 4 (target)

**Verdict:** PASS / FAIL

---

## 6C — LTM Session Knowledge (Tier 3)

### Test 1 — Write correctness

**`step=ltm` line from first call:**

```
[paste step=ltm line here]
```

Expected: `hit=false` (nothing cached yet on first call)

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
| Row written after first answered turn | Yes — 1 row | | |
| `exploration_status` | `complete` | | |
| `confidence` | `high` or `medium` | | |
| `repo_indexed_at` matches `repositories.indexed_at` | Yes | | |

### Test 2 — Cache hit on repeat query

**`step=ltm` line from second call (same session, same question):**

```
[paste step=ltm line here]
```

Expected: `hit=true`

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| `step=ltm hit=true` on second call | Yes | | |

### Test 3 — Stale detection after re-index

**New `indexed_at` after re-index:** [paste timestamp from repositories table]

**`step=ltm` line from call after re-index:**

```
[paste step=ltm line here]
```

Expected: `hit=false` (old entry is stale — `repo_indexed_at` < new `indexed_at`)

**Supabase SQL — stale detection:**

```sql
SELECT
    cm.feature_name,
    cm.repo_indexed_at            AS ltm_written_at,
    r.indexed_at                  AS repo_reindexed_at,
    CASE
        WHEN cm.repo_indexed_at < r.indexed_at THEN 'STALE'
        ELSE 'FRESH'
    END                           AS status
FROM conversation_memory cm
JOIN repositories r ON r.id = cm.repo_id
WHERE cm.session_id = 'eval-session-001';
```

```
[paste query result here]
```

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| Old entry `status` column | `STALE` | | |
| `step=ltm hit=false` after re-index | Yes | | |
| New entry written after stale-detection call | Yes | | |

---

## 6D — LTM Tiers 1 & 2 (User & Repo Facts)

**Status:** Out of scope — `AUTH_ENABLED=false` in current dev setup.

LTM Tiers 1 (user preferences) and 2 (user-repo facts) are only active for
authenticated users. They are additive — injected into the LLM prompt but do
not affect retrieval or citation validation. Their absence does not affect
Pillars 1–5.

To evaluate these tiers, set `AUTH_ENABLED=true`, register a user via
`POST /auth/register`, and send requests with `X-API-Key: <token>`.
See `docs/evaluation-plan-v2.md → Pillar 7D` for detailed steps.

---

## Key Numbers Summary

| Metric | Value | Target | Pass? |
|---|---|---|---|
| Planner: no `repository_walk` for Q1–Q4 | | Pass | |
| Planner: confidence > 0.0 | | > 0.0 | |
| `answer_status = answered` all 4 Qs | | Always | |
| `iteration_count ≤ 1` all 4 Qs | | ≤ 1 | |
| LTM write after Q1 answered | | 1 row in DB | |
| LTM cache hit on second call | | `hit=true` | |
| LTM stale detection after re-index | | `hit=false` + STALE | |
| LTM Tiers 1 & 2 | N/A | Out of scope | N/A |

---

## Verdict

**PASS / FAIL / PARTIAL**

[One paragraph: planner accuracy, agent loop efficiency, LTM write/hit/stale
results. Note any question that triggered re-retrieval and why.]

---

## Notes

[Any unexpected planner classifications, cache miss on second call (explain why),
rate-limit events that affected timing, comparisons to prior runs.]
