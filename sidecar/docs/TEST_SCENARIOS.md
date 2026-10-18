# Gods-Eye-View Sidecar: Verification & Test Scenarios

This document outlines the **6 Core Verification Test Suites** used to validate the stability, isolation, database tiering, and AI integrations of the Gods-Eye-View Sidecar.

---

## Test Suite 1: Upstream Isolation & Security Gate Bypass

### Test 1.1: Verify Zero-Touch Upstream Codebase Isolation
* **Objective:** Ensure not a single upstream source file has been modified or tainted.
* **Execution:**
  ```bash
  git status --porcelain
  ```
* **Success Criteria:**
  No modified files in `src/`, `server/`, `package.json`, `vite.config.*`, `Dockerfile`, or `compose.yaml`. Only files in `/sidecar/` and `docker-compose.sidecar.yml` appear.

### Test 1.2: Verify Upstream MCP Loopback Security Bypass
* **Objective:** Ensure `mcp-gateway` can query `upstream:4173/mcp` over loopback without `403 Forbidden`.
* **Execution:**
  ```bash
  docker exec gods-eye-view-mcp-gateway-1 python3 -c "
  import urllib.request
  req = urllib.request.Request('http://127.0.0.1:4173/mcp', data=b'{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}', headers={'Content-Type':'application/json'})
  res = urllib.request.urlopen(req)
  print('HTTP Status:', res.status)
  "
  ```
* **Success Criteria:** Outputs `HTTP Status: 200` (not `403 {"error":"The MCP server only accepts local requests"}`).

---

## Test Suite 2: Multi-Tier Storage Engine Verification

### Test 2.1: Verify Tile38 Hot In-Memory Ingestion & Rolling TTL
* **Objective:** Confirm live aircraft and ship vectors are populated in RAM with TTL eviction.
* **Execution:**
  ```bash
  docker exec gods-eye-view-tile38-1 tile38-cli scan flights
  ```
* **Success Criteria:** Returns live spatial keys with coordinates and fields (`alt`, `hdg`, `spd`). Keys automatically expire after 120 seconds.

### Test 2.2: Verify TimescaleDB 4D Telemetry Ingestion
* **Objective:** Verify hypertable row counts and chunk creation.
* **Execution:**
  ```bash
  docker exec gods-eye-view-timescaledb-1 psql -U postgres -d gods_eye_sidecar -c "
  SELECT count(*) FROM telemetry_history;
  SELECT chunk_name, range_start, range_end, is_compressed FROM timescaledb_information.chunks;
  "
  ```
* **Success Criteria:** Millions of records partitioned into daily chunks with compression policies scheduled.

### Test 2.3: Verify PostGIS Permanent Static Infrastructure Sub-Millisecond Spatial Query
* **Objective:** Confirm 44,000+ permanent infrastructure assets return in <5ms.
* **Execution:**
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/tools/find_static_infrastructure \
    -H "Content-Type: application/json" \
    -d '{"lat": 37.7749, "lon": -122.4194, "radius_km": 25, "category": "datacenter", "limit": 3}'
  ```
* **Success Criteria:** Responds in <5ms with facilities like 365 Main Street and 360 Spear Street datacenters.

---

## Test Suite 3: Headless Mode & Harvester State Machine

### Test 3.1: Verify Headless State Transition
* **Objective:** Ensure the system enters headless background harvesting after 60 seconds of browser inactivity.
* **Execution:**
  ```bash
  curl -s http://localhost:8080/health
  ```
* **Success Criteria:** If no browser requests have arrived for >60s, `"headless": true` is returned.

### Test 3.2: Verify Continuous Data Archiving in Headless Mode
* **Objective:** Confirm TimescaleDB continues recording flight telemetry even when no browser tab is open.
* **Execution:**
  ```bash
  BEFORE=$(docker exec gods-eye-view-timescaledb-1 psql -U postgres -d gods_eye_sidecar -t -c "SELECT count(*) FROM telemetry_history;")
  sleep 20
  AFTER=$(docker exec gods-eye-view-timescaledb-1 psql -U postgres -d gods_eye_sidecar -t -c "SELECT count(*) FROM telemetry_history;")
  echo "Before: $BEFORE -> After: $AFTER"
  ```
* **Success Criteria:** `AFTER` is strictly greater than `BEFORE` without any browser open.

---

## Test Suite 4: Remote Visual Cockpit Control (Browser + WebSocket)

### Test 4.1: Verify Co-Pilot Bridge Script Injection
* **Objective:** Confirm `proxy-tee` injects `copilot-bridge.js` into the HTML page.
* **Execution:**
  ```bash
  curl -s http://localhost:8080/ | grep "copilot-bridge.js"
  ```
* **Success Criteria:** Outputs `<script src="/__sidecar/copilot-bridge.js" defer></script>`.

### Test 4.2: Verify Active WebSocket Connection
* **Objective:** Confirm browser connects to the MCP Gateway WebSocket hub.
* **Execution:**
  Open `http://localhost:8080` in Chrome/Safari, then run:
  ```bash
  curl -s http://localhost:8000/health | grep '"browser_connected":true'
  ```
