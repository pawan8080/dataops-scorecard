"""Orchestrate one pipeline run and log everything about it.

    python -m pipeline.run --state-dir state [--fault bad_values] [--offline]

Every run, successful or not, appends one record to state/logs/runs.jsonl and
one record per data test to state/logs/tests.jsonl. Those logs are the only
input the scorecard needs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from pipeline import faults
from pipeline.config import DBT_DIR, ROOT
from pipeline.ingest import extract
from pipeline.load import load


def git(*args: str) -> str | None:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True,
                                       stderr=subprocess.DEVNULL).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def run_dbt(db_path: Path) -> dict:
    env = {**os.environ, "DUCKDB_PATH": str(db_path)}
    (DBT_DIR / "target" / "run_results.json").unlink(missing_ok=True)
    proc = subprocess.run(
        ["dbt", "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
         "--no-use-colors", "--quiet"],
        env=env, capture_output=True, text=True,
    )
    results_file = DBT_DIR / "target" / "run_results.json"
    if not results_file.exists():
        raise RuntimeError(f"dbt did not produce results:\n{proc.stdout[-2000:]}{proc.stderr[-2000:]}")
    results = json.loads(results_file.read_text())["results"]

    tests, models = [], []
    for r in results:
        uid = r["unique_id"]
        kind = uid.split(".")[0]
        if kind == "test":
            tests.append({
                "test": uid.split(".")[2],
                "status": r["status"],          # pass | fail | error | warn | skipped
                "failures": r.get("failures") or 0,
                "duration_s": round(r.get("execution_time", 0), 3),
            })
        elif kind == "model":
            models.append({"model": uid.split(".")[-1], "status": r["status"],
                           "duration_s": round(r.get("execution_time", 0), 3)})
    return {"returncode": proc.returncode, "tests": tests, "models": models}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--fault", default=os.environ.get("FAULT"))
    ap.add_argument("--offline", action="store_true", help="use a local fixture instead of the API")
    ap.add_argument("--trigger", default=os.environ.get("GITHUB_EVENT_NAME", "local"))
    ap.add_argument("--chaos-rate", type=float, default=float(os.environ.get("CHAOS_RATE", "0")))
    args = ap.parse_args()

    state = Path(args.state_dir).resolve()
    raw_dir, log_dir = state / "raw", state / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    db_path = ROOT / "warehouse.duckdb"

    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    fault = faults.choose_fault(args.fault, args.trigger, args.chaos_rate, run_id)

    record = {
        "run_id": run_id,
        "started_at": started.isoformat(),
        "trigger": args.trigger,
        "commit_sha": git("rev-parse", "HEAD"),
        "commit_authored_at": git("show", "-s", "--format=%aI", "HEAD"),
        "github_run_id": os.environ.get("GITHUB_RUN_ID"),
        "fault_injected": fault,
        "source": "offline_fixture" if args.offline else "open-meteo",
        "status": "success",
        "failed_stage": None,
        "error": None,
        "stage_durations_s": {},
        "rows_ingested": 0,
        "rows_loaded": 0,
        "models_built": 0,
        "tests_total": 0,
        "tests_passed": 0,
        "tests_failed": 0,
    }
    tests: list[dict] = []
    stage = "ingest"
    t0 = time.perf_counter()

    try:
        faults.before_extract(fault)
        raw_path, payloads = extract(run_id, raw_dir, offline=args.offline)
        ingested_at = json.loads(raw_path.read_text())["ingested_at"]
        record["rows_ingested"] = sum(len(p["daily"]["time"]) for p in payloads)
        record["stage_durations_s"]["ingest"] = round(time.perf_counter() - t0, 3)

        stage, t0 = "load", time.perf_counter()
        payloads = faults.apply(fault, payloads)
        record["rows_loaded"] = load(db_path, raw_dir, run_id, payloads, ingested_at)
        record["stage_durations_s"]["load"] = round(time.perf_counter() - t0, 3)

        stage, t0 = "transform", time.perf_counter()
        dbt = run_dbt(db_path)
        record["stage_durations_s"]["transform"] = round(time.perf_counter() - t0, 3)
        tests = dbt["tests"]
        record["models_built"] = sum(m["status"] == "success" for m in dbt["models"])
        record["tests_total"] = len(tests)
        record["tests_passed"] = sum(t["status"] == "pass" for t in tests)
        record["tests_failed"] = sum(t["status"] in ("fail", "error") for t in tests)
        record["tests_skipped"] = sum(t["status"] == "skipped" for t in tests)
        if dbt["returncode"] != 0:
            failed = [t["test"] for t in tests if t["status"] in ("fail", "error")]
            failed_models = [m["model"] for m in dbt["models"] if m["status"] == "error"]
            raise RuntimeError(f"dbt build failed. Failing tests: {failed}; failing models: {failed_models}")
        stage = None
    except Exception as exc:  # noqa: BLE001 - every failure must be logged
        record["status"] = "failed"
        record["failed_stage"] = stage
        record["error"] = f"{type(exc).__name__}: {exc}"[:500]
        record["stage_durations_s"].setdefault(stage, round(time.perf_counter() - t0, 3))
        traceback.print_exc()

    finished = datetime.now(timezone.utc)
    record["finished_at"] = finished.isoformat()
    record["duration_s"] = round((finished - started).total_seconds(), 3)

    with open(log_dir / "runs.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
    with open(log_dir / "tests.jsonl", "a") as f:
        for t in tests:
            f.write(json.dumps({"run_id": run_id, **t}) + "\n")

    print(json.dumps({k: record[k] for k in ("run_id", "status", "failed_stage", "fault_injected",
                                             "tests_passed", "tests_total", "duration_s")}))

    if out := os.environ.get("GITHUB_OUTPUT"):
        with open(out, "a") as f:
            f.write(f"status={record['status']}\n")
    # Exit 0 so logging, metrics and the dashboard still publish; the workflow
    # marks the run red in a later step using the status output.
    return 0


if __name__ == "__main__":
    sys.exit(main())
