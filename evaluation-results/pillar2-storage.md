# Pillar 2 — Storage & Embedding Quality Results

**Run date:**  
**Run by:**  
**Repo under test:** sample-repo (`../sample-repo`)  
**Repo ID in DB:**  
**Environment:**
- DATABASE_URL: postgresql://postgres:...@db.[ref].supabase.co:5432/postgres
- VOYAGE_API_KEY: pa-... (prefix only)
- Embedding model: voyage-code-3 (1024 dimensions)
- Script: `platform/scripts/verify_storage.py`

---

## Raw Output

[paste full terminal output here — unedited]

---

## Key Numbers

| Metric | Value | Target | Pass? |
|---|---|---|---|
| Entity count in DB | | 62 | |
| CONTAINS relationships | | 48 | |
| IMPORTS relationships | | 11 | |
| CALLS relationships | | 23 | |
| INHERITS relationships | | 2 | |
| IMPLEMENTS relationships | | 3 | |
| INSTANTIATES relationships | | ≥ 1 | |
| NULL embeddings | | 0 | |
| Embedding dimension | | 1024 | |
| Ranking Q1 (auth/token keywords) | | PASSED | |
| Ranking Q2 (AuthService.validate ranks above UserModel.validate) | | PASSED | |
| Ranking Q3 (format_audit_log ranks above format_user_record) | | PASSED | |

---

## Verdict

**PASS / FAIL / PARTIAL**

[One paragraph: what passed, what failed, and why.]

---

## Notes

[Anomalies, unexpected output, comparisons to prior runs.]
