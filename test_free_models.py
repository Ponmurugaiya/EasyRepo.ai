"""test_free_models.py — Test every free LLM model against its live API.

Usage:
    python test_free_models.py              # test all configured providers
    python test_free_models.py --provider groq
    python test_free_models.py --provider gemini
    python test_free_models.py --provider cerebras
    python test_free_models.py --provider openrouter
    python test_free_models.py --provider cohere
    python test_free_models.py --provider cloudflare
    python test_free_models.py --fast      # 1 model per provider only

Each model gets a minimal ping prompt ("Reply with: OK") to verify the API
key, model ID, and LiteLLM routing are all wired up correctly.
Results are printed as a table and written to  test_free_models_report.md.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Load .env ─────────────────────────────────────────────────────────────────
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

# ── Add platform/ to path ────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent / "platform"))

# test_free_models.py makes REAL API calls — quota consumed here IS real.
# We use the production Redis namespace (llm:) so quota is tracked correctly.
# Use --fast to test only 1 model per provider (6 calls total) instead of all 21.
# Do NOT set LLM_QUOTA_KEY_PREFIX here.

from src.generation.llm_client import (
    GROQ_MODELS,
    GEMINI_MODELS,
    CEREBRAS_MODELS,
    OPENROUTER_FREE_MODELS,
    COHERE_FREE_MODELS,
    CLOUDFLARE_FREE_MODELS,
    _call_model,
    _import_litellm,
)

# ── Test prompt — tiny, fast, unambiguous ─────────────────────────────────────
PING_PROMPT = "Reply with exactly the word: OK"
SYSTEM_PROMPT = "You are a test assistant. Follow instructions exactly."

# ── Providers → (model_list, env_var, extra_env_vars) ────────────────────────
PROVIDERS: dict[str, tuple[list[str], str, list[str]]] = {
    "groq":        (GROQ_MODELS,            "GROQ_API_KEY",       []),
    "gemini":      (GEMINI_MODELS,          "GEMINI_API_KEY",     []),
    "cerebras":    (CEREBRAS_MODELS,        "CEREBRAS_API_KEY",   []),
    "openrouter":  (OPENROUTER_FREE_MODELS, "OPENROUTER_API_KEY", []),
    "cohere":      (COHERE_FREE_MODELS,     "COHERE_API_KEY",     []),
    "cloudflare":  (CLOUDFLARE_FREE_MODELS, "CLOUDFLARE_API_KEY", ["CLOUDFLARE_ACCOUNT_ID"]),
}

# ANSI colours
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


@dataclass
class ModelResult:
    provider: str
    model: str
    status: str          # "pass" | "fail" | "skip"
    latency_ms: Optional[float] = None
    response_preview: str = ""
    error: str = ""


def _fmt_status(status: str) -> str:
    if status == "pass":
        return f"{GREEN}PASS{RESET}"
    if status == "fail":
        return f"{RED}FAIL{RESET}"
    return f"{YELLOW}SKIP{RESET}"


def test_model(
    litellm,
    model: str,
    api_key: str,
    provider: str,
) -> ModelResult:
    """Run a single ping test against *model*."""
    t0 = time.monotonic()
    try:
        kwargs: dict = dict(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": PING_PROMPT},
            ],
            temperature=0.2,
            max_tokens=64,   # tiny — just needs one word back
        )
        if api_key:
            kwargs["api_key"] = api_key

        response = litellm.completion(**kwargs)
        answer = response.choices[0].message.content or ""
        if not answer:
            raise ValueError("Empty response")
        latency = (time.monotonic() - t0) * 1000
        preview = answer.strip()[:80].replace("\n", " ")
        return ModelResult(
            provider=provider,
            model=model,
            status="pass",
            latency_ms=round(latency, 0),
            response_preview=preview,
        )
    except Exception as exc:
        latency = (time.monotonic() - t0) * 1000
        return ModelResult(
            provider=provider,
            model=model,
            status="fail",
            latency_ms=round(latency, 0),
            error=str(exc)[:200],
        )


def run_tests(
    provider_filter: Optional[str] = None,
    fast_mode: bool = False,
) -> list[ModelResult]:
    litellm = _import_litellm()
    litellm.success_callback = []
    litellm.failure_callback = []
    # Suppress noisy litellm output
    import logging
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    logging.getLogger("litellm").setLevel(logging.ERROR)

    results: list[ModelResult] = []

    for provider, (models, key_var, extra_vars) in PROVIDERS.items():
        if provider_filter and provider != provider_filter:
            continue

        api_key = os.environ.get(key_var, "")
        missing_vars = [v for v in [key_var] + extra_vars if not os.environ.get(v)]

        if missing_vars:
            print(f"\n{YELLOW}[{provider.upper()}]{RESET} Skipping — missing env vars: {', '.join(missing_vars)}")
            for m in models:
                results.append(ModelResult(provider=provider, model=m, status="skip",
                                           error=f"Missing: {', '.join(missing_vars)}"))
            continue

        models_to_test = [models[0]] if fast_mode else models
        print(f"\n{BOLD}{CYAN}[{provider.upper()}]{RESET} Testing {len(models_to_test)} model(s)...")
        print(f"  API key: {key_var}={api_key[:12]}...{api_key[-4:]}")

        for model in models_to_test:
            short = model.split("/", 1)[-1] if "/" in model else model
            print(f"  → {short:<55}", end="", flush=True)
            result = test_model(litellm, model, api_key, provider)
            results.append(result)

            if result.status == "pass":
                print(f"{_fmt_status('pass')}  {result.latency_ms:.0f}ms  \"{result.response_preview}\"")
            else:
                print(f"{_fmt_status('fail')}  {result.latency_ms:.0f}ms")
                # Print truncated error indented
                err_lines = result.error.split(". ")
                for line in err_lines[:2]:
                    print(f"    {RED}{line}{RESET}")

            # Small delay to respect rate limits between models in same provider
            time.sleep(0.5)

    return results


def print_summary(results: list[ModelResult]) -> None:
    passed  = [r for r in results if r.status == "pass"]
    failed  = [r for r in results if r.status == "fail"]
    skipped = [r for r in results if r.status == "skip"]

    print(f"\n{'─'*70}")
    print(f"{BOLD}SUMMARY{RESET}")
    print(f"{'─'*70}")
    print(f"  {GREEN}Passed : {len(passed)}{RESET}")
    print(f"  {RED}Failed : {len(failed)}{RESET}")
    print(f"  {YELLOW}Skipped: {len(skipped)}{RESET}")
    print(f"  Total  : {len(results)}")

    if passed:
        avg_lat = sum(r.latency_ms for r in passed) / len(passed)
        fastest = min(passed, key=lambda r: r.latency_ms)
        print(f"\n  Avg latency (passing): {avg_lat:.0f}ms")
        print(f"  Fastest model        : {fastest.model}  ({fastest.latency_ms:.0f}ms)")

    if failed:
        print(f"\n{RED}Failed models:{RESET}")
        for r in failed:
            print(f"  ✗ {r.model}")
            print(f"    {r.error[:120]}")


def write_report(results: list[ModelResult]) -> Path:
    report_path = Path(__file__).parent / "test_free_models_report.md"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    passed  = [r for r in results if r.status == "pass"]
    failed  = [r for r in results if r.status == "fail"]
    skipped = [r for r in results if r.status == "skip"]

    lines = [
        f"# Free LLM Models Test Report",
        f"",
        f"Generated: {now}",
        f"",
        f"| | Count |",
        f"|---|---|",
        f"| ✅ Passed  | {len(passed)} |",
        f"| ❌ Failed  | {len(failed)} |",
        f"| ⏭ Skipped | {len(skipped)} |",
        f"| **Total**  | **{len(results)}** |",
        f"",
        f"---",
        f"",
        f"## Results by Provider",
        f"",
    ]

    # Group by provider
    providers_seen: dict[str, list[ModelResult]] = {}
    for r in results:
        providers_seen.setdefault(r.provider, []).append(r)

    for provider, prov_results in providers_seen.items():
        p_pass  = sum(1 for r in prov_results if r.status == "pass")
        p_fail  = sum(1 for r in prov_results if r.status == "fail")
        p_skip  = sum(1 for r in prov_results if r.status == "skip")
        lines += [
            f"### {provider.upper()}  ({p_pass} pass / {p_fail} fail / {p_skip} skip)",
            f"",
            f"| Model | Status | Latency | Response / Error |",
            f"|---|---|---|---|",
        ]
        for r in prov_results:
            icon = "✅" if r.status == "pass" else ("❌" if r.status == "fail" else "⏭")
            lat  = f"{r.latency_ms:.0f}ms" if r.latency_ms else "-"
            note = r.response_preview if r.status == "pass" else r.error[:100]
            lines.append(f"| `{r.model}` | {icon} {r.status.upper()} | {lat} | {note} |")
        lines.append("")

    if failed:
        lines += [
            "---",
            "",
            "## Failed Models — Full Errors",
            "",
        ]
        for r in failed:
            lines += [
                f"### `{r.model}`",
                f"```",
                r.error,
                f"```",
                "",
            ]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Test all free LLM models via LiteLLM")
    parser.add_argument(
        "--provider", "-p",
        choices=list(PROVIDERS.keys()),
        default=None,
        help="Only test this provider (default: all)",
    )
    parser.add_argument(
        "--fast", "-f",
        action="store_true",
        help="Test only the first (best) model per provider instead of all",
    )
    args = parser.parse_args()

    print(f"{BOLD}{'='*70}")
    print(f"  EasyRepo — Free LLM Models Live Test")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}{RESET}")

    if args.fast:
        print(f"{YELLOW}[fast mode] Testing 1 model per provider{RESET}")

    # Warn about missing Cloudflare account ID
    if os.environ.get("CLOUDFLARE_API_KEY") and not os.environ.get("CLOUDFLARE_ACCOUNT_ID"):
        print(f"\n{YELLOW}⚠ CLOUDFLARE_API_KEY is set but CLOUDFLARE_ACCOUNT_ID is missing.{RESET}")
        print(f"  Cloudflare tests will be skipped. Add CLOUDFLARE_ACCOUNT_ID to .env")

    results = run_tests(provider_filter=args.provider, fast_mode=args.fast)
    print_summary(results)

    report = write_report(results)
    print(f"\n{CYAN}Report written to: {report}{RESET}")

    # Exit non-zero if any tests failed (not just skipped)
    failed = [r for r in results if r.status == "fail"]
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
