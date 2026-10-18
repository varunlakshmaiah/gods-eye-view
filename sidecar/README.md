# Gods-Eye-View Headless Spatio-Temporal Sidecar

> **24/7 Headless World Engine & Visual Co-Pilot for `gods-eye-view`**

---

## 📚 Documentation Suite

Comprehensive technical guides are available in the [`docs/`](./docs) directory:
* 🛰️ **[Personal C6ISR Blueprint (PERSONAL_C6ISR.md)](./docs/PERSONAL_C6ISR.md):** The low-resource, sovereign situational awareness framework, pragmatic boundaries, and non-military civilian use cases.
* 📋 **[Personal Intelligence Implementation Plan (PLAN_PERSONAL_INTELLIGENCE.md)](./docs/PLAN_PERSONAL_INTELLIGENCE.md):** Phased technical implementation roadmap for autonomous tripwires, alert buses, and AI watch loops.
* 🏛️ **[System Architecture (ARCHITECTURE.md)](./docs/ARCHITECTURE.md):** System topology, network namespace pairing, loopback bypass, and data flow.
* 📐 **[Technical Design Specifications (DESIGN.md)](./docs/DESIGN.md):** Dynamic MCP tool reflection, multi-tier spatial storage, SGP4 orbital mechanics, and WebSocket bridge.
* 🛠️ **[Build & Deployment Guide (BUILD_AND_DEPLOYMENT.md)](./docs/BUILD_AND_DEPLOYMENT.md):** Step-by-step setup, container fleet operations, Claude Desktop integration, and PostGIS infrastructure seeding.
* 🧪 **[Verification & Test Scenarios (TEST_SCENARIOS.md)](./docs/TEST_SCENARIOS.md):** 6 end-to-end verification test suites covering isolation, geofencing, headless mode, cockpit control, and Claude STDIO.

---

## 1. Upstream Isolation Contract

This project is structured as a non-invasive sidecar to `bilawalsidhu/gods-eye-view`.
- **Zero changes** to upstream source files (`src/`, `server/`, `package.json`, `vite.config.*`, `Dockerfile`, `compose.yaml`).
- All sidecar components reside inside `/sidecar/` and `/docker-compose.sidecar.yml`.
- **Zero merge conflicts** when pulling from `upstream/main`.

### Upstream Sync SOP
To pull and integrate upstream updates cleanly:
```bash
# Fetch and merge latest upstream changes
git fetch upstream
git merge upstream/main

# Re-run sidecar tests and rebuild container images if needed
docker compose -f docker-compose.sidecar.yml build
docker compose -f docker-compose.sidecar.yml up -d
```

---

## 2. Architecture Overview

```
                      +-----------------------------+
                      |   Browser 3D Cesium Globe   |
                      |   (window.__godsEyeView)    |
                      +--------------+--------------+
                                     |
                         injected copilot-bridge.js
                                     |
                                     v (WebSocket)
+-----------------------+     +------+----------------+
|  proxy-tee (Port 8080)|     | mcp-gateway (Port 8000)|
|  - Reverse Proxy      |<--->| - 5 Spatial AI Tools  |
|  - Ingestion Tap      |     | - SGP4 Propagator     |
|  - Headless Cron Loop |     | - WebSocket Hub       |
+-----------+-----------+     +-------+---------------+
            |                         |
      +-----+-----+             +-----+-----+
      |           |             |           |
      v           v             v           v
+-----------+ +-----------+ +-------+ +-------------+
| Upstream  | | Tile38    | | Fast  | | TimescaleDB |
| Port 4173 | | Port 9851 | | MCP   | | Port 5432   |
| (Vite Dev)| | (Hot Live)| | Server| | (Cold Hist) |
+-----------+ +-----------+ +-------+ +-------------+
```

