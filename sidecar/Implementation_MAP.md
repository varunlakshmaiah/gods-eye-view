# Upstream Discovery Map: `gods-eye-view`

*Auto-generated architectural discovery map compiled during Phase 1 of the Sidecar implementation.*

---

## 1. Upstream Server Topology

- **Server Architecture:** Vite Dev/Preview Server with Connect/Vite middleware plugins. There is **no standalone Express or Fastify server**. Every local provider proxy is implemented as a Vite plugin via `configureServer` and `configurePreviewServer`.
- **Default Port:** `4173` (defined in `build/vite.js:58`, overridable via `PORT` environment variable).
- **Default Host:** `localhost` (in development) or `0.0.0.0` (in containerized runs via `Dockerfile:39`).
- **Allowed Hosts:** Governed by `build/allowedHosts.js` and `GEV_ALLOWED_HOSTS`.
- **Docker Compose Setup:** `compose.yaml` exposes port `127.0.0.1:4173:4173`.
- **Configuration Entry Points:**
  - `vite.config.js` -> re-exports `server/standalone/vite.config.js`
  - `server/standalone/vite.config.js` -> loads `.env`, invokes `localProviderPlugins()` from `server/providers/local.js`
  - `server/providers/local.js` -> registers all upstream proxies in deterministic order.

---

## 2. API Proxy Routes & Data Contracts

### 2.1 Civilian Aviation (OpenSky & adsb.lol)
- **Primary Endpoint:** `GET /api/flights`
  - **Upstream Target:** `https://opensky-network.org/api/states/all?extended=1`
  - **Fallback Target:** `https://api.adsb.lol/v2/lat/{lat}/lon/{lon}/dist/250` (triggered when OpenSky rate limit exhausted or fails)
  - **Caching / Rate Limiting:** 9s base memory cache (`OPENSKY_CACHE_MS`), adaptive backoff (9s -> 30s -> 90s -> 300s) based on remaining credits. Stale-on-error response.
  - **Data Shape (OpenSky Vector):**
    ```json
    {
      "time": 1700000000,
      "states": [
        [
          "a01234",    // [0] icao24 hex
          "UAL123  ",  // [1] callsign
          "USA",       // [2] origin_country
          1700000000,  // [3] time_position (epoch s)
          1700000000,  // [4] last_contact (epoch s)
          -122.4194,   // [5] longitude
          37.7749,     // [6] latitude
          10668.0,     // [7] baro_altitude (meters)
          false,       // [8] on_ground
          245.5,       // [9] velocity (m/s)
          180.0,       // [10] true_track (heading deg)
          0.0,         // [11] vertical_rate (m/s)
          null,        // [12] sensors
          10800.0,     // [13] geo_altitude (meters)
          "1200",      // [14] squawk
          false,       // [15] spi
          0,           // [16] position_source
          8            // [17] category
        ]
      ]
    }
    ```
- **Track Endpoint:** `GET /api/flights/track?icao24={icao24}`
  - **Upstream:** `https://opensky-network.org/api/tracks/all?icao24={icao24}&time=0`

### 2.2 Military Aviation (adsb.lol)
- **Endpoint:** `GET /api/military`
  - **Upstream Target:** `https://api.adsb.lol/v2/mil`
  - **Caching:** 12s TTL, stale fallback on 429/5xx.
  - **Data Shape:** JSON object with `ac: [ { hex, flight, r, t, alt_baro, gs, track, lat, lon, ... } ]`.
- **Track Endpoint:** `GET /api/military/track?hex={hex}`
  - **Upstream:** `https://adsb.lol/data/traces/{hex[-2:]}/trace_full_{hex}.json`

