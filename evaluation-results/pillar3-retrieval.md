# Pillar 3 — Retrieval Quality Results

**Run date:**  
**Run by:**  
**Repo under test:** sample-repo (`../sample-repo`)  
**Repo ID in DB:**  
**Environment:**
- DATABASE_URL: postgresql://postgres:...@db.[ref].supabase.co:5432/postgres
- VOYAGE_API_KEY: pa-... (prefix only)
- Scripts: `platform/scripts/validate_retrieval.py` + `platform/scripts/analyze_q3_rankings.py`

---

## Raw Output — validate_retrieval.py

[paste full terminal output here — unedited]

---

## Raw Output — analyze_q3_rankings.py

[paste full terminal output here — unedited]

---

## Key Numbers

### Scenario Pass/Fail

| Metric | Value | Target | Pass? |
|---|---|---|---|
| Scenario 1: multi_hop_call_chain | | PASS | |
| Scenario 2: multi_level_inheritance | | PASS | |
| Scenario 3: interface_implementation | | PASS | |
| Scenario 4: method_disambiguation | | PASS | |
| Scenario 5: textual_similarity_no_conflation | | PASS | |
| Scenario 6: orphan_file_isolation | | PASS | |
| **Total scenarios passing** | | **6 / 6** | |
| Expansion edges verified vs DB | | 100% | |
| AuthService.validate rank | | #1 | |
| format_audit_log rank | | #1 | |
| Score gap rank1 vs rank2 (Q3) | | ≥ 0.02 | |
| Orphan file external expansions | | 0 | |

### Numeric Retrieval Metrics (Precision@K, MRR, Noise, Token Budget)

> These metrics require additions to `validate_retrieval.py` (see
> `evaluation-plan.md` Pillar 3 "What needs to be added" and the
> `compute_metrics()` implementation sketch). Record as "not computed" until
> the script is updated.

| Metric | Formula | Value | Target | Pass? |
|---|---|---|---|---|
| Precision@10 | relevant hits in top 10 / 10 | not computed | ≥ 0.5 | — |
| Recall@10 | relevant hits in top 10 / total relevant | not computed | ≥ 0.7 | — |
| MRR | 1 / rank of first relevant hit | not computed | ≥ 0.7 | — |
| Graph expansion noise ratio | non-relevant expanded / total expanded | not computed | ≤ 0.3 | — |
| Token budget utilisation | total_tokens_est / token_budget | not computed | ≤ 0.9 | — |
| Truncated flag fired | scenarios where context was truncated | not computed | 0 | — |

**Implementation sketch for `compute_metrics()`** (add to `validate_retrieval.py`):

```python
def compute_metrics(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> dict:
    hits = [1 if eid in relevant_ids else 0 for eid in retrieved_ids[:k]]
    precision_at_k = sum(hits) / k
    first_hit = next((i + 1 for i, h in enumerate(hits) if h), None)
    mrr = 1 / first_hit if first_hit else 0.0
    recall_at_k = sum(hits) / len(relevant_ids) if relevant_ids else 0.0
    return {"precision@k": precision_at_k, "recall@k": recall_at_k, "mrr": mrr}
```

---

## Scenario Notes

Brief note on what each scenario retrieved and whether it matched expectations.
Copy the entity IDs or names logged per scenario from the raw output.

**Scenario 1 — multi_hop_call_chain:**

**Scenario 2 — multi_level_inheritance:**

**Scenario 3 — interface_implementation:**

**Scenario 4 — method_disambiguation:**

**Scenario 5 — textual_similarity_no_conflation:**

**Scenario 6 — orphan_file_isolation:**

---

## Verdict

**PASS / FAIL / PARTIAL**

[One paragraph: what passed, what failed, and why.]

---

## Notes

[Anomalies, unexpected output, comparisons to prior runs.]
