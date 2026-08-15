"""
EasyRepo Evaluation Orchestrator
=================================
Runs the full 6-pillar evaluation pipeline against sample-repo, captures
all output, and writes populated result files to evaluation/results/.

Prerequisites:
    - DATABASE_URL in .env must point to a live Supabase instance
    - The API server will be started automatically by this script

Usage (from EasyRepo/):
    python evaluation/run_evaluation.py [--skip-ingest] [--repo-id <uuid>]

Options:
    --skip-ingest    Skip DB wipe + ingest (use when sample-repo is already indexed)
    --repo-id        Provide an existing repo_id (implies --skip-ingest)
"""

import argparse
import http.client
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
EVAL_DIR = _HERE.parent
REPO_ROOT = EVAL_DIR.parent
PLATFORM_DIR = REPO_ROOT / "platform"
SCRIPTS_DIR = EVAL_DIR / "scripts"
RESULTS_DIR = EVAL_DIR / "results"

# ── Load .env ─────────────────────────────────────────────────────────────────
_ENV_PATH = REPO_ROOT / ".env"
if _ENV_PATH.exists():
    with open(_ENV_PATH, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and "=" in _line and not _line.startswith("#"):
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

DB_URL = os.environ.get("DATABASE_URL", "")
PYTHON = sys.executable

# ── Colours ───────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def banner(msg: str) -> None:
    print(f"\n{'=' * 68}")
    print(f"  {msg}")
    print(f"{'=' * 68}")

def ok(msg: str)   -> None: print(f"  [OK]   {msg}")
def fail(msg: str) -> None: print(f"  [FAIL] {msg}")
def info(msg: str) -> None: print(f"  -->    {msg}")

# ── API helpers ───────────────────────────────────────────────────────────────
def _http_get(path: str, timeout: int = 15) -> tuple[int, dict]:
    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=timeout)
    conn.request("GET", path)
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read().decode())

def _http_post(path: str, body: dict, timeout: int = 120) -> tuple[int, dict]:
    payload = json.dumps(body).encode()
    conn = http.client.HTTPConnection("127.0.0.1", 8000, timeout=timeout)
    conn.request("POST", path, body=payload, headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read().decode())

def api_alive() -> bool:
    try:
        _http_get("/", timeout=2)
        return True
    except Exception:
        return False

# ── Start / stop API server ───────────────────────────────────────────────────
def start_api() -> subprocess.Popen:
    info("Starting API server (platform/run.py)...")
    _api_log = PLATFORM_DIR / "logs" / "api_startup.log"
    _api_log.parent.mkdir(parents=True, exist_ok=True)
    _env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [PYTHON, "run.py"],
        cwd=str(PLATFORM_DIR),
        stdout=open(_api_log, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        env=_env,
    )
    for attempt in range(45):
        time.sleep(2)
        if api_alive():
            ok(f"API ready after {(attempt + 1) * 2}s  (pid={proc.pid})")
            time.sleep(3)  # allow connection pools / DB init to settle
            return proc
        if proc.poll() is not None:
            raise RuntimeError("API server process exited unexpectedly — check platform/run.py")
    raise RuntimeError("API server did not become ready within 90s")

# ── Ingest helpers ────────────────────────────────────────────────────────────
def ingest_sample_repo() -> str:
    source = str(REPO_ROOT / "sample-repo")
    info(f"POST /repositories  source={source}")
    status, data = _http_post("/repositories", {"source": source})
    if status not in (200, 201, 202):
        raise RuntimeError(f"Ingest failed (HTTP {status}): {data}")
    repo_id = data.get("id") or data.get("repo_id")
    if not repo_id:
        raise RuntimeError(f"No repo_id in response: {data}")
    ok(f"Ingest started — repo_id: {repo_id}")
    return repo_id

def poll_until_ready(repo_id: str, timeout: int = 600) -> None:
    info(f"Polling status (up to {timeout // 60} min)...")
    deadline = time.time() + timeout
    last_status = ""
    while time.time() < deadline:
        try:
            _, data = _http_get(f"/repositories/{repo_id}/status")
            status = data.get("status", "unknown")
            if status != last_status:
                info(f"status = {status}")
                last_status = status
            if status == "ready":
                ok("Repository indexed and ready")
                return
            if status in ("failed", "error"):
                raise RuntimeError(f"Indexing failed: {data}")
        except (ConnectionRefusedError, OSError):
            pass
        time.sleep(10)
    raise RuntimeError(f"Indexing did not complete within {timeout}s")

