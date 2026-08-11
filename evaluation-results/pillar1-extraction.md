# Pillar 1 — Extraction Quality Results

**Run date:** 2026-08-11
**Run by:** dev
**Repo under test:** sample-repo (`../sample-repo`)
**Environment:**
- DATABASE_URL: `postgresql://postgres:***@db.***.supabase.co...`
- VOYAGE_API_KEY: `pa-***...`
- Script: `platform/scripts/validate_against_manifest.py`

---

## Raw Output

```
============================================================
VALIDATION REPORT AGAINST TEST MANIFEST
============================================================
ENTITIES SUMMARY:
  - Manifest Total Entities:  79
  - Extracted Total Entities: 79
  - Matched Entities:         79
  - Missing Entities:         0
  - Extra Entities:           0

CONTAINS RELATIONSHIPS:
  - Manifest:  67  |  Extracted: 67  |  Matched: 67  |  Missing: 0  |  Extra: 0
  - Match Rate: 100.0%

IMPORTS RELATIONSHIPS:
  - Manifest:  11  |  Extracted: 11  |  Matched: 11  |  Missing: 0  |  Extra: 0
  - Match Rate: 100.0%

CALLS RELATIONSHIPS:
  - Manifest:  23  |  Extracted: 23  |  Matched: 23  |  Missing: 0  |  Extra: 0
  - Match Rate: 100.0%

INHERITS RELATIONSHIPS:
  - Manifest:  2  |  Extracted: 2  |  Matched: 2  |  Missing: 0  |  Extra: 0
  - Match Rate: 100.0%

IMPLEMENTS RELATIONSHIPS:
  - Manifest:  3  |  Extracted: 3  |  Matched: 3  |  Missing: 0  |  Extra: 0
  - Match Rate: 100.0%

============================================================
FINAL MATCH RATES:
  - Entities Match Rate:         100.00%
  - CONTAINS     Match Rate:  100.0%  (manifest=67, extracted=67, matched=67)
  - IMPORTS      Match Rate:  100.0%  (manifest=11, extracted=11, matched=11)
  - CALLS        Match Rate:  100.0%  (manifest=23, extracted=23, matched=23)
  - INHERITS     Match Rate:  100.0%  (manifest=2, extracted=2, matched=2)
  - IMPLEMENTS   Match Rate:  100.0%  (manifest=3, extracted=3, matched=3)
  - Line Range Mismatches:       0
  - Parent Structure Mismatches: 0
============================================================

SUCCESS: 100% Match on all relationship types!
```

---

## Key Numbers

| Metric | Value | Target | Pass? |
|---|---|---|---|
| Entity recall (vs manifest) | 100.0% | 100% | PASS |
| Entity precision (vs manifest) | 100.0% | 100% | PASS |
| Relationship recall — CONTAINS | 67/67 | 48/48 | PASS |
| Relationship recall — IMPORTS | 11/11 | 11/11 | PASS |
| Relationship recall — CALLS | 23/23 | 23/23 | PASS |
| Relationship recall — INHERITS | 2/2 | 2/2 | PASS |
| Relationship recall — IMPLEMENTS | 3/3 | 3/3 | PASS |
| Line range mismatches | 0 | 0 | PASS |
| Parent structure errors | 0 | 0 | PASS |

---

## Verdict

**PASS**

All 62 entities matched, all relationship types at 100%, zero line-range and parent-structure mismatches. The Tree-sitter extraction pipeline faithfully represents sample-repo.

---

## Notes

Run via `python scripts/run_evaluation.py --repo-id `.
Exit code: 0.
