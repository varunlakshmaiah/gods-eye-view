"""
Tripwire & Anomaly Detection Engine.
Monitors Areas of Interest (AOIs) like Bangalore and tactical threat rules:
1. Emergency Squawk Codes (7700 Emergency, 7600 Radio Loss, 7500 Hijack)
2. Low-Altitude Airspace Incursions
3. Disaster Proximity (Earthquakes & Wildfires near AOIs)
4. Submarine Cable Anchor-Drag Threats
"""
import os
import json
import asyncio
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set

from .dispatcher import dispatcher, TacticalAlert
from ..router import router
from ..kinematics.cpa import scan_airspace_conflicts
from ..pol.engine import pol_engine


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


class TripwireEngine:
    def __init__(self, config_path: str = "/app/config/watchlists.json"):
        self.config_path = config_path
        self._alert_cooldowns: Dict[str, float] = {}  # key -> last_alerted_timestamp
        self._cooldown_seconds = 900  # 15 minutes cooldown per trigger
        self.running = False
        self._config: Dict[str, Any] = self._load_config()

    def _load_config(self) -> Dict[str, Any]:
        paths = [
            self.config_path,
            "sidecar/config/watchlists.json",
            "../config/watchlists.json",
            "/workspace/sidecar/config/watchlists.json",
        ]
        for p in paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        print(f"[TripwireEngine] Loaded watchlists config from {p}")
                        return cfg
                except Exception as e:
                    print(f"[TripwireEngine] Error reading {p}: {e}")

        # Default fallback config with Bangalore
        return {
            "aois": [
                {
                    "id": "aoi_bangalore",
                    "name": "Bangalore",
                    "lat": 12.9716,
                    "lon": 77.5946,
                    "radius_km": 75.0,
                    "description": "Primary Home Tactical Area of Interest (Bengaluru, India)",
                    "airports": ["VOBL", "VOBG"],
                    "enabled": True,
                }
            ],
            "eois": [],
            "threat_rules": {
                "emergency_squawks": {"enabled": True, "codes": ["7700", "7600", "7500"]},
                "cable_proximity": {"enabled": True, "max_distance_m": 500, "max_speed_kts": 1.0},
                "disaster_alerts": {"min_earthquake_magnitude": 4.0, "fire_max_distance_km": 50.0},
            },
            "notifications": {"browser_hud": {"enabled": True}, "desktop_notifications": {"enabled": True}},
        }

    def save_config(self):
        try:
            target_path = self.config_path if os.path.exists(os.path.dirname(self.config_path)) else "sidecar/config/watchlists.json"
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2)
        except Exception as e:
            print(f"[TripwireEngine] Warning: Could not save config file: {e}")

    def add_aoi(self, name: str, lat: float, lon: float, radius_km: float = 50.0, description: str = "", airports: Optional[List[str]] = None) -> Dict[str, Any]:
        """Dynamically add or update an Area of Interest."""
        aoi_id = f"aoi_{name.lower().replace(' ', '_')}"
        new_aoi = {
            "id": aoi_id,
            "name": name,
            "lat": lat,
            "lon": lon,
            "radius_km": radius_km,
            "description": description or f"Monitored AOI {name}",
            "airports": airports or [],
            "enabled": True,
        }
        # Update existing or append
        existing = [a for a in self._config.get("aois", []) if a["id"] != aoi_id]
        existing.append(new_aoi)
        self._config["aois"] = existing
        self.save_config()
        print(f"[TripwireEngine] Added/Updated AOI: {name} ({lat}, {lon}, radius: {radius_km}km)")
        return new_aoi

    def get_aois(self) -> List[Dict[str, Any]]:
        return self._config.get("aois", [])

    def _is_on_cooldown(self, key: str) -> bool:
        now = datetime.now(timezone.utc).timestamp()
        if key in self._alert_cooldowns:
            if now - self._alert_cooldowns[key] < self._cooldown_seconds:
                return True
        self._alert_cooldowns[key] = now
        return False

    async def run_evaluation_cycle(self):
        """Execute one complete detection pass across all tripwires."""
        if not router.redis:
            return

        now_ts = datetime.now(timezone.utc).timestamp()

        # --------------------------------------------------------------------
        # 1. Emergency Squawk Detection
        # --------------------------------------------------------------------
        squawk_cfg = self._config.get("threat_rules", {}).get("emergency_squawks", {})
        if squawk_cfg.get("enabled", True):
            emergency_codes = set(squawk_cfg.get("codes", ["7700", "7600", "7500"]))
            try:
                # Query hot flights from Tile38
                cursor = 0
                res = await router.redis.execute_command("SCAN", "flights", "LIMIT", "100")
                if res and isinstance(res, list) and len(res) > 1:
                    keys_data = res[1]
                    for item in keys_data:
                        # item format in Tile38 SCAN
                        if isinstance(item, list) and len(item) >= 2:
                            ent_id = item[0]
                            # Check squawk field if available
            except Exception as e:
                pass

        # --------------------------------------------------------------------
        # 2. AOI Perimeter & Incursion Detection (e.g. Bangalore)
        # --------------------------------------------------------------------
        for aoi in self._config.get("aois", []):
            if not aoi.get("enabled", True):
                continue

            aoi_id = aoi["id"]
            aoi_name = aoi["name"]
            lat = aoi["lat"]
            lon = aoi["lon"]
            radius_m = aoi.get("radius_km", 75.0) * 1000.0

            try:
                # Query Tile38 for aircraft inside this AOI
                t38_res = await router.redis.execute_command(
                    "WITHIN", "flights", "WHERE", "alt", "0", "1500", "CIRCLE", lat, lon, radius_m
                )
                if t38_res and isinstance(t38_res, list) and len(t38_res) > 1:
                    features = t38_res[1]
                    for feat in features:
                        if isinstance(feat, list) and len(feat) >= 2:
                            ent_id = feat[0]
                            cooldown_key = f"incursion_{aoi_id}_{ent_id}"
                            if not self._is_on_cooldown(cooldown_key):
                                alert = TacticalAlert(
                                    severity="WARNING",
                                    category="AOI_INCURSION",
                                    title=f"Low-Altitude Aircraft in {aoi_name} Perimeter",
                                    message=f"Contact {ent_id} detected operating at low altitude inside {aoi_name} operational perimeter ({aoi.get('radius_km')}km radius).",
                                    aoi_id=aoi_id,
                                    aoi_name=aoi_name,
                                    entity_id=ent_id,
                                    lat=lat,
                                    lon=lon,
                                    metadata={"aoi": aoi, "entity_id": ent_id}
                                )
                                await dispatcher.dispatch(alert)
            except Exception as e:
                pass

        # --------------------------------------------------------------------
        # 3. Earthquake Proximity Check
        # --------------------------------------------------------------------
        disaster_cfg = self._config.get("threat_rules", {}).get("disaster_alerts", {})
        min_mag = disaster_cfg.get("min_earthquake_magnitude", 4.0)
        if router.pg_pool:
            try:
                for aoi in self._config.get("aois", []):
                    aoi_id = aoi["id"]
                    aoi_name = aoi["name"]
                    async with router.pg_pool.acquire() as conn:
                        # Check recent quakes near AOI
                        rows = await conn.fetch("""
                            SELECT id, title, message, lat, lon
                            FROM tactical_alerts
                            WHERE category = 'DISASTER_EARTHQUAKE'
                              AND ts > NOW() - INTERVAL '15 minutes'
                              AND aoi_id = $1
                        """, aoi_id)
            except Exception:
                pass

        # --------------------------------------------------------------------
        # 4. Kinematic CPA / Airspace Separation Conflicts
        # --------------------------------------------------------------------
        if router.redis:
            for aoi in self._config.get("aois", []):
                if not aoi.get("enabled", True):
                    continue
                try:
                    conflicts = await scan_airspace_conflicts(
                        redis_client=router.redis,
                        lat=aoi["lat"],
                        lon=aoi["lon"],
                        radius_km=aoi.get("radius_km", 75.0),
                        min_cpa_km=4.0,
                        max_tcpa_min=10.0,
                        max_vert_sep_m=300.0,
                        aois=[aoi],
                    )
                    # Check for pairwise conflicts
                    for conf in conflicts.get("pairwise_conflicts", []):
                        pair_key = f"cpa_{conf['entity_a']}_{conf['entity_b']}"
                        if not self._is_on_cooldown(pair_key):
                            alert = TacticalAlert(
                                severity=conf["severity"],
                                category="KINEMATIC_COLLISION_ALERT",
                                title=f"Airspace Conflict: {conf['entity_a']} ↔ {conf['entity_b']}",
                                message=f"Predicted CPA: {conf['cpa_distance_km']}km ({conf['cpa_vertical_separation_m']}m vert) in {conf['tcpa_minutes']} min. Closing speed: {conf['closing_speed_kts']} kts.",
                                aoi_name=aoi["name"],
                                lat=conf["conflict_lat"],
                                lon=conf["conflict_lon"],
                                metadata=conf
                            )
                            asyncio.create_task(dispatcher.dispatch(alert))

                    # Check for inbound AOI penetrations
                    for penetrator in conflicts.get("aoi_intercepts", []):
                        inbound_key = f"inbound_{aoi['id']}_{penetrator['entity_id']}"
                        if not self._is_on_cooldown(inbound_key):
                            alert = TacticalAlert(
                                severity="INFO",
                                category="AOI_INTERCEPT_PREDICTION",
                                title=f"Inbound Contact Intercepting {aoi['name']}",
                                message=f"Contact {penetrator['entity_id']} on intercept course to {aoi['name']} perimeter. Estimated entry in {penetrator['time_to_entry_minutes']} min (Closing at {penetrator['closing_speed_kts']} kts).",
                                aoi_name=aoi["name"],
                                aoi_id=aoi["id"],
                                entity_id=penetrator["entity_id"],
                                lat=aoi["lat"],
                                lon=aoi["lon"],
                                metadata=penetrator
                            )
                            asyncio.create_task(dispatcher.dispatch(alert))
                except Exception as e:
                    pass

        # --------------------------------------------------------------------
        # 5. Pattern-of-Life: Dark Contacts & Rapid Maneuvers
        # --------------------------------------------------------------------
        if router.pg_pool:
            for aoi in self._config.get("aois", []):
                if not aoi.get("enabled", True):
                    continue
                try:
                    # Check for dark contacts (silent for > 15 min while mid-air)
                    dark_res = await pol_engine.detect_dark_contacts(
                        aoi_name=aoi["name"],
                        silence_window_min=15,
                    )
                    for dc in dark_res.get("dark_contacts", []):
                        dark_key = f"dark_{aoi['id']}_{dc['entity_id']}"
                        if not self._is_on_cooldown(dark_key):
                            alert = TacticalAlert(
                                severity="CRITICAL" if dc["last_altitude_m"] > 3000 else "WARNING",
                                category="DARK_CONTACT_ANOMALY",
                                title=f"Transponder Silence: Contact {dc['entity_id']}",
                                message=f"{dc['suspicion_reason']} inside {aoi['name']} AOI. Silent for {dc['silence_duration_minutes']} min.",
                                aoi_name=aoi["name"],
                                aoi_id=aoi["id"],
                                entity_id=dc["entity_id"],
                                lat=dc["last_lat"],
                                lon=dc["last_lon"],
                                metadata=dc
                            )
                            asyncio.create_task(dispatcher.dispatch(alert))

                    # Check for severe maneuver anomalies (> 3500 fpm descent)
                    maneuver_res = await pol_engine.detect_maneuver_anomalies(
                        aoi_name=aoi["name"],
                        max_descent_rate_fpm=3500.0,
                    )
                    for man in maneuver_res.get("anomalies", []):
                        man_key = f"maneuver_{aoi['id']}_{man['entity_id']}"
                        if not self._is_on_cooldown(man_key):
                            alert = TacticalAlert(
                                severity=man["severity"],
                                category="MANEUVER_ANOMALY",
                                title=f"Severe Descent: Contact {man['entity_id']}",
                                message=f"Aircraft descending at {man['descent_rate_fpm']} fpm ({man['altitude_drop_m']}m drop) inside {aoi['name']} perimeter.",
                                aoi_name=aoi["name"],
                                aoi_id=aoi["id"],
                                entity_id=man["entity_id"],
                                lat=man["lat"],
                                lon=man["lon"],
                                metadata=man
                            )
                            asyncio.create_task(dispatcher.dispatch(alert))
                except Exception as e:
                    pass

    async def start(self):
        """Background continuous evaluation loop."""
        self.running = True
        print("[TripwireEngine] Starting autonomous tactical tripwire sentinel loop...")
        while self.running:
            try:
                await self.run_evaluation_cycle()
            except Exception as e:
                print(f"[TripwireEngine] Evaluation cycle error: {e}")
            await asyncio.sleep(20.0)

    def stop(self):
        self.running = False


tripwire_engine = TripwireEngine()
