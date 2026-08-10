# Pillar 5 — Citation Quality Results

**Run date:**  
**Run by:**  
**Repo under test:** sample-repo (`../sample-repo`)  
**Repo ID in DB:**  
**Environment:**
- DATABASE_URL: postgresql://postgres:...@db.[ref].supabase.co:5432/postgres
- GROQ_API_KEY: gsk_... (prefix only)
- GEMINI_API_KEY: AQ... (prefix only)
- Script: `platform/tests/test_ask_endpoint.py` (same run as Pillar 4)

> This file records the citation classification columns (Def / CS / Bad)
> from the same terminal output as Pillar 4. The answer text quality is
> recorded in `pillar4-answers.md`; this file records hallucination rate
> and citation type breakdown only.

---

## Raw Output

[paste full terminal output here — same as pillar4-answers.md, unedited]

---

## Key Numbers — 3-Way Citation Classification

| Question | Total citations | Definition (Def) | Call-site (CS) | Unsupported (Bad) | Hall% | Pass? |
|---|---|---|---|---|---|---|
| Q1 | | | | | | |
| Q2 | | | | | | |
| Q3 | | | | | | |
| Q4 | | | | | | |
| **OVERALL** | | | | | **0.0%** | |

---

## Citation Type Breakdown

**Definition citations (`definition`):**  
Cited range overlaps a real entity's declared lines AND entity name appears
in preceding prose, OR a non-CALLS relationship (IMPORTS / INHERITS /
IMPLEMENTS / CONTAINS / INSTANTIATES) backs the claim.

_All definition citations from the run — any anomalies noted here:_

**Call-site citations (`call_site`):**  
Preceding text describes an invocation AND a real CALLS edge exists in the
DB from caller to callee at that line.

_All call-site citations from the run — any anomalies noted here:_

**Unsupported citations (`unsupported`):**  
File/line not in context, OR no relationship of any type backs the claim.

_Paste any unsupported citations here with their reason strings:_

```
[none expected — paste here if any appear]
```

---

## Validator Behaviour Notes

_(Record any observations about parent-chain walking, fuzzy path matching,
or edge type classification that are visible in the output)_

- Parent-chain walking (IMPORTS on module entities):
- Fuzzy file path matching:
- CONTAINS-child classification:

---

## Verdict

**PASS / FAIL / PARTIAL**

[One paragraph: hallucination rate, whether all citations are correctly
classified, any anomalies in the validator behaviour.]

---

## Notes

[Any comparisons to the previously verified 67-citation / 0.0% run.
Reference `known-limitations.md §1` if relevant.]
