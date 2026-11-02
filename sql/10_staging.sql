-- Staging: one clean, typed row per call, with a category and quality flags.
-- A view, so it always reflects the latest raw data.
CREATE OR REPLACE VIEW stg_calls AS
WITH base AS (
    SELECT
        incident_number,
        call_ts,
        call_ts::DATE                         AS call_date,
        hour(call_ts)                         AS call_hour,
        isodow(call_ts)                       AS call_dow,          -- 1 = Monday
        trim(type)                            AS call_type,
        lower(trim(type))                     AS t,
        trim(address)                         AS address,
        latitude,
        longitude
    FROM calls_raw
)
SELECT
    incident_number, call_ts, call_date, call_hour, call_dow, call_type, address, latitude, longitude,
    -- Rules are applied in order; first match wins. "Auto Fire Alarm" must be an Alarm,
    -- not a Fire, and "MVI Medic" a Traffic call, so those rules come first.
    CASE
        WHEN regexp_matches(t, 'alarm|detector|^afa')                              THEN 'Alarm'
        WHEN regexp_matches(t, '^mvi|motor vehicle')                               THEN 'Traffic'
        WHEN regexp_matches(t, 'fire|burn|smoke')                                  THEN 'Fire'
        WHEN regexp_matches(t, 'rescue|extrication')                               THEN 'Rescue'
        WHEN regexp_matches(t, 'gas|odor|wires|electrical|spill|hazmat|fuel|water job|tran?sformer')
                                                                                   THEN 'Hazard'
        WHEN regexp_matches(t, 'aid|medic|overdose|nurseline|amr|acuity|triaged|medical|crisis|poison|violence')
                                                                                   THEN 'Medical'
        ELSE 'Other'
    END                                                       AS category,
    regexp_matches(t, '^test\b|mis test')                     AS is_test,
    latitude IS NOT NULL AND longitude IS NOT NULL            AS has_coords,
    latitude  BETWEEN 47.48 AND 47.74
        AND longitude BETWEEN -122.46 AND -122.22             AS in_seattle_bbox
FROM base;
