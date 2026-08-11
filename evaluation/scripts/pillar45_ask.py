"""Pillar 4 + 5 — Answer Quality & Citation Quality
Tests the full pipeline through the HTTP API across 4 canonical evaluation questions.
  vector search → graph expansion → LiteLLM (Groq primary / Gemini fallback) → citation validation
Canonical location: evaluation/scripts/pillar45_ask.py

Usage (from EasyRepo/):
    python evaluation/scripts/pillar45_ask.py --repo-id <uuid>
"""

import argparse
import http.client
import json
import sys
import textwrap
import time

API_HOST = "127.0.0.1"
API_PORT = 8000

# ── Canonical evaluation questions (evaluation-guide.md §Pillar 4) ────────────
QUESTIONS = [
    {
        "id": "Q1",
        "text": "Walk me through what happens when a user logs in, from entry point to completion",
        "key_entities": ["login_user", "AuthService.validate", "UserModel", "auth_service.py"],
    },
    {
        "id": "Q2",
        "text": "What does AdminUser inherit and how does permission checking work?",
        "key_entities": ["AdminUser", "UserModel", "BaseModel", "check_permission"],
    },
    {
        "id": "Q3",
        "text": "Is there any function in this codebase that has no dependencies on other code?",
        "key_entities": ["format_audit_log", "format_user_record", "truncate_text"],
    },
    {
        "id": "Q4",
        "text": "What does the validate method do?",
        "key_entities": ["AuthService.validate", "UserModel.validate"],
    },
]


def ask(repo_id: str, question: str) -> dict:
    payload = json.dumps({"query": question, "top_k": 20}).encode()
    conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=120)
    conn.request(
        "POST",
        f"/repositories/{repo_id}/ask",
        body=payload,
        headers={"Content-Type": "application/json"},
    )
    resp = conn.getresponse()
    body = resp.read().decode()
    conn.close()
    if resp.status != 200:
        raise RuntimeError(f"HTTP {resp.status}: {body[:400]}")
    return json.loads(body)


def run(repo_id: str) -> bool:
    print("=" * 70)
    print("LIVE END-TO-END API TEST  — POST /repositories/<repo_id>/ask")
    print(f"repo_id: {repo_id}")
    print("=" * 70)

    results = []
    total_citations = 0
    total_hallucinations = 0

    for q_def in QUESTIONS:
        q_id = q_def["id"]
        question = q_def["text"]
        print(f"\n[{q_id}] {question}")
        print("-" * 60)
        t0 = time.time()
        try:
            data = ask(repo_id, question)
        except Exception as e:
            print(f"  ERROR: {e}")
            return False
        elapsed = time.time() - t0

        answer   = data["answer"]
        cites    = data["citations"]
        provider = data.get("provider", "unknown")
        ctx_ids  = data.get("context_entities", [])

        total_citations    += cites["total_citations"]
        total_hallucinations += len(cites["unsupported_citations"])

        results.append({
            "q_id": q_id,
            "question": question,
            "answer": answer,
            "key_entities": q_def["key_entities"],
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
            print(f"    ... [{len(answer) - 600} more chars]")

        # Show any unsupported citations
        if cites["unsupported_citations"]:
            print("\n  UNSUPPORTED CITATIONS:")
            for c in cites["unsupported_citations"]:
                print(f"    {c['raw']}  reason={c['reason']}")

        # Completeness check — key entity names must appear in answer text
        print("\n  COMPLETENESS CHECK (key entities in answer):")
        for entity in q_def["key_entities"]:
            found = entity.lower() in answer.lower()
            mark = "✓" if found else "✗"
            print(f"    [{mark}] {entity}")

    # ── Summary table ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"{'Q':<4} {'Provider':<10} {'Time':>6}  {'Total':>5}  {'Def':>4}  {'CS':>4}  {'Bad':>4}  Hall%")
    print("-" * 70)
    for r in results:
        print(f"{r['q_id']:<4} {r['provider']:<10} {r['elapsed']:>5.1f}s  "
              f"{r['total_citations']:>5}  {r['definition_citations']:>4}  "
              f"{r['call_site_citations']:>4}  {r['unsupported_citations']:>4}  "
              f"{r['hallucination_rate']:.1%}")

    overall_rate = total_hallucinations / total_citations if total_citations else 0.0
    print("-" * 70)
    print(f"{'OVERALL':<15} total={total_citations}  unsupported={total_hallucinations}  "
          f"hallucination_rate={overall_rate:.1%}")

    providers_used = set(r["provider"] for r in results)
    print(f"\nProviders used: {', '.join(sorted(providers_used))}")

    # ── Assertions ────────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("ASSERTIONS")
    print("=" * 70)

    failures = []

    # 1. All responses came from a real LLM (not mock / unknown)
    for r in results:
        if r["provider"] not in {"groq", "gemini"}:
            failures.append(f"{r['q_id']}: unexpected provider {r['provider']!r}")

    # 2. Every answer has at least one citation
    for r in results:
        if r["total_citations"] == 0:
            failures.append(f"{r['q_id']}: zero citations — answer may be uncited prose")

    # 3. Hallucination rate must be exactly 0.0% (evaluation-guide.md §Pillar 4 pass criteria)
    for r in results:
        if r["hallucination_rate"] > 0.0:
            failures.append(
                f"{r['q_id']}: hallucination_rate={r['hallucination_rate']:.1%} — must be 0.0%"
            )

    if total_citations > 0 and overall_rate > 0.0:
        failures.append(
            f"Overall hallucination rate {overall_rate:.1%} > 0.0% target"
        )

    # 4. Definition + call-site citations exist across all questions
    total_verified = sum(r["definition_citations"] + r["call_site_citations"] for r in results)
    if total_verified == 0:
        failures.append("Zero verified (definition + call-site) citations across all questions")

    # 5. Completeness check — all key entity names must appear in the answer
    for r in results:
        for entity in r["key_entities"]:
            if entity.lower() not in r["answer"].lower():
                failures.append(
                    f"{r['q_id']}: key entity '{entity}' not mentioned in answer"
                )

    if failures:
        print("FAILED:")
        for f in failures:
            print(f"  ✗ {f}")
        return False
    else:
        print("ALL ASSERTIONS PASSED ✓")
        print()
        print(f"  - Provider used: {', '.join(sorted(providers_used))}")
        print(f"  - Real citations validated: {total_verified} verified")
        print(f"  - Overall hallucination rate: {overall_rate:.1%}")
        print(f"  - All four canonical questions answered through HTTP /ask endpoint")
        return True


def main():
    parser = argparse.ArgumentParser(
        description="End-to-end evaluation test: Pillars 4 + 5 (answer quality + citation quality)"
    )
    parser.add_argument(
        "--repo-id",
        type=str,
        required=True,
        help="Repository UUID as returned by POST /repositories (from the ingest step).",
    )
    args = parser.parse_args()

    success = run(args.repo_id)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
