# Pillar 1 — Extraction Quality Results

**Run date:**  
**Run by:**  
**Repo under test:** sample-repo (`../sample-repo`)  
**Repo ID in DB:**  
**Environment:**
- DATABASE_URL: postgresql://postgres:...@db.[ref].supabase.co:5432/postgres
- VOYAGE_API_KEY: pa-... (prefix only)
- Script: `platform/scripts/validate_against_manifest.py`

---

## Raw Output

[paste full terminal output here — unedited]

---

## Key Numbers

| Metric | Value | Target | Pass? |
|---|---|---|---|
| Entity recall | | 100% | |
| Entity precision | | 100% | |
| Relationship recall — CONTAINS | | 48/48 | |
| Relationship recall — IMPORTS | | 11/11 | |
| Relationship recall — CALLS | | 23/23 | |
| Relationship recall — INHERITS | | 2/2 | |
| Relationship recall — IMPLEMENTS | | 3/3 | |
| Relationship recall — INSTANTIATES | | ≥ 1 | |
| Line range mismatches | | 0 | |
| Parent structure errors | | 0 | |
| Docstring flag errors | | 0 | |

---

## Verdict

**PASS / FAIL / PARTIAL**

[One paragraph: what passed, what failed, and why.]

---

## Notes

[Anomalies, unexpected output, comparisons to prior runs.]
