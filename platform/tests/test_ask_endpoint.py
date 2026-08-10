"""
Live end-to-end test: POST /repositories/sample-repo/ask

Tests the full pipeline through the HTTP API:
  vector search → graph expansion → LiteLLM (Groq primary) → citation validation

Closes known-limitations.md item #1.
"""

import http.client
import json
import sys
import textwrap
import time

API_HOST = "127.0.0.1"
API_PORT = 8000
REPO_ID  = "sample-repo"

# Same four questions used in Step 7 CLI verification
QUESTIONS = [
    "How does authentication work?",
    "What does the UserService do and what does it call?",
    "How is AdminModel related to UserModel?",
    "What is in the isolated utils file?",
]

def ask(question: str) -> dict:
    payload = json.dumps({"query": question, "top_k": 20}).encode()
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=120)
    conn.request(
        "POST",
        f"/repositories/{REPO_ID}/ask",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}: {body[:400]}")
    return json.loads(body)

# ── run all four questions ────────────────────────────────────────────────────
print("=" * 70)
print("LIVE END-TO-END API TEST  — POST /repositories/sample-repo/ask")
print("=" * 70)

results = []
total_citations = 0
total_hallucinations = 0

for i, question in enumerate(QUESTIONS, 1):
    print(f"\n[{i}/{len(QUESTIONS)}] {question}")
    print("-" * 60)
    t0 = time.time()
    try:
        data = ask(question)
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)
    elapsed = time.time() - t0

    answer   = data["answer"]
    cites    = data["citations"]
    provider = data.get("provider", "unknown")
    ctx_ids  = data.get("context_entities", [])

    total_citations    += cites["total_citations"]
    total_hallucinations += len(cites["unsupported_citations"])

    results.append({
        "question": question,
        "provider": provider,
        "elapsed":  elapsed,
        "total_citations": cites["total_citations"],
        "definition_citations": len(cites["definition_citations"]),
        "call_site_citations": len(cites["call_site_citations"]),
        "unsupported_citations": len(cites["unsupported_citations"]),
        "hallucination_rate": cites["hallucination_rate"],
        "context_entities": len(ctx_ids),
    })

    print(f"  Provider  : {provider.upper()}")
    print(f"  Elapsed   : {elapsed:.1f}s")
    print(f"  Context   : {len(ctx_ids)} entities")
    print(f"  Citations : {cites['total_citations']} total  "
          f"({len(cites['definition_citations'])} def  "
          f"{len(cites['call_site_citations'])} call-site  "
          f"{len(cites['unsupported_citations'])} unsupported)")
    print(f"  Hallucination rate: {cites['hallucination_rate']:.1%}")

    # Show answer (wrapped, first 600 chars)
    print("\n  ANSWER:")
    for line in textwrap.wrap(answer[:600], width=66):
        print(f"    {line}")
    if len(answer) > 600:
        print(f"    ... [{len(answer)-600} more chars]")

    # Show any unsupported citations
    if cites["unsupported_citations"]:
        print("\n  UNSUPPORTED CITATIONS:")
        for c in cites["unsupported_citations"]:
            print(f"    {c['raw']}  reason={c['reason']}")

# ── summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"{'Q':<4} {'Provider':<10} {'Time':>6}  {'Total':>5}  {'Def':>4}  {'CS':>4}  {'Bad':>4}  Hall%")
print("-" * 70)
for i, r in enumerate(results, 1):
    print(f"{i:<4} {r['provider']:<10} {r['elapsed']:>5.1f}s  "
          f"{r['total_citations']:>5}  {r['definition_citations']:>4}  "
          f"{r['call_site_citations']:>4}  {r['unsupported_citations']:>4}  "
          f"{r['hallucination_rate']:.1%}")

overall_rate = total_hallucinations / total_citations if total_citations else 0.0
print("-" * 70)
print(f"{'OVERALL':<15} total={total_citations}  unsupported={total_hallucinations}  "
      f"hallucination_rate={overall_rate:.1%}")

providers_used = set(r["provider"] for r in results)
print(f"\nProviders used: {', '.join(sorted(providers_used))}")

# ── assertions ────────────────────────────────────────────────────────────────
print("\n" + "=" * 70)
print("ASSERTIONS")
print("=" * 70)

failures = []

# 1. All responses came from a real LLM (not mock)
for i, r in enumerate(results, 1):
    if r["provider"] not in {"groq", "gemini"}:
        failures.append(f"Q{i}: unexpected provider {r['provider']!r}")

# 2. Every answer has at least one citation
for i, r in enumerate(results, 1):
    if r["total_citations"] == 0:
        failures.append(f"Q{i}: zero citations — answer may be uncited prose")

# 3. Overall hallucination rate is reasonable (≤ 20% allows for INSTANTIATES gap)
if total_citations > 0 and overall_rate > 0.20:
    failures.append(
        f"Hallucination rate {overall_rate:.1%} exceeds 20% threshold"
    )

# 4. Definition + call-site citations exist across all questions
total_verified = sum(r["definition_citations"] + r["call_site_citations"] for r in results)
if total_verified == 0:
    failures.append("Zero verified (definition + call-site) citations across all questions")

if failures:
    print("FAILED:")
    for f in failures:
        print(f"  ✗ {f}")
    sys.exit(1)
else:
    print("ALL ASSERTIONS PASSED ✓")
    print()
    print("Known-limitations.md item #1 is now CLOSED.")
    print(f"  - Provider used: {', '.join(sorted(providers_used))}")
    print(f"  - Real citations validated: {total_verified} verified")
    print(f"  - Overall hallucination rate: {overall_rate:.1%}")
    print(f"  - All four questions answered through the HTTP /ask endpoint")
