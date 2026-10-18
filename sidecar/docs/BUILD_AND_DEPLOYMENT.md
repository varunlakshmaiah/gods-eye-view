# Gods-Eye-View Sidecar: Build, Operations & Deployment Guide

## 1. Prerequisites & System Requirements

* **Operating System:** macOS (Apple Silicon or Intel), Linux (x86_64 or ARM64), Windows (WSL2).
* **Docker:** Docker Engine 24+ and Docker Compose v2.20+.
* **Hardware Sizing:**
  * Minimum: 4 CPU cores, 8 GB RAM, 15 GB free disk space.
  * Recommended: 8 CPU cores, 16 GB RAM, 50 GB SSD storage.

---

## 2. Environment Configuration

Copy the example environment configuration into place:
```bash
cp sidecar/.env.sidecar.example sidecar/.env.sidecar
```

### Key Environment Parameters

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `POSTGRES_DB` | `gods_eye_sidecar` | TimescaleDB database name |
| `POSTGRES_USER` | `postgres` | TimescaleDB superuser |
| `POSTGRES_PASSWORD` | `postgres` | TimescaleDB password |
| `HEADLESS_ENABLED` | `true` | Enables 24/7 background feed harvester |
| `HEADLESS_FLIGHTS_INTERVAL_MS` | `15000` | Background flight polling interval (15s) |
| `HEADLESS_VESSELS_INTERVAL_MS` | `30000` | Background vessel polling interval (30s) |
| `HEADLESS_FIRMS_INTERVAL_MS` | `1800000` | Background NASA FIRMS fire polling (30m) |
| `HEADLESS_CELESTRAK_INTERVAL_MS`| `21600000` | Background CelesTrak TLE polling (6h) |

---

## 3. Launching the Sidecar Fleet

### Full Build & Start
From the project root:
```bash
docker compose -f docker-compose.sidecar.yml up -d --build
```

### Component Status Check
Verify that all 5 containers are running and healthy:
```bash
docker compose -f docker-compose.sidecar.yml ps
```
Expected output:
* `gods-eye-view-upstream-1` (Up)
* `gods-eye-view-proxy-tee-1` (Up)
* `gods-eye-view-mcp-gateway-1` (Up)
* `gods-eye-view-tile38-1` (Up)
* `gods-eye-view-timescaledb-1` (Up)

### Health Verification Endpoints
* **Proxy-Tee Health:**
  ```bash
  curl -s http://localhost:8080/health
  # -> {"status":"ok","service":"proxy-tee","headless":false,"playback":"live"}
  ```
* **MCP Gateway Status:**
  ```bash
  curl -s http://localhost:8000/health
  # -> {"status":"ok","browser_connected":true,"tile38_connected":true,"timescaledb_connected":true}
  ```
* **Unified Tool Registry:**
  ```bash
  curl -s http://localhost:8000/api/mcp/tools | grep -o '"name":' | wc -l
  # -> 36 tools
  ```

---

## 4. Seeding Permanent Global Infrastructure (PostGIS)

To seed 44,000+ permanent infrastructure assets (datacenters, dams, submarine cables, military bases) into TimescaleDB PostGIS:

```bash
docker run --rm \
  --net=container:gods-eye-view-upstream-1 \
  -v $(pwd):/workspace:ro \
  gods-eye-view-mcp-gateway:latest \
  python3 /workspace/sidecar/db-init/seed_infrastructure.py
```

Expected output:
```text
[SeedInfra] Ingested 4,351 datacenters.
[SeedInfra] Ingested 703 dams.
[SeedInfra] Ingested 694 submarine cables.
[SeedInfra] Ingested 1,917 cable landing points.
[SeedInfra] Ingested 36,466 military installations.
  [SeedInfra] SUCCESS! Total Permanent Assets: 44,131
```

---

## 5. AI Assistant Client Configuration

### A. Claude Desktop (macOS / Windows)
Add the server entry to `~/Library/Application Support/Claude/claude_desktop_config.json`:

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
*Restart Claude Desktop to register all 36 tools.*

### B. Cursor / Windsurf / Cline (SSE Remote Mode)
Connect via Server-Sent Events (SSE) directly to the gateway port:
* **Protocol:** `sse`
* **Endpoint URL:** `http://localhost:8000/mcp/sse`

---

## 6. Database Operations & Maintenance

### TimescaleDB Retention Window Modification
By default, historical telemetry drops after 30 days. To change this to 14 days or 90 days:
```bash
docker exec -it gods-eye-view-timescaledb-1 psql -U postgres -d gods_eye_sidecar -c "
SELECT remove_retention_policy('telemetry_history');
SELECT add_retention_policy('telemetry_history', INTERVAL '14 days');
"
```

### Manual Compression Trigger
Chunks older than 24 hours compress automatically twice daily. To force immediate compression:
```bash
docker exec -it gods-eye-view-timescaledb-1 psql -U postgres -d gods_eye_sidecar -c "
SELECT run_job(1000);
"
```

### Inspect Table & Hypertable Sizes
```bash
docker exec -it gods-eye-view-timescaledb-1 psql -U postgres -d gods_eye_sidecar -c "
SELECT 
    schemaname,
    relname,
    n_live_tup AS rows,
    pg_size_pretty(pg_total_relation_size(relid)) AS size
FROM pg_stat_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
"
```

---

## 7. Port Allocation Cheatsheet

| Port | Service | Internal Container Port | Description |
| :--- | :--- | :--- | :--- |
| **8080** | `proxy-tee` | 8080 | **Primary Web Entrypoint** (Browser 3D Cockpit with Bridge) |
| **8000** | `mcp-gateway` | 8000 | **AI Entrypoint** (REST APIs + SSE MCP + WebSocket Hub) |
| **4173** | `upstream` | 4173 | Raw upstream Vite server (optional direct access) |
| **9851** | `tile38` | 9851 | Real-time geospatial cache (Redis protocol) |
| **5432** | `timescaledb` | 5432 | PostgreSQL 16 + TimescaleDB + PostGIS database |
