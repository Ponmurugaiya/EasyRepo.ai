"""Quick script to verify Redis quota state after a 429 is persisted."""
import sys, os
from pathlib import Path

for line in (Path(__file__).parent / ".env").read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent / "platform"))

# Default: show production keys. Pass --test to inspect test namespace.
import argparse
_parser = argparse.ArgumentParser(add_help=False)
_parser.add_argument("--test", action="store_true")
_args, _ = _parser.parse_known_args()
if _args.test:
    os.environ["LLM_QUOTA_KEY_PREFIX"] = "test"
    print("(using test namespace: prefix=test)\n")

from src.generation.quota_store import get_quota_store
from src.generation.llm_client import ALL_MODEL_SPECS

store = get_quota_store()

print("\n=== Redis Quota State (all models) ===\n")
print(f"{'Model':<55} {'RPM/lim':>10} {'RPD/lim':>12} {'RPD TTL':>10} {'Available':>10}")
print("─" * 105)

for spec in ALL_MODEL_SPECS:
    api_key = os.environ.get(spec.env_key, "")
    if not api_key:
        continue
    status = store.get_status(spec.model_id)
    if status.get("redis") == "unavailable":
        print(f"  Redis unavailable")
        break
    rpm_str = f"{status['rpm_last_60s']}/{spec.rpm_free}"
    rpd_str = f"{status['rpd_today']}/{spec.rpd_free if spec.rpd_free else '∞'}"
    ttl_str = f"{status['rpd_key_ttl_s']}s" if status['rpd_key_ttl_s'] > 0 else "no TTL"
    avail = store.is_available(spec)
    flag = "" if avail else " ← EXHAUSTED"
    print(f"  {spec.model_id:<53} {rpm_str:>10} {rpd_str:>12} {ttl_str:>10} {'yes' if avail else 'NO':>10}{flag}")

print()
