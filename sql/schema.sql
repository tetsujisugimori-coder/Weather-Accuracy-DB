PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS providers (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS forecast_areas (
    id INTEGER PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES providers(id),
    area_code TEXT NOT NULL,
    name TEXT NOT NULL,
    name_en TEXT,
    parent_area_id INTEGER REFERENCES forecast_areas(id),
    area_level TEXT NOT NULL CHECK (
        area_level IN ('prefecture', 'primary', 'grouped_municipality', 'municipality')
    ),
    valid_from TEXT,
    valid_to TEXT,
    source_url TEXT NOT NULL,
    master_verified_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (provider_id, area_code),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS observation_stations (
    id INTEGER PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES providers(id),
    station_code TEXT NOT NULL,
    name TEXT NOT NULL,
    name_en TEXT,
    station_type TEXT,
    element_flags TEXT,
    latitude REAL NOT NULL CHECK (latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    elevation REAL,
    active_from TEXT,
    active_to TEXT,
    source_url TEXT NOT NULL,
    master_verified_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (provider_id, station_code),
    CHECK (active_to IS NULL OR active_from IS NULL OR active_to > active_from)
);

CREATE TABLE IF NOT EXISTS station_area_memberships (
    id INTEGER PRIMARY KEY,
    station_id INTEGER NOT NULL REFERENCES observation_stations(id) ON DELETE CASCADE,
    forecast_area_id INTEGER NOT NULL REFERENCES forecast_areas(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL DEFAULT 'located_in' CHECK (relation_type IN ('located_in', 'verification_target')),
    valid_from TEXT,
    valid_to TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (station_id, forecast_area_id, relation_type),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS forecast_runs (
    id INTEGER PRIMARY KEY,
    provider_id INTEGER NOT NULL REFERENCES providers(id),
    fetched_at TEXT NOT NULL,
    issued_at TEXT,
    source_url TEXT NOT NULL,
    raw_file_path TEXT,
    content_sha256 TEXT,
    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (provider_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY,
    forecast_run_id INTEGER NOT NULL REFERENCES forecast_runs(id) ON DELETE CASCADE,
    forecast_area_id INTEGER NOT NULL REFERENCES forecast_areas(id),
    target_start TEXT NOT NULL,
    target_end TEXT NOT NULL,
    forecast_type TEXT NOT NULL,
    weather_code TEXT,
    weather_text TEXT,
    precipitation_probability INTEGER CHECK (precipitation_probability BETWEEN 0 AND 100),
    high_temperature REAL,
    low_temperature REAL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (forecast_run_id, forecast_area_id, target_start, target_end, forecast_type)
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY,
    station_id INTEGER NOT NULL REFERENCES observation_stations(id),
    observed_at TEXT NOT NULL,
    precipitation_mm REAL CHECK (precipitation_mm >= 0),
    temperature_c REAL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (station_id, observed_at)
);

CREATE TABLE IF NOT EXISTS master_imports (
    id INTEGER PRIMARY KEY,
    master_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    verified_at TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (master_name, verified_at)
);

CREATE INDEX IF NOT EXISTS idx_forecast_areas_parent ON forecast_areas(parent_area_id);
CREATE INDEX IF NOT EXISTS idx_station_memberships_area ON station_area_memberships(forecast_area_id);
CREATE INDEX IF NOT EXISTS idx_forecast_runs_issued_at ON forecast_runs(issued_at);
CREATE INDEX IF NOT EXISTS idx_forecast_runs_fetched_at ON forecast_runs(fetched_at);
CREATE INDEX IF NOT EXISTS idx_forecasts_target_start ON forecasts(target_start);
CREATE INDEX IF NOT EXISTS idx_forecasts_area ON forecasts(forecast_area_id);
CREATE INDEX IF NOT EXISTS idx_observations_observed_at ON observations(observed_at);