### Storage Topology
1. **Tile38 (Hot Spatial Tier):** In-memory sub-5ms geofencing, radius (`NEARBY`), and bounding-box queries with automatic TTL eviction.
2. **TimescaleDB + PostGIS (Cold Archive Tier):** Hypertable partitioned into 1-day chunks, H3 hexagonal indexing (res 5 & res 9), and columnar compression for telemetry older than 24h.

---

## 3. Quick Start

### Step 1: Prepare Environment
```bash
cp sidecar/.env.sidecar.example sidecar/.env.sidecar
```

### Step 2: Launch Stack
```bash
docker compose -f docker-compose.sidecar.yml up -d
```

### Step 3: Open the Visual Cockpit
Open your browser to:
**`http://localhost:8080`**

*(Note: Navigate to the `proxy-tee` port `8080`, not `4173`. The proxy transparently renders the upstream app while injecting the Co-Pilot bridge and tapping live telemetry into Tile38 and TimescaleDB).*

---

## 4. MCP Tools & API Reference

The `mcp-gateway` (Port `8000`) exposes deterministic spatial tools via MCP (SSE / stdio) and standard HTTP REST:

### 1. `resolve_anchor`
Resolves place names, airport codes (ICAO/IATA), callsigns, MMSIs, or coordinate strings to exact 4D coordinate fixes.
```bash
curl -X POST http://localhost:8000/api/tools/resolve_anchor \
  -H "Content-Type: application/json" \
  -d '{"query": "KSFO"}'
```

### 2. `get_scene_context`
Returns a 250km distance-ranked Contact Roster with compass bearing (`N`, `NE`, `SW`), distance in km, closing/receding vector status, atmospheric weather, and overhead satellite look-angles.
- Automatically routes to **Tile38** if `timestamp == "now"`.
- Automatically routes to **TimescaleDB** if `timestamp` is historical.
```bash
curl -X POST http://localhost:8000/api/tools/get_scene_context \
  -H "Content-Type: application/json" \
  -d '{"lat": 37.7749, "lon": -122.4194, "radius_km": 100, "timestamp": "now"}'
```

### 3. `get_entity_trajectory`
Reconstructs the full historical 4D trajectory for an aircraft or vessel over a designated time window.
```bash
curl -X POST http://localhost:8000/api/tools/get_entity_trajectory \
  -H "Content-Type: application/json" \
  -d '{
    "entity_id": "a01234",
    "start_time": "2024-01-01T00:00:00Z",
    "end_time": "2024-01-01T04:00:00Z"
  }'
```

### 4. `predict_satellite_passes`
Calculates upcoming satellite passes and topocentric look-angles (azimuth, elevation, range) over an observer location using the headless SGP4 propagator.
```bash
curl -X POST http://localhost:8000/api/tools/predict_satellite_passes \
  -H "Content-Type: application/json" \
  -d '{"lat": 37.7749, "lon": -122.4194, "duration_hours": 6, "min_elevation_deg": 20.0}'
```

### 5. `control_visual_cockpit`
Sends camera navigation, entity selection, layer toggling, or time-replay commands to the active browser UI via WebSocket.
```bash
curl -X POST http://localhost:8000/api/tools/control_visual_cockpit \
  -H "Content-Type: application/json" \
  -d '{
    "action": "fly_to_coordinate",
    "params": {"lat": 37.6188, "lon": -122.3754, "altitude_m": 3000, "duration_s": 2.5}
  }'
```

### 6. `find_static_infrastructure`
Performs ultra-fast (<5ms) local PostGIS radius queries over 44,000+ permanently persisted global infrastructure assets (`datacenter`, `dam`, `military_installation`, `submarine_cable`, `cable_landing_point`) with zero external API calls.
```bash
curl -X POST http://localhost:8000/api/tools/find_static_infrastructure \
  -H "Content-Type: application/json" \
  -d '{"lat": 37.7749, "lon": -122.4194, "radius_km": 50, "category": "datacenter", "limit": 10}'
```

