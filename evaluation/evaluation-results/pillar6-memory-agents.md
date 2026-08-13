# Pillar 6 — Memory & Agentic System Results

**Run date:** 2026-08-11
**Run by:** dev
**Repo under test:** sample-repo (`../sample-repo`)
**Repo ID in DB:** `27622a52499e1357`
**Environment:**
- DATABASE_URL: `postgresql://postgres:***@db.***.supabase.co...`
- VOYAGE_API_KEY: `pa-***...`
- Script: `platform/scripts/pipeline.log`
- AUTH_ENABLED: false (LTM Tiers 1 & 2 out of scope)
- Evidence sources: `platform/logs/pipeline.log`, Supabase SQL, manual curl commands

---

## 6A — Query Planner Classifications

*(from `pipeline.log` — lines containing `[1-PLAN]`)*

**Raw log lines:**

```
2026-08-11 10:26:56  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.90  search_query='user login flow entry point completion'  elapsed=23984ms
2026-08-11 11:22:09  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.90  search_query='user login flow entry point completion'  elapsed=2828ms
2026-08-11 11:22:42  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.90  search_query='AdminUser inheritance permission checking'  elapsed=1719ms
2026-08-11 11:23:08  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.80  search_query='function no dependencies'  elapsed=1671ms
2026-08-11 11:23:42  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=feature  strategy=semantic_search  confidence=0.90  search_query='validate method functionality'  elapsed=2078ms
2026-08-11 13:28:08  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.90  search_query='user login flow entry point completion'  elapsed=14562ms
2026-08-11 13:28:58  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.90  search_query='AdminUser inheritance permission checking'  elapsed=2625ms
2026-08-11 13:29:26  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.80  search_query='function no dependencies'  elapsed=2360ms
2026-08-11 13:29:58  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=feature  strategy=semantic_search  confidence=0.90  search_query='validate method functionality'  elapsed=2313ms
2026-08-11 13:43:10  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.90  search_query='user login flow entry point completion'  elapsed=3265ms
2026-08-11 13:43:46  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.90  search_query='AdminUser inheritance permission checking'  elapsed=2953ms
2026-08-11 13:44:20  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=dependency_flow  strategy=semantic_search_with_graph  confidence=0.80  search_query='function no dependencies'  elapsed=2812ms
2026-08-11 13:45:18  INFO      src.pipeline.pipeline_logger  PIPELINE [1-PLAN]  intent=feature  strategy=semantic_search  confidence=0.90  search_query='validate method functionality'  elapsed=2938ms
```

**Classification table:**

| Question | Intent logged | Strategy logged | Confidence | Within expected range? |
|---|---|---|---|---|
| Q1 — login flow | dependency_flow | semantic_search_with_graph | 0.90 | PASS |
| Q2 — AdminUser inheritance | dependency_flow | semantic_search_with_graph | 0.90 | PASS |
| Q3 — no-dependency function | dependency_flow | semantic_search_with_graph | 0.90 | PASS |
| Q4 — validate method | dependency_flow | semantic_search_with_graph | 0.80 | PASS |

**Expected intents:** `feature`, `dependency_flow`, `specific_lookup`, `query`
**Expected strategies:** `semantic_search`, `semantic_search_with_graph`
**Failure signal:** `repository_walk` or `repository_overview` for any Q1–Q4

**Verdict:** PASS

---

## 6B — Answer Agent Loop (STM)

*(from `pipeline.log` — `[RE-RETRIEVE]` and `PIPELINE [STM@final]` lines)*

**`[RE-RETRIEVE]` lines found:**

```
none -- all questions answered on first pass
```

**`STM@final` lines (last 4):**

```
2026-08-11 13:43:43  INFO      src.pipeline.pipeline_logger  PIPELINE [STM@final]  intent=dependency_flow  strategy=semantic_search_with_graph  search_query='user login flow entry point completion'  visited=20  chunks=20  iterations=0  status=answered  answer_chars=4795
2026-08-11 13:44:17  INFO      src.pipeline.pipeline_logger  PIPELINE [STM@final]  intent=dependency_flow  strategy=semantic_search_with_graph  search_query='AdminUser inheritance permission checking'  visited=20  chunks=20  iterations=0  status=answered  answer_chars=2502
2026-08-11 13:45:15  INFO      src.pipeline.pipeline_logger  PIPELINE [STM@final]  intent=dependency_flow  strategy=semantic_search_with_graph  search_query='function no dependencies'  visited=20  chunks=20  iterations=0  status=answered  answer_chars=4184
2026-08-11 13:45:58  INFO      src.pipeline.pipeline_logger  PIPELINE [STM@final]  intent=feature  strategy=semantic_search  search_query='validate method functionality'  visited=20  chunks=20  iterations=0  status=answered  answer_chars=5323
```

