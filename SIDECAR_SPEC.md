# Architectural Specification: `gods-eye-view` Headless Spatio-Temporal Sidecar

## 1. Prime Directive (Upstream Isolation Contract)
This repository is an active fork of `bilawalsidhu/gods-eye-view`. 
To guarantee zero merge conflicts when pulling `upstream/main`:
1. **DO NOT MODIFY** any existing files tracked by the upstream repository (`src/`, `server/`, `package.json`, `vite.config.*`, `Dockerfile`, or `docker-compose.yml`).
2. **ALL NEW CODE** must reside inside a new root-level directory: `/sidecar/`.
3. **ALL ORCHESTRATION** must reside in `/docker-compose.sidecar.yml` and `/sidecar/.env.sidecar`.
4. If the visual frontend requires a WebSocket bridge for Co-Pilot camera control, implement it as an external script injected dynamically by the reverse proxy (`sub_filter` / HTML body injection) OR as a single-line optional import documented in `sidecar/README.md`.

---

## 2. System Objective
Extend `gods-eye-view` from an ephemeral browser-only 3D visualizer into a **24/7 Headless Spatio-Temporal World Engine + Visual Co-Pilot**:
- **Single Internet Footprint:** Reuse `gods-eye-view`'s existing local proxy server and rate-limit/quota governors. Never duplicate external API calls.
- **Live Spatial State (Hot Tier):** Mirror all live telemetry into **Tile38** for sub-5ms radius (`NEARBY`), bounding-box (`WITHIN`), and geofence queries.
- **Time-Travel Archive (Cold Tier):** Mirror all timestamped telemetry into **TimescaleDB (PostgreSQL 16 + PostGIS + H3)** for historical playback and point-in-time queries.
- **Headless Orbital Math:** Run a background **SGP4 propagator** reading `gods-eye-view`'s cached CelesTrak TLE files so satellite positions and passes can be queried headlessly at any past, present, or future timestamp.
- **Dual-Mode MCP Gateway:** Expose a Model Context Protocol (MCP) + REST API server for custom AI assistants that works **24/7 when the browser is closed** and **synchronizes with the 3D globe camera/layers when the browser is open**.

---

## 3. Target Directory Structure

```text
gods-eye-view/
├── (All original upstream files remain 100% untouched)
├── docker-compose.sidecar.yml       # Master compose stack (Upstream + Sidecars)
├── SIDECAR_SPEC.md                  # This architectural blueprint
└── sidecar/
    ├── .env.sidecar.example         # Environment variables & intervals
    ├── README.md                    # Setup, Upstream Sync SOP, and API docs
    ├── UPSTREAM_MAP.md              # Auto-generated discovery map from Phase 1
    ├── db-init/
    │   └── 01_init_timescale.sql    # PostGIS, Hypertables, Compression policies
    ├── proxy-tee/                   # Service 1: Non-Invasive Reverse Proxy & Tap
    │   ├── Dockerfile
    │   ├── package.json
    │   └── src/
    │       ├── index.ts             # Fastify/Express proxy intercepting upstream API
    │       ├── normalizer.ts        # Converts upstream JSON into canonical 4D records
    │       ├── sinks/
    │       │   ├── tile38.ts        # Updates live spatial state with TTL
    │       │   └── timescale.ts     # Batch-inserts historical telemetry
    │       ├── playback.ts          # Switches upstream feed between LIVE and DB REPLAY
    │       └── heartbeat.ts         # Background poller when browser UI is closed
    ├── mcp-gateway/                 # Service 2: Headless AI Agent & MCP Server
    │   ├── Dockerfile
    │   ├── pyproject.toml           # FastAPI + FastMCP + sgp4 + h3 + asyncpg + redis
    │   └── src/
    │       ├── main.py              # MCP Server (SSE/stdio) + REST API + WebSocket Hub
    │       ├── router.py            # Temporal router (Now -> Tile38, Past -> TimescaleDB)
    │       ├── sgp4_engine.py       # Headless satellite TLE propagator & pass predictor
    │       ├── geocoder.py          # Local place-name to coordinate resolution
    │       ├── ws_copilot.py        # Bi-directional WebSocket hub for active browser UI
    │       └── tools/               # Deterministic spatial tools for AI Assistant
    │           ├── scene_roster.py  # 250km Contact Roster & bearing/distance math
    │           ├── entity_track.py  # Two-hop entity location & historical trail
    │           ├── environment.py   # Weather & seismic state at (lat, lon, time)
    │           └── ui_control.py    # Camera flyTo, layer toggles, time-replay trigger
    └── ui-bridge/
        └── copilot-bridge.js        # Lightweight client script injected by proxy-tee
```

---

## 4. Component Specifications

### Component A: `sidecar/proxy-tee` (The Ingestion Mirror & Playback Switch)
1. **Discovery Step:** Inspect the upstream `gods-eye-view` codebase first to identify the exact local server port and API endpoints used for flights (OpenSky/adsb.lol), ships (AIS), satellites (CelesTrak TLEs), weather (GFS/Open-Meteo), earthquakes (USGS), and wildfires (FIRMS).
2. **Pass-Through Tee:**
   - Proxy all frontend traffic to the upstream `gods-eye-view` server transparently.
   - When an API response is returned by the upstream server, clone the stream asynchronously without blocking the browser response.
   - Normalize entity coordinates into `(timestamp_utc, layer, entity_id, lat, lon, altitude_m, velocity_kts, heading_deg, h3_res5, h3_res9, metadata_json)`.
