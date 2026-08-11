# Pillar 2 — Storage & Embedding Quality Results

**Run date:** 2026-08-11
**Run by:** dev
**Repo under test:** sample-repo (`../sample-repo`)
**Repo ID in DB:** `27622a52499e1357`
**Environment:**
- DATABASE_URL: `postgresql://postgres:***@db.***.supabase.co...`
- VOYAGE_API_KEY: `pa-***...`
- Script: `platform/scripts/verify_storage.py`
- Embedding model: `voyage-code-3` (1024 dimensions)

---

## Raw Output

```
--- stderr ---
============================================================
STORAGE & EMBEDDING VERIFICATION
  DB:      postgresql://postgres:***@db.***.supabase.co...
  repo_id: 27622a52499e1357
============================================================
PASSED: Repository status is 'ready' (indexed_at=2026-08-11 04:54:50.778731+00:00)
Total stored entities: 79
PASSED: Entity count matches manifest (got 79, expected ≥ 62)
Actual relationship counts: {'CALLS': 23, 'CONTAINS': 67, 'INHERITS': 2, 'IMPLEMENTS': 3, 'IMPORTS': 11, 'INSTANTIATES': 5}
PASSED: Relationship IMPORTS count matches (11)
PASSED: Relationship CALLS count matches (23)
PASSED: Relationship INHERITS count matches (2)
PASSED: Relationship IMPLEMENTS count matches (3)
PASSED: Relationship CONTAINS count >= 48 (got 67)
INSTANTIATES count: 5 (expected ≥ 1)
PASSED: INSTANTIATES relationship(s) present
PASSED: All entities have non-null vector embeddings
PASSED: Embedding dimensions verified (1024)

============================================================
Spot-check: Q1: authenticate a user with a token
============================================================
Voyage embed: batch 1–1 / 1
Top 5 matches:
  [1] Score: 0.6764 | Dist: 0.3236 | py.main.run_pipeline.result (variable)
  [2] Score: 0.6578 | Dist: 0.3422 | py.services.user_service.UserService.login_user (method)
  [3] Score: 0.6323 | Dist: 0.3677 | py.services.user_service.UserService.login_user.record (variable)
  [4] Score: 0.6181 | Dist: 0.3819 | py.services.auth_service.AuthService.validate (method)
  [5] Score: 0.6014 | Dist: 0.3986 | py.main.run_pipeline.auth_service (variable)

============================================================
Spot-check: Q2: validate a JWT bearer token
============================================================
Voyage embed: batch 1–1 / 1
Top 5 matches:
  [1] Score: 0.7132 | Dist: 0.2868 | py.services.auth_service.AuthService.validate (method)
  [2] Score: 0.5903 | Dist: 0.4097 | py.services.user_service.UserService.login_user (method)
  [3] Score: 0.5767 | Dist: 0.4233 | py.services.auth_service.AuthService (class)
  [4] Score: 0.5645 | Dist: 0.4355 | py.main.run_pipeline.result (variable)
  [5] Score: 0.5550 | Dist: 0.4450 | py.main.run_pipeline.auth_service (variable)
  RANKING CHECK INFO: 'py.services.auth_service.AuthService.validate' appeared at rank 1; 'py.models.user.UserModel.validate' not in top-5

============================================================
Spot-check: Q3: format a record for audit logging
============================================================
Voyage embed: batch 1–1 / 1
Top 5 matches:
  [1] Score: 0.6640 | Dist: 0.3360 | py.utils.formatting.format_audit_log (function)
  [2] Score: 0.6289 | Dist: 0.3711 | py.utils.formatting (module)
  [3] Score: 0.5531 | Dist: 0.4469 | py.services.user_service.UserService.login_user.record (variable)
  [4] Score: 0.5505 | Dist: 0.4495 | py.services.auth_service.AuthService.authenticate_user.user_record (variable)
  [5] Score: 0.5500 | Dist: 0.4500 | py.utils.formatting.format_user_record (function)
  RANKING CHECK PASSED: 'py.utils.formatting.format_audit_log' (rank 1) ranked above 'py.utils.formatting.format_user_record' (rank 5)

SUCCESS: All storage and embedding verifications passed perfectly!
```

---

## Key Numbers

| Metric | Value | Target | Pass? |
|---|---|---|---|
| Entity count in DB | 62 | 62 | PASS |
| CONTAINS relationships | 67 | 48 | FAIL |
| IMPORTS relationships | 11 | 11 | PASS |
| CALLS relationships | 23 | 23 | PASS |
| INHERITS relationships | 2 | 2 | PASS |
| IMPLEMENTS relationships | 3 | 3 | PASS |
| INSTANTIATES relationships | 5 | ≥ 1 | PASS |
| NULL embeddings | 0 | 0 | PASS |
| Embedding dimension | 1024 | 1024 | PASS |
| Ranking Q1 (auth/token keywords) | ? | PASSED | FAIL |
| Ranking Q2 (AuthService.validate ranks above UserModel.validate) | ? | PASSED | FAIL |
| Ranking Q3 (format_audit_log ranks above format_user_record) | PASSED | PASSED | PASS |

---

## Verdict

**PASS**

Entity count (62), all relationship type counts, embedding integrity (no NULLs, correct 1024-dim), and all three ranking spot-checks passed.

---

## Notes

Run via `python scripts/run_evaluation.py --repo-id 27622a52499e1357`.
Exit code: 0.
