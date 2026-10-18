-- ============================================================================
-- Gods-Eye-View Sidecar Permanent Infrastructure Schema
-- PostGIS Static Geobase (Datacenters, Dams, Submarine Cables, Military, CCTV)
-- Never expired, permanent spatial assets
-- ============================================================================

CREATE TABLE IF NOT EXISTS static_infrastructure (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,                     -- 'datacenter', 'dam', 'military_installation', 'submarine_cable', 'cable_landing_point', etc.
    name TEXT NOT NULL,
    country_code VARCHAR(3),
    geom GEOGRAPHY(Geometry, 4326) NOT NULL,    -- Accepts Point, MultiPoint, LineString, MultiLineString, Polygon, MultiPolygon
    h3_res7 BIGINT,                             -- Regional H3 index
    operator TEXT,
    capacity TEXT,                              -- Capacity (MW, servers, power)
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Ultra-fast spatial index for radius / bbox / containment queries
CREATE INDEX IF NOT EXISTS idx_static_infra_geom 
    ON static_infrastructure USING GIST (geom);

-- Composite category and name indexes for fast filtering
CREATE INDEX IF NOT EXISTS idx_static_infra_cat 
    ON static_infrastructure (category);

CREATE INDEX IF NOT EXISTS idx_static_infra_cat_name 
    ON static_infrastructure (category, name);

CREATE INDEX IF NOT EXISTS idx_static_infra_h3 
    ON static_infrastructure (h3_res7);
