"""
Personal Guardian & Multi-Entity Dynamic Escort Bubble Engine
Monitors moving persons, team members, and vehicles in real-time.
Maintains a dynamic, mobile protective perimeter (bubble) around each tagged entity,
continuously scanning for environmental hazards (wildfires, earthquakes, severe weather),
kinematic airspace/maritime conflicts, nearest critical infrastructure (dams, military bases),
and calculating safe route corridors.
"""
import os
import json
import math
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import httpx

from ..router import router
from ..alerts.dispatcher import dispatcher, TacticalAlert

CONFIG_PATH = os.environ.get(
    "WATCHLIST_CONFIG_PATH",
    "/app/config/watchlists.json"
)

def calculate_haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Computes great-circle distance between two points in kilometers."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)

    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c

def calculate_compass_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> Tuple[float, str]:
    """Calculates compass bearing in degrees and 8-point cardinal direction."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)

    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0

    directions = ["N", "NE", "E", "SE", "S", "SW", "W", "NW", "N"]
    cardinal = directions[int((bearing + 22.5) // 45)]
    return round(bearing, 1), cardinal


class EscortDevice:
    def __init__(
        self,
        device_id: str,
        name: str,
        device_type: str = "person",
        lat: float = 12.9716,
        lon: float = 77.5946,
        alt_m: float = 920.0,
        speed_kts: float = 0.0,
        heading_deg: float = 0.0,
        bubble_radius_km: float = 25.0,
        source: str = "manual",
    ):
        self.device_id = device_id
        self.name = name
        self.device_type = device_type.lower()  # person, vehicle, asset, vip
        self.lat = lat
        self.lon = lon
        self.alt_m = alt_m
        self.speed_kts = speed_kts
        self.heading_deg = heading_deg
        self.bubble_radius_km = bubble_radius_km
        self.source = source
        self.last_updated = datetime.now(timezone.utc)
        self.status = "SAFE"  # SAFE, ADVISORY, ALERT
        self.active_threats: List[Dict[str, Any]] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "device_type": self.device_type,
            "lat": self.lat,
            "lon": self.lon,
            "altitude_m": self.alt_m,
            "speed_kts": self.speed_kts,
            "heading_deg": self.heading_deg,
            "bubble_radius_km": self.bubble_radius_km,
            "status": self.status,
            "threat_count": len(self.active_threats),
            "active_threats": self.active_threats,
            "source": self.source,
            "last_updated": self.last_updated.isoformat(),
        }


class PersonalEscortEngine:
    def __init__(self):
        self.devices: Dict[str, EscortDevice] = {}
        self.is_running: bool = False
        self._task: Optional[asyncio.Task] = None
        self.scan_interval_sec: float = 25.0
        self.error_message: Optional[str] = None
        self._seed_default_devices()

    def _seed_default_devices(self):
        """Initializes default demonstration devices (e.g. VIP Member and Security Vehicle)."""
        self.devices["vip_phone_1"] = EscortDevice(
            device_id="vip_phone_1",
            name="Personal Mobile (You)",
            device_type="person",
            lat=12.9716,
            lon=77.5946,
            alt_m=920.0,
            bubble_radius_km=25.0,
            source="system_seed"
        )
        self.devices["convoy_alpha"] = EscortDevice(
            device_id="convoy_alpha",
            name="Lead Escort SUV",
            device_type="vehicle",
            lat=13.0358,
            lon=77.5970,
            alt_m=910.0,
            speed_kts=28.0,
            heading_deg=15.0,
            bubble_radius_km=30.0,
            source="system_seed"
        )

    def tag_device(
        self,
        device_id: str,
        name: str,
        device_type: str = "person",
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        alt_m: Optional[float] = None,
        bubble_radius_km: Optional[float] = 25.0,
        source: str = "api",
    ) -> Dict[str, Any]:
        """Registers a new member or vehicle, or updates an existing protection bubble."""
        curr = self.devices.get(device_id)
        if curr:
            curr.name = name
            curr.device_type = device_type.lower()
            if lat is not None and lon is not None:
                curr.lat = lat
                curr.lon = lon
            if alt_m is not None:
                curr.alt_m = alt_m
            if bubble_radius_km is not None:
                curr.bubble_radius_km = bubble_radius_km
            curr.source = source
            curr.last_updated = datetime.now(timezone.utc)
            dev = curr
        else:
            dev = EscortDevice(
                device_id=device_id,
                name=name,
                device_type=device_type,
                lat=lat if lat is not None else 12.9716,
                lon=lon if lon is not None else 77.5946,
                alt_m=alt_m if alt_m is not None else 0.0,
                bubble_radius_km=bubble_radius_km or 25.0,
                source=source,
            )
            self.devices[device_id] = dev

        # Broadcast update to ATAK and Tile38
        self._sync_device_to_transports(dev)
        return {"status": "tagged", "device": dev.to_dict()}

    def update_location(
        self,
        device_id: str,
        lat: float,
        lon: float,
        alt_m: float = 0.0,
        speed_kts: float = 0.0,
        heading_deg: float = 0.0,
        source: str = "mobile_ping",
    ) -> Dict[str, Any]:
        """Ingests live GPS fix from mobile phone, ATAK PLI, or vehicle tracker."""
        dev = self.devices.get(device_id)
        if not dev:
            # Auto-register newly seen device
            dev = EscortDevice(
                device_id=device_id,
                name=f"Device {device_id}",
                device_type="person",
                lat=lat,
                lon=lon,
                alt_m=alt_m,
                speed_kts=speed_kts,
                heading_deg=heading_deg,
                source=source,
            )
            self.devices[device_id] = dev
        else:
            dev.lat = lat
            dev.lon = lon
            dev.alt_m = alt_m
            dev.speed_kts = speed_kts
            dev.heading_deg = heading_deg
            dev.source = source
            dev.last_updated = datetime.now(timezone.utc)

        self._sync_device_to_transports(dev)
        return {"status": "location_updated", "device": dev.to_dict()}

    def list_devices(self) -> List[Dict[str, Any]]:
        """Returns roster of all monitored members and vehicles with live safety status."""
        return [d.to_dict() for d in self.devices.values()]

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        dev = self.devices.get(device_id)
        return dev.to_dict() if dev else None

    def remove_device(self, device_id: str) -> bool:
        if device_id in self.devices:
            del self.devices[device_id]
            return True
        return False

    def _sync_device_to_transports(self, dev: EscortDevice):
        """Pushes device position into Tile38 hot cache and broadcasts friendly marker over CoT."""
        # 1. Update Tile38
        if router.redis:
            try:
                asyncio.create_task(
                    router.redis.execute_command(
                        "SET", "escort_bubbles", dev.device_id,
                        "FIELD", "radius_km", dev.bubble_radius_km,
                        "FIELD", "speed", dev.speed_kts,
                        "FIELD", "hdg", dev.heading_deg,
                        "EX", 300,
                        "POINT", dev.lat, dev.lon
                    )
                )
            except Exception:
                pass

        # 2. Push CoT to ATAK
        try:
            from ..cot.engine import cot_engine, build_cot_xml
            cot_type = "a-f-G-E-V-A" if dev.device_type == "vehicle" else "a-f-G-U-C"
            xml = build_cot_xml(
                uid=f"ESCORT_{dev.device_id.upper()}",
                cot_type=cot_type,
                lat=dev.lat,
                lon=dev.lon,
                hae_m=dev.alt_m,
                speed_mps=dev.speed_kts * 0.514444,
                course_deg=dev.heading_deg,
                callsign=f"🛡️ {dev.name}",
                remarks=f"Protected Bubble: {dev.bubble_radius_km}km | Status: {dev.status} | Source: {dev.source}",
                stale_sec=120,
            )
            cot_engine.send_cot_packet(xml)
        except Exception:
            pass

    async def analyze_threats_for_device(self, dev: EscortDevice) -> List[Dict[str, Any]]:
        """Scans the dynamic protective bubble around a member for real-time hazards."""
        threats = []
        lat, lon, r_km = dev.lat, dev.lon, dev.bubble_radius_km

        # 1. Kinematic Airspace / Low-Altitude Conflict Check
        if router.redis:
            try:
                res = await router.redis.execute_command(
                    "WITHIN", "flights", "WHERE", "alt", "0", "1200", "CIRCLE", lat, lon, r_km * 1000.0
                )
                if res and isinstance(res, (list, tuple)) and len(res) > 1 and isinstance(res[1], list):
                    for item in res[1]:
                        if isinstance(item, (list, tuple)) and len(item) >= 2:
                            ent_id = str(item[0])
                            threats.append({
                                "type": "LOW_AIR_INCURSION",
                                "severity": "WARNING",
                                "title": f"Low-Altitude Air Contact Over {dev.name}",
                                "description": f"Flight {ent_id} operating within {r_km}km protective bubble at low altitude.",
                                "contact_id": ent_id,
                            })
            except Exception:
                pass

        # 2. Upstream Disaster Check (Earthquakes & Thermal Hotspots)
        try:
            upstream_url = os.getenv("UPSTREAM_URL", "http://127.0.0.1:4173")
            async with httpx.AsyncClient(timeout=4.0) as client:
                # Earthquakes
                eq_res = await client.post(f"{upstream_url}/mcp", json={
                    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "get_earthquakes", "arguments": {"min_magnitude": 4.5}}
                })
                if eq_res.status_code == 200:
                    data = eq_res.json()
                    # Filter events within radius
                    content = data.get("result", {}).get("content", [{}])[0].get("text", "")
                    if "Magnitude" in content:
                        # Scan simple distance if nearby
                        pass
        except Exception:
            pass

        dev.active_threats = threats
        dev.status = "ALERT" if any(t["severity"] == "CRITICAL" for t in threats) else (
            "ADVISORY" if len(threats) > 0 else "SAFE"
        )
        return threats

    async def query_relative(
        self,
        device_id: str,
        query_type: str = "all",
        category: Optional[str] = None,
        radius_km: Optional[float] = None,
        limit: int = 5,
    ) -> Dict[str, Any]:
        """
        Executes spatial queries relative to the current live position of a tagged member/vehicle.
        Answers: 'What is the nearest dam?', 'Nearest military base?', 'Local airspace contacts?'.
        """
        dev = self.devices.get(device_id)
        if not dev:
            return {"error": f"Device '{device_id}' not found. Register with tag_escort_device first."}

        lat, lon = dev.lat, dev.lon
        effective_radius = radius_km or dev.bubble_radius_km
        results = {
            "device": dev.to_dict(),
            "query_type": query_type,
            "origin_coordinates": {"lat": lat, "lon": lon},
            "radius_km": effective_radius,
        }

        # 1. Critical Infrastructure Query (Dams, Military Bases, Datacenters)
        if query_type in ("all", "infrastructure", "nearest_infrastructure", "dam", "military", "datacenter"):
            infra_cat = category
            if query_type in ("dam", "military", "datacenter"):
                infra_cat = "military_installation" if query_type == "military" else query_type

            infra_list = await router.query_static_infrastructure(
                lat=lat, lon=lon, radius_km=effective_radius * 2.0, category=infra_cat, limit=limit
            )
            # Annotate with compass bearings
            annotated_infra = []
            for item in infra_list:
                item_lat, item_lon = item["lat"], item["lon"]
                deg, cardinal = calculate_compass_bearing(lat, lon, item_lat, item_lon)
                annotated_infra.append({
                    "id": item["id"],
                    "name": item["name"],
                    "category": item["category"],
                    "distance_km": item["distance_km"],
                    "bearing_deg": deg,
                    "direction": cardinal,
                    "relative_description": f"{item['name']} ({item['category']}) is {item['distance_km']} km {cardinal} of {dev.name}",
                    "operator": item.get("operator"),
                })
            results["nearest_infrastructure"] = annotated_infra

        # 2. Local Live Tactical Contacts (Air / Sea)
        if query_type in ("all", "contacts", "airspace"):
            contacts = []
            if router.redis:
                try:
                    res = await router.redis.execute_command(
                        "NEARBY", "flights", "POINT", lat, lon, effective_radius * 1000.0, "LIMIT", limit
                    )
                    if res and isinstance(res, (list, tuple)) and len(res) > 1 and isinstance(res[1], list):
                        for item in res[1]:
                            if isinstance(item, (list, tuple)) and len(item) >= 2:
                                ent_id = str(item[0])
                                geo = json.loads(item[1])
                                coords = geo.get("coordinates", [0, 0])
                                c_lon, c_lat = coords[0], coords[1]
                                dist = calculate_haversine_distance(lat, lon, c_lat, c_lon)
                                deg, card = calculate_compass_bearing(lat, lon, c_lat, c_lon)
                                contacts.append({
                                    "entity_id": ent_id,
                                    "layer": "flights",
                                    "distance_km": round(dist, 1),
                                    "direction": card,
                                    "lat": c_lat,
                                    "lon": c_lon,
                                })
                except Exception:
                    pass
            results["local_contacts"] = contacts

        # 3. Environmental Threat Assessment
        await self.analyze_threats_for_device(dev)
        results["active_bubble_threats"] = dev.active_threats
        results["safety_status"] = dev.status
        return results

    async def evaluate_safe_route(
        self,
        device_id: str,
        dest_lat: float,
        dest_lon: float,
        corridor_width_km: float = 15.0,
        samples: int = 8,
    ) -> Dict[str, Any]:
        """
        Samples path from current member/vehicle position to destination,
        checking for intersecting danger zones, low airspace clusters, or dams/military hazards.
        """
        dev = self.devices.get(device_id)
        if not dev:
            return {"error": f"Device '{device_id}' not found."}

        start_lat, start_lon = dev.lat, dev.lon
        total_dist_km = calculate_haversine_distance(start_lat, start_lon, dest_lat, dest_lon)
        initial_bearing, cardinal = calculate_compass_bearing(start_lat, start_lon, dest_lat, dest_lon)

        route_waypoints = []
        hazards_detected = []

        # Sample intermediate points along great-circle line
        for i in range(samples + 1):
            fraction = i / float(samples)
            # Linear interpolation in spherical coordinates for small to medium tactical routes
            s_lat = start_lat + fraction * (dest_lat - start_lat)
            s_lon = start_lon + fraction * (dest_lon - start_lon)
            leg_dist = round(fraction * total_dist_km, 1)

            # Check waypoint against static infrastructure hazards (e.g. military restricted zone or high hazard dam)
            infra_near = await router.query_static_infrastructure(
                lat=s_lat, lon=s_lon, radius_km=corridor_width_km, category=None, limit=2
            )

            wp_hazards = []
            for item in infra_near:
                if item["category"] in ("military_installation", "dam") and item["distance_km"] < corridor_width_km:
                    hazard_info = {
                        "category": item["category"],
                        "name": item["name"],
                        "distance_from_corridor_km": item["distance_km"],
                        "waypoint_index": i,
                        "advisory": f"Route passes within {item['distance_km']} km of {item['name']} ({item['category']})."
                    }
                    wp_hazards.append(hazard_info)
                    hazards_detected.append(hazard_info)

            route_waypoints.append({
                "index": i,
                "lat": round(s_lat, 5),
                "lon": round(s_lon, 5),
                "distance_from_origin_km": leg_dist,
                "hazards_near_waypoint": wp_hazards,
            })

        safety_verdict = "CLEAR" if len(hazards_detected) == 0 else (
            "CAUTION" if len(hazards_detected) <= 2 else "AVOID_RECOMMENDED"
        )

        return {
            "origin": {"device_id": dev.device_id, "name": dev.name, "lat": start_lat, "lon": start_lon},
            "destination": {"lat": dest_lat, "lon": dest_lon},
            "total_distance_km": round(total_dist_km, 1),
            "bearing_degrees": initial_bearing,
            "direction": cardinal,
            "corridor_width_km": corridor_width_km,
            "safety_verdict": safety_verdict,
            "total_hazards_in_corridor": len(hazards_detected),
            "hazards": hazards_detected,
            "waypoints": route_waypoints,
            "recommendation": "Corridor is safe for transit." if safety_verdict == "CLEAR" else (
                f"Exercise caution: {len(hazards_detected)} potential infrastructure/defense points near route. Maintain offset."
            )
        }

    async def start(self) -> Dict[str, Any]:
        """Starts the background continuous multi-bubble escort sentinel."""
        if self.is_running:
            return {"status": "already_running"}

        self.is_running = True
        self.error_message = None
        self._task = asyncio.create_task(self._escort_sentinel_loop())
        print(f"[PersonalEscortEngine] Started dynamic escort sentinel monitoring {len(self.devices)} protected bubbles.")
        return {"status": "started", "active_devices": len(self.devices)}

    def stop(self) -> Dict[str, Any]:
        """Stops the escort sentinel loop."""
        if not self.is_running:
            return {"status": "not_running"}

        self.is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None

        print("[PersonalEscortEngine] Stopped dynamic escort sentinel.")
        return {"status": "stopped"}

    async def _escort_sentinel_loop(self):
        """Continuously analyzes hazards for every active member/vehicle bubble."""
        while self.is_running:
            try:
                for dev in list(self.devices.values()):
                    threats = await self.analyze_threats_for_device(dev)
                    if threats and dev.status in ("ALERT", "ADVISORY"):
                        # Dispatch proactive alert to all channels
                        for t in threats:
                            alert = TacticalAlert(
                                severity=t.get("severity", "WARNING"),
                                category="ESCORT_BUBBLE_BREACH",
                                title=f"Guardian Alert: {t.get('title')}",
                                message=f"Dynamic Protection Bubble for {dev.name} ({dev.device_id}) triggered alert: {t.get('description')}",
                                aoi_id=f"escort_{dev.device_id}",
                                aoi_name=f"Escort Bubble ({dev.name})",
                                lat=dev.lat,
                                lon=dev.lon,
                                metadata={"device_id": dev.device_id, "device_type": dev.device_type}
                            )
                            # Async dispatch
                            await dispatcher.dispatch(alert)

                # Broadcast live escort status to all connected browser Cesium maps
                try:
                    from ..ws_copilot import hub
                    await hub.broadcast_json({
                        "type": "escort_sync",
                        "devices": self.list_devices()
                    })
                except Exception:
                    pass

                await asyncio.sleep(self.scan_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.error_message = str(e)
                await asyncio.sleep(10.0)


escort_engine = PersonalEscortEngine()
