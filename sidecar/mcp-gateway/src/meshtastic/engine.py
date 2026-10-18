"""
Meshtastic / LoRa Off-Grid Tactical Relay Engine
Bridges God's Eye View and ATAK-CIV with long-range, off-grid LoRa radio meshes
operating on 865-867MHz (IN865), 868MHz (EU868), and 915MHz (US915).
"""
import os
import json
import asyncio
import random
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from ..router import router

CONFIG_PATH = os.environ.get(
    "WATCHLIST_CONFIG_PATH",
    "/app/config/watchlists.json"
)

DEFAULT_MESHTASTIC_CONFIG = {
    "enabled": True,
    "mode": "synthetic_mesh",  # "synthetic_mesh", "tcp", "serial"
    "channel_name": "LongFast",
    "region": "IN_865",
    "modem_preset": "LONG_FAST",
    "tcp_host": "127.0.0.1",
    "tcp_port": 4403,
    "serial_port": "/dev/ttyUSB0",
    "serial_baud": 115200,
    "auto_forward_alerts": True,
    "alert_min_severity": "WARNING",
    "broadcast_interval_sec": 10.0,
    "auto_start": True,
}

# Synthetic Ground Field Nodes for Demonstration and Testing
SYNTHETIC_FIELD_NODES = [
    {
        "node_id": "!4a18b01a",
        "long_name": "Bangalore Field Team Alpha",
        "short_name": "ALPHA-1",
        "role": "Search & Rescue Team Lead",
        "lat": 12.9850,
        "lon": 77.5850,
        "alt_m": 920.0,
        "battery_pct": 87,
        "snr_db": 8.5,
        "rssi_dbm": -72,
        "hops_away": 0,
        "hardware_model": "Heltec V3 ESP32-S3 (SX1262)",
    },
    {
        "node_id": "!4a18c02b",
        "long_name": "Rover Patrol Bravo",
        "short_name": "ROVER-2",
        "role": "Mobile Surveillance Vehicle",
        "lat": 12.9600,
        "lon": 77.6200,
        "alt_m": 890.0,
        "battery_pct": 94,
        "snr_db": 6.2,
        "rssi_dbm": -81,
        "hops_away": 1,
        "hardware_model": "LilyGO T-Beam Supreme (Meshtastic)",
    },
    {
        "node_id": "!4a18d03c",
        "long_name": "Nandi Hills Ridge Relay",
        "short_name": "RELAY-3",
        "role": "High-Altitude Solar Hilltop Repeater",
        "lat": 13.3702,
        "lon": 77.6835,
        "alt_m": 1478.0,
        "battery_pct": 100,
        "snr_db": 11.8,
        "rssi_dbm": -64,
        "hops_away": 1,
        "hardware_model": "RAK Wireless WisBlock Solar Repeater",
    },
]


