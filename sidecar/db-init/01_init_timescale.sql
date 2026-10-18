-- ============================================================================
-- Gods-Eye-View Sidecar Spatio-Temporal Database Schema
-- TimescaleDB (PostgreSQL 16) + PostGIS
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;
CREATE EXTENSION IF NOT EXISTS postgis CASCADE;

-- ----------------------------------------------------------------------------
-- 1. Entity Telemetry History (Hot-to-Cold Hypertable)
-- Stores 4D coordinate fixes for aircraft, vessels, satellites, fires, etc.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS telemetry_history (
    ts TIMESTAMPTZ NOT NULL,
    layer TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    h3_res5 BIGINT NOT NULL,
    h3_res9 BIGINT NOT NULL,
    geom GEOGRAPHY(PointZ, 4326),
    altitude_m REAL,
    speed_kts REAL,
    heading_deg REAL,
    payload JSONB
);

-- Convert to Timescale Hypertable partitioned by 1-day chunks
SELECT create_hypertable(
    'telemetry_history',
    'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- Spatio-temporal and entity index coverage
CREATE INDEX IF NOT EXISTS idx_telemetry_h3_ts 
    ON telemetry_history (h3_res5, ts DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_entity_ts 
    ON telemetry_history (entity_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_layer_ts 
    ON telemetry_history (layer, ts DESC);

CREATE INDEX IF NOT EXISTS idx_telemetry_geom 
    ON telemetry_history USING GIST (geom);

-- Columnar Compression: Compress chunks older than 24 hours
-- Segmented by layer and regional H3 resolution 5 hex cell
ALTER TABLE telemetry_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'layer, h3_res5',
    timescaledb.compress_orderby = 'ts DESC'
);

SELECT add_compression_policy('telemetry_history', INTERVAL '24 hours', if_not_exists => TRUE);

-- Retention policy (optional default 30 days)
SELECT add_retention_policy('telemetry_history', INTERVAL '30 days', if_not_exists => TRUE);

-- ----------------------------------------------------------------------------
-- 2. Weather & Environmental Grid History
-- Aggregated weather and atmospheric vectors indexed by H3 res 5
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS weather_grid_history (
    ts TIMESTAMPTZ NOT NULL,
    h3_res5 BIGINT NOT NULL,
    geom GEOGRAPHY(Point, 4326),
    temp_c REAL,
    wind_speed_kts REAL,
    wind_dir_deg REAL,
    precipitation_mm REAL,
    weather_code INTEGER,
    payload JSONB
);

SELECT create_hypertable(
    'weather_grid_history',
    'ts',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_weather_h3_ts 
    ON weather_grid_history (h3_res5, ts DESC);

ALTER TABLE weather_grid_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'h3_res5',
    timescaledb.compress_orderby = 'ts DESC'
);

SELECT add_compression_policy('weather_grid_history', INTERVAL '48 hours', if_not_exists => TRUE);

-- ----------------------------------------------------------------------------
-- 3. Orbital TLE Archive
-- Historical NORAD Two-Line Element sets for accurate past/future propagation
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tle_archive (
    norad_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    group_name TEXT NOT NULL,
    epoch_ts TIMESTAMPTZ NOT NULL,
    line1 TEXT NOT NULL,
    line2 TEXT NOT NULL,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (norad_id, epoch_ts)
);

CREATE INDEX IF NOT EXISTS idx_tle_norad_epoch 
    ON tle_archive (norad_id, epoch_ts DESC);

CREATE INDEX IF NOT EXISTS idx_tle_group 
    ON tle_archive (group_name, epoch_ts DESC);
