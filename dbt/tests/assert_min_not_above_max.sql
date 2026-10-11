-- Daily minimum temperature can never exceed the daily maximum.
select city, date, temp_min_c, temp_max_c
from {{ ref('stg_weather_daily') }}
where temp_min_c > temp_max_c
