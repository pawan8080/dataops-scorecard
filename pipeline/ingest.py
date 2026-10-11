"""Extract: pull daily weather for each city from Open-Meteo."""

from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from pipeline.config import CITIES, DAILY_FIELDS, OPEN_METEO_URL, PAST_DAYS, REQUEST_TIMEOUT_S


def fetch_city(city: dict) -> dict:
    params = {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "daily": ",".join(DAILY_FIELDS),
        "past_days": PAST_DAYS,
        "forecast_days": 1,
        "timezone": "UTC",
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT_S)
    resp.raise_for_status()
    payload = resp.json()
    payload["city"] = city["city"]
    return payload


def offline_city(city: dict, today: date, seed: int) -> dict:
    """Payload with the same shape as Open-Meteo, for running without network.

    Used by local tests and the demo seed only; scheduled runs always hit the API.
    """
    rng = random.Random(f"{city['city']}-{seed}")
    days = [today - timedelta(days=i) for i in range(PAST_DAYS, -1, -1)]
    base = 15 - abs(city["latitude"] - 35) * 0.4
    season = 8 * math.cos((today.timetuple().tm_yday - 200) / 365 * 2 * math.pi)
    tmax = [round(base + season + rng.uniform(-4, 4), 1) for _ in days]
    return {
        "latitude": city["latitude"],
        "longitude": city["longitude"],
        "city": city["city"],
        "daily_units": {f: "" for f in DAILY_FIELDS},
        "daily": {
            "time": [d.isoformat() for d in days],
            "temperature_2m_max": tmax,
            "temperature_2m_min": [round(t - rng.uniform(4, 11), 1) for t in tmax],
            "precipitation_sum": [round(max(0.0, rng.gauss(1.5, 3)), 1) for _ in days],
            "wind_speed_10m_max": [round(rng.uniform(5, 40), 1) for _ in days],
        },
    }


def extract(run_id: str, raw_dir: Path, offline: bool = False, today: date | None = None) -> tuple[Path, list[dict]]:
    """Fetch every city and save the untouched responses as one raw file."""
    today = today or datetime.now(timezone.utc).date()
    if offline:
        payloads = [offline_city(c, today, seed=hash(run_id) % 10_000) for c in CITIES]
    else:
        payloads = [fetch_city(c) for c in CITIES]

    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{run_id}.json"
    record = {
        "run_id": run_id,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "source": "offline_fixture" if offline else "open-meteo",
        "payloads": payloads,
    }
    path.write_text(json.dumps(record))
    return path, payloads
