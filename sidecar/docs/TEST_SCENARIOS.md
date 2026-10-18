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
