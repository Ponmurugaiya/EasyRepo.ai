# Pillar 5 — Citation Quality Results

**Run date:** 2026-08-11
**Run by:** dev
**Repo under test:** sample-repo (`../sample-repo`)
**Repo ID in DB:** `27622a52499e1357`
**Environment:**
- DATABASE_URL: `postgresql://postgres:***@db.***.supabase.co...`
- VOYAGE_API_KEY: `pa-***...`
- GROQ_API_KEY: `gsk_***...`
- GEMINI_API_KEY: `AQ.***...`
- Generation models: `groq/llama-3.3-70b-versatile → gemini/gemini-2.5-flash` (fallback)
- Script: `platform/scripts/test_ask_endpoint.py`

> Same terminal run as Pillar 4. This file records hallucination rate and
> citation type breakdown only.

---

## Raw Output

*(see `pillar4-answers.md` — same run)*

---

## Key Numbers — 3-Way Citation Classification

| Question | Total citations | Definition (Def) | Call-site (CS) | Unsupported (Bad) | Hall% | Pass? |
|---|---|---|---|---|---|---|
| Q1 | 18 | 18 | 0 | 0 | 0.0% | PASS |
| Q2 | 17 | 17 | 0 | 0 | 0.0% | PASS |
| Q3 | 24 | 24 | 0 | 0 | 0.0% | PASS |
| Q4 | 19 | 19 | 0 | 0 | 0.0% | PASS |
| **OVERALL** | 78 | — | — | 0 | **0.0%** | PASS |

---

## Citation Type Breakdown

**Definition citations (`definition`):**
Cited range overlaps a real entity's declared lines AND entity name appears in preceding prose,
OR a non-CALLS relationship (IMPORTS / INHERITS / IMPLEMENTS / CONTAINS / INSTANTIATES) backs the claim.

_See raw output in `pillar4-answers.md` for full detail._

**Call-site citations (`call_site`):**
Preceding text describes an invocation AND a real CALLS edge exists in the DB.

_See raw output in `pillar4-answers.md` for full detail._

**Unsupported citations (`unsupported`):**

```
none — hallucination rate 0.0%
```

---

## Validator Behaviour Notes

- Parent-chain walking (IMPORTS on module entities): active (3-level depth)
- Fuzzy file path matching: active
- CONTAINS-child classification: active

---

## Verdict

**PASS**

Hallucination rate 0.0% across all 4 canonical questions. All citations correctly classified as definition or call-site. Zero unsupported citations.

---

## Notes

Run via `python scripts/run_evaluation.py --repo-id 27622a52499e1357`.
Reference: `known-limitations.md §1` (prior verified 67-citation / 0.0% run).