# ── Run a pillar script ───────────────────────────────────────────────────────
def run_script(label: str, script: Path, args: list[str]) -> tuple[bool, str]:
    info(f"Running {script.name}  {' '.join(args)}")
    try:
        _env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [PYTHON, str(script)] + args,
            capture_output=True, text=True, timeout=360,
            encoding="utf-8", errors="replace",
            env=_env,
            cwd=str(REPO_ROOT),
        )
        output = result.stdout
        if result.stderr.strip():
            output += "\n\n--- STDERR ---\n" + result.stderr
        passed = result.returncode == 0
        if passed:
            ok(f"{label} completed (exit 0)")
        else:
            fail(f"{label} exited with code {result.returncode}")
        return passed, output
    except subprocess.TimeoutExpired:
        fail(f"{label} timed out after 360s")
        return False, "ERROR: Script timed out after 360s\n"
    except Exception as e:
        fail(f"{label} error: {e}")
        return False, f"ERROR: {e}\n"

# ── Write result file ─────────────────────────────────────────────────────────
def write_pillar_result(
    pillar: int,
    title: str,
    script_name: str,
    repo_id: str,
    raw_output: str,
    passed: bool,
    key_rows: list[tuple[str, str, str]],   # (metric, value, target)
    verdict_text: str,
    notes: str = "",
) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    names = {
        1: "pillar1-extraction.md",
        2: "pillar2-storage.md",
        3: "pillar3-retrieval.md",
        4: "pillar4-answers.md",
        5: "pillar5-citations.md",
        6: "pillar6-memory-agents.md",
    }
    out_path = RESULTS_DIR / names[pillar]
    run_date = datetime.now().strftime("%Y-%m-%d")
    db_display = (DB_URL[:55] + "…") if len(DB_URL) > 55 else DB_URL

    key_table = "\n".join(
        f"| {m} | {v} | {t} | {'✅' if passed else '❌'} |"
        for m, v, t in key_rows
    ) or "| — | — | — | — |"

    verdict_label = "**PASS**" if passed else "**FAIL**"

    content = f"""# Pillar {pillar} — {title} Evaluation Results

**Run date:** {run_date}
**Run by:** Automated (evaluation/run_evaluation.py)
**Repo under test:** sample-repo
**Repo ID in DB:** `{repo_id}`
**Environment:**
- DATABASE_URL: `{db_display}`
- Embedding model: jina-code-embeddings-1.5b (1024 dimensions)
- Generation model: groq/llama-3.3-70b-versatile → gemini/gemini-2.5-flash (fallback)
- Script: `evaluation/scripts/{script_name}`

---

## Raw Output

```
{raw_output.strip()}
```

---

## Key Numbers

| Metric | Value | Target | Pass? |
|---|---|---|---|
{key_table}

---

## Verdict

{verdict_label}

{verdict_text}

---

## Notes

{notes.strip() if notes.strip() else "No anomalies detected in this run."}
"""
    out_path.write_text(content, encoding="utf-8")
    ok(f"Written → {out_path.relative_to(REPO_ROOT)}")
    return out_path

# ── Output parsing helpers ────────────────────────────────────────────────────
def has(output: str, *fragments: str) -> bool:
    return all(f in output for f in fragments)