### 7. `get_active_alerts`
Retrieves recent tactical alerts, squawk emergencies, disaster triggers, and perimeter tripwire incursions from TimescaleDB:
```bash
curl -X POST http://localhost:8000/api/tools/get_active_alerts \
  -H "Content-Type: application/json" \
  -d '{"limit": 20}'
```

### 8. `add_area_of_interest`
Registers or updates an Area of Interest (AOI) perimeter tripwire (default: Bangalore 75km radius around `12.9716, 77.5946` with VOBL/VOBG filtering):
```bash
curl -X POST http://localhost:8000/api/tools/add_area_of_interest \
  -H "Content-Type: application/json" \
  -d '{"name": "Bangalore Urban", "lat": 12.9716, "lon": 77.5946, "radius_km": 75.0, "description": "Home AOI"}'
```

### 9. `test_tactical_alert`
Simulates and dispatches a tactical alert across all configured channels (Browser HUD banner, macOS desktop notification, Telegram, Discord, and Slack webhooks):
```bash
curl -X POST http://localhost:8000/api/tools/test_tactical_alert \
  -H "Content-Type: application/json" \
  -d '{"title": "Perimeter Breach Test", "severity": "CRITICAL", "message": "Low altitude contact breached Bangalore perimeter"}'
```

### 10. `generate_intelligence_summary`
Synthesizes a multi-domain situational Intelligence Summary (INTSUM) dossier for an AOI (air traffic, incursions, hazards, infrastructure, upcoming satellite passes):
```bash
curl -X POST http://localhost:8000/api/tools/generate_intelligence_summary \
  -H "Content-Type: application/json" \
  -d '{"aoi_name": "Bangalore", "window_hours": 12}'
```

### 11. `dispatch_intelligence_summary`
Generates and broadcasts an INTSUM situational briefing across all notification channels (Browser HUD, Telegram, Discord, Slack, DB):
```bash
curl -X POST http://localhost:8000/api/tools/dispatch_intelligence_summary \
  -H "Content-Type: application/json" \
  -d '{"aoi_name": "Bangalore", "window_hours": 12}'
```

### 12. `toggle_aoi_perimeters`
Toggles or sets the visibility of 3D Area of Interest perimeter rings and tactical geofence beacons on the Cesium browser globe:
```bash
curl -X POST http://localhost:8000/api/tools/toggle_aoi_perimeters \
  -H "Content-Type: application/json" \
  -d '{"visible": true}'
```

### 13. Upstream Federated Core Tools (30 Tools)
`mcp-gateway` automatically discovers and proxies all 30 upstream God's Eye View MCP tools directly:
- **`situation_brief`**: Unified operational picture (weather, quakes, fires, aircraft, ships, cyclones).
- **`military_awareness`**: 250km radar perimeter of military aircraft, surface warships, and defense installations.
- **`show_in_gods_eye_view`**: Live 3D Cesium camera and layer positioning with direct browser links.
- **`get_earthquakes`**, **`get_weather`**, **`get_weather_radar`**, **`get_flights`**, **`get_ships`**, **`get_satellites`**, **`get_wildfires`**, **`get_air_quality`**, **`get_cctv`**, and more.

All 42 tools can be invoked via standard HTTP `POST /api/tools/{tool_name}` or directly through Claude Desktop / Cursor / Windsurf via MCP.

---

## 5. Claude Desktop & MCP Integration

