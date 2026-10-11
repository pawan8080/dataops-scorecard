-- Latest observation per city and date. Re-pulled days overwrite earlier versions.
with ranked as (
    select
        *,
        row_number() over (partition by city, date order by ingested_at desc) as rn
    from {{ source('raw', 'weather_daily') }}
)
select
    city,
    latitude,
    longitude,
    date,
    cast(temp_max_c as double)          as temp_max_c,
    cast(temp_min_c as double)          as temp_min_c,
    cast(precipitation_mm as double)    as precipitation_mm,
    cast(wind_speed_max_kmh as double)  as wind_speed_max_kmh,
    run_id                              as source_run_id,
    ingested_at
from ranked
where rn = 1
