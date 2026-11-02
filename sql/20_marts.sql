-- Marts: small tables the dashboard reads. Rebuilt in full on every run.
-- $as_of is the run date in Seattle; "complete" days are those before it.
-- Test records are excluded everywhere.

CREATE OR REPLACE TABLE mart_daily AS
WITH days AS (
    SELECT unnest(generate_series((SELECT min(call_date) FROM stg_calls),
                                  $as_of::DATE - 1, INTERVAL 1 DAY))::DATE AS call_date
),
counts AS (
    SELECT call_date, count(*) AS calls,
           count(*) FILTER (WHERE category = 'Medical') AS medical,
           count(*) FILTER (WHERE category = 'Fire')    AS fire
    FROM stg_calls WHERE NOT is_test AND call_date < $as_of::DATE
    GROUP BY call_date
)
SELECT d.call_date,
       coalesce(c.calls, 0)   AS calls,
       coalesce(c.medical, 0) AS medical,
       coalesce(c.fire, 0)    AS fire,
       avg(coalesce(c.calls, 0)) OVER (ORDER BY d.call_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
                              AS calls_7d_avg
FROM days d LEFT JOIN counts c USING (call_date)
ORDER BY d.call_date;

CREATE OR REPLACE TABLE mart_monthly AS
SELECT date_trunc('month', call_date)::DATE AS month,
       sum(calls)                           AS calls,
       count(*)                             AS days_in_data,
       count(*) = day(last_day(min(call_date))) AS complete_month
FROM mart_daily
GROUP BY 1 ORDER BY 1;

-- Last 30 complete days vs the same 30 days one year earlier, by category and by type.
CREATE OR REPLACE TABLE mart_category_30d AS
SELECT category,
       count(*) FILTER (WHERE call_date >= $as_of::DATE - 30)                         AS calls_30d,
       count(*) FILTER (WHERE call_date >= $as_of::DATE - 30 - 365
                          AND call_date <  $as_of::DATE - 365)                        AS calls_30d_last_year
FROM stg_calls
WHERE NOT is_test AND call_date < $as_of::DATE
GROUP BY category ORDER BY calls_30d DESC;

CREATE OR REPLACE TABLE mart_type_30d AS
SELECT call_type, any_value(category) AS category, count(*) AS calls_30d
FROM stg_calls
WHERE NOT is_test AND call_date >= $as_of::DATE - 30 AND call_date < $as_of::DATE
GROUP BY call_type ORDER BY calls_30d DESC;

-- Average calls per hour, by weekday and hour, over the last 52 complete weeks.
CREATE OR REPLACE TABLE mart_hour_dow AS
WITH win AS (
    SELECT * FROM stg_calls
    WHERE NOT is_test AND call_date >= $as_of::DATE - 364 AND call_date < $as_of::DATE
)
SELECT call_dow, call_hour, count(*) / 52.0 AS avg_calls
FROM win GROUP BY ALL ORDER BY ALL;

-- Points for the map: last 7 complete days, inside the Seattle bounding box.
CREATE OR REPLACE TABLE mart_recent_points AS
SELECT incident_number, call_ts, call_type, category, latitude, longitude
FROM stg_calls
WHERE NOT is_test AND in_seattle_bbox
  AND call_date >= $as_of::DATE - 7 AND call_date < $as_of::DATE;

CREATE OR REPLACE TABLE mart_kpis AS
SELECT
    $as_of::DATE - 1                                                            AS last_complete_day,
    (SELECT calls FROM mart_daily WHERE call_date = $as_of::DATE - 1)           AS calls_yesterday,
    (SELECT avg(calls) FROM mart_daily WHERE call_date >= $as_of::DATE - 7)     AS avg_last_7d,
    (SELECT avg(calls) FROM mart_daily
      WHERE call_date >= $as_of::DATE - 7 - 364 AND call_date < $as_of::DATE - 364)
                                                                                AS avg_same_7d_last_year,
    (SELECT sum(calls) FROM mart_daily
      WHERE call_date >= date_trunc('year', $as_of::DATE - 1))                  AS ytd_calls,
    (SELECT sum(calls) FROM mart_daily
      WHERE call_date >= date_trunc('year', $as_of::DATE - 1) - INTERVAL 1 YEAR
        AND call_date <  $as_of::DATE - INTERVAL 1 YEAR)                        AS ytd_calls_last_year,
    (SELECT count(*) FROM stg_calls WHERE NOT is_test)                          AS total_calls,
    (SELECT min(call_date) FROM stg_calls)                                      AS first_day;
