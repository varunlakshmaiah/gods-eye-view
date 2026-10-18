"""
Seed permanent static infrastructure into TimescaleDB PostGIS table static_infrastructure.
Ingests bundled global datasets:
- Datacenters (OpenStreetMap)
- Hydroelectric Dams (OpenStreetMap / Open Infrastructure Map)
- Submarine Cables (TeleGeography)
- Cable Landing Stations (TeleGeography)
- Military Installations (OpenStreetMap)
"""
import os
import sys
import json
import asyncio
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@timescaledb:5432/gods_eye_sidecar")
BASE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")


async def seed():
    print(f"[SeedInfra] Connecting to {DATABASE_URL}...")
    conn = await asyncpg.connect(DATABASE_URL)

    # Ensure table exists
    await conn.execute("""
    CREATE TABLE IF NOT EXISTS static_infrastructure (
        id TEXT PRIMARY KEY,
        category TEXT NOT NULL,
        name TEXT NOT NULL,
        country_code VARCHAR(3),
        geom GEOGRAPHY(Geometry, 4326) NOT NULL,
        h3_res7 BIGINT,
        operator TEXT,
        capacity TEXT,
        metadata JSONB DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        updated_at TIMESTAMPTZ DEFAULT NOW()
    );

    CREATE INDEX IF NOT EXISTS idx_static_infra_geom 
        ON static_infrastructure USING GIST (geom);

    CREATE INDEX IF NOT EXISTS idx_static_infra_cat 
        ON static_infrastructure (category);

    CREATE INDEX IF NOT EXISTS idx_static_infra_cat_name 
        ON static_infrastructure (category, name);
    """)

    # 1. Ingest Datacenters
    dc_file = os.path.join(BASE_DIR, "src/data/local_data/datacenters/datacenters.geojsonl")
    if os.path.exists(dc_file):
        print("[SeedInfra] Ingesting datacenters...")
        batch = []
        with open(dc_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.trim() if hasattr(line, "trim") else line.strip()
                if not line:
                    continue
                feat = json.loads(line)
                f_id = f"dc_{feat.get('id')}"
                props = feat.get("properties", {})
                tags = props.get("tags", {})
                name = tags.get("name") or props.get("name") or f"Datacenter {feat.get('id')}"
                operator = tags.get("operator") or props.get("operator")
                capacity = tags.get("capacity") or tags.get("servers")
                geom_str = json.dumps(feat.get("geometry"))
                meta_str = json.dumps(props)
                batch.append((f_id, "datacenter", name, operator, capacity, geom_str, meta_str))

        if batch:
            await conn.executemany("""
                INSERT INTO static_infrastructure (id, category, name, operator, capacity, geom, metadata)
                VALUES ($1, $2, $3, $4, $5, ST_SetSRID(ST_GeomFromGeoJSON($6), 4326)::geography, $7::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    operator = EXCLUDED.operator,
                    capacity = EXCLUDED.capacity,
                    geom = EXCLUDED.geom,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW();
            """, batch)
            print(f"[SeedInfra] Ingested {len(batch)} datacenters.")

    # 2. Ingest Dams
    dams_file = os.path.join(BASE_DIR, "src/data/local_data/dams/dams.geojsonl")
    if os.path.exists(dams_file):
        print("[SeedInfra] Ingesting dams...")
        batch = []
        with open(dams_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                feat = json.loads(line)
                raw_id = feat.get("id")
                f_id = f"dam_{abs(raw_id) if raw_id is not None else len(batch)}"
                props = feat.get("properties", {})
                tags = props.get("tags", {})
                name = tags.get("name") or props.get("name") or f"Dam {raw_id}"
                operator = tags.get("operator") or props.get("operator")
                capacity = props.get("output") or tags.get("plant:output:electricity")
                geom_str = json.dumps(feat.get("geometry"))
                meta_str = json.dumps(props)
                batch.append((f_id, "dam", name, operator, capacity, geom_str, meta_str))

        if batch:
            await conn.executemany("""
                INSERT INTO static_infrastructure (id, category, name, operator, capacity, geom, metadata)
                VALUES ($1, $2, $3, $4, $5, ST_SetSRID(ST_GeomFromGeoJSON($6), 4326)::geography, $7::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    operator = EXCLUDED.operator,
                    capacity = EXCLUDED.capacity,
                    geom = EXCLUDED.geom,
                    metadata = EXCLUDED.metadata,
                    updated_at = NOW();
            """, batch)
            print(f"[SeedInfra] Ingested {len(batch)} dams.")

    # 3. Ingest Submarine Cables
    cables_file = os.path.join(BASE_DIR, "src/data/local_data/telegeography_submarine_cables/cable-geo.json")
    if os.path.exists(cables_file):
        print("[SeedInfra] Ingesting submarine cables...")
        with open(cables_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            features = data.get("features", [])
            batch = []
            for feat in features:
                props = feat.get("properties", {})
                cable_id = props.get("id") or props.get("feature_id")
                if not cable_id:
                    continue
                f_id = f"cable_{cable_id}"
                name = props.get("name") or "Submarine Cable"
                geom_str = json.dumps(feat.get("geometry"))
                meta_str = json.dumps(props)
                batch.append((f_id, "submarine_cable", name, None, None, geom_str, meta_str))

            if batch:
                await conn.executemany("""
                    INSERT INTO static_infrastructure (id, category, name, operator, capacity, geom, metadata)
                    VALUES ($1, $2, $3, $4, $5, ST_SetSRID(ST_GeomFromGeoJSON($6), 4326)::geography, $7::jsonb)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        geom = EXCLUDED.geom,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW();
                """, batch)
                print(f"[SeedInfra] Ingested {len(batch)} submarine cables.")

    # 4. Ingest Cable Landing Points
    landing_file = os.path.join(BASE_DIR, "src/data/local_data/telegeography_submarine_cables/landing-point-geo.json")
    if os.path.exists(landing_file):
        print("[SeedInfra] Ingesting cable landing points...")
        with open(landing_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            features = data.get("features", [])
            batch = []
            for feat in features:
                props = feat.get("properties", {})
                lp_id = props.get("id")
                if not lp_id:
                    continue
                f_id = f"landing_{lp_id}"
                name = props.get("name") or "Cable Landing Station"
                country = props.get("country")
                geom_str = json.dumps(feat.get("geometry"))
                meta_str = json.dumps(props)
                batch.append((f_id, "cable_landing_point", name, country, geom_str, meta_str))

            if batch:
                await conn.executemany("""
                    INSERT INTO static_infrastructure (id, category, name, country_code, geom, metadata)
                    VALUES ($1, $2, $3, $4, ST_SetSRID(ST_GeomFromGeoJSON($5), 4326)::geography, $6::jsonb)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        country_code = EXCLUDED.country_code,
                        geom = EXCLUDED.geom,
                        metadata = EXCLUDED.metadata,
                        updated_at = NOW();
                """, batch)
                print(f"[SeedInfra] Ingested {len(batch)} cable landing points.")

    # 5. Ingest Military Installations
    military_file = os.path.join(BASE_DIR, "src/data/local_data/osm_military_names/names.json")
    if os.path.exists(military_file):
        print("[SeedInfra] Ingesting military installations...")
        with open(military_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            rows = data.get("records", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
            classes = data.get("classes", []) if isinstance(data, dict) else []
            # rows format: [id, name, lon, lat, minLon, minLat, maxLon, maxLat, type_idx, area]
            batch = []
            for row in rows:
                if len(row) < 4:
                    continue
                m_id = f"mil_{row[0]}"
                name = row[1]
                lon = float(row[2])
                lat = float(row[3])
                type_idx = row[8] if len(row) > 8 else None
                class_name = classes[type_idx] if (type_idx is not None and type_idx < len(classes)) else None
                meta = {
                    "type_code": type_idx,
                    "class": class_name,
                    "area_m2": row[9] if len(row) > 9 else None,
                    "bbox": [row[4], row[5], row[6], row[7]] if len(row) > 7 else None
                }
                meta_str = json.dumps(meta)
                batch.append((m_id, "military_installation", name, lon, lat, meta_str))

            if batch:
                # Chunk inserts into batches of 2000
                chunk_size = 2000
                total = len(batch)
                for i in range(0, total, chunk_size):
                    chunk = batch[i:i + chunk_size]
                    await conn.executemany("""
                        INSERT INTO static_infrastructure (id, category, name, geom, metadata)
                        VALUES ($1, $2, $3, ST_SetSRID(ST_MakePoint($4, $5), 4326)::geography, $6::jsonb)
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            geom = EXCLUDED.geom,
                            metadata = EXCLUDED.metadata,
                            updated_at = NOW();
                    """, chunk)
                print(f"[SeedInfra] Ingested {total} military installations.")

    # Final summary count
    total_count = await conn.fetchval("SELECT COUNT(*) FROM static_infrastructure")
    breakdown = await conn.fetch("SELECT category, COUNT(*) FROM static_infrastructure GROUP BY category ORDER BY count DESC")
    print("\n=======================================================")
    print(f"  [SeedInfra] SUCCESS! Total Permanent Assets: {total_count}")
    print("=======================================================")
    for row in breakdown:
        print(f"  - {row['category']}: {row['count']:,} assets")
    print("=======================================================\n")

    await conn.close()


if __name__ == "__main__":
    asyncio.run(seed())