* **Success Criteria:** Returns `"browser_connected": true`.

### Test 4.3: Live Camera Flight Command Execution
* **Objective:** Command the Cesium globe camera to fly to a target coordinate.
* **Execution:**
  ```bash
  curl -X POST http://localhost:8000/api/tools/control_visual_cockpit \
    -H "Content-Type: application/json" \
    -d '{
      "action": "fly_to_coordinate",
      "params": {"lat": 35.6762, "lon": 139.6503, "altitude_m": 8000, "duration_s": 3}
    }'
  ```
* **Success Criteria:** Returns `{"status":"delivered"}` and the browser globe immediately flies to Tokyo!

---

## Test Suite 5: Unified MCP Tool Discovery (36 Tools)

### Test 5.1: Verify Dynamic Reflection Catalog Count
* **Objective:** Verify MCP Gateway advertises all 6 native sidecar tools + 30 upstream core tools.
* **Execution:**
  ```bash
  curl -s http://127.0.0.1:8000/api/mcp/tools | python3 -c "
  import sys, json
  data = json.load(sys.stdin)
  print('Total Tools:', len(data['tools']))
  "
  ```
* **Success Criteria:** Outputs `Total Tools: 36`.

### Test 5.2: Test Federated Upstream Tool Execution
* **Objective:** Verify execution of upstream tools (`get_earthquakes`) through `mcp-gateway`.
* **Execution:**
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/tools/get_earthquakes \
    -H "Content-Type: application/json" \
    -d '{"min_magnitude": 5.0}'
  ```
* **Success Criteria:** Returns structured text with live USGS seismic events.

---

## Test Suite 6: Claude Desktop STDIO Protocol Handshake

### Test 6.1: Verify STDIO JSON-RPC Handshake & Tool Invocation
* **Objective:** Test the exact communication channel used by Claude Desktop over stdin/stdout.
* **Execution:**
  ```bash
  python3 -c "
  import subprocess, json

  p = subprocess.Popen(
      ['docker', 'exec', '-i', 'gods-eye-view-mcp-gateway-1', 'python3', '-m', 'src.mcp_stdio'],
      stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
  )

  def send(req):
      p.stdin.write(json.dumps(req) + '\n')
      p.stdin.flush()

  send({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'test','version':'1.0'}}})
  init_resp = json.loads(p.stdout.readline())
  print('Init status:', init_resp.get('result', {}).get('serverInfo', {}).get('name'))

  send({'jsonrpc':'2.0','method':'notifications/initialized'})

  send({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'resolve_anchor','arguments':{'query':'KSFO'}}})
  tool_resp = json.loads(p.stdout.readline())
  print('resolve_anchor success:', 'San Francisco' in tool_resp['result']['content'][0]['text'])

  p.terminate()
  "
  ```
* **Success Criteria:**
  Outputs:
  * `Init status: gods-eye-view`
  * `resolve_anchor success: True`

---

## Test Suite 7: Tactical Tripwires & Multi-Channel Alert Dispatch

### Test 7.1: Verify Tactical Alert Injection Across All Dispatch Channels
* **Objective:** Confirm manual alert dispatches to Browser HUD, macOS Desktop, Database, ATAK CoT, and Meshtastic LoRa.
* **Execution:**
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/alerts/test \
    -H "Content-Type: application/json" \
    -d '{
      "title": "Manual Incursion Test",
      "severity": "CRITICAL",
      "message": "Unidentified target entering Bangalore AOI perimeter",
      "aoi_name": "Bangalore",
      "lat": 12.9716,
      "lon": 77.5946
    }' | jq .
  ```
* **Success Criteria:** Returns `dispatch_results` with `'browser_hud': true`, `'db_logged': true`, `'atak_cot': true`, `'meshtastic_lora': true`.

### Test 7.2: Verify Recent Alerts Retrieval
* **Objective:** Retrieve stored alerts from PostgreSQL / in-memory buffer.
* **Execution:**
  ```bash
  curl -s http://127.0.0.1:8000/api/alerts/recent | jq .
  ```
* **Success Criteria:** Returns list of recent alerts including timestamps and severity codes.

---

## Test Suite 8: AI Watchstander & Pattern-of-Life (PoL) Analytics

### Test 8.1: Verify 12-Hour Automated INTSUM Dossier Generation
* **Objective:** Generate a comprehensive tactical intelligence briefing for Bangalore AOI.
* **Execution:**
  ```bash
  curl -s "http://127.0.0.1:8000/api/intsum/generate?aoi=Bangalore&hours=12" | jq .
  ```
* **Success Criteria:** Returns structured JSON with `executive_summary`, `threat_level`, `stats` (air/sea/disaster totals), and `key_events`.

### Test 8.2: Verify Non-Cooperative Dark Contact Detection
* **Objective:** Scan for targets whose transponders ceased broadcasting while airborne or underway.
* **Execution:**
  ```bash
  curl -s "http://127.0.0.1:8000/api/pol/dark_contacts?aoi=Bangalore&silence_min=15" | jq .
  ```
* **Success Criteria:** Returns classified dark contact candidates or empty list if no dropouts exist.

