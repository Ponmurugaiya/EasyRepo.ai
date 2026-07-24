"""
Run the 4 validation queries against sample-repo and print full output.
Usage: python scripts/run_validation_queries.py
"""
import os
import sys
import subprocess

# Force UTF-8 output on Windows
os.environ["PYTHONIOENCODING"] = "utf-8"
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Load key from .env at repo root
env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

API_KEY = os.environ.get("GEMINI_API_KEY", "")
DB_URL = "postgresql://postgres:postgres@127.0.0.1:5435/easyrepo"
REPO_ID = "sample-repo"

QUESTIONS = [
    ("Q1 — Login Flow", "Walk me through what happens when a user logs in, from entry point to completion"),
    ("Q2 — AdminUser Inheritance", "What does AdminUser inherit and how does permission checking work?"),
    ("Q3 — No-Dependency Functions", "Is there any function in this codebase that has no dependencies on other code?"),
    ("Q4 — validate (ambiguous)", "What does the validate method do?"),
]

for label, question in QUESTIONS:
    print("\n" + "=" * 70)
    print(f"  {label}")
    print(f"  Q: {question}")
    print("=" * 70)

    result = subprocess.run(
        [
            sys.executable, "-m", "src.cli",
            "ask", REPO_ID, question,
            "--db-url", DB_URL,
            "--gemini-key", API_KEY,
            "--top-k", "12",
            "--token-budget", "8000",
        ],
        capture_output=True,
        text=True,
        cwd=os.path.join(os.path.dirname(__file__), ".."),
    )
    print(result.stdout)
    if result.stderr:
        print("STDERR:", result.stderr[:1000])
    if result.returncode != 0:
        print(f"[exit code {result.returncode}]")
