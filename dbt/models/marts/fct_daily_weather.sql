select
    city,
    date,
    temp_max_c,
    temp_min_c,
    round(temp_max_c - temp_min_c, 1)       as temp_range_c,
    round((temp_max_c + temp_min_c) / 2, 1) as temp_mean_c,
    precipitation_mm,
    precipitation_mm >= 1.0                 as is_wet_day,
    wind_speed_max_kmh
from {{ ref('stg_weather_daily') }}
