"""
Sovereign Edge SDR Radio Ingestion Engine
Ingests local RF Mode-S / ADS-B and AIS transponder signals from physical RTL-SDR dongles
(via dump1090 / readsb / rtl-ais / SBS port 30003) or runs high-fidelity sovereign RF simulation.
"""
import os
import json
import asyncio
import math
import random
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import httpx

from ..router import router
from ..ws_copilot import ws_copilot

CONFIG_PATH = os.environ.get(
    "WATCHLIST_CONFIG_PATH",
    "/app/config/watchlists.json"
)

DEFAULT_SDR_CONFIG = {
    "enabled": False,
    "mode": "synthetic_rf",
    "station_name": "Bangalore Sovereign Edge SDR-1",
    "frequency_mhz": 1090.0,
    "source_host": "127.0.0.1",
    "source_port": 30003,
    "poll_interval_sec": 2.0,
    "http_url": "http://127.0.0.1:8080/data/aircraft.json",
    "auto_start": False,
}


class SdrEngine:
    def __init__(self):
        self.config: Dict[str, Any] = dict(DEFAULT_SDR_CONFIG)
        self.is_running: bool = False
        self._task: Optional[asyncio.Task] = None
        self.contacts: Dict[str, Dict[str, Any]] = {}
        self.total_messages: int = 0
        self.start_time: Optional[datetime] = None
        self.last_message_time: Optional[datetime] = None
        self.error_message: Optional[str] = None
        self.reload_config()

    def reload_config(self):
        """Loads SDR settings from watchlists.json or environment."""
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cfg = data.get("sdr_receiver")
                    if cfg and isinstance(cfg, dict):
                        self.config.update(cfg)
        except Exception as e:
            print(f"[SdrEngine] Error reading SDR config: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Returns receiver health, message metrics, and active sovereign RF contacts."""
        now = datetime.now(timezone.utc)
        runtime_sec = (now - self.start_time).total_seconds() if self.start_time else 0.0
        msg_rate = (self.total_messages / runtime_sec) if runtime_sec > 1.0 else 0.0

        # Purge contacts not seen in last 120 seconds
        active_cutoff = now - timedelta(seconds=120)
        active_contacts = [
            c for c in self.contacts.values()
            if datetime.fromisoformat(c["last_seen"]) > active_cutoff
        ]

        return {
            "status": "online" if self.is_running else "standby",
            "is_running": self.is_running,
            "mode": self.config.get("mode", "synthetic_rf"),
            "station_name": self.config.get("station_name", "Edge SDR Station"),
            "frequency_mhz": self.config.get("frequency_mhz", 1090.0),
            "source_host": self.config.get("source_host"),
            "source_port": self.config.get("source_port"),
            "total_messages": self.total_messages,
            "message_rate_hz": round(msg_rate, 1),
            "active_rf_contacts_count": len(active_contacts),
            "last_message_time": self.last_message_time.isoformat() if self.last_message_time else None,
            "error_message": self.error_message,
        }

    def configure(
        self,
        mode: Optional[str] = None,
        station_name: Optional[str] = None,
        frequency_mhz: Optional[float] = None,
        source_host: Optional[str] = None,
        source_port: Optional[int] = None,
        http_url: Optional[str] = None,
        poll_interval_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Updates SDR receiver configuration."""
        if mode:
            if mode not in ["synthetic_rf", "tcp_sbs", "http_json"]:
                return {"status": "error", "message": f"Invalid mode '{mode}'. Choose 'synthetic_rf', 'tcp_sbs', or 'http_json'."}
            self.config["mode"] = mode
        if station_name:
            self.config["station_name"] = station_name
        if frequency_mhz is not None:
            self.config["frequency_mhz"] = frequency_mhz
        if source_host:
            self.config["source_host"] = source_host
        if source_port is not None:
            self.config["source_port"] = source_port
        if http_url:
            self.config["http_url"] = http_url
        if poll_interval_sec is not None:
            self.config["poll_interval_sec"] = poll_interval_sec

        return {"status": "configured", "config": self.config}

    async def start(self) -> Dict[str, Any]:
        """Starts the sovereign SDR background ingestion stream."""
        if self.is_running:
            return {"status": "already_running", "mode": self.config.get("mode")}

        self.is_running = True
        self.start_time = datetime.now(timezone.utc)
        self.error_message = None
        mode = self.config.get("mode", "synthetic_rf")

        if mode == "synthetic_rf":
            self._task = asyncio.create_task(self._synthetic_rf_loop())
        elif mode == "tcp_sbs":
            self._task = asyncio.create_task(self._tcp_sbs_loop())
        elif mode == "http_json":
            self._task = asyncio.create_task(self._http_json_loop())
        else:
            self.is_running = False
            return {"status": "error", "message": f"Unsupported SDR mode: {mode}"}

        print(f"[SdrEngine] Started sovereign SDR ingestion in mode '{mode}'")
        return {"status": "started", "mode": mode, "station": self.config.get("station_name")}

    def stop(self) -> Dict[str, Any]:
        """Stops SDR ingestion."""
        if not self.is_running:
            return {"status": "not_running"}

        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

        print("[SdrEngine] Sovereign SDR ingestion stopped.")
        return {"status": "stopped", "total_messages": self.total_messages}

    async def _record_contact_point(self, contact: Dict[str, Any]):
        """Persists contact into Tile38 hot cache, TimescaleDB, and sends live Co-Pilot update."""
        now = datetime.now(timezone.utc)
        hex_id = contact["hex_ident"].lower()
        self.total_messages += 1
        self.last_message_time = now

        contact["last_seen"] = now.isoformat()
        if hex_id not in self.contacts:
            contact["first_seen"] = now.isoformat()
            contact["messages_count"] = 1
        else:
            contact["messages_count"] = self.contacts[hex_id].get("messages_count", 0) + 1
            contact["first_seen"] = self.contacts[hex_id].get("first_seen", now.isoformat())

        self.contacts[hex_id] = contact

        lat = contact.get("lat")
        lon = contact.get("lon")
        alt_m = contact.get("altitude_m", 0.0)
        speed = contact.get("speed_kts", 0.0)
        hdg = contact.get("heading_deg", 0.0)
        squawk = contact.get("squawk", "")
        rssi = contact.get("rssi_dbfs", -18.0)

        if lat is None or lon is None:
            return

        # 1. Tile38 Hot In-Memory Geo-cache
        try:
            r = router.get_tile38_sync()
            if r:
                r.execute_command(
                    "SET", "flights", hex_id,
                    "FIELD", "alt", alt_m,
                    "FIELD", "hdg", hdg,
                    "FIELD", "spd", speed,
                    "FIELD", "rssi", rssi,
                    "FIELD", "sdr_local", 1,
                    "EX", 120,
                    "POINT", lat, lon
                )
        except Exception as e:
            pass

        # 2. TimescaleDB Telemetry History
        if router.pg_pool:
            try:
                payload_json = json.dumps({
                    "callsign": contact.get("callsign", ""),
                    "squawk": squawk,
                    "rssi_dbfs": rssi,
                    "source": "sdr_local_rf",
                    "station": self.config.get("station_name", "Sovereign Edge SDR"),
                    "frequency_mhz": self.config.get("frequency_mhz", 1090.0),
                })
                async with router.pg_pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO telemetry_history (
                            ts, layer, entity_id, h3_res5, h3_res9, geom, altitude_m, speed_kts, heading_deg, payload
                        ) VALUES (
                            $1, 'flights', $2, 0, 0, ST_SetSRID(ST_MakePoint($3, $4, $5), 4326)::geography, $5, $6, $7, $8
                        )
                        """,
                        now,
                        hex_id,
                        lon,
                        lat,
                        alt_m,
                        speed,
                        hdg,
                        payload_json
                    )
            except Exception as e:
                pass

    async def _synthetic_rf_loop(self):
        """High-fidelity simulation of an RTL-SDR USB dongle tuned to 1090MHz ADS-B in Bangalore."""
        print("[SdrEngine] Synthetic Mode-S RF receiver active on 1090MHz (Bangalore local bubble)")
        # Simulated aircraft in local Bangalore terminal area
        sim_flights = [
            {
                "hex_ident": "801793",
                "callsign": "AIC505",
                "lat": 13.1989,
                "lon": 77.7088,
                "altitude_m": 2400.0,
                "speed_kts": 240.0,
                "heading_deg": 190.0,
                "squawk": "1200",
                "rssi_dbfs": -12.4,
            },
            {
                "hex_ident": "8021a4",
                "callsign": "IGO881",
                "lat": 12.9550,
                "lon": 77.6720,
                "altitude_m": 1850.0,
                "speed_kts": 195.0,
                "heading_deg": 270.0,
                "squawk": "3412",
                "rssi_dbfs": -14.8,
            },
            {
                "hex_ident": "800df9",
                "callsign": "SEJ302",
                "lat": 13.1420,
                "lon": 77.6100,
                "altitude_m": 3100.0,
                "speed_kts": 280.0,
                "heading_deg": 355.0,
                "squawk": "7700",  # Emergency squawk for sovereign tripwire trigger
                "rssi_dbfs": -9.2,
            },
        ]

        while self.is_running:
            try:
                for fl in sim_flights:
                    # Kinematic step
                    rad = math.radians(fl["heading_deg"])
                    dist_km = (fl["speed_kts"] * 1.852) * (2.0 / 3600.0)  # distance in 2 seconds
                    dlat = (dist_km / 6371.0) * (180.0 / math.pi) * math.cos(rad)
                    dlon = (dist_km / 6371.0) * (180.0 / math.pi) * (math.sin(rad) / math.cos(math.radians(fl["lat"])))
                    fl["lat"] += dlat
                    fl["lon"] += dlon

                    # Realistic RF signal fluctuation (-0.5 to +0.5 dB)
                    fl["rssi_dbfs"] = round(fl["rssi_dbfs"] + random.uniform(-0.4, 0.4), 1)

                    await self._record_contact_point(dict(fl))

                await asyncio.sleep(self.config.get("poll_interval_sec", 2.0))
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.error_message = str(e)
                await asyncio.sleep(3.0)

    async def _tcp_sbs_loop(self):
        """Connects to a real RTL-SDR BaseStation SBS-1 TCP stream (port 30003)."""
        host = self.config.get("source_host", "127.0.0.1")
        port = int(self.config.get("source_port", 30003))

        while self.is_running:
            try:
                print(f"[SdrEngine] Connecting to SBS TCP feed at {host}:{port}...")
                reader, writer = await asyncio.open_connection(host, port)
                print(f"[SdrEngine] Connected to RTL-SDR SBS feed at {host}:{port}")

                while self.is_running:
                    line = await reader.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="ignore").strip()
                    parts = text.split(",")
                    if len(parts) >= 11 and parts[0] == "MSG":
                        msg_type = parts[1]
                        hex_id = parts[4].lower()
                        callsign = parts[10].strip() if len(parts) > 10 else None
                        alt_ft = float(parts[11]) if len(parts) > 11 and parts[11] else None
                        spd_kts = float(parts[12]) if len(parts) > 12 and parts[12] else None
                        track_deg = float(parts[13]) if len(parts) > 13 and parts[13] else None
                        lat = float(parts[14]) if len(parts) > 14 and parts[14] else None
                        lon = float(parts[15]) if len(parts) > 15 and parts[15] else None
                        squawk = parts[17].strip() if len(parts) > 17 else ""

                        contact = self.contacts.get(hex_id, {
                            "hex_ident": hex_id,
                            "callsign": callsign or hex_id.upper(),
                            "lat": lat,
                            "lon": lon,
                            "altitude_m": (alt_ft * 0.3048) if alt_ft else 0.0,
                            "speed_kts": spd_kts or 0.0,
                            "heading_deg": track_deg or 0.0,
                            "squawk": squawk,
                            "rssi_dbfs": -15.0,
                        })

                        if lat is not None: contact["lat"] = lat
                        if lon is not None: contact["lon"] = lon
                        if alt_ft is not None: contact["altitude_m"] = round(alt_ft * 0.3048, 1)
                        if spd_kts is not None: contact["speed_kts"] = spd_kts
                        if track_deg is not None: contact["heading_deg"] = track_deg
                        if callsign: contact["callsign"] = callsign
                        if squawk: contact["squawk"] = squawk

                        if contact.get("lat") is not None and contact.get("lon") is not None:
                            await self._record_contact_point(contact)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.error_message = f"Connection failed to {host}:{port}: {e}"
                await asyncio.sleep(5.0)

    async def _http_json_loop(self):
        """Polls dump1090/readsb HTTP JSON endpoint (aircraft.json)."""
        url = self.config.get("http_url", "http://127.0.0.1:8080/data/aircraft.json")
        poll_sec = self.config.get("poll_interval_sec", 2.0)

        async with httpx.AsyncClient(timeout=4.0) as client:
            while self.is_running:
                try:
                    res = await client.get(url)
                    if res.status_code == 200:
                        data = res.json()
                        aircraft_list = data.get("aircraft", [])
                        for ac in aircraft_list:
                            hex_id = ac.get("hex", "").lower()
                            lat = ac.get("lat")
                            lon = ac.get("lon")
                            if not hex_id or lat is None or lon is None:
                                continue

                            alt_baro = ac.get("alt_baro")
                            alt_m = (float(alt_baro) * 0.3048) if (alt_baro and str(alt_baro) != "ground") else 0.0
                            speed = float(ac.get("speed", 0.0))
                            track = float(ac.get("track", 0.0))
                            callsign = ac.get("flight", hex_id.upper()).strip()
                            squawk = ac.get("squawk", "")
                            rssi = float(ac.get("rssi", -18.0))

                            contact = {
                                "hex_ident": hex_id,
                                "callsign": callsign,
                                "lat": lat,
                                "lon": lon,
                                "altitude_m": alt_m,
                                "speed_kts": speed,
                                "heading_deg": track,
                                "squawk": squawk,
                                "rssi_dbfs": rssi,
                            }
                            await self._record_contact_point(contact)
                    await asyncio.sleep(poll_sec)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.error_message = f"HTTP poll failed: {e}"
                    await asyncio.sleep(5.0)

    def get_contacts(self) -> List[Dict[str, Any]]:
        """Returns all currently active contacts received via sovereign edge RF."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=120)
        return [
            c for c in self.contacts.values()
            if datetime.fromisoformat(c["last_seen"]) > cutoff
        ]


sdr_engine = SdrEngine()
