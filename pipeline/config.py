"""Static configuration for the pipeline."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DBT_DIR = ROOT / "dbt"

# Cities pulled from Open-Meteo on every run.
CITIES = [
    {"city": "Calgary", "latitude": 51.05, "longitude": -114.07},
    {"city": "Vancouver", "latitude": 49.28, "longitude": -123.12},
    {"city": "Toronto", "latitude": 43.65, "longitude": -79.38},
    {"city": "New York", "latitude": 40.71, "longitude": -74.01},
    {"city": "London", "latitude": 51.51, "longitude": -0.13},
    {"city": "Tokyo", "latitude": 35.68, "longitude": 139.69},
]

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Daily variables requested from the API. This list is also the data contract:
# if the source stops sending one of these fields, the load stage fails loudly.
DAILY_FIELDS = [
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "wind_speed_10m_max",
]

PAST_DAYS = 3          # re-pull recent days so late corrections are picked up
REQUEST_TIMEOUT_S = 30

# Faults the pipeline can inject on purpose to exercise detection and recovery.
FAULTS = ["schema_change", "bad_values", "late_data", "api_timeout"]
