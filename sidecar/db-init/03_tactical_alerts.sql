-- ============================================================================
-- Gods-Eye-View Sidecar Tactical Alerts Schema
-- Records autonomous tripwire breaches, emergency squawks, and anomalies
-- ============================================================================

CREATE TABLE IF NOT EXISTS tactical_alerts (
    id TEXT PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    severity TEXT NOT NULL,         -- 'CRITICAL', 'WARNING', 'INFO'
    category TEXT NOT NULL,         -- 'SQUAWK_EMERGENCY', 'AOI_INCURSION', 'DISASTER_EARTHQUAKE', 'DISASTER_FIRE', 'CABLE_THREAT'
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    aoi_id TEXT,
    entity_id TEXT,
    lat REAL,
    lon REAL,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_alerts_ts ON tactical_alerts (ts DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_category ON tactical_alerts (category);
CREATE INDEX IF NOT EXISTS idx_alerts_severity ON tactical_alerts (severity);
