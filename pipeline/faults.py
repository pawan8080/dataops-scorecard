"""Deliberate, labelled fault injection.

Faults are applied in memory to the current run only. Raw files on disk always
hold what the source actually sent, so the next clean run recovers on its own.
Every injected fault is recorded in the run log, so injected failures are never
mistaken for real ones in the metrics.
"""

from __future__ import annotations

import copy
import random

from pipeline.config import FAULTS


class InjectedTimeout(TimeoutError):
    """Simulates the upstream API not responding."""


def choose_fault(requested: str | None, trigger: str, chaos_rate: float, run_id: str) -> str | None:
    if requested and requested != "none":
        if requested not in FAULTS:
            raise ValueError(f"Unknown fault '{requested}'. Options: {FAULTS}")
        return requested
    # Chaos only on scheduled runs, so pushes measure real change failures.
    if trigger == "schedule" and chaos_rate > 0:
        rng = random.Random(run_id)
        if rng.random() < chaos_rate:
            return rng.choice(FAULTS)
    return None


def before_extract(fault: str | None) -> None:
    if fault == "api_timeout":
        raise InjectedTimeout("Injected fault: Open-Meteo request timed out after 30s")


def apply(fault: str | None, payloads: list[dict]) -> list[dict]:
    """Return payloads as the loader will see them, with any fault applied."""
    if fault not in {"schema_change", "bad_values", "late_data"}:
        return payloads
    out = copy.deepcopy(payloads)
    for p in out:
        daily = p["daily"]
        if fault == "schema_change":
            # Upstream renames a field without notice.
            daily["temp_max_c"] = daily.pop("temperature_2m_max")
        elif fault == "bad_values":
            # A sensor glitch sends impossible readings for the latest day.
            daily["temperature_2m_max"][-1] = 999.0
        elif fault == "late_data":
            # Source is behind: the newest days never arrive.
            for key in daily:
                daily[key] = daily[key][:-3]
    return out
