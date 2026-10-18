# Sovereign Edge SDR Radio Receiver ($30 Hardware)

This module turns your Gods-Eye-View Sidecar into an **autonomous, offline-capable radar station**. By connecting a low-cost Software-Defined Radio (SDR) USB dongle, you track aircraft transponders and marine AIS broadcasts directly over the air with zero internet connectivity and zero external API dependencies.

---

## 1. Supported Hardware

Any standard RTL2832U-based DVB-T USB receiver works:
* **Recommended:** **RTL-SDR Blog V4** (~$30 USD) with dipole antenna kit.
* **FlightAware Pro Stick Plus** (includes built-in 1090MHz bandpass filter & LNA).
* **Nooelec NESDR SMArTee v2 / v3**.

---

## 2. Monitored Radio Frequencies

| Band | Frequency | Target Protocols | Typical Range |
| :--- | :--- | :--- | :--- |
| **ADS-B / Mode-S** | **1090 MHz** | Civil & Military Transponders (altitude, speed, heading, squawks) | 100 – 250 Nautical Miles |
| **UAT (US Only)** | **978 MHz** | General Aviation & Weather Data | 50 – 120 Nautical Miles |
| **Marine AIS** | **161.975 & 162.025 MHz** | VHF Marine Ships, Tankers, Cargo Vessels | 25 – 60 Nautical Miles |

---

## 3. Operational Modes

The Sovereign SDR Engine in `mcp-gateway` supports three distinct operating modes:

1. **`synthetic_rf` (Default / Simulation Mode):**
   - High-fidelity autonomous RF generator centered on Bangalore Kempegowda / HAL airspace.
   - Emulates real Mode-S transponders with fluctuating RF RSSI (-9 to -25 dBFS), Doppler kinematic vectors, and squawk alerts.
   - Allows instant testing and development without requiring physical SDR hardware connected.

2. **`tcp_sbs` (BaseStation Port 30003 Stream):**
   - Universal standard stream output by `dump1090-fa`, `readsb`, `dump978`, or `PiAware`.
   - Connects to any local SDR receiver on the host or local network (`127.0.0.1:30003`).

3. **`http_json` (readsb / dump1090 JSON Polling):**
   - Polls `http://localhost:8080/data/aircraft.json` or `tar1090` feeds every 2 seconds.

---

## 4. MCP Tools & API Reference

### 1. `get_sdr_status`
```bash
curl -X GET http://localhost:8000/api/sdr/status
```

### 2. `start_sdr_stream`
```bash
curl -X POST http://localhost:8000/api/sdr/start
```

### 3. `get_sdr_contacts`
```bash
curl -X GET http://localhost:8000/api/sdr/contacts
```

### 4. `configure_sdr_receiver`
```bash
curl -X POST http://localhost:8000/api/sdr/configure \
  -H "Content-Type: application/json" \
  -d '{"mode": "tcp_sbs", "source_host": "192.168.1.100", "source_port": 30003}'
```

### 5. `stop_sdr_stream`
```bash
curl -X POST http://localhost:8000/api/sdr/stop
```
