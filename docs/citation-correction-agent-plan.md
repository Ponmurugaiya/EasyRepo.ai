# Citation Correction Agent — Plan

## The Problem

The citation validator already runs after every answer and correctly identifies
three categories:

- **definition_citations** — valid, file/line matched a real entity
- **call_site_citations** — valid, CALLS edge verified in graph
- **unsupported_citations** — INVALID: either the file path doesn't exist in
  context, the line range doesn't cover any entity, or a claimed CALLS
  relationship has no matching graph edge

The current pipeline logs the unsupported citations and returns them to the
frontend as `unsupported_citations` in the `ValidationReport`. The frontend
shows the hallucination rate but the bad citations remain in the answer text.

**What needs to happen instead:**
When `unsupported_citations` is non-empty, a Correction Agent rewrites the
answer, replacing or removing each bad citation with the correct one — using
the `nearest_entity` hint the validator already computed and the real entity
list from the DB.

---

## What the Validator Already Gives Us

Each `CitationMismatch` (unsupported citation) contains:

```python
@dataclass
class CitationMismatch:
    raw: str           # e.g. "[auth/service.py:99-110]"  ← the bad tag in the text
    file_path: str     # what the LLM claimed
    start_line: int
    end_line: int
    reason: str        # e.g. "No entity in 'auth/service.py' covers lines 99-110."
    nearest_entity: str | None  # e.g. "'authenticate' [auth/service.py:45-89]"
```

And each `CitationMatch` (valid citation) contains:

```python
@dataclass
class CitationMatch:
    raw: str                    # e.g. "[auth/service.py:45-89]"
    file_path: str
    start_line: int
    end_line: int
    matched_entity_id: str      # e.g. "py.auth.service.authenticate"
    matched_entity_name: str    # e.g. "authenticate"
    citation_type: str          # "definition" or "call_site"
```

This means we already know:
1. Exactly which citation tags in the text are wrong (`raw`)
2. What the nearest real entity is (`nearest_entity`)
3. What the correct citation tag should be (derived from `nearest_entity`)

---

## Correction Strategy — Two Approaches

### Approach A: Deterministic string replacement (no LLM needed)
For each unsupported citation where `nearest_entity` is populated:
1. Parse `nearest_entity` — format is `"'entity_name' [file_path:start-end]"`
2. Extract the correct citation tag from it: `[file_path:start-end]`
3. Replace the bad `raw` tag in the answer text with the correct tag

**When this works:** Line-range mistakes and path prefix mismatches
(e.g. `[service.py:99]` when the real entity is at `[auth/service.py:45-89]`).

**When this fails:** `nearest_entity` is None (file path not in context at all).

### Approach B: LLM Correction Agent (for remaining bad citations)
For unsupported citations where `nearest_entity` is None, an LLM agent:
1. Receives the paragraph containing the bad citation
2. Receives a list of real available entities for that paragraph's topic
3. Is asked to either replace the citation with a correct one or remove it

**When to use:** Only when `nearest_entity` is None AND the hallucination rate
exceeds a threshold (default: `> 0`). Skip if `unsupported_citations` is empty.

---

## Plan: Hybrid Correction (Deterministic First, LLM Fallback)

```
validate_citations(answer) → ValidationReport
        │
        ▼
unsupported_citations empty?
        │ YES → return answer unchanged
        │ NO
        ▼
CitationCorrectionAgent.run(answer, report, context_entities)
        │
        ├── Step 1: Deterministic pass
        │       For each unsupported citation with nearest_entity:
        │         parse nearest_entity → correct_tag
        │         replace raw tag with correct_tag in answer text
        │
        ├── Step 2: LLM pass (only if any remain without nearest_entity)
        │       Build correction prompt with:
        │         - Paragraphs containing remaining bad citations
        │         - List of valid entities available for each paragraph
        │         - Instruction: fix or remove each bad citation
        │       Call llama-3.1-8b-instant (fast, cheap)
        │       Parse corrected segments back into answer
        │
        └── Step 3: Re-validate
                Run validate_citations() on the corrected answer
                Log improvement: before/after unsupported counts
                Return corrected answer + new ValidationReport
```

---

## Where It Sits in the Pipeline

