select
    city,
    date_trunc('month', date)::date        as month,
    count(*)                               as days_observed,
    round(avg(temp_mean_c), 1)             as avg_temp_c,
    max(temp_max_c)                        as max_temp_c,
    min(temp_min_c)                        as min_temp_c,
    round(sum(precipitation_mm), 1)        as total_precip_mm,
    sum(case when is_wet_day then 1 else 0 end) as wet_days
from {{ ref('fct_daily_weather') }}
group by 1, 2