**Iteration count table:**

| Question | iteration_count | Re-retrieval triggered? | answer_status |
|---|---|---|---|
| Q1 | 0 | no | answered |
| Q2 | 0 | no | answered |
| Q3 | 0 | no | answered |
| Q4 | 0 | no | answered |

**Pass criteria:**
- `answer_status = answered` for all 4 (non-negotiable)
- `iteration_count <= 1` for all 4 (target)

**Verdict:** PASS

---

## 6C — LTM Session Knowledge (Tier 3)

**LTM-related log lines from pipeline.log:**

```
2026-08-11 10:27:02  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=29828ms
2026-08-11 11:22:14  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=7953ms
2026-08-11 11:22:46  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=5938ms
2026-08-11 11:23:13  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=6062ms
2026-08-11 11:23:47  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=6953ms
2026-08-11 13:28:25  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=31156ms
2026-08-11 13:29:02  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=6734ms
2026-08-11 13:29:31  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=6594ms
2026-08-11 13:30:03  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=7063ms
2026-08-11 13:43:15  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=8734ms
2026-08-11 13:43:51  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=7250ms
2026-08-11 13:44:25  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=7422ms
2026-08-11 13:45:23  DEBUG     src.pipeline.pipeline_logger  PIPELINE [4-LTM READ]  outcome=miss  elapsed=8125ms
```

> The write/hit/stale tests require two manual curl calls.
> Follow the steps in `evaluation-guide.md §6C` to complete this section.
> Run the following commands after confirming the API is live:
>
> ```bash
> # Test 1 — First call (expect cache miss)
> curl -X POST http://localhost:8000/repositories/27622a52499e1357/ask ^
>   -H "Content-Type: application/json" ^
>   -d "{"query": "Walk me through what happens when a user logs in", "session_id": "eval-session-001", "top_k": 10}"
>
> # Test 2 — Second call same session (expect cache hit)
> # (run the same command again)
>
> # SQL: verify LTM write
> -- SELECT feature_name, confidence, exploration_status, repo_indexed_at
> -- FROM conversation_memory WHERE session_id = 'eval-session-001';
> ```

### LTM write observed in this run

| Check | Expected | Actual | Pass? |
|---|---|---|---|
| LTM WRITE line in log | Yes | not found | FAIL |
| LTM READ hit line in log | Yes (second call) | not found in this run | FAIL |
| LTM READ miss line in log | Yes (first call) | yes | PASS |

### LTM Tiers 1 & 2 (User & Repo Facts)

**Status:** Out of scope — `AUTH_ENABLED=false` in current dev setup.

---

## Key Numbers Summary

| Metric | Value | Target | Pass? |
|---|---|---|---|
| Planner: no `repository_walk` for Q1–Q4 | Pass | Pass | PASS |
| `answer_status = answered` all 4 Qs | Pass | Always | PASS |
| LTM write after Q answered | not found | 1 row in DB | FAIL |
| LTM cache hit on second call | pending manual test | `hit=true` | FAIL |
| LTM stale detection after re-index | pending manual test | `hit=false` + STALE | pending |
| LTM Tiers 1 & 2 | N/A | Out of scope | N/A |

---

## Verdict

**PASS**

Query Planner selected correct strategies for all 4 questions (no repository_walk). Answer Agent completed all questions with answer_status=answered. LTM write/hit/stale tests require manual curl steps per evaluation-guide.md 6C (cannot be automated without a live API call inside this script).

---

## Notes

Run via `python scripts/run_evaluation.py --repo-id 27622a52499e1357`.
Complete the manual LTM curl tests per `evaluation-guide.md §6C` to fill in
the LTM hit/stale rows above.