```
                     ask.py
                        │
                run_pipeline()
                        │
              Answer Agent → answer text
                        │
              validate_citations() → ValidationReport
                        │
          unsupported_citations > 0?
                  YES   │   NO
                   ▼    │    ▼
  CitationCorrectionAgent   return as-is
      deterministic pass
      + LLM pass if needed
             │
      re-validate
             │
    corrected answer +
    new ValidationReport
             │
          ask.py builds AskResponse
             │
          Frontend receives:
            - answer (corrected prose)
            - citations (re-validated, lower hallucination_rate)
```

The correction happens **between** the first `validate_citations()` call and
building the `AskResponse`. The frontend gets the corrected answer — it never
sees the original bad citations.

---

## New File

**`src/generation/citation_correction_agent.py`**

```python
@dataclass
class CorrectionResult:
    corrected_answer: str
    original_unsupported: int    # count before correction
    remaining_unsupported: int   # count after correction
    corrections_made: int        # how many bad tags were fixed
    report: ValidationReport     # re-validated report on corrected answer
```

Public API:
```python
def run(
    answer: str,
    report: ValidationReport,
    context_entities: list[EntityModel],
    final_context,
    db_session,
) -> CorrectionResult:
    """Correct unsupported citations and return the patched answer."""
```

---

## Deterministic Correction — Exact Logic

The validator already computes `nearest_entity` as:
```python
f"'{closest.name}' [{closest.start_line}-{closest.end_line}]"
```
using the real `file_path` from the matched entity.

Wait — that's not quite right. Looking at `_describe_nearest()`:
```python
def _describe_nearest(entities, target_line):
    closest = min(entities, key=lambda e: min(abs(e.start_line - target_line), ...))
    return f"'{closest.name}' [{closest.start_line}-{closest.end_line}]"
```

The `entities` list is `file_entity_map.get(file_path)` — the entities for the
claimed file path. So `nearest_entity` gives line range but **uses the claimed
file path** (which may be wrong if the file path was wrong). We need to look
at the actual entity to get the correct file path.

**Fix in `CitationMismatch`:** Add `nearest_entity_id: str | None` alongside
`nearest_entity`. The correction agent can then look up the entity by ID to
get its canonical file path and exact line range.

Since changing `CitationMismatch` would be a small schema change, an alternative
is to make the correction agent re-query the DB for the entity by name when
`nearest_entity` contains one.

---

## Validator Change Required: Add `nearest_entity_id`

```python
@dataclass
class CitationMismatch:
    raw: str
    file_path: str
    start_line: int
    end_line: int
    reason: str
    nearest_entity: Optional[str] = None
    nearest_entity_id: Optional[str] = None   # NEW — canonical entity ID for correction
```

In `_describe_nearest()`:
```python
def _describe_nearest(entities, target_line):
    closest = min(entities, key=...)
    return (
        f"'{closest.name}' [{closest.file_path}:{closest.start_line}-{closest.end_line}]",
        closest.id,
    )
```

And in `validate_citations()` where `_describe_nearest` is called:
```python
nearest, nearest_id = _describe_nearest(candidates, start_line)
unsupported.append(CitationMismatch(..., nearest_entity=nearest, nearest_entity_id=nearest_id))
```

---

## LLM Correction Prompt

