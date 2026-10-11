"""Turn the run log into the scorecard's metrics.json.

    python -m metrics.compute --state-dir state --out dashboard/data/metrics.json

Inputs
  state/logs/runs.jsonl    one record per pipeline run (written by pipeline.run)
  state/logs/tests.jsonl   one record per data test per run
  git history of main      commits that changed pipeline code (for lead time)

Definitions (all over a rolling window, default 30 days)
  Deployment           first successful run of a new commit on main.
  Lead time            commit authored -> that commit's deployment.
  Change failure rate  share of new commits whose first run failed for a real
                       (not injected) reason.
  Time to restore      first failed run of an incident -> next successful run.
  Daily delivery SLA   share of days with at least one successful run.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_PATHS = ["pipeline", "dbt", "metrics", "dashboard", "requirements.txt", ".github/workflows"]


def ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def pct(n: float, d: float) -> float | None:
    return round(100 * n / d, 1) if d else None


def median(xs: list[float]) -> float | None:
    return round(statistics.median(xs), 2) if xs else None


def p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))], 2)


# ---------------------------------------------------------------- git helpers
def git_commits() -> list[dict]:
    """Commits on the current branch that touched pipeline code."""
    try:
        out = subprocess.check_output(
            ["git", "log", "--format=%H|%aI|%s", "--", *CODE_PATHS],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    commits = []
    for line in out.splitlines():
        sha, authored, subject = line.split("|", 2)
        commits.append({"sha": sha, "authored_at": authored, "subject": subject})
    return commits


def is_ancestor(a: str, b: str) -> bool:
    if a == b:
        return True
    return subprocess.run(["git", "merge-base", "--is-ancestor", a, b], cwd=ROOT,
                          capture_output=True).returncode == 0


# ---------------------------------------------------------------- metrics
def compute(runs: list[dict], tests: list[dict], commits: list[dict], targets: dict,
            window_days: int, now: datetime, use_git: bool) -> dict:
    runs = sorted(runs, key=lambda r: r["started_at"])
    start = now - timedelta(days=window_days)
    win = [r for r in runs if ts(r["started_at"]) >= start]
    ok = [r for r in win if r["status"] == "success"]
    failed = [r for r in win if r["status"] != "success"]
    organic_failed = [r for r in failed if not r.get("fault_injected")]

    # Deployments: first successful run per commit sha.
    first_run_by_sha: dict[str, dict] = {}
    deploy_by_sha: dict[str, dict] = {}
    for r in runs:
        sha = r.get("commit_sha")
        if not sha:
            continue
        first_run_by_sha.setdefault(sha, r)
        if r["status"] == "success" and sha not in deploy_by_sha:
            deploy_by_sha[sha] = r
    deploys_in_window = [d for d in deploy_by_sha.values() if ts(d["finished_at"]) >= start]
    covered_days = max(7.0, min(window_days, (now - ts(runs[0]["started_at"])).total_seconds() / 86400)) if runs else 1.0
    deploy_freq = round(len(deploys_in_window) / (covered_days / 7), 2) if runs else None

    # Lead time: each commit deploys with the first successful run that contains it.
    deploy_list = sorted(deploy_by_sha.items(), key=lambda kv: kv[1]["finished_at"])
    lead_times, commit_rows = [], []
    for c in commits:
        authored = ts(c["authored_at"])
        deployed = None
        for sha, run in deploy_list:
            if ts(run["finished_at"]) < authored:
                continue
            if sha == c["sha"] or (use_git and is_ancestor(c["sha"], sha)):
                deployed = run
                break
        hours = round((ts(deployed["finished_at"]) - authored).total_seconds() / 3600, 2) if deployed else None
        if deployed and ts(deployed["finished_at"]) >= start:
            lead_times.append(hours)
        commit_rows.append({"sha": c["sha"][:7], "subject": c["subject"], "authored_at": c["authored_at"],
                            "deployed_at": deployed["finished_at"] if deployed else None,
                            "lead_time_hours": hours})

    # Change failure rate: new commits in window whose first run failed for a real reason.
    changes = [r for r in first_run_by_sha.values() if ts(r["started_at"]) >= start]
    change_failures = [r for r in changes if r["status"] != "success" and not r.get("fault_injected")]

    # Incidents: consecutive failed runs, closed by the next success.
    incidents, current = [], None
    for r in runs:
        if r["status"] != "success":
            if current is None:
                current = {"started_at": r["started_at"], "runs": [r]}
            else:
                current["runs"].append(r)
        elif current is not None:
            current["restored_at"] = r["finished_at"]
            incidents.append(current)
            current = None
    if current is not None:
        current["restored_at"] = None
        incidents.append(current)

    incident_rows = []
    for inc in incidents:
        first = inc["runs"][0]
        ttr = (round((ts(inc["restored_at"]) - ts(inc["started_at"])).total_seconds() / 3600, 2)
               if inc["restored_at"] else None)
        incident_rows.append({
            "started_at": inc["started_at"],
            "restored_at": inc["restored_at"],
            "time_to_restore_hours": ttr,
            "failed_runs": len(inc["runs"]),
            "stage": first.get("failed_stage"),
            "fault_injected": first.get("fault_injected"),
            "kind": "injected" if first.get("fault_injected") else "real",
            "error": first.get("error"),
            "run_id": first["run_id"],
            "stage_durations_s": first.get("stage_durations_s", {}),
            "trigger": first.get("trigger"),
        })
    win_incidents = [i for i in incident_rows if ts(i["started_at"]) >= start]
    ttrs = [i["time_to_restore_hours"] for i in win_incidents if i["time_to_restore_hours"] is not None]

    # Daily delivery SLA.
    first_day = max(start, ts(runs[0]["started_at"])).date() if runs else now.date()
    days = [(first_day + timedelta(days=i)) for i in range((now.date() - first_day).days + 1)]
    ok_days = {ts(r["finished_at"]).date() for r in ok}
    # Today only counts once it has had a chance to succeed.
    sla_days = [d for d in days if d < now.date() or d in ok_days]
    sla = pct(sum(d in ok_days for d in sla_days), len(sla_days))

    # Data tests.
    win_ids = {r["run_id"] for r in win}
    win_tests = [t for t in tests if t["run_id"] in win_ids]
    executed = [t for t in win_tests if t["status"] in ("pass", "fail", "error", "warn")]
    passed = [t for t in executed if t["status"] == "pass"]
    test_failures = Counter(t["test"] for t in executed if t["status"] in ("fail", "error"))
    contract_violations = sum(1 for r in failed if (r.get("error") or "").startswith("ContractViolation"))

    # Performance.
    durations = [r["duration_s"] for r in ok]
    stage_avgs = {}
    for stage in ("ingest", "load", "transform"):
        vals = [r["stage_durations_s"].get(stage) for r in ok if r["stage_durations_s"].get(stage) is not None]
        stage_avgs[stage] = round(statistics.mean(vals), 2) if vals else None

    # Daily series.
    by_day = defaultdict(lambda: {"runs": 0, "success": 0, "failed": 0, "injected": 0, "durations": [],
                                  "tests_pass": 0, "tests_exec": 0, "deploys": 0, "rows": 0})
    tests_by_run = defaultdict(list)
    for t in tests:
        tests_by_run[t["run_id"]].append(t)
    for r in win:
        d = by_day[ts(r["started_at"]).date().isoformat()]
        d["runs"] += 1
        d["rows"] += r.get("rows_ingested", 0)
        if r["status"] == "success":
            d["success"] += 1
            d["durations"].append(r["duration_s"])
        else:
            d["failed"] += 1
            d["injected"] += bool(r.get("fault_injected"))
        for t in tests_by_run[r["run_id"]]:
            if t["status"] in ("pass", "fail", "error", "warn"):
                d["tests_exec"] += 1
                d["tests_pass"] += t["status"] == "pass"
    for dep in deploys_in_window:
        by_day[ts(dep["finished_at"]).date().isoformat()]["deploys"] += 1
    daily = []
    for day in sorted(by_day):
        d = by_day[day]
        daily.append({"date": day, "runs": d["runs"], "success": d["success"], "failed": d["failed"],
                      "injected_failures": d["injected"], "deploys": d["deploys"], "rows_ingested": d["rows"],
                      "avg_duration_s": round(statistics.mean(d["durations"]), 2) if d["durations"] else None,
                      "test_pass_rate_pct": pct(d["tests_pass"], d["tests_exec"])})

    last_ok = ok[-1]["finished_at"] if ok else None
    values = {
        "deployment_frequency_per_week": deploy_freq,
        "lead_time_hours_median": median(lead_times),
        "change_failure_rate_pct": pct(len(change_failures), len(changes)),
        "time_to_restore_hours_median": median(ttrs),
        "run_success_rate_pct": pct(len(ok), len(ok) + len(organic_failed)),
        "daily_delivery_sla_pct": sla,
        "test_pass_rate_pct": pct(len(passed), len(executed)),
        "run_duration_p95_s": p95(durations),
    }

    scorecard = []
    for key, spec in targets.items():
        if key.startswith("_"):
            continue
        v, t = values.get(key), spec["target"]
        if v is None:
            status = "no_data"
        elif spec["better"] == "higher":
            status = "met" if v >= t else ("at_risk" if v >= t * 0.9 else "missed")
        else:
            status = "met" if v <= t else ("at_risk" if v <= t * 1.1 else "missed")
        scorecard.append({"key": key, "label": spec["label"], "unit": spec["unit"], "value": v,
                          "target": t, "better": spec["better"], "status": status})

    return {
        "generated_at": now.isoformat(),
        "repo_url": (f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}"
                     if os.environ.get("GITHUB_REPOSITORY") else None),
        "window_days": window_days,
        "demo": any(r.get("demo") for r in runs),
        "first_run_at": runs[0]["started_at"] if runs else None,
        "totals": {
            "runs": len(win), "successful_runs": len(ok), "failed_runs": len(failed),
            "real_failures": len(organic_failed), "injected_failures": len(failed) - len(organic_failed),
            "success_rate_including_injected_pct": pct(len(ok), len(win)),
            "deployments": len(deploys_in_window), "changes": len(changes),
            "change_failures": len(change_failures), "incidents": len(win_incidents),
            "open_incident": bool(incident_rows and incident_rows[-1]["restored_at"] is None),
            "hours_since_last_success": round((now - ts(last_ok)).total_seconds() / 3600, 2) if last_ok else None,
            "last_success_at": last_ok,
            "tests_executed": len(executed), "tests_failed": len(executed) - len(passed),
            "contract_violations": contract_violations,
            "rows_ingested": sum(r.get("rows_ingested", 0) for r in win),
            "pipeline_compute_minutes": round(sum(r["duration_s"] for r in win) / 60, 2),
            "median_duration_s": median(durations),
        },
        "scorecard": scorecard,
        "stage_avg_duration_s": stage_avgs,
        "failures_by_stage": dict(Counter(r.get("failed_stage") or "unknown" for r in failed)),
        "failures_by_fault": dict(Counter(r.get("fault_injected") or "real (not injected)" for r in failed)),
        "top_failing_tests": [{"test": k, "failures": v} for k, v in test_failures.most_common(8)],
        "daily": daily,
        "incidents": list(reversed(incident_rows))[:20],
        "recent_changes": commit_rows[:15],
        "recent_runs": [{k: r.get(k) for k in ("run_id", "started_at", "status", "trigger", "fault_injected",
                                                "failed_stage", "error", "duration_s", "stage_durations_s",
                                                "tests_passed", "tests_failed", "tests_skipped", "tests_total",
                                                "rows_ingested", "rows_loaded", "models_built")}
                        for r in reversed(runs[-25:])],
        "timeline": [{"t": r["started_at"], "s": r["status"], "f": r.get("fault_injected"),
                      "st": r.get("failed_stage"), "d": r.get("duration_s"), "tr": r.get("trigger")}
                     for r in win],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--out", default="dashboard/data/metrics.json")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--now", help="ISO timestamp to compute 'now' as (for reproducible builds)")
    args = ap.parse_args()

    state = Path(args.state_dir)
    runs = read_jsonl(state / "logs" / "runs.jsonl")
    tests = read_jsonl(state / "logs" / "tests.jsonl")
    targets = json.loads((ROOT / "metrics" / "targets.json").read_text())

    demo_commits = read_jsonl(state / "logs" / "commits.jsonl")
    use_git = not demo_commits
    commits = demo_commits or git_commits()

    now = ts(args.now) if args.now else datetime.now(timezone.utc)
    metrics = compute(runs, tests, commits, targets, args.window_days, now, use_git)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=1))
    print(f"Wrote {out} ({metrics['totals']['runs']} runs in window)")


if __name__ == "__main__":
    main()