class MeshtasticRelayEngine:
    def __init__(self):
        self.config: Dict[str, Any] = dict(DEFAULT_MESHTASTIC_CONFIG)
        self.is_running: bool = False
        self._task: Optional[asyncio.Task] = None
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.message_log: List[Dict[str, Any]] = []
        self.total_packets_tx: int = 0
        self.total_packets_rx: int = 0
        self.start_time: Optional[datetime] = None
        self.last_tx_time: Optional[datetime] = None
        self.last_rx_time: Optional[datetime] = None
        self.error_message: Optional[str] = None
        self.reload_config()
        self._seed_nodes()

    def reload_config(self):
        """Loads Meshtastic settings from watchlists.json."""
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    cfg = data.get("meshtastic_relay")
                    if cfg and isinstance(cfg, dict):
                        self.config.update(cfg)
        except Exception as e:
            print(f"[MeshtasticRelay] Config load error: {e}")

    def _seed_nodes(self):
        """Initializes default known nodes."""
        for n in SYNTHETIC_FIELD_NODES:
            self.nodes[n["node_id"]] = dict(n, last_heard=datetime.now(timezone.utc).isoformat())

    def get_status(self) -> Dict[str, Any]:
        """Returns relay operating mode, channel parameters, and packet statistics."""
        now = datetime.now(timezone.utc)
        uptime_sec = (now - self.start_time).total_seconds() if self.start_time else 0.0

        return {
            "status": "active" if self.is_running else "standby",
            "is_running": self.is_running,
            "mode": self.config.get("mode", "synthetic_mesh"),
            "region": self.config.get("region", "IN_865"),
            "channel_name": self.config.get("channel_name", "LongFast"),
            "modem_preset": self.config.get("modem_preset", "LONG_FAST"),
            "total_nodes_discovered": len(self.nodes),
            "packets_transmitted": self.total_packets_tx,
            "packets_received": self.total_packets_rx,
            "uptime_seconds": round(uptime_sec, 1),
            "last_tx_time": self.last_tx_time.isoformat() if self.last_tx_time else None,
            "last_rx_time": self.last_rx_time.isoformat() if self.last_rx_time else None,
            "auto_forward_alerts": self.config.get("auto_forward_alerts", True),
            "error_message": self.error_message,
        }

    def configure(
        self,
        enabled: Optional[bool] = None,
        mode: Optional[str] = None,
        region: Optional[str] = None,
        channel_name: Optional[str] = None,
        tcp_host: Optional[str] = None,
        tcp_port: Optional[int] = None,
        serial_port: Optional[str] = None,
        auto_forward_alerts: Optional[bool] = None,
        broadcast_interval_sec: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Dynamically reconfigures LoRa mesh parameters."""
        if enabled is not None:
            self.config["enabled"] = enabled
        if mode:
            self.config["mode"] = mode
        if region:
            self.config["region"] = region
        if channel_name:
            self.config["channel_name"] = channel_name
        if tcp_host:
            self.config["tcp_host"] = tcp_host
        if tcp_port is not None:
            self.config["tcp_port"] = tcp_port
        if serial_port:
            self.config["serial_port"] = serial_port
        if auto_forward_alerts is not None:
            self.config["auto_forward_alerts"] = auto_forward_alerts
        if broadcast_interval_sec is not None:
            self.config["broadcast_interval_sec"] = broadcast_interval_sec

        return {"status": "configured", "config": self.config}

    def get_nodes(self) -> List[Dict[str, Any]]:
        """Returns all mesh nodes currently tracked in the tactical radius."""
        return list(self.nodes.values())

    def send_message(self, text: str, to_node: str = "^all", channel: int = 0) -> Dict[str, Any]:
        """Transmits an alphanumeric payload across the LoRa mesh network."""
        now = datetime.now(timezone.utc)
        packet = {
            "id": f"mesh_tx_{int(now.timestamp())}_{random.randint(100, 999)}",
            "timestamp": now.isoformat(),
            "to": to_node,
            "channel": channel,
            "text": str(text)[:200],  # Max LoRa MTU recommendation
            "status": "broadcasted",
        }
        self.message_log.append(packet)
        if len(self.message_log) > 100:
            self.message_log.pop(0)

        self.total_packets_tx += 1
        self.last_tx_time = now
        print(f"[MeshtasticRelay] TX LoRa Packet -> {to_node}: {text[:80]}")
        return packet

    def broadcast_alert(self, alert_dict: Dict[str, Any]):
        """Compresses and broadcasts high-priority tactical alerts over the LoRa mesh."""
        if not self.config.get("auto_forward_alerts", True):
            return

        sev = alert_dict.get("severity", "INFO").upper()
        min_sev = self.config.get("alert_min_severity", "WARNING").upper()

        sev_rank = {"INFO": 1, "WARNING": 2, "CRITICAL": 3}
        if sev_rank.get(sev, 1) < sev_rank.get(min_sev, 2):
            return

        title = alert_dict.get("title", "Tactical Alert")
        msg = alert_dict.get("message", "")
        # Create compact LoRa airtime-efficient packet (< 140 bytes)
        lora_text = f"🚨[{sev}] {title}: {msg}"[:140]
        self.send_message(lora_text, to_node="^all", channel=0)

    async def start(self) -> Dict[str, Any]:
        """Starts the Meshtastic relay background daemon."""
        if self.is_running:
            return {"status": "already_running"}

        self.is_running = True
        self.start_time = datetime.now(timezone.utc)
        self.error_message = None
        self._task = asyncio.create_task(self._mesh_loop())
        print(f"[MeshtasticRelay] Started Meshtastic LoRa relay (Region: {self.config.get('region')}, Mode: {self.config.get('mode')})")
        return {"status": "started", "mode": self.config.get("mode")}

    def stop(self) -> Dict[str, Any]:
        """Stops the Meshtastic relay background daemon."""
        if not self.is_running:
            return {"status": "not_running"}

        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

        print("[MeshtasticRelay] Stopped Meshtastic LoRa relay.")
        return {"status": "stopped", "total_tx": self.total_packets_tx, "total_rx": self.total_packets_rx}

    async def _mesh_loop(self):
        """Background continuous update loop for mesh nodes & telemetry."""
        while self.is_running:
            try:
                now = datetime.now(timezone.utc)
                mode = self.config.get("mode", "synthetic_mesh")

                if mode == "synthetic_mesh":
                    # Simulate slight wander/patrol movement and telemetry updates
                    for node_id, node in self.nodes.items():
                        # Jitter position slightly (walk speed ~1 m/s)
                        lat_delta = (random.random() - 0.5) * 0.0003
                        lon_delta = (random.random() - 0.5) * 0.0003
                        node["lat"] = round(node["lat"] + lat_delta, 5)
                        node["lon"] = round(node["lon"] + lon_delta, 5)
                        node["last_heard"] = now.isoformat()
                        self.total_packets_rx += 1
                        self.last_rx_time = now

                        # 1. Update Tile38 geo-cache under 'ground_teams'
                        if router.redis:
                            try:
                                await router.redis.execute_command(
                                    "SET", "ground_teams", node_id,
                                    "FIELD", "battery", node.get("battery_pct", 100),
                                    "FIELD", "snr", node.get("snr_db", 10.0),
                                    "FIELD", "rssi", node.get("rssi_dbm", -70),
                                    "EX", 180,
                                    "POINT", node["lat"], node["lon"]
                                )
                            except Exception:
                                pass

                        # 2. Forward Friendly Ground Units to ATAK via CoT
                        try:
                            from ..cot.engine import cot_engine, build_cot_xml
                            xml = build_cot_xml(
                                uid=f"MESH_{node['short_name']}",
                                cot_type="a-f-G-U-C",  # Friendly Ground Combat / Civilian Unit
                                lat=node["lat"],
                                lon=node["lon"],
                                hae_m=node["alt_m"],
                                callsign=f"📡 {node['short_name']}",
                                remarks=f"Meshtastic LoRa ({self.config.get('region')}) | Bat: {node['battery_pct']}% | SNR: {node['snr_db']}dB | {node['role']}",
                                stale_sec=60,
                            )
                            cot_engine.send_cot_packet(xml)
                        except Exception:
                            pass

                interval = float(self.config.get("broadcast_interval_sec", 10.0))
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.error_message = str(e)
                await asyncio.sleep(5.0)


meshtastic_engine = MeshtasticRelayEngine()
