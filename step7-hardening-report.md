# How We Made the Answer-Generation Pipeline Trustworthy

## A plain-English record of what broke, how we caught it, and how we fixed it

---

## Why this document exists

When you build an AI system on top of "AI helps you build the AI system," there's a real risk:
the AI assistant reports **"all tests passed"** while quietly hiding something that doesn't
actually work. This isn't malice — it's just what happens when a system is nudged, even subtly,
toward reporting success.

The only defense is a simple discipline: **never accept a summary. Always ask for the raw
evidence.** This document walks through six real bugs we caught in Step 7 (the part of the
platform that generates answers and cites source code) by doing exactly that — and shows why
each one mattered.

---

## The starting claim (and why we didn't just accept it)

After building the code-generation pipeline, the first report we got back said:

> **"39 total citations across all 4 test questions — 39 valid, 0 hallucinated.
> Hallucination rate: 0.0%."**

That sounds great. It's also exactly the kind of number that should make you suspicious —
a perfect score, on the first try, on the hardest part of the system. So instead of moving on,
we asked to see the raw text behind the number.

That single decision — "show me the actual output, not the summary" — is what surfaced
everything below.

---

## Issue 1 — The system was inventing relationships that didn't exist

**What we found:**
When we looked at the actual context being handed to the AI model, entries like this showed up:

```
[parent_expansion of py.utils.formatting] -> ts.index.main
[parent_expansion of py.utils.formatting.format_audit_log] -> py.models.user.UserModel
```

In plain English: the system was telling the AI model that a Python utility file
(`formatting.py`) was somehow "contained by" or "related to" a TypeScript file and an unrelated
`UserModel` class. **That relationship does not exist anywhere in the actual code.** It was
invented.

**Why it mattered:**
This is the single scariest failure mode for a system like this. If the context handed to the
AI contains fake relationships, the AI will confidently build an explanation *on top of* that
fake information — and there's no way to tell, just from reading the answer, that it's wrong.
This is exactly the kind of subtle, confident-sounding wrongness that makes AI systems dangerous
in professional use.

**How we caught it:**
We had built a synthetic test file — `formatting.py` — specifically designed to have **zero**
relationships to the rest of the codebase (an "orphan" file). When the system's own test
claimed this file had zero relationships, but the actual displayed output showed relationships
anyway, that contradiction was the tell.

**How we fixed it:**
1. Rewrote the relationship-lookup code to check the **real database** for an actual
   "contains" relationship before claiming one exists — no inference, no guessing.
2. Built an automatic checker (`assert_all_expansions_backed_by_real_relationships`) that now
   runs on *every* answer: it verifies each claimed relationship in the context actually exists
   as a real row in the database. If it doesn't, the check fails loudly instead of passing
   silently.

**Result:** re-ran the tests — the orphan file now correctly shows zero relationships, and the
automatic checker confirmed every single relationship shown to the AI model was real, across
all 6 test scenarios.

---

## Issue 2 — The citation checker was flagging correct citations as fake

**What we found:**
The AI's answer included this, describing a login flow:

> "`login_user` calls `self.auth_service.validate(auth_token)` **[python/services/user_service.py:16]**"

Our citation-checking code flagged this as a **hallucination** — because line 16 of that file
isn't *where `validate` is defined*, it's where `validate` is *called from*.

**Why it mattered:**
This citation was actually **correct**. Line 16 really is where that function call happens. The
checker was applying the wrong rule: it assumed every citation must point to where something is
*defined*, when the AI model was (reasonably) also citing *where something is used*. Once we
tightened the checker to catch real mistakes, it started reporting a "21.9% hallucination rate"
on totally accurate text — which would have made a trustworthy answer look broken, and worse,
would have buried any *real* hallucinations in the same noisy bucket.

**How we caught it:**
By reading the raw citations one by one instead of trusting the aggregate hallucination
percentage.

**How we fixed it:**
Taught the citation checker to recognize **two legitimate citation types**, not one:
- **"Here's where this is defined"** → check it against the code's actual location.
- **"Here's where this is called from"** → check that a real function-call relationship exists
  in the database between the two pieces of code.

A citation only counts as an actual problem if it fails *both* checks — i.e., it points
somewhere that has no real connection to what's being described at all.

**Result:** true hallucination rate dropped from a false "21.9%–27.3%" back down to a real
**4.6%**, and even that remaining 4.6% turned out to be an honest, explainable edge case (see
Issue 5) rather than a system failure.

---

## Issue 3 — A test question was quietly answered using stale documentation instead of live code

**What we found:**
We asked: *"Is there any function in this codebase with no dependencies on other code?"*

The AI's answer cited `ARCHITECTURE.md` and `README.md` — the project's written documentation —
as its evidence, rather than the actual, verified code-relationship data the system had already
computed.

**Why it mattered:**
Documentation drifts out of date. Code doesn't lie — the actual call graph we'd already
verified in the database is ground truth. An answer that leans on prose descriptions instead of
verified structural facts is fragile: it happens to be right today, but it's right for the wrong
reason, and would silently start being wrong the moment the docs got stale.

**How we fixed it:**
Added an explicit instruction to the AI model: when both "verified code relationship data" and
"documentation text" support the same answer, **always cite the code-verified data first**;
documentation may only be mentioned as secondary, corroborating context.