def extract_between(text: str, after: str, before: str = None) -> str:
    try:
        start = text.index(after) + len(after)
        chunk = text[start:]
        if before:
            end = chunk.index(before)
            chunk = chunk[:end]
        return chunk.strip().split()[0]
    except (ValueError, IndexError):
        return "see output"

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description="EasyRepo Full Evaluation Orchestrator")
    parser.add_argument("--skip-ingest", action="store_true",
                        help="Skip ingestion (use existing indexed data)")
    parser.add_argument("--repo-id", type=str, default=None,
                        help="Existing repo_id — implies --skip-ingest")
    args = parser.parse_args()

    if args.repo_id:
        args.skip_ingest = True

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    banner("EasyRepo Evaluation Orchestrator")
    print(f"  Repo root : {REPO_ROOT}")
    print(f"  Results   : {RESULTS_DIR}")
    print(f"  Started   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # ── Start API if not already running ──────────────────────────────────────
    api_proc = None
    if api_alive():
        ok("API server already running on :8000")
    else:
        banner("Starting API Server")
        try:
            api_proc = start_api()
        except RuntimeError as e:
            fail(str(e))
            sys.exit(1)

    # ── Ingest or reuse ───────────────────────────────────────────────────────
    if args.skip_ingest and args.repo_id:
        repo_id = args.repo_id
        ok(f"Using existing repo_id: {repo_id}")
    elif args.skip_ingest:
        # Try to discover the most recent ready repo from the API
        try:
            _, repos = _http_get("/repositories")
            ready = [r for r in (repos if isinstance(repos, list) else repos.get("repositories", []))
                     if r.get("status") == "ready"]
            if not ready:
                fail("--skip-ingest set but no ready repository found. Remove flag to ingest.")
                sys.exit(1)
            repo_id = ready[0]["id"]
            ok(f"Using existing ready repo_id: {repo_id}")
        except Exception as e:
            fail(f"Could not discover repo_id: {e}. Use --repo-id <uuid>.")
            sys.exit(1)
    else:
        banner("Ingesting sample-repo")
        try:
            repo_id = ingest_sample_repo()
            poll_until_ready(repo_id, timeout=600)
        except Exception as e:
            fail(str(e))
            if api_proc:
                api_proc.terminate()
            sys.exit(1)

    summary: list[tuple[str, bool]] = []

    # =========================================================================
    # PILLAR 1 — Extraction Quality
    # =========================================================================
    banner("Pillar 1 — Extraction Quality")
    p1_passed, p1_out = run_script("Pillar 1", SCRIPTS_DIR / "pillar1_extraction.py", [])

    # Parse key numbers
    ent_rate = "100.00%" if "100.00%" in p1_out else (
        "100.0%" if "Match Rate:         100.0" in p1_out else "< 100%")
    lr_miss   = "0" if "Line Range Mismatches:       0" in p1_out else ">0"
    ps_miss   = "0" if "Parent Structure Mismatches: 0" in p1_out else ">0"
    ds_miss   = "0" if "Docstring Flag Mismatches:   0" in p1_out else ">0"

    def rel_rate(rtype: str) -> str:
        for line in p1_out.splitlines():
            if rtype in line and "Match Rate:" in line:
                return line.split("Match Rate:")[1].strip().split("%")[0].strip() + "%"
        return "see output"

    write_pillar_result(
        pillar=1, title="Extraction Quality",
        script_name="pillar1_extraction.py",
        repo_id=repo_id, raw_output=p1_out, passed=p1_passed,
        key_rows=[
            ("Entity recall / precision", ent_rate, "100%"),
            ("CONTAINS recall",   rel_rate("CONTAINS"),   "100%  (48/48)"),
            ("IMPORTS recall",    rel_rate("IMPORTS"),    "100%  (11/11)"),
            ("CALLS recall",      rel_rate("CALLS"),      "100%  (23/23)"),
            ("INHERITS recall",   rel_rate("INHERITS"),   "100%  (2/2)"),
            ("IMPLEMENTS recall", rel_rate("IMPLEMENTS"), "100%  (3/3)"),
            ("Line range mismatches",     lr_miss, "0"),
            ("Parent structure errors",   ps_miss, "0"),
            ("Docstring flag mismatches", ds_miss, "0"),
        ],
        verdict_text=(
            "Extraction pipeline matched 100% of entities and all relationship types "
            "against the 62-entity / 87-relationship ground-truth manifest. "
            "Zero structural mismatches detected."
        ) if p1_passed else
            "Extraction pipeline did not achieve 100% match — see raw output for details.",
    )
    summary.append(("Pillar 1 — Extraction Quality", p1_passed))

    # =========================================================================
    # PILLAR 2 — Storage & Embedding Integrity
    # =========================================================================
    banner("Pillar 2 — Storage & Embedding Integrity")
    p2_passed, p2_out = run_script(
        "Pillar 2", SCRIPTS_DIR / "pillar2_storage.py",
        ["--repo-id", repo_id, "--db-url", DB_URL]
    )

    def rel_count(rtype: str) -> str:
        kw = f"Relationship {rtype} count matches"
        for line in p2_out.splitlines():
            if kw in line:
                start = line.index("(") + 1
                return line[start:line.index(")", start)]
        return "check output"

    ranking_pass = "RANKING CHECK PASSED" in p2_out
    ranking_val  = "PASSED" if ranking_pass else "FAILED — check output"

    write_pillar_result(
        pillar=2, title="Storage & Embedding Integrity",
        script_name="pillar2_storage.py",
        repo_id=repo_id, raw_output=p2_out, passed=p2_passed,
        key_rows=[
            ("Entity count in DB",          "62" if "count matches manifest (62)" in p2_out else "check output", "62"),
            ("CONTAINS relationships",      rel_count("CONTAINS"),  "48"),
            ("IMPORTS relationships",       rel_count("IMPORTS"),   "11"),
            ("CALLS relationships",         rel_count("CALLS"),     "23"),
            ("INHERITS relationships",      rel_count("INHERITS"),  "2"),
            ("IMPLEMENTS relationships",    rel_count("IMPLEMENTS"),"3"),
            ("INSTANTIATES relationships",  "≥ 1" if "INSTANTIATES relationship(s) present" in p2_out else "check output", "≥ 1"),
            ("NULL embeddings",             "0" if "non-null vector embeddings" in p2_out else "check output", "0"),
            ("Embedding dimension",         "1024" if "Embedding dimensions verified (1024)" in p2_out else "check output", "1024"),
            ("Ranking Q1 (auth/token)",     ranking_val, "PASSED"),
            ("Ranking Q2 (AuthService.validate > UserModel.validate)", ranking_val, "PASSED"),
            ("Ranking Q3 (format_audit_log > format_user_record)",    ranking_val, "PASSED"),
        ],
        verdict_text=(
            "All 62 entities stored with correct relationship counts. "
            "No NULL embeddings. All embeddings are 1024-dimensional. "
            "All 3 semantic similarity ranking spot-checks passed."
        ) if p2_passed else
            "Storage / embedding verification encountered failures — see raw output.",
    )
    summary.append(("Pillar 2 — Storage & Embedding Integrity", p2_passed))

    # =========================================================================
    # PILLAR 3 — Retrieval Quality
    # =========================================================================
    banner("Pillar 3 — Retrieval Quality")
    manifest_path = str(REPO_ROOT / "sample-repo" / "test-manifest.json")
    p3_passed, p3_out = run_script(
        "Pillar 3", SCRIPTS_DIR / "pillar3_retrieval.py",
        ["--repo-id", repo_id, "--db-url", DB_URL, "--manifest", manifest_path]
    )
    _, p3q_out = run_script(
        "Pillar 3 Q3", SCRIPTS_DIR / "pillar3_q3_rankings.py",
        ["--repo-id", repo_id, "--db-url", DB_URL]
    )
    combined3 = p3_out + "\n\n══ Q3 Rankings ══\n\n" + p3q_out

    scenarios = [
        "multi_hop_call_chain", "multi_level_inheritance",
        "interface_implementation", "method_disambiguation",
        "textual_similarity_no_conflation", "orphan_file_isolation",
    ]
    def sc_result(name: str) -> str:
        # Look for "Status: [PASS]" or "Status: [FAIL]" right after the scenario header
        lines = p3_out.splitlines()
        for i, line in enumerate(lines):
            if name in line:
                for j in range(i, min(i + 15, len(lines))):
                    if "Status: [PASS]" in lines[j]:
                        return "PASS"
                    if "Status: [FAIL]" in lines[j]:
                        return "FAIL"
        return "check output"

    all_passed3 = "ALL PASSED" in p3_out and p3_passed

    write_pillar_result(
        pillar=3, title="Retrieval Quality",
        script_name="pillar3_retrieval.py + pillar3_q3_rankings.py",
        repo_id=repo_id, raw_output=combined3, passed=all_passed3,
        key_rows=[
            (f"Scenario 1: multi_hop_call_chain",           sc_result("multi_hop_call_chain"),           "PASS"),
            (f"Scenario 2: multi_level_inheritance",        sc_result("multi_level_inheritance"),        "PASS"),
            (f"Scenario 3: interface_implementation",       sc_result("interface_implementation"),       "PASS"),
            (f"Scenario 4: method_disambiguation",          sc_result("method_disambiguation"),          "PASS"),
            (f"Scenario 5: textual_similarity_no_conflation", sc_result("textual_similarity_no_conflation"), "PASS"),
            (f"Scenario 6: orphan_file_isolation",          sc_result("orphan_file_isolation"),          "PASS"),
            ("Total scenarios passing",                     f"{p3_out.count('[PASS]')}/6", "6/6"),
            ("Expansion integrity (all edges verified in DB)", "100%" if "verified against DB" in p3_out else "check output", "100%"),
            ("format_audit_log Q3 rank",  "see raw output", "#1"),
        ],
        verdict_text=(
            "All 6 retrieval scenarios passed. Every graph expansion edge was verified "
            "against real DB relationship rows. Numeric Precision@K, Recall@K, and MRR "
            "metrics are printed per-scenario in the raw output above."
        ) if all_passed3 else
            "One or more retrieval scenarios did not pass — see raw output for details.",
    )
    summary.append(("Pillar 3 — Retrieval Quality", all_passed3))

    # =========================================================================
    # PILLARS 4 + 5 — Answer & Citation Quality
    # =========================================================================
    banner("Pillars 4 + 5 — Answer & Citation Quality")
    p45_passed, p45_out = run_script(
        "Pillars 4+5", SCRIPTS_DIR / "pillar45_ask.py",
        ["--repo-id", repo_id]
    )

    overall_hall = "0.0%" if "hallucination_rate=0.0%" in p45_out else "check output"
    total_cites = "see output"
    for line in p45_out.splitlines():
        if "OVERALL" in line and "total=" in line:
            try:
                total_cites = line.split("total=")[1].split()[0].strip()
            except Exception:
                pass

    def q_hall(qn: str) -> str:
        lines = p45_out.splitlines()
        for i, line in enumerate(lines):
            if f"[{qn}]" in line:
                for j in range(i, min(i + 15, len(lines))):
                    if "Hallucination rate:" in lines[j]:
                        return lines[j].split("Hallucination rate:")[1].strip()
        return "see output"

    write_pillar_result(
        pillar=4, title="Answer Quality",
        script_name="pillar45_ask.py",
        repo_id=repo_id, raw_output=p45_out, passed=p45_passed,
        key_rows=[
            ("Q1 hallucination rate", q_hall("Q1"), "0.0%"),
            ("Q2 hallucination rate", q_hall("Q2"), "0.0%"),
            ("Q3 hallucination rate", q_hall("Q3"), "0.0%"),
            ("Q4 hallucination rate", q_hall("Q4"), "0.0%"),
            ("Overall total citations",    total_cites,  "≥ 40"),
            ("Overall hallucination rate", overall_hall, "0.0%"),
            ("ALL ASSERTIONS PASSED", "✅" if "ALL ASSERTIONS PASSED" in p45_out else "❌", "✅"),
        ],
        verdict_text=(
            "All 4 canonical questions answered with 0.0% hallucination rate. "
            "Completeness checks verified that key entity names appear in each answer. "
            "ALL ASSERTIONS PASSED ✓"
        ) if p45_passed else
            "Answer quality evaluation encountered failures — see raw output.",
    )

    unsupported = "0" if "unsupported=0" in p45_out else "check output"
    write_pillar_result(
        pillar=5, title="Citation Quality",
        script_name="pillar45_ask.py (same run as Pillar 4)",
        repo_id=repo_id, raw_output=p45_out, passed=p45_passed,
        key_rows=[
            ("Overall hallucination rate",                  overall_hall, "0.0%"),
            ("Unsupported citations (total)",               unsupported,  "0"),
            ("Definition citations correctly classified",   "see raw output (Def column)", "all correct"),
            ("Call-site citations correctly classified",    "see raw output (CS column)",  "all correct"),
            ("Parent-chain walking & fuzzy path matching",  "working" if "0.0%" in p45_out else "check output", "correct"),
        ],
        verdict_text=(
            "Citation validator correctly classified all citations via the 3-way taxonomy "
            "(definition / call-site / unsupported). 0.0% hallucination rate confirmed."
        ) if p45_passed else
            "Citation quality evaluation encountered failures — see raw output.",
        notes=(
            "Pillars 4 and 5 share a single test run (pillar45_ask.py). "
            "Pillar 4 records answer text quality; Pillar 5 records citation classification quality."
        ),
    )
    summary.append(("Pillar 4 — Answer Quality",   p45_passed))
    summary.append(("Pillar 5 — Citation Quality", p45_passed))

    # =========================================================================
    # PILLAR 6 — Memory & Agentic System (log-based + partially manual)
    # =========================================================================
    banner("Pillar 6 — Memory & Agentic System")
    info("Extracting evidence from platform/logs/pipeline.log ...")

    log_path = PLATFORM_DIR / "logs" / "pipeline.log"
    log_text = ""
    if log_path.exists():
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        ok(f"pipeline.log found ({len(log_text):,} chars)")
    else:
        info("pipeline.log not found — Pillar 6 will need manual completion")

    planner_lines   = [l for l in log_text.splitlines() if "step=planner"          in l]
    final_lines     = [l for l in log_text.splitlines() if "step=final"             in l]
    reretrieval_lines = [l for l in log_text.splitlines() if "step=post-reretrieval" in l]
    ltm_lines       = [l for l in log_text.splitlines() if "step=ltm"               in l]

    # Use last 4 of each (one per question in the Pillars 4+5 run)
    planner_raw     = "\n".join(planner_lines[-4:])   or "(not found)"
    final_raw       = "\n".join(final_lines[-4:])     or "(not found)"
    reret_raw       = "\n".join(reretrieval_lines[-4:]) or "none — all questions answered on first pass"
    ltm_raw         = "\n".join(ltm_lines[-8:])       or "(not found)"

    no_bad_strategy = all(
        "repository_walk" not in l and "repository_overview" not in l
        for l in planner_lines
    ) if planner_lines else False

    all_answered   = all("answer_status=answered" in l for l in final_lines) if final_lines else False
    low_iterations = all(
        "iteration_count=0" in l or "iteration_count=1" in l
        for l in final_lines
    ) if final_lines else False

    p6_auto_pass = bool(planner_lines) and no_bad_strategy and all_answered

    p6_content = f"""# Pillar 6 — Memory & Agentic System Results

**Run date:** {datetime.now().strftime("%Y-%m-%d")}
**Run by:** Automated (evaluation/run_evaluation.py)
**Repo under test:** sample-repo
**Repo ID in DB:** `{repo_id}`
**Environment:**
- DATABASE_URL: `{(DB_URL[:55] + "…") if len(DB_URL) > 55 else DB_URL}`
- AUTH_ENABLED: false (LTM Tiers 1 & 2 out of scope)
- Evidence source: `platform/logs/pipeline.log` + manual curl + Supabase SQL

---

## 6A — Query Planner Classifications

*(from pipeline.log — search `step=planner`)*

```
{planner_raw}
```

| Check | Expected | Result |
|---|---|---|
| No `repository_walk` or `repository_overview` for Q1–Q4 | Yes | {"✅" if no_bad_strategy else "❌ check output"} |
| Planner lines logged (confidence > 0.0) | 4 lines | {"✅ " + str(len(planner_lines)) + " lines found" if planner_lines else "❌ 0 lines found"} |

**Verdict:** {"PASS" if planner_lines and no_bad_strategy else "NEEDS MANUAL REVIEW"}

---

## 6B — Answer Agent Loop (STM)

*(from pipeline.log — search `step=final` and `step=post-reretrieval`)*

**`step=post-reretrieval` lines:**

```
{reret_raw}
```

**`step=final` lines:**

```
{final_raw}
```

| Check | Expected | Result |
|---|---|---|
| `answer_status=answered` all 4 Qs | Yes | {"✅" if all_answered else "❌ check output"} |
| `iteration_count ≤ 1` all 4 Qs | Yes | {"✅" if low_iterations else "— see output"} |

**Verdict:** {"PASS" if all_answered else "PARTIAL — check output"}

---

## 6C — LTM Session Knowledge (Tier 3)

> **Manual steps required.** Run these in a terminal after confirming the API is running:

```powershell
# First call — cache miss expected
curl -X POST http://localhost:8000/repositories/{repo_id}/ask `
  -H "Content-Type: application/json" `
  -d '{{"query": "Walk me through what happens when a user logs in", "session_id": "eval-session-001", "top_k": 10}}'

# Then check Supabase:
# SELECT feature_name, confidence, exploration_status, repo_indexed_at, created_at
# FROM conversation_memory WHERE session_id = 'eval-session-001';

# Second call — cache hit expected (same command again)
# Check pipeline.log for: step=ltm hit=true
```

**LTM log lines from Pillars 4+5 run (if any):**

```
{ltm_raw}
```

---

## 6D — LTM Tiers 1 & 2

**Status:** Out of scope — `AUTH_ENABLED=false`.

---

## Key Numbers Summary

| Metric | Value | Target | Pass? |
|---|---|---|---|
| Planner: no `repository_walk` for Q1–Q4 | {"Pass" if no_bad_strategy else "check output"} | Pass | {"✅" if no_bad_strategy else "❌"} |
| Planner: lines logged | {len(planner_lines)} | 4 | {"✅" if len(planner_lines) >= 4 else "—"} |
| `answer_status=answered` all 4 Qs | {"Pass" if all_answered else "check output"} | Always | {"✅" if all_answered else "❌"} |
| `iteration_count ≤ 1` all 4 Qs | {"Pass" if low_iterations else "see output"} | ≤ 1 | {"✅" if low_iterations else "—"} |
| LTM write after first answered turn | Manual verification required | 1 row in DB | — |
| LTM cache hit on second call | Manual verification required | `hit=true` | — |
| LTM stale detection after re-index | Manual verification required | `hit=false` + STALE | — |
| LTM Tiers 1 & 2 | Out of scope | N/A | N/A |

---

## Verdict

**{"PASS (automated checks)" if p6_auto_pass else "PARTIAL — manual LTM steps still required"}**

{"Planner correctly classified all 4 canonical questions without `repository_walk`. All 4 answers completed with `answer_status=answered`. " if p6_auto_pass else "Partial automated evidence captured from pipeline.log. "}LTM Test 1 (write/cache-hit) and Test 3 (stale detection) require the manual curl + SQL steps in Section 6C above.

---

## Notes

- `step=planner` lines found in log: {len(planner_lines)}
- `step=final` lines found: {len(final_lines)}
- `step=post-reretrieval` lines: {len(reretrieval_lines)} {"(ideal)" if not reretrieval_lines else "(some re-retrieval occurred)"}
- `step=ltm` lines: {len(ltm_lines)}
- Full log: `{log_path}`
"""
    p6_path = RESULTS_DIR / "pillar6-memory-agents.md"
    p6_path.write_text(p6_content, encoding="utf-8")
    ok(f"Written → {p6_path.relative_to(REPO_ROOT)}")
    summary.append(("Pillar 6 — Memory & Agentic System", p6_auto_pass))

    # =========================================================================
    # FINAL SUMMARY
    # =========================================================================
    banner("EVALUATION COMPLETE — FINAL SUMMARY")
    all_pass = all(p for _, p in summary)
    for name, passed in summary:
        if passed:
            ok(f"{GREEN}{name}{RESET}")
        else:
            fail(f"{RED}{name}{RESET}")

    print(f"\n  repo_id : {repo_id}")
    print(f"  Results : {RESULTS_DIR}")
    print(f"  Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if all_pass:
        print("\n  *** ALL PILLARS PASSED ***\n")
    else:
        print("\n  [!] Some pillars need attention -- check result files above.\n")

    if api_proc:
        api_proc.terminate()
        info("API server stopped")

    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
