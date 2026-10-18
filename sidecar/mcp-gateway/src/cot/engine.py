"""
Cursor-on-Target (CoT) & ATAK Tactical Data Link Engine
Converts Gods-Eye-View spatial tracks, radar domes, and tactical alerts into
standard MIL-STD-2525 Cursor-on-Target XML and broadcasts over UDP (port 4242 & 6969)
for real-time consumption by ATAK, WinTAK, and iTAK mobile clients.
"""
import os
import json
import asyncio
import socket
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import xml.etree.ElementTree as ET

from ..router import router

CONFIG_PATH = os.environ.get(
    "WATCHLIST_CONFIG_PATH",
    "/app/config/watchlists.json"
)

DEFAULT_COT_CONFIG = {
    "enabled": True,
    "target_host": "255.255.255.255",
    "target_port": 4242,
    "unicast_targets": ["127.0.0.1", "host.docker.internal"],
    "broadcast_interval_sec": 3.0,
    "stale_offset_sec": 60,
    "auto_start": True,
    "include_flights": True,
    "include_vessels": True,
    "include_sdr_rf": True,
    "include_alerts": True,
    "include_aois": True,
}


def build_cot_xml(
    uid: str,
    cot_type: str,
    lat: float,
    lon: float,
    hae_m: float = 0.0,
    speed_mps: float = 0.0,
    course_deg: float = 0.0,
    callsign: str = "",
    remarks: str = "",
    stale_sec: int = 60,
    emergency_type: Optional[str] = None,
) -> str:
    """Constructs a MIL-STD-2525 Cursor-on-Target 2.0 XML packet."""
    now = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=stale_sec)

    time_fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
    time_str = now.strftime(time_fmt)
    stale_str = stale.strftime(time_fmt)

    event = ET.Element("event", {
        "version": "2.0",
        "uid": str(uid),
        "type": str(cot_type),
        "how": "m-g",
        "time": time_str,
        "start": time_str,
        "stale": stale_str,
    })

    ET.SubElement(event, "point", {
        "lat": f"{float(lat):.6f}",
        "lon": f"{float(lon):.6f}",
        "hae": f"{float(hae_m):.1f}",
        "ce": "10.0",
        "le": "15.0",
    })

    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact", {"callsign": str(callsign or uid)})
    ET.SubElement(detail, "track", {
        "speed": f"{float(speed_mps):.1f}",
        "course": f"{float(course_deg):.1f}",
    })

    if emergency_type:
        ET.SubElement(detail, "emergency", {
            "cancel": "false",
            "type": emergency_type,
        }).text = emergency_type

    if remarks:
        ET.SubElement(detail, "remarks").text = str(remarks)

    # ATAK Visual Marker Styling
    ET.SubElement(detail, "precisionlocation", {
        "altsrc": "GPS",
        "geopointsrc": "USER",
    })

    return ET.tostring(event, encoding="utf-8", xml_declaration=True).decode("utf-8")