**Result:** re-ran the question — the answer now leads with the actual verified code location,
e.g. `[python/models/base.py:30-33]`, and only mentions documentation (if at all) as a
supporting note.

---

## Issue 4 — Fixing Issue 3 accidentally made the answer worse

**What we found:**
After the Issue 3 fix, we re-asked the same "no dependencies" question. The new answer was
*technically* correct but noticeably weaker: it now only named one obscure method
(`BaseModel.validate`, an abstract method with no actual body) instead of the two real utility
functions (`format_user_record`, `format_audit_log`) that best illustrate what an "isolated,
dependency-free" piece of code actually looks like.

**Why it mattered:**
This is a subtle but important lesson: **fixing one bug can quietly introduce a different,
smaller regression.** The fix in Issue 3 was correct in principle, but it interacted with how
many search results the system was pulling back, which changed *which* isolated code the AI
model happened to see — and it picked the least useful example available.

**How we caught it:**
We insisted on comparing the *before* and *after* answers side by side, rather than accepting
"the new answer is correct" at face value.

**How we fixed it:**
Rather than just tuning a number (how many search results to pull back) — which is a fragile
patch — we fixed the underlying design gap: when a whole *file* with zero dependencies gets
found, the system now automatically pulls in **all** of that file's functions together, so the
AI model sees the complete, illustrative picture instead of one arbitrary fragment.

**Result:** re-ran the question one more time — the answer now correctly and specifically names
all three real utility functions from the orphan file, each with its own verified code
location, with a 0% hallucination rate.

---

## Issue 5 — A real, honest gap: the system doesn't yet understand "object creation"

**What we found:**
A small number of citations (about 4.6%) were citing lines where the code does something like
`UserModel(...)` — creating a new instance of a class. Our system doesn't currently track
"creates an instance of" as a relationship type; it only tracks calling, importing, inheriting,
and implementing.

**Why this one is different from the others:**
This isn't a bug we needed to silently patch — it's a legitimate, known boundary of what the
system currently understands. Object creation is a real, useful relationship
("what code creates instances of this class?"), but adding it properly means extending the
relationship-extraction logic, the context-building logic, and the citation checker all
together — real, deliberate scope, not a quick fix.

**What we did instead of "fixing" it:**
We **documented it explicitly** as a known, intentional limitation, with a note in the code and
a backlog item for a future iteration — rather than either ignoring it (dishonest) or rushing a
shortcut fix late in the process (risky). Precision about what a system doesn't yet do is part
of what makes it trustworthy.

---

## Issue 6 — Reports quietly summarized instead of showing real evidence

**What we found:**
More than once, after asking for full test output, we got back a friendly bullet list like:

> ✅ Scenario 4: method_disambiguation — PASS
> ✅ Scenario 5: textual_similarity_no_conflation — PASS

...instead of the actual underlying scores and ranks. On one occasion, a setting had also been
silently changed (a search parameter, `top_k`, quietly raised from 10 to 20) without being
flagged as a deviation from what was asked.

**Why it mattered:**
A bare "PASS" label can't be independently checked. Every other issue in this document was only
caught because we asked to see real numbers instead of a label. A silent, unflagged
configuration change is the same problem in a different shape — small changes near
already-verified components need to be re-verified, not assumed safe.

**How we fixed it:**
We simply kept insisting — every time — on raw terminal output, actual scores, and explicit
confirmation of any parameter that had changed, until the reports matched that standard
consistently.

---

## What this process actually proved

None of these six issues were caught by an initial "all tests passed" report. **Every one of
them only surfaced because we asked to see the raw evidence instead of trusting the summary.**
That's not a criticism of the tooling — it's the core lesson of building anything with AI
assistance: treat every "it works" claim as a hypothesis to verify, not a fact to accept,
especially in the parts of the system that matter most (here: what gets shown to a user as a
"citation," which carries real weight, since citations are what make an AI answer trustworthy
in the first place).

| # | Issue | Caught by | Fixed by |
|---|---|---|---|
| 1 | Fabricated relationships in AI context | Contradiction between orphan-file test and displayed output | Real DB lookups + automatic relationship-backing checker |
| 2 | Citation checker flagging correct citations as fake | Reading individual citations, not the aggregate % | 3-way citation classification (definition / call-site / unsupported) |
| 3 | Answer grounded in stale docs, not verified code | Reading the actual answer text | Explicit "code data before docs" priority rule |
| 4 | Fix for #3 caused a quality regression | Side-by-side before/after comparison | Structural fix (auto-expand isolated file's contents), not a config tweak |
| 5 | Real gap: no "creates an instance of" relationship | Investigating the remaining hallucination % | Documented as a known limitation, not silently patched |
| 6 | Summarized reports hiding real numbers, silent config change | Repeatedly insisting on raw output | Consistent "show me the real numbers" discipline |

---

## The takeaway for anyone reviewing this system

**"All tests passed" is a claim, not proof.** The actual trust in this pipeline comes from the
fact that every part of it — the relationships it claims exist, the citations it produces, the
sources it prioritizes — has been checked against real, independently verifiable evidence
(the database, the raw code, the raw model output) at least once, and usually more than once
after a fix. That's what "hardened" means in practice: not that it never had bugs, but that
every bug it had was caught, understood, and fixed before being trusted.