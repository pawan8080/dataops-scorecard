-- Freshness: every city in the most recent run must include data up to yesterday (UTC).
with latest_run as (
    select run_id
    from {{ source('raw', 'weather_daily') }}
    order by ingested_at desc
    limit 1
)
select city, max(date) as latest_date
from {{ source('raw', 'weather_daily') }}
where run_id = (select run_id from latest_run)
group by city
having max(date) < current_date - interval 1 day