3. **Sinks:**
   - **Tile38 Sink:** Execute `SET <layer> <entity_id> FIELD alt <alt> FIELD hdg <hdg> FIELD spd <spd> EX <ttl_seconds> POINT <lat> <lon>`.
   - **TimescaleDB Sink:** Buffer records in memory and flush via batch insert every 2 seconds.
4. **Headless Keep-Alive Cron:**
   - Track active browser connections. When 0 browser clients are active, trigger an internal cron loop that requests the upstream proxy's data routes at safe intervals (e.g., Flights: 15s, Weather: 30m, Earthquakes: 5m, TLEs: 6h) respecting upstream rate-limit governors.
5. **HTML Script Injection (Zero-Touch UI Bridge):**
   - When serving the main HTML page from upstream, inject `<script src="/__sidecar/copilot-bridge.js" defer></script>` before `</body>` so the browser UI connects to the WebSocket bridge with zero edits to upstream source files.

### Component B: `sidecar/db-init/01_init_timescale.sql` (Spatio-Temporal Schema)
1. Enable `timescaledb` and `postgis` extensions.
2. Create hypertable `telemetry_history` partitioned by `ts` (1-day chunk interval):
   - Columns: `ts TIMESTAMPTZ NOT NULL`, `layer TEXT NOT NULL`, `entity_id TEXT NOT NULL`, `h3_res5 BIGINT NOT NULL`, `h3_res9 BIGINT NOT NULL`, `geom GEOGRAPHY(PointZ, 4326)`, `speed_kts REAL`, `heading_deg REAL`, `payload JSONB`.
   - Indexes: Composite index on `(h3_res5, ts DESC)`, `(entity_id, ts DESC)`, and GiST index on `geom`.
   - Compression: Enable native Timescale columnar compression segmented by `layer, h3_res5`, ordered by `ts DESC`, compressing chunks older than 24 hours.
3. Create hypertable `weather_grid_history` keyed by `(ts, h3_res5)` for continuous environmental parameters.
4. Create table `tle_archive` storing historical CelesTrak Two-Line Elements keyed by `(norad_id, epoch_ts)` for accurate past/future SGP4 propagation.

### Component C: `sidecar/mcp-gateway` (Headless Query & MCP Tool Server)
Expose both an **MCP Server (SSE & stdio)** and a **FastAPI REST interface** implementing these deterministic tools:
1. `resolve_anchor(query: str, timestamp: Optional[str])`:
   - Resolves place names/airport codes via local geocoder OR resolves dynamic callsigns/MMSIs/NORAD IDs ("Two-Hop Lookup") to exact `(lat, lon, alt, timestamp)`.
2. `get_scene_context(lat: float, lon: float, radius_km: float = 100, timestamp: str = "now", layers: list[str] = ["all"])`:
   - **If `timestamp == "now"` (within 60s):** Queries Tile38 (`NEARBY`) + live weather + live SGP4 satellite look-angles.
   - **If `timestamp` is historical:** Queries TimescaleDB using `h3_res5` partition pruning + `ST_DWithin` + `ASOF` temporal interpolation between pings, and runs SGP4 against `tle_archive` for that historical timestamp.
   - **Output:** Returns a compressed JSON **Scene Context Snapshot** containing an environmental digest and a distance-ranked Contact Roster with compass bearing (`NE`, `SW`), distance in km, closing/receding vector status, and altitude in meters.
3. `get_entity_trajectory(entity_id: str, start_time: str, end_time: str)`:
   - Reconstructs full 4D path from TimescaleDB.
4. `predict_satellite_passes(lat: float, lon: float, start_time: str, duration_hours: int, min_elevation_deg: float = 15.0)`:
   - Uses `sgp4` to calculate all overhead satellite passes and look-angles.
5. `control_visual_cockpit(action: str, params: dict)`:
   - Checks if a browser client is connected to `ws_copilot`.
   - Supports actions: `get_active_viewport`, `fly_to_coordinate`, `select_entity`, `toggle_layer`, and `set_time_replay_mode(timestamp)`.

---

## 5. Implementation Phases & Verification Gates
Execute this build in 5 sequential phases. Do not advance to the next phase until the current phase builds and passes verification:
- **Phase 1 (Repo Discovery & Mapping):** Scan upstream `gods-eye-view` to map its exact ports, proxy endpoints, data shapes, and Cesium viewer global state. Document findings in `sidecar/UPSTREAM_MAP.md`.
- **Phase 2 (Storage & Docker Topology):** Create `docker-compose.sidecar.yml` and `sidecar/db-init/01_init_timescale.sql`. Verify Tile38 and TimescaleDB start cleanly.
- **Phase 3 (Proxy Tee & Normalizer):** Build `sidecar/proxy-tee` with response mirroring, H3 indexing, Tile38/TimescaleDB sinks, and headless heartbeat.
- **Phase 4 (MCP Gateway & SGP4 Engine):** Build `sidecar/mcp-gateway` with the 5 spatial tools, temporal routing, and SGP4 orbital propagator.
- **Phase 5 (Zero-Touch UI Bridge & Replay Mode):** Build `sidecar/ui-bridge/copilot-bridge.js`, HTML injection in `proxy-tee`, and historical DB replay switching.