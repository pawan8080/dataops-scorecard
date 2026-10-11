"""Generate a clearly labelled DEMO run history for previewing the dashboard.

    python scripts/seed_demo.py --state-dir demo_state --days 45
    python -m metrics.compute --state-dir demo_state --out dashboard/data/metrics.json

Every record is tagged "demo": true and the dashboard shows a DEMO banner when it
sees one. This never touches the real run log on the `data` branch.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

TESTS = [
    "source_not_null_raw_weather_daily_run_id", "source_not_null_raw_weather_daily_city",
    "source_not_null_raw_weather_daily_date", "assert_latest_run_is_fresh",
    "unique_combination_stg_weather_daily_city__date", "not_null_stg_weather_daily_city",
    "accepted_values_stg_weather_daily_city", "not_null_stg_weather_daily_date",
    "not_null_stg_weather_daily_temp_max_c", "value_between_stg_weather_daily_temp_max_c",
    "not_null_stg_weather_daily_temp_min_c", "value_between_stg_weather_daily_temp_min_c",
    "value_between_stg_weather_daily_precipitation_mm", "value_between_stg_weather_daily_wind_speed_max_kmh",
    "assert_min_not_above_max", "unique_combination_fct_daily_weather_city__date",
    "value_between_fct_daily_weather_temp_range_c", "unique_combination_agg_city_monthly_city__month",
]
SUBJECTS = [
    "Add wind speed range test", "Refactor staging dedupe", "Add monthly city aggregate",
    "Tighten freshness check", "Bump dbt-duckdb", "Add Tokyo to city list", "Improve contract errors",
    "Parallelize city fetches", "Add wet-day flag to fact table", "Document metric definitions",
    "Cache pip in workflow", "Add min/max sanity test", "Split stage timings", "Tune retry on 5xx",
]


def sha(seed: str) -> str:
    return hashlib.sha1(seed.encode()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="demo_state")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    logs = Path(args.state_dir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=args.days)

    # Commits: a few per week during working hours, one real breaking change + fix.
    commits, t = [], start + timedelta(hours=10)
    while t < now - timedelta(hours=6):
        t += timedelta(hours=rng.uniform(20, 70))
        t = t.replace(hour=rng.randint(9, 18))
        commits.append({"sha": sha(f"c{len(commits)}"), "authored_at": t.isoformat(),
                        "subject": rng.choice(SUBJECTS), "breaks": False})
    for i in (len(commits) // 3, (2 * len(commits)) // 3):
        commits[i]["breaks"] = True
        commits[i]["subject"] = "Rename precipitation column in staging"
        fix_t = ts(commits[i]["authored_at"]) + timedelta(hours=rng.uniform(1.5, 4))
        commits.insert(i + 1, {"sha": sha(f"fix{i}"), "authored_at": fix_t.isoformat(),
                               "subject": "Fix: restore precipitation column name", "breaks": False})
    commits = sorted((c for c in commits if ts(c["authored_at"]) < now - timedelta(hours=2)),
                     key=lambda c: c["authored_at"])

    # Events: a scheduled run every 3h, plus a push run a few minutes after each commit
    # (simulating a short review delay before the push for some commits).
    events = []
    t = start
    while t < now:
        events.append(("schedule", t + timedelta(minutes=17)))
        t += timedelta(hours=3)
    for c in commits:
        quick = c["subject"].startswith(("Fix", "Rename")) or rng.random() < 0.6
        delay = timedelta(minutes=rng.uniform(5, 40)) if quick else timedelta(hours=rng.uniform(4, 30))
        events.append(("push", ts(c["authored_at"]) + delay, c))
    events.sort(key=lambda e: e[1])

    runs, tests = [], []
    head = {"sha": sha("initial"), "breaks": False}
    for ev in events:
        trigger, when = ev[0], ev[1]
        if when > now:
            continue
        if trigger == "push":
            head = ev[2]
        run_id = when.strftime("%Y%m%dT%H%M%SZ")
        fault = rng.choice(["schema_change", "bad_values", "late_data", "api_timeout"]) \
            if trigger == "schedule" and rng.random() < 0.08 else None
        ingest = round(rng.uniform(1.2, 3.5) * (1.4 if when.weekday() == 0 else 1), 2)
        load = round(rng.uniform(0.2, 0.6), 2)
        transform = round(rng.uniform(3.0, 5.5) + (when - start).days * 0.015, 2)
        rec = {"run_id": run_id, "started_at": when.isoformat(), "trigger": trigger,
               "commit_sha": head["sha"], "commit_authored_at": head.get("authored_at"),
               "fault_injected": fault, "source": "demo", "demo": True, "status": "success",
               "failed_stage": None, "error": None, "rows_ingested": 30, "rows_loaded": 30,
               "models_built": 3, "stage_durations_s": {"ingest": ingest, "load": load, "transform": transform}}
        statuses = {name: "pass" for name in TESTS}

        if fault == "api_timeout":
            rec.update(status="failed", failed_stage="ingest", rows_ingested=0, rows_loaded=0, models_built=0,
                       error="InjectedTimeout: Injected fault: Open-Meteo request timed out after 30s",
                       stage_durations_s={"ingest": 30.0})
            statuses = {}
        elif fault == "schema_change":
            rec.update(status="failed", failed_stage="load", rows_loaded=0, models_built=0,
                       error="ContractViolation: Schema contract violation for Calgary: "
                             "missing=['temperature_2m_max'] unexpected=['temp_max_c']",
                       stage_durations_s={"ingest": ingest, "load": 0.01})
            statuses = {}
        elif fault == "late_data":
            statuses = {n: "skipped" for n in TESTS}
            statuses.update({n: "pass" for n in TESTS[:3]})
            statuses["assert_latest_run_is_fresh"] = "fail"
            rec.update(status="failed", failed_stage="transform", models_built=0,
                       error="RuntimeError: dbt build failed. Failing tests: ['assert_latest_run_is_fresh']")
        elif fault == "bad_values":
            statuses["value_between_stg_weather_daily_temp_max_c"] = "fail"
            for n in TESTS[-3:]:
                statuses[n] = "skipped"
            rec.update(status="failed", failed_stage="transform", models_built=1,
                       error="RuntimeError: dbt build failed. Failing tests: "
                             "['value_between_stg_weather_daily_temp_max_c']")
        elif head.get("breaks"):
            for n in TESTS[4:]:
                statuses[n] = "skipped"
            rec.update(status="failed", failed_stage="transform", models_built=0,
                       error="RuntimeError: dbt build failed. Failing tests: []; failing models: "
                             "['stg_weather_daily'] (Binder Error: column precipitation_sum not found)")

        rec["tests_total"] = len(statuses)
        rec["tests_passed"] = sum(s == "pass" for s in statuses.values())
        rec["tests_failed"] = sum(s == "fail" for s in statuses.values())
        rec["tests_skipped"] = sum(s == "skipped" for s in statuses.values())
        dur = sum(rec["stage_durations_s"].values()) + 0.3
        rec["duration_s"] = round(dur, 2)
        rec["finished_at"] = (when + timedelta(seconds=dur)).isoformat()
        runs.append(rec)
        for name, st in statuses.items():
            tests.append({"run_id": run_id, "test": name, "status": st,
                          "failures": 6 if st == "fail" else 0, "duration_s": round(rng.uniform(0.02, 0.2), 3)})

    (logs / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs))
    (logs / "tests.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tests))
    (logs / "commits.jsonl").write_text("".join(
        json.dumps({k: c[k] for k in ("sha", "authored_at", "subject")}) + "\n" for c in reversed(commits)))
    print(f"Demo history: {len(runs)} runs, {len(commits)} commits in {logs}")


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


if __name__ == "__main__":
    main()