class CotEngine:
    def __init__(self):
        self.config: Dict[str, Any] = dict(DEFAULT_COT_CONFIG)
        self.is_running: bool = False
        self._task: Optional[asyncio.Task] = None
        self._sock: Optional[socket.socket] = None
        self.total_packets_sent: int = 0
        self.start_time: Optional[datetime] = None
        self.last_broadcast_time: Optional[datetime] = None
        self.last_entity_count: int = 0
        self.error_message: Optional[str] = None
        self.reload_config()

    def reload_config(self):
        """Loads CoT settings from watchlists.json."""
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cfg = data.get("cot_broadcast")
                    if cfg and isinstance(cfg, dict):
                        self.config.update(cfg)
        except Exception as e:
            print(f"[CotEngine] Error reading CoT config: {e}")

    def _init_socket(self):
        """Creates a non-blocking UDP broadcast socket."""
        try:
            if self._sock:
                self._sock.close()
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.setblocking(False)
        except Exception as e:
            self.error_message = f"Failed to initialize UDP broadcast socket: {e}"
            print(f"[CotEngine] Socket error: {e}")

    def get_status(self) -> Dict[str, Any]:
        """Returns broadcaster status, packet transmission rates, and target endpoints."""
        now = datetime.now(timezone.utc)
        runtime_sec = (now - self.start_time).total_seconds() if self.start_time else 0.0
        rate = (self.total_packets_sent / runtime_sec) if runtime_sec > 1.0 else 0.0

        targets = [f"{self.config.get('target_host', '255.255.255.255')}:{self.config.get('target_port', 4242)}"]
        for u in self.config.get("unicast_targets", []):
            targets.append(f"{u}:{self.config.get('target_port', 4242)}")

        return {
            "status": "streaming" if self.is_running else "standby",
            "is_running": self.is_running,
            "target_port": self.config.get("target_port", 4242),
            "broadcast_targets": targets,
            "broadcast_interval_sec": self.config.get("broadcast_interval_sec", 3.0),
            "total_packets_sent": self.total_packets_sent,
            "packet_rate_hz": round(rate, 1),
            "last_entity_count": self.last_entity_count,
            "last_broadcast_time": self.last_broadcast_time.isoformat() if self.last_broadcast_time else None,
            "error_message": self.error_message,
        }

    def configure(
        self,
        enabled: Optional[bool] = None,
        target_host: Optional[str] = None,
        target_port: Optional[int] = None,
        unicast_targets: Optional[List[str]] = None,
        broadcast_interval_sec: Optional[float] = None,
        stale_offset_sec: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Dynamically reconfigures CoT broadcaster parameters."""
        if enabled is not None:
            self.config["enabled"] = enabled
        if target_host:
            self.config["target_host"] = target_host
        if target_port is not None:
            self.config["target_port"] = target_port
        if unicast_targets is not None:
            self.config["unicast_targets"] = unicast_targets
        if broadcast_interval_sec is not None:
            self.config["broadcast_interval_sec"] = broadcast_interval_sec
        if stale_offset_sec is not None:
            self.config["stale_offset_sec"] = stale_offset_sec

        return {"status": "configured", "config": self.config}

    def send_cot_packet(self, xml_text: str):
        """Transmits raw CoT XML to broadcast address and all unicast targets."""
        if not self._sock:
            self._init_socket()
        if not self._sock:
            return

        payload = xml_text.encode("utf-8")
        port = int(self.config.get("target_port", 4242))

        # 1. Main Broadcast (Subnet broadcast e.g. 255.255.255.255)
        main_host = self.config.get("target_host", "255.255.255.255")
        try:
            self._sock.sendto(payload, (main_host, port))
            self.total_packets_sent += 1
        except Exception:
            pass

        # 2. Unicast Targets (Phone IPs, host.docker.internal, etc.)
        for host in self.config.get("unicast_targets", []):
            try:
                self._sock.sendto(payload, (host, port))
                self.total_packets_sent += 1
            except Exception:
                pass

        self.last_broadcast_time = datetime.now(timezone.utc)

    def broadcast_alert(self, alert_dict: Dict[str, Any]):
        """Immediately transmits a tactical alert as an ATAK high-priority emergency marker."""
        lat = alert_dict.get("lat") or 12.9716
        lon = alert_dict.get("lon") or 77.5946
        title = alert_dict.get("title", "Tactical Sentinel Alert")
        msg = alert_dict.get("message", "")
        uid = f"ALERT_{alert_dict.get('id', int(datetime.now(timezone.utc).timestamp()))}"

        xml = build_cot_xml(
            uid=uid,
            cot_type="t-x-d-a",  # ATAK Tactical Incursion / Emergency Diamond
            lat=lat,
            lon=lon,
            hae_m=100.0,
            callsign=f"🚨 {title}",
            remarks=msg,
            stale_sec=300,  # Alert stays visible in ATAK for 5 minutes
            emergency_type="911 Alert",
        )
        self.send_cot_packet(xml)

    async def start(self) -> Dict[str, Any]:
        """Starts the background CoT streaming daemon."""
        if self.is_running:
            return {"status": "already_running"}

        self._init_socket()
        self.is_running = True
        self.start_time = datetime.now(timezone.utc)
        self.error_message = None
        self._task = asyncio.create_task(self._cot_streaming_loop())
        print(f"[CotEngine] Started Cursor-on-Target (CoT) UDP streaming on port {self.config.get('target_port', 4242)}")
        return {"status": "started", "port": self.config.get("target_port", 4242)}

    def stop(self) -> Dict[str, Any]:
        """Stops the CoT streaming daemon."""
        if not self.is_running:
            return {"status": "not_running"}

        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        if self._sock:
            self._sock.close()
            self._sock = None

        print("[CotEngine] CoT UDP streaming stopped.")
        return {"status": "stopped", "total_packets_sent": self.total_packets_sent}

    async def _cot_streaming_loop(self):
        """Continuous background loop streaming active contacts to ATAK."""
        stale_sec = int(self.config.get("stale_offset_sec", 60))

        while self.is_running:
            try:
                entity_count = 0

                # 1. Pull active flights & vessels from Tile38
                if router.redis:
                    for col in ["flights", "vessels", "military"]:
                        try:
                            # Scan objects within collection
                            res = await router.redis.execute_command("SCAN", col, "LIMIT", 100)
                            if res and isinstance(res, (list, tuple)) and len(res) >= 2 and isinstance(res[1], list):
                                for item in res[1]:
                                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                                        ent_id = str(item[0])
                                        geo = json.loads(item[1])
                                        coords = geo.get("coordinates", [])
                                        if len(coords) >= 2:
                                            lon, lat = coords[0], coords[1]
                                            alt = coords[2] if len(coords) > 2 else 0.0

                                            fields_dict = {}
                                            if len(item) > 2 and isinstance(item[2], list):
                                                f_list = item[2]
                                                for i in range(0, len(f_list) - 1, 2):
                                                    fields_dict[f_list[i]] = f_list[i + 1]

                                            if "alt" in fields_dict:
                                                try:
                                                    alt = float(fields_dict["alt"])
                                                except (ValueError, TypeError):
                                                    pass

                                            spd = 0.0
                                            if "spd" in fields_dict:
                                                try:
                                                    spd = float(fields_dict["spd"]) * 0.514444  # knots to m/s
                                                except (ValueError, TypeError):
                                                    pass

                                            hdg = 0.0
                                            if "hdg" in fields_dict:
                                                try:
                                                    hdg = float(fields_dict["hdg"])
                                                except (ValueError, TypeError):
                                                    pass

                                            # Determine MIL-STD-2525 Type
                                            if col == "flights":
                                                cot_type = "a-f-A-C-F"  # Civil Fixed Wing Aircraft
                                            elif col == "military":
                                                cot_type = "a-f-A-M-F"  # Military Air
                                            else:
                                                cot_type = "a-f-S-C"    # Surface Vessel

                                            xml = build_cot_xml(
                                                uid=f"{col.upper()}_{ent_id}",
                                                cot_type=cot_type,
                                                lat=lat,
                                                lon=lon,
                                                hae_m=alt,
                                                speed_mps=spd,
                                                course_deg=hdg,
                                                callsign=ent_id.upper(),
                                                remarks=f"Layer: {col} | Tracked by Gods-Eye-View Sidecar",
                                                stale_sec=stale_sec,
                                            )
                                            self.send_cot_packet(xml)
                                            entity_count += 1
                        except Exception:
                            pass

                # 2. Broadcast Direct SDR RF contacts (Mode-S / ADS-B / AIS)
                if self.config.get("include_sdr_rf", True):
                    try:
                        from ..sdr.engine import sdr_engine
                        for c in sdr_engine.get_contacts():
                            lat = c.get("lat")
                            lon = c.get("lon")
                            if lat is not None and lon is not None:
                                callsign = c.get("callsign") or c.get("icao", "RF_TRACK")
                                alt = float(c.get("alt_m") or c.get("alt", 0.0))
                                spd = float(c.get("speed_mps") or c.get("speed", 0.0))
                                hdg = float(c.get("heading") or c.get("track", 0.0))
                                rssi = c.get("rssi_dbfs", -20.0)
                                is_emergency = c.get("is_emergency", False)

                                cot_type = "t-x-d-a" if is_emergency else ("a-f-A-C-F" if c.get("type") == "adsb" else "a-f-S-C")
                                emergency_type = "911 Alert" if is_emergency else None

                                xml = build_cot_xml(
                                    uid=f"SDR_{c.get('icao') or c.get('mmsi') or callsign}",
                                    cot_type=cot_type,
                                    lat=lat,
                                    lon=lon,
                                    hae_m=alt,
                                    speed_mps=spd,
                                    course_deg=hdg,
                                    callsign=f"📡 {callsign}",
                                    remarks=f"Direct RF Ingestion (RTL-SDR) | RSSI: {rssi} dBFS | Squawk: {c.get('squawk', 'N/A')}",
                                    stale_sec=stale_sec,
                                    emergency_type=emergency_type,
                                )
                                self.send_cot_packet(xml)
                                entity_count += 1
                    except Exception:
                        pass

                # 3. Broadcast AOI Perimeters to ATAK
                if self.config.get("include_aois", True):
                    from ..alerts.engine import tripwire_engine
                    for aoi in tripwire_engine.get_aois():
                        if not aoi.get("enabled", True):
                            continue
                        xml = build_cot_xml(
                            uid=f"AOI_{aoi['id']}",
                            cot_type="b-m-p-s-p-loc",  # Tactical perimeter point / center
                            lat=aoi["lat"],
                            lon=aoi["lon"],
                            hae_m=10.0,
                            callsign=f"🛡️ {aoi['name'].upper()} AOI",
                            remarks=f"Radius: {aoi.get('radius_km', 50)}km | {aoi.get('description', '')}",
                            stale_sec=300,
                        )
                        self.send_cot_packet(xml)
                        entity_count += 1

                # 4. Broadcast Radar Surveillance Stations to ATAK
                try:
                    from ..radar.engine import radar_engine
                    for st in radar_engine.get_stations(enabled_only=True):
                        xml = build_cot_xml(
                            uid=f"RADAR_{st['id']}",
                            cot_type="a-f-G-I-U-R",  # Ground Installation Radar
                            lat=st["lat"],
                            lon=st["lon"],
                            hae_m=st["antenna_height_m"],
                            callsign=f"📡 {st['name']}",
                            remarks=f"Band: {st['band']} | Max Range: {st['max_range_km']}km | FL330 Horizon: {st.get('horizon_high_alt_10000m_km', 'N/A')}km",
                            stale_sec=300,
                        )
                        self.send_cot_packet(xml)
                        entity_count += 1
                except Exception:
                    pass

                self.last_entity_count = entity_count
                await asyncio.sleep(float(self.config.get("broadcast_interval_sec", 3.0)))

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.error_message = str(e)
                await asyncio.sleep(4.0)


cot_engine = CotEngine()
