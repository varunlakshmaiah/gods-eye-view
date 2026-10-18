# Implementation Plan: Personal Intelligence Platform (Personal C6ISR)

> **Extending Gods-Eye-View Sidecar into an Autonomous Situational Awareness & Alerting Sentinel**

---

## 1. System Vision & Objective

Transform the current Gods-Eye-View Sidecar from a **reactive query engine** (where Claude only responds when asked) into a **proactive, autonomous personal intelligence sentinel** that:
1. Continuously monitors your Areas of Interest (AOIs) and Watchlisted Entities (EOIs).
2. Detects spatial, kinematic, and disaster anomalies in real time.
3. Dispatches instant alerts to your phone (Telegram / Webhooks) when critical thresholds are breached.
4. Generates scheduled Intelligence Summaries (INTSUM) without requiring user prompts.
5. Retains a sovereign, offline-capable radar bubble via low-cost Software-Defined Radio (SDR).

---

## 2. Phased Architecture & Execution Roadmap

```mermaid
flowchart TD
    subgraph Phase1 ["Phase 1: Ingestion & Storage (COMPLETED)"]
        P1A["Tile38 Hot In-Memory Geofencing (120s TTL)"]
        P1B["TimescaleDB 4D Telemetry Archive (30d Rolling)"]
        P1C["PostGIS Static Geobase (44k Assets in 30MB)"]
        P1D["36 Unified MCP Tools (Claude Desktop)"]
    end

    subgraph Phase2 ["Phase 2: Tactical Tripwires & Alert Bus (IMMEDIATE)"]
        P2A["AOI & Watchlist Configuration (YAML / DB)"]
        P2B["Real-time Tripwire & Anomaly Daemon (Tile38 Hooks)"]
        P2C["Emergency Squawk & Threat Detectors"]
        P2D["Multi-Channel Alert Dispatcher (Telegram / Webhook)"]
    end

    subgraph Phase3 ["Phase 3: Autonomous AI Watchstander (AGENTIC)"]
        P3A["Automated INTSUM Scheduled Briefings"]
        P3B["Proactive Cockpit Dispatch (Auto-Fly on Alert)"]
        P3C["Pattern-of-Life (PoL) Trajectory Analytics"]
    end

    subgraph Phase4 ["Phase 4: Kinematics & Sovereign SDR (ADVANCED)"]
        P4A["Kinematic CPA / TCPA Collision Vectors"]
        P4B["3D Radar & Visual Engagement Fans"]
        P4C["Edge RTL-SDR USB Hardware Ingest ($30 Dongle)"]
    end

    Phase1 --> Phase2
    Phase2 --> Phase3
    Phase3 --> Phase4
```

---

## 3. Phase 2: Tactical Tripwires & Alert Bus (Immediate Focus)

### Milestone 2.1: Watchlist & AOI Registry
Create a clean, human-editable configuration (`sidecar/config/watchlists.yml` or database table) defining your personal watch boundaries:
* **Areas of Interest (AOIs):**
  * Home / Family Cities (e.g. San Francisco, Tokyo, London with custom radius).
  * Travel destinations, properties, or critical infrastructure nodes.
* **Entities of Interest (EOIs):**
  * Specific flight numbers / callsigns (family flights, medical transport, VIP).
  * Vessel MMSIs (local ferries, supply ships).
  * Space objects (ISS, Starlink passes).

### Milestone 2.2: Real-time Anomaly & Event Detectors
Build an event evaluation daemon inside `sidecar/proxy-tee` or `mcp-gateway` evaluating telemetry as it streams in:
1. **Transponder Emergency Codes:**
   * Squawk `7700` (General In-Flight Emergency).
   * Squawk `7600` (Radio Communications Failure).
   * Squawk `7500` (Unlawful Interference / Hijack).
2. **Low-Altitude Airspace Incursions:**
   * Aircraft descending below 1,500m within 10km of an AOI or critical infrastructure (nuclear plants, datacenters).
3. **Disaster Proximity Tripwires:**
   * USGS Earthquake $\ge$ M4.5 within 200km of an AOI.
   * NASA FIRMS Wildfire thermal anomaly within 30km of an AOI.
   * NOAA Tropical Cyclone center projected towards an AOI.
4. **Subsea Infrastructure Threat:**
   * Commercial vessels moving at $<1.0\text{ knot}$ (anchoring / dragging) within 500m of a mapped fiber-optic cable.

### Milestone 2.3: Multi-Channel Alert Bus
A unified dispatcher sending structured alerts:
* **Telegram Bot:** Direct messages to your phone with location coordinates, contact details, and a one-click link to open God's Eye View centered on the incident.
* **Discord / Slack Webhooks:** Dedicated `#tactical-alerts` channel.
* **Local OS Notifications:** Native macOS notifications via `osascript`.

---

## 4. Phase 3: Autonomous AI Watchstander (Agentic Loop)

### Milestone 3.1: Scheduled Intelligence Summary (INTSUM)
* A background cron worker in `mcp-gateway` (e.g., every 2 hours or every morning at 08:00):
  1. Queries TimescaleDB and Tile38 for all activity across your AOIs over the preceding window.
  2. Synthesizes an executive **INTSUM Dossier** (air traffic density, weather changes, seismic events, infrastructure status).
  3. Formats it in concise markdown and pushes it to your Telegram / Discord.

### Milestone 3.2: Automated Cockpit Reaction
* When a high-severity alert triggers (e.g. Squawk 7700 or M5.0+ earthquake), the gateway dispatches a command over `/ws/copilot` to:
  * Fly the active browser Cesium globe to the event coordinates.
  * Turn on relevant data layers (e.g. earthquakes, weather radar).
  * Flash an alert banner on the HUD.

### Milestone 3.3: Pattern-of-Life (PoL) Analytics
* Detect "Dark Vessels": Marine vessels that cease broadcasting AIS transmissions inside territorial chokepoints.
* Track route deviations: Aircraft departing substantially from standard flight corridors.

---

## 5. Phase 4: Kinematics & Sovereign Hardware SDR

### Milestone 4.1: Intercept & Collision Kinematics (CPA / TCPA)
* Implement vector geometry calculating:
  * **CPA (Closest Point of Approach):** Minimum predicted distance between moving entities.
  * **TCPA (Time to CPA):** Estimated time in seconds until point of closest approach.

### Milestone 4.2: 3D Engagement & Sensor Range Domes
* Project visual 3D radar domes and observation envelopes around airports and naval stations in Cesium.

### Milestone 4.3: Sovereign Edge SDR Radio Receiver ($30 USB Dongle)
* Connect an RTL-SDR USB dongle running a lightweight container with `dump1090-fa` (1090MHz ADS-B) and `rtl-ais` (162MHz VHF).
* Feed raw radio packets directly into Tile38/TimescaleDB.
* **Outcome:** Your system tracks air and maritime traffic within 200+ miles completely offline, with zero reliance on external APIs or internet connectivity.

---

## 6. Implementation Deliverables & Next Action

To initiate Phase 2 immediately, the recommended next steps are:
1. **Create `sidecar/config/watchlists.yml`** for user-defined AOIs, airports, and alert thresholds.
2. **Build `sidecar/proxy-tee/src/alerts/` engine** to detect squawks, fire proximity, and cable anchor-drag events.
3. **Build the Telegram / Webhook notification bridge** with message formatting and direct globe links.