Connect Claude Desktop to God's Eye View with a single unified entry point in `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "gods-eye-view": {
      "command": "/usr/local/bin/docker",
      "args": [
        "exec",
        "-i",
        "gods-eye-view-mcp-gateway-1",
        "python3",
        "-m",
        "src.mcp_stdio"
      ]
    }
  }
}
```

Once connected, Claude Desktop will automatically see all **35+ tools** (5 Sidecar Native tools + 30 Upstream Core tools), enabling autonomous intelligence retrieval, real-time spatial analytics, and live 3D Cesium cockpit control in the browser!

---

## 6. Headless Operation

When no browser sessions are open, `proxy-tee` automatically enters **Headless Mode** after 60 seconds of inactivity. An internal keep-alive cron periodically polls upstream feeds at safe intervals (Flights: 15s, Vessels: 30s, FIRMS: 30m, CelesTrak: 6h) respecting upstream rate-limit governors.

This ensures Tile38 and TimescaleDB maintain a continuous, 24/7 spatio-temporal archive even when no user has the browser open.

---

## 7. Upstream Invariants & Compatibility Architecture

To guarantee **100% upstream project originality** without a single modification to upstream files (`src/`, `server/`, etc.), the sidecar explicitly accounts for several internal security and architectural gates found in `bilawalsidhu/gods-eye-view`:

### 1. Same-Site Security Gate (`admitSameSiteRequest`)
- **Upstream Files:** `src/localRequestGate.mjs`, `server/providers/common/same-site.js`
- **Protected Routes:** `/api/realtime/token`, `/api/openai/hud-summary`, `/api/google/nearby-places`, `/api/tomtom/flow/*`, `/api/mapillary/*`
- **Behavior:** Blocks any request containing proxy headers (`x-forwarded-for`, `via`, `forwarded`, `cf-*`) or mismatched `Origin` headers with a `403 Forbidden` ("Proxied requests are not accepted").
- **Sidecar Compatibility:** `proxy-tee` strips all proxy headers and rewrites `Host: localhost:4173` and `Origin: http://localhost:4173` before forwarding, allowing upstream to validate requests identically to native execution.

### 2. Key Setup Remote Peer Check (`admitKeySetupRequest`)
- **Upstream Files:** `server/standalone/key-setup.js`
- **Protected Routes:** `/api/setup/status`, `/api/setup/keys` (the "POWER UP" dashboard modal)
- **Behavior:** Strictly checks that the connecting socket IP is either a loopback address or present in the `GEV_KEY_SETUP_TRUSTED_PEERS` environment variable.
- **Sidecar Compatibility:** In `docker-compose.sidecar.yml`, `proxy-tee` is assigned static internal IP `172.28.0.10`, and `upstream` is configured with `GEV_KEY_SETUP_TRUSTED_PEERS: 172.28.0.10,127.0.0.1`. No upstream code is modified.

### 3. Loopback-Pinned Upstream MCP Server
- **Upstream Files:** `server/mcp/plugin.js` (`/mcp`)
- **Behavior:** Upstream runs an embedded MCP server that checks `LOOPBACK_ADDRESSES` (`127.0.0.1, ::1`). It blocks any external container IP with `403 The MCP server only accepts local requests`.
- **Sidecar Compatibility:** `mcp-gateway` runs with `network_mode: "service:upstream"`, sharing `upstream`'s network namespace and loopback adapter. This allows `mcp-gateway` to communicate directly with `http://127.0.0.1:4173/mcp` without modifying upstream code, transparently federating all 30 upstream tools alongside the 5 native sidecar tools.

### 4. Per-Socket Rate Limiting
- **Upstream Files:** `server/providers/common/rate-limit.js`
- **Behavior:** Upstream inspects `req.socket.remoteAddress` and intentionally ignores forwarded headers to calculate client rate limits (`GEV_RATELIMIT_OPENAI_PER_MIN=30`).
- **Sidecar Compatibility:** Because all requests routed through `proxy-tee` arrive with the proxy container's IP, high-volume automated testing or multi-client environments should set `GEV_RATELIMIT_OPENAI_PER_MIN=0` in `.env` if limit exhaustion occurs.

### 5. Vite Server Restart on Key Save
- **Upstream Files:** `server/standalone/key-setup.js`
- **Behavior:** Upstream calls `server.restart()` 250ms after writing keys to `.env`.
- **Sidecar Compatibility:** `proxy-tee` handles the transient 1-2 second connection drop via an `error` interceptor that returns `503 UPSTREAM_RESTARTING` rather than crashing the proxy process or dropping sockets abruptly.