Used only when `nearest_entity_id` is None (file path wasn't in context at all).

```
System:
You are a citation correction assistant for a code intelligence system.
Citations use the format [file_path:start_line-end_line].

You will receive:
1. A paragraph from a code explanation with one or more INVALID citations marked with ⚠️
2. A list of VALID entities you may cite instead

Your task: rewrite ONLY the marked citations. Either:
- Replace with the correct [file_path:start-end] from the valid entities list
- Remove the citation entirely if no valid entity matches the claim

Rules:
- Do NOT change any other text in the paragraph
- Do NOT invent new citations
- Only use file paths and line numbers from the valid entities list
- If unsure, remove the citation rather than guessing

User:
Paragraph:
"The authentication flow starts in UserService ⚠️[services/auth.py:99-110]
which calls validate_token() to verify the JWT."

Invalid citation: [services/auth.py:99-110]
Reason: No entity covers lines 99-110.

Valid entities you may use:
- authenticate [src/services/auth.py:45-89]
- validate_token [src/services/auth.py:92-115]
- UserService [src/services/auth.py:12-44]

Rewrite the paragraph with corrected citations only:
```

Expected output:
```
"The authentication flow starts in UserService [src/services/auth.py:12-44]
which calls validate_token() [src/services/auth.py:92-115] to verify the JWT."
```

---

## Schema Changes to `CitationMismatch`

Minimal — one new optional field `nearest_entity_id`. The field is also added to
`CitationMismatchSchema` (Pydantic, in `schemas.py`) and exposed in the API
response so the frontend has it if needed.

The frontend currently ignores `unsupported_citations` content beyond showing
the hallucination rate, so no frontend changes are needed.

---

## Changes Required

### New file
| File | Purpose |
|---|---|
| `src/generation/citation_correction_agent.py` | Deterministic + LLM correction |

### Modified files
| File | Change |
|---|---|
| `src/generation/citation_validator.py` | Add `nearest_entity_id` to `CitationMismatch`; update `_describe_nearest()` to return entity ID |
| `src/api/schemas.py` | Add `nearest_entity_id: str | None` to `CitationMismatchSchema` |
| `src/api/routers/ask.py` | Call `CitationCorrectionAgent.run()` after `validate_citations()` when unsupported count > 0 |
| `src/pipeline/pipeline_logger.py` | Add `step_citation_correction()` trace method |

---

## ask.py Integration (After Correction Agent is Built)

```python
# ── Citation validation ───────────────────────────────────────────────────
context_entities = collect_context_entities(final_context)
report = validate_citations(
    answer=answer,
    context_entities=context_entities,
    final_context=final_context,
    db_session=db,
)

# ── Citation correction (if any unsupported citations found) ─────────────
if report.unsupported_citations:
    from src.generation.citation_correction_agent import run as correct_citations
    correction = correct_citations(
        answer=answer,
        report=report,
        context_entities=context_entities,
        final_context=final_context,
        db_session=db,
    )
    answer = correction.corrected_answer
    report = correction.report
    if pipeline_result.trace:
        pipeline_result.trace.step_citation_correction(
            original_unsupported=correction.original_unsupported,
            remaining_unsupported=correction.remaining_unsupported,
            corrections_made=correction.corrections_made,
        )
```

---

## New Pipeline Log Line

```
PIPELINE [6-CITE]        total=8  definition=6  call_site=1  unsupported=1  hallucination_rate=12.5%
PIPELINE [6-CORRECT]     original_unsupported=1  corrections_made=1  remaining=0  method=deterministic
PIPELINE [6-CITE-FINAL]  total=8  definition=7  call_site=1  unsupported=0  hallucination_rate=0.0%
PIPELINE DONE            status=answered  provider=gemini  citations=8  total_ms=9100
```

When the LLM correction path is triggered:
```
PIPELINE [6-CORRECT]     original_unsupported=2  corrections_made=1  remaining=1  method=llm  llm_ms=380
```

---

## Threshold & Safety Rules

| Condition | Behaviour |
|---|---|
| `unsupported_citations` is empty | Skip correction entirely — no overhead |
| All bad citations have `nearest_entity_id` | Deterministic pass only — no LLM call |
| Some bad citations have no `nearest_entity_id` | Deterministic pass + LLM pass for remainder |
| LLM correction fails | Log warning, return original answer + original report |
| Correction makes hallucination_rate worse | Log warning, return original answer (safety guard) |
| `CITATION_CORRECTION_ENABLED=false` in env | Skip correction entirely (opt-out flag) |

---

## Implementation Order

| Step | Task | Risk |
|---|---|---|
| 1 | Add `nearest_entity_id` to `CitationMismatch` + update `_describe_nearest()` | Low — additive field |
| 2 | Add `nearest_entity_id` to `CitationMismatchSchema` in schemas.py | None — additive |
| 3 | Add `step_citation_correction()` to `pipeline_logger.py` | None |
| 4 | Build `citation_correction_agent.py` — deterministic pass | Low |
| 5 | Build `citation_correction_agent.py` — LLM pass | Medium |
| 6 | Wire into `ask.py` after `validate_citations()` | Low — guarded by `if unsupported_citations` |
| 7 | Test with a query known to produce wrong citations | — |
