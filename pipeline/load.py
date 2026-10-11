"""Load: rebuild the raw layer of the DuckDB warehouse from raw files.

The warehouse is rebuilt on every run from the raw history, so it never drifts
from source. The current run's payloads (with any injected fault) are loaded on
top of history, after a data-contract check.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from pipeline.config import DAILY_FIELDS

COLUMNS = ["run_id", "ingested_at", "city", "latitude", "longitude", "date",
           "temp_max_c", "temp_min_c", "precipitation_mm", "wind_speed_max_kmh"]


class ContractViolation(Exception):
    """Source data no longer matches the agreed schema."""


def check_contract(payloads: list[dict]) -> None:
    for p in payloads:
        daily = p.get("daily", {})
        missing = [f for f in DAILY_FIELDS + ["time"] if f not in daily]
        unexpected = [f for f in daily if f not in DAILY_FIELDS + ["time"]]
        if missing or unexpected:
            raise ContractViolation(
                f"Schema contract violation for {p.get('city')}: "
                f"missing={missing} unexpected={unexpected}"
            )
        lengths = {len(daily[f]) for f in DAILY_FIELDS + ["time"]}
        if len(lengths) != 1:
            raise ContractViolation(f"Ragged arrays for {p.get('city')}: lengths={lengths}")


def to_rows(run_id: str, ingested_at: str, payloads: list[dict]) -> list[tuple]:
    rows = []
    for p in payloads:
        d = p["daily"]
        for i, day in enumerate(d["time"]):
            rows.append((run_id, ingested_at, p["city"], p["latitude"], p["longitude"], day,
                         d["temperature_2m_max"][i], d["temperature_2m_min"][i],
                         d["precipitation_sum"][i], d["wind_speed_10m_max"][i]))
    return rows


def load(db_path: Path, raw_dir: Path, current_run_id: str, current_payloads: list[dict],
         ingested_at: str) -> int:
    """Rebuild raw.weather_daily. Returns rows loaded for the current run."""
    check_contract(current_payloads)

    history = []
    for f in sorted(raw_dir.glob("*.json")):
        rec = json.loads(f.read_text())
        if rec["run_id"] == current_run_id:
            continue
        try:
            check_contract(rec["payloads"])
        except ContractViolation:
            continue  # quarantine historical files that break the contract
        history.extend(to_rows(rec["run_id"], rec["ingested_at"], rec["payloads"]))

    current = to_rows(current_run_id, ingested_at, current_payloads)

    if db_path.exists():
        db_path.unlink()
    con = duckdb.connect(str(db_path))
    con.execute("create schema raw")
    con.execute("""
        create table raw.weather_daily (
            run_id varchar, ingested_at timestamptz, city varchar,
            latitude double, longitude double, date date,
            temp_max_c double, temp_min_c double,
            precipitation_mm double, wind_speed_max_kmh double
        )
    """)
    if history or current:
        con.executemany(f"insert into raw.weather_daily values ({','.join('?' * len(COLUMNS))})",
                        history + current)
    con.close()
    return len(current)