### Test 8.3: Verify Kinematic Flight Maneuver Anomalies
* **Objective:** Scan for rapid uncoordinated descents (> 3,500 fpm) and erratic course changes.
* **Execution:**
  ```bash
  curl -s "http://127.0.0.1:8000/api/pol/maneuvers?aoi=Bangalore&max_fpm=3500" | jq .
  ```
* **Success Criteria:** Returns detected kinematic anomalies.

---

## Test Suite 9: Kinematic CPA/TCPA & 3D Radar Horizons

### Test 9.1: Verify 4D Pairwise Closest Point of Approach (CPA) Solver
* **Objective:** Compute separation vector and time-to-closest-point-of-approach between two entities.
* **Execution:**
  ```bash
  # Pick two active hex IDs from: curl -s http://127.0.0.1:8000/api/tools/get_scene_context -H "Content-Type: application/json" -d '{"lat":12.9716,"lon":77.5946,"radius_km":100}'
  curl -s -X POST http://127.0.0.1:8000/api/kinematics/conflicts \
    -H "Content-Type: application/json" \
    -d '{"lat": 12.9716, "lon": 77.5946, "radius_km": 100, "min_cpa_km": 10.0, "max_tcpa_min": 20.0}' | jq .
  ```
* **Success Criteria:** Returns list of conflict pairs with `cpa_distance_km`, `tcpa_seconds`, and `closure_rate_kts`.

### Test 9.2: Verify Radar Horizon & Line-of-Sight Calculation
* **Objective:** Evaluate atmospheric refraction line-of-sight radar shadow.
* **Execution:**
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/radar/coverage \
    -H "Content-Type: application/json" \
    -d '{"lat": 13.5, "lon": 77.8, "altitude_m": 1200.0, "station_id": "VOBL_ASR"}' | jq .
  ```
* **Success Criteria:** Returns `is_visible`, `radar_horizon_km`, `slant_range_km`, `elevation_deg`, and `in_cone_of_silence`.

---

## Test Suite 10: Sovereign Edge RTL-SDR Radio Feeder

### Test 10.1: Verify Edge Radio Receiver Status & Contacts
* **Objective:** Query sovereign edge radio receiver metrics and active local RF contacts.
* **Execution:**
  ```bash
  curl -s http://127.0.0.1:8000/api/sdr/status | jq .
  curl -s http://127.0.0.1:8000/api/sdr/contacts | jq .
  ```
* **Success Criteria:** Returns `status`, `frequency_mhz` (1090.0 or 162.0), message rates, and contact list.

---

## Test Suite 11: ATAK / Cursor-on-Target (CoT 2.0 UDP Port 4242)

### Test 11.1: Verify CoT Broadcaster Metrics
* **Objective:** Confirm continuous UDP streaming on port 4242.
* **Execution:**
  ```bash
  curl -s http://127.0.0.1:8000/api/cot/status | jq .
  ```
* **Success Criteria:** `status: "streaming"`, `target_port: 4242`, `total_packets_sent > 0`.

### Test 11.2: Broadcast Instant CoT Marker to Mobile ATAK
* **Objective:** Inject a high-priority marker to test mobile device rendering.
* **Execution:**
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/cot/broadcast_test \
    -H "Content-Type: application/json" \
    -d '{"callsign": "MY_BASE", "lat": 12.9716, "lon": 77.5946, "remarks": "Personal TOC Ground Station"}' | jq .
  ```
* **Success Criteria:** Returns `status: "broadcast_sent"` and valid XML starting with `<event version="2.0" uid="TEST_MY_BASE" ...>`.

---

## Test Suite 12: Meshtastic / LoRa Off-Grid Tactical Mesh Relay

### Test 12.1: Verify LoRa Mesh Relay Status & Discovered Nodes
* **Objective:** Confirm active node tracking across the decentralized LoRa radio mesh.
* **Execution:**
  ```bash
  curl -s http://127.0.0.1:8000/api/meshtastic/status | jq .
  curl -s http://127.0.0.1:8000/api/meshtastic/nodes | jq .
  ```
* **Success Criteria:** `status: "active"`, `region: "IN_865"`, `total_nodes_discovered: 3`, with GPS coordinates, battery percentages, and SNR values.

### Test 12.2: Transmit Alphanumeric Text Message Over Mesh
* **Objective:** Broadcast message to field nodes without internet or cellular connectivity.
* **Execution:**
  ```bash
  curl -s -X POST http://127.0.0.1:8000/api/meshtastic/send \
    -H "Content-Type: application/json" \
    -d '{"text": "TOC to ROVER-2: Recon perimeter checkpoint Bravo.", "to_node": "^all"}' | jq .
  ```
* **Success Criteria:** Returns `status: "broadcasted"` with packet ID and timestamp.

### Test 12.3: Verify Total MCP Tool Inventory (Must Equal 65)
* **Objective:** Verify all 65 tools (35 Native Sidecar + 30 Upstream Federated) are active and discoverable.
* **Execution:**
  ```bash
  curl -s http://127.0.0.1:8000/api/mcp/tools | jq '.tools | length'
  ```
* **Success Criteria:** Outputs exactly `65`.