### 2.3 Maritime Vessels (AIS)
- **Live Ingestion:** Persistent WebSocket connecting to `wss://stream.aisstream.io/v0/stream` (`server/providers/vessels/ais-live.js`).
- **Client Endpoint:** `GET /api/vessels`
  - **Format:** In-memory snapshot map (capped at 50,000 vessels, 30m stale window).
  - **Data Shape:**
    ```json
    {
      "rows": [
        {
          "lat": 37.7749,
          "lon": -122.4194,
          "name": "MAERSK MC-KINNEY MOLLER",
          "mmsi": "219018271",
          "imo": "9619907",
          "type": "Cargo",
          "destination": "OAKLAND",
          "speed": 14.2,
          "course": 265.0,
          "heading": 266.0,
          "last_position_UTC": "2024-01-01T12:00:00Z",
          "last_position_epoch": 1704110400
        }
      ],
      "source": "AISStream",
      "status": "live",
      "newestPositionAt": 1704110400
    }
    ```
- **Track Endpoint:** `GET /api/vessels/track?mmsi={mmsi}` (ring-buffer of 64 points per vessel).

### 2.4 Orbital Objects (CelesTrak TLEs)
- **Endpoint:** `GET /api/celestrak/{group}` (e.g., `stations`, `active`, `starlink`, `visual`)
  - **Upstream:** `https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle`
  - **Caching:** 6 hours memory + disk caching in `.gev-cache/celestrak-{group}.json`.
  - **Data Shape:** Raw 3-line TLE format plain text.

### 2.5 Space Launches
- **Endpoint:** `GET /api/launches`
  - **Upstream:** Launch Library 2 (`https://ll.thespacedevs.com/2.3.0/launches/...`)
  - **Caching:** 15m TTL disk/memory.

### 2.6 Wildfires (NASA FIRMS)
- **Endpoint:** `GET /api/firms`
  - **Upstream:** NASA FIRMS CSV area feed (`VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, `VIIRS_SNPP_NRT`, `MODIS_NRT`).
  - **Caching:** 30m TTL disk/memory. Requires `FIRMS_MAP_KEY`.
  - **Data Shape:**
    ```json
    {
      "fetchedAt": 1704110400000,
      "fires": [
        {
          "latitude": 34.05,
          "longitude": -118.25,
          "brightness": 330.4,
          "confidence": "nominal",
          "frp": 12.8,
          "acq_date": "2024-01-01",
          "acq_time": "0815",
          "satellite": "N20"
        }
      ]
    }
    ```

### 2.7 Weather & Environment
- **Radar & Lightning:** `GET /api/weather/manifest`, `GET /api/weather/tile`, `GET /api/weather/image` (NOAA nowCOAST WMS).
- **Point Weather Effects:** `GET /api/weather-effects?lat={lat}&lon={lon}` (Open-Meteo, 5m cache).
- **Wind Vector Grids:** `GET /api/wind/?model={gfs|ifs}` and `GET /api/wind/grid/{id}.bin` (ECMWF IFS / NOAA GFS, 1h cache).
- **Cyclones:** `GET /api/cyclones` (NOAA NHC, 5m cache).
- **Earthquakes:** Direct client-side fetch from USGS GeoJSON feed (`https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson`).

---

## 3. Frontend Cesium Integration Hooks

- **Cesium Viewer Instance:** Created in `src/app/viewer.js` inside container `#cesiumContainer`.
- **Primary Global Hook:** `window.__godsEyeView` (initialized in `src/app/tools.js`), exposing:
  - `viewer`: Direct Cesium `Viewer` instance.
  - `viewer.camera`: Camera controller (`flyTo`, `setView`, `heading`, `pitch`, `roll`, `positionCartographic`).
  - `dataManager`: `LayerLifecycle` instance managing active data layer registrations, toggling, and data feeds.
  - `sceneDirector`: Camera animation director.
  - `weatherEffects`: Point and atmospheric visual controllers.
  - `requestRender()`: Forces a Cesium scene render tick.
- **Layer System:** Controlled by `LayerLifecycle` (`src/data/lifecycle.js`). Layers can be queried and toggled programmatically via `dataManager.enableLayer(id)` / `dataManager.disableLayer(id)` / `dataManager.isLayerEnabled(id)`.
- **UI Bridge Target:** Inject `<script src="/__sidecar/copilot-bridge.js" defer></script>` before `</body>` in the upstream `index.html` stream. The script binds to `window.__godsEyeView` to allow remote camera and layer control over WebSocket.
