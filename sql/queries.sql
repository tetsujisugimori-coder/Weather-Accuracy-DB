-- Phase 1: master/status queries. Forecast accuracy queries are intentionally
-- deferred until the corresponding Phase 2-5 import and analysis features exist.

-- SELECT / WHERE / ORDER BY: active Kanagawa observation stations.
SELECT station_code, name, latitude, longitude, elevation
FROM observation_stations
WHERE active_to IS NULL
ORDER BY station_code;

-- JOIN: area hierarchy.
SELECT child.area_code,
       child.name,
       child.area_level,
       parent.name AS parent_name
FROM forecast_areas AS child
LEFT JOIN forecast_areas AS parent
  ON parent.id = child.parent_area_id
 AND parent.valid_to IS NULL
WHERE child.valid_to IS NULL
ORDER BY child.area_code;

-- GROUP BY / COUNT: number of areas per hierarchy level.
SELECT area_level, COUNT(*) AS area_count
FROM forecast_areas
WHERE valid_to IS NULL
GROUP BY area_level
ORDER BY CASE area_level
  WHEN 'prefecture' THEN 1
  WHEN 'primary' THEN 2
  WHEN 'grouped_municipality' THEN 3
  ELSE 4
END;

-- JOIN: observation stations located in each grouped municipality area.
SELECT area.name AS grouped_area,
       COUNT(station.id) AS station_count
FROM forecast_areas AS area
LEFT JOIN station_area_memberships AS membership
  ON membership.forecast_area_id = area.id
 AND membership.relation_type = 'located_in'
 AND membership.valid_to IS NULL
LEFT JOIN observation_stations AS station
  ON station.id = membership.station_id
 AND station.active_to IS NULL
WHERE area.area_level = 'grouped_municipality'
  AND area.valid_to IS NULL
GROUP BY area.id, area.name
ORDER BY area.area_code;

-- MIN / MAX / AVG: Phase 1 example using station elevations.
SELECT MIN(elevation) AS minimum_elevation,
       MAX(elevation) AS maximum_elevation,
       AVG(elevation) AS average_elevation
FROM observation_stations;

-- Date condition example (UTC text in lexicographically sortable ISO 8601 form).
SELECT *
FROM observations
WHERE observed_at >= '2026-08-01T00:00:00Z'
  AND observed_at <  '2026-09-01T00:00:00Z'
ORDER BY observed_at;

-- Representative index check. The UNIQUE(station_id, observed_at) constraint
-- creates sqlite_autoindex_observations_1, so no duplicate station-only index is needed.
EXPLAIN QUERY PLAN
SELECT observed_at, precipitation_mm, temperature_c
FROM observations
WHERE station_id = 1
ORDER BY observed_at;
-- Phase 2: run history with separate issue/fetch times and document kinds.
SELECT id, document_type, issued_at, fetched_at, status,
       raw_file_path, raw_file_sha256, document_sha256
FROM forecast_runs
ORDER BY issued_at DESC, id DESC;

-- Phase 2: preserve the original area/station target distinction.
SELECT
    run.document_type,
    run.issued_at,
    forecast.target_start,
    forecast.target_end,
    forecast.forecast_type,
    area.area_code,
    station.station_code,
    forecast.weather_code,
    forecast.precipitation_probability,
    forecast.high_temperature,
    forecast.low_temperature
FROM forecasts AS forecast
JOIN forecast_runs AS run ON run.id = forecast.forecast_run_id
LEFT JOIN forecast_areas AS area ON area.id = forecast.forecast_area_id
LEFT JOIN observation_stations AS station ON station.id = forecast.station_id
ORDER BY forecast.target_start, run.issued_at, forecast.id;

-- idx_forecasts_run must support a run-only lookup; the area/station natural
-- key indexes are partial and cannot satisfy this predicate by themselves.
EXPLAIN QUERY PLAN
SELECT *
FROM forecasts
WHERE forecast_run_id = 1;
