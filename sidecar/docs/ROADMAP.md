# Gods-Eye-View Sidecar: Suggested Features & Optimizations

Proposals from a review of the sidecar code at `v1.15.0-sidecar`. Each item names the code it affects. Effort: **S** = hours, **M** = a day or two, **L** = several days. Any new capability should get its own entry in [`config/features.json`](../config/features.json) (see [FEATURE_FLAGS.md](./FEATURE_FLAGS.md)).

---

## 1. Correctness & Reliability Fixes (do first)

| # | Issue | Where | Suggested fix | Effort |
| :--- | :--- | :--- | :--- | :--- |
| 1 | Emergency-squawk check only sees the first 100 flights (`SCAN flights LIMIT 100`) | `mcp-gateway/src/alerts/engine.py` | Page through `SCAN` with a cursor, or filter in Tile38 (`SCAN flights WHERE ...`) and store squawk as a field | S |
| 2 | CoT broadcaster sends at most 100 entities per layer, picked arbitrarily | `mcp-gateway/src/cot/engine.py` | Send contacts `NEARBY` each AOI or escort bubble instead of a global `SCAN` | S |
| 3 | Escort devices and their bubbles are kept only in memory, so a restart loses them | `mcp-gateway/src/escort/engine.py` | Save devices to TimescaleDB (or a JSON file next to `watchlists.json`) | S |
| 4 | Entities of interest (`eois` in `watchlists.json`) are defined but never used | `alerts/engine.py` | Track watched callsigns/MMSIs and alert when they appear, enter an AOI or go dark | M |
| 5 | `get_scene_context` takes the 50 most recently fetched TLE rows, not 50 distinct satellites, and rebuilds them on every call | `main.py` | `DISTINCT ON (norad_id)` plus an in-memory cache of parsed satellites, refreshed when the TLE archive changes | S |
| 6 | The bridge always connects to `ws://<host>:8000`. That port is bound to 127.0.0.1, so phones and other LAN devices can't connect, and HTTPS pages (8443) block it as mixed content. | `ui-bridge/copilot-bridge.js` | Connect same-origin to `/ws/copilot` (`proxy-tee` already proxies it), using `wss://` on HTTPS | S |
| 7 | The normalizers depend on upstream response formats, and the fork is far behind upstream | `proxy-tee/src/normalizer.ts` | Save sample upstream responses as test fixtures and add normalizer unit tests, so an upstream merge that breaks capture fails CI | M |

## 2. Performance Optimizations

| # | Optimization | Where | Benefit | Effort |
| :--- | :--- | :--- | :--- | :--- |
| 1 | Replace the 20s polling tripwire loop with **Tile38 geofences** (`SETHOOK` / `FENCE` on AOIs) that push events to the gateway | `alerts/engine.py` | Alerts in under a second, less CPU, no scan limits | M |
| 2 | Drop the 5s HTTP escort poll; rely on `escort_sync` over WebSocket and poll only while disconnected | `copilot-bridge.js` | ~720 fewer requests per hour per open tab | S |
| 3 | Insert telemetry with `COPY` or `unnest()` arrays instead of a long multi-row `VALUES` string | `proxy-tee/src/sinks/timescale.ts` | Faster inserts for large flight batches, smaller SQL strings | S |
| 4 | Parse each captured response only once, and skip storing positions that haven't moved since the last write | `proxy-tee/src/index.ts`, `normalizer.ts` | Fewer Tile38/TimescaleDB writes for stationary ships and aircraft on the ground | S |
| 5 | Add TimescaleDB **continuous aggregates** (hourly per H3 cell and layer) for INTSUM and pattern-of-life queries | `db-init/` | INTSUM and dark-contact queries run in milliseconds over 30 days | M |
| 6 | Reuse one shared HTTP client in the dispatcher and the stdio bridge | `alerts/dispatcher.py`, `mcp_stdio.py` | Lower latency per alert and tool call | S |
| 7 | Satellite passes: coarse 2-minute steps, refining only near a rise or set; cache results per observer and hour | `sgp4_engine.py` | Roughly 10× less computation (today: 100 satellites × 1,440 steps per 12h) | M |
| 8 | Multi-stage Docker builds; `npm ci` with a committed lockfile; drop `build-essential` from the runtime image | `Dockerfile`s | Smaller images, reproducible builds | S |
| 9 | Compose `healthcheck:` entries with `depends_on: condition: service_healthy` | `docker-compose.sidecar.yml` | No race on startup (gateway connecting before the database is ready) | S |

## 3. Security Hardening

| # | Item | Effort |
| :--- | :--- | :--- |
| 1 | Remove the committed TLS private key (`proxy-tee/certs/key.pem`); generate one at first start or with `mkcert` | S |
| 2 | Require a token for write endpoints. `proxy-tee` listens on `0.0.0.0:8080` and forwards `/api/escort`, `/api/aois` and `/api/radar` to the gateway with no authentication. | M |
| 3 | Move database credentials out of compose into `.env.sidecar` / secrets; no default `postgres/postgres` | S |
| 4 | Restrict gateway CORS (currently `*` with credentials) to the proxy origin | S |
| 5 | Stop committing build artefacts (`android-beacon/bin/*.class`, APKs); build them in CI and attach them to releases | S |

## 4. New Features

| # | Feature | Description | Proposed flag | Effort |
| :--- | :--- | :--- | :--- | :--- |
| 1 | **Feature toggle panel in the HUD** | Switches in the injected escort HUD that call `PUT /api/features/{id}` (needs auth item 2 first) | `feature_admin_ui` | M |
| 2 | **Earthquake & weather capture** | Add normalizers for USGS quakes and weather; fill the unused `weather_grid_history` table | `ingest_earthquakes`, `ingest_weather` | M |
| 3 | **Prometheus `/metrics`** | Capture rate, buffer size, Tile38/DB latency, alerts per channel, engine status, enabled-feature count | `metrics_endpoint` | S |
| 4 | **Alert routing rules** | Per-channel minimum severity, quiet hours, per-AOI recipients, a daily digest instead of instant pushes | `alert_routing` | M |
| 5 | **More notification channels** | ntfy.sh, Pushover, email (SMTP) and a generic signed webhook | `notify_ntfy`, `notify_email`, `notify_webhook` | S each |
| 6 | **Replay timeline scrubber** | A time slider in the browser driving `/__sidecar/playback`, with play/pause and speed | `replay_timeline_ui` | M |
| 7 | **Trajectory export** | GeoJSON / KML / CSV export of `get_entity_trajectory` and AOI history | `trajectory_export` | S |
| 8 | **Configurable INTSUM schedule** | Cron-style schedule per AOI instead of hard-coded Bangalore every 12h | extends `intsum_schedule` | S |
| 9 | **Anomaly scoring** | Per-entity baseline (usual routes and altitudes from continuous aggregates) and alerts on deviation | `anomaly_scoring` | L |
| 10 | **Scheduled feature windows** | Turn features on or off by time of day (e.g. CoT only during field ops) | extends `features.json` | M |
| 11 | **Multi-user escort sharing** | Signed share links so a family member or teammate can follow one bubble read-only | `escort_sharing` | M |

## 5. Documentation Hygiene

* Fix the tool counts that disagree (5 / 36 / 55 / 70) by generating the tool table from `GET /api/mcp/tools`.
* Add `v1.13`–`v1.15` (TomTom traffic tagging, HTTPS 8443, Android beacon) to the version table in `FEATURES.md`.
