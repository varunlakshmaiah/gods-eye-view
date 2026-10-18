"""
Radar Coverage Horizons & 3D Sensor Range Domes Engine
Calculates line-of-sight radar horizons, blind cones, and 3D sensor coverage envelopes
using standard 4/3 effective Earth atmospheric refraction physics.
"""
import os
import json
import math
from typing import Dict, Any, List, Optional
from ..router import router
from ..ws_copilot import ws_copilot

CONFIG_PATH = os.environ.get(
    "WATCHLIST_CONFIG_PATH",
    "/app/config/watchlists.json"
)

# Standard Geodetic & Atmospheric Refraction Constants
EARTH_RADIUS_M = 6371000.0
K_ATMOSPHERIC_FACTOR = 4.0 / 3.0  # Standard 4/3 effective Earth radius model
EFFECTIVE_EARTH_RADIUS_M = EARTH_RADIUS_M * K_ATMOSPHERIC_FACTOR  # ~8,494,667 m

DEFAULT_RADAR_STATIONS = [
    {
        "id": "VOBL_ASR",
        "name": "Kempegowda ASR/MSSR",
        "location": "Bengaluru International Airport",
        "lat": 13.2001,
        "lon": 77.7088,
        "antenna_height_m": 940.0,
        "max_range_km": 463.0,
        "band": "S/L-Band MSSR",
        "cone_of_silence_deg": 35.0,
        "enabled": True,
    },
    {
        "id": "VOBG_TAR",
        "name": "HAL Terminal Area Radar",
        "location": "HAL Old Airport Bengaluru",
        "lat": 12.9499,
        "lon": 77.6682,
        "antenna_height_m": 915.0,
        "max_range_km": 222.0,
        "band": "Terminal TAR",
        "cone_of_silence_deg": 40.0,
        "enabled": True,
    },
    {
        "id": "YELAHANKA_AFS_AD",
        "name": "Yelahanka Air Defense Radar",
        "location": "AFS Yelahanka (IAF)",
        "lat": 13.1355,
        "lon": 77.6062,
        "antenna_height_m": 930.0,
        "max_range_km": 296.0,
        "band": "3D Surveillance Air Defense",
        "cone_of_silence_deg": 30.0,
        "enabled": True,
    },
    {
        "id": "MUMBAI_VABB_ASR",
        "name": "Mumbai ASR/MSSR",
        "location": "CSMIA Mumbai",
        "lat": 19.0896,
        "lon": 72.8656,
        "antenna_height_m": 35.0,
        "max_range_km": 463.0,
        "band": "S/L-Band MSSR",
        "cone_of_silence_deg": 35.0,
        "enabled": True,
    },
]


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two coordinates."""
    r_lat1 = math.radians(lat1)
    r_lat2 = math.radians(lat2)
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)

    a = math.sin(d_lat / 2.0) ** 2 + math.cos(r_lat1) * math.cos(r_lat2) * math.sin(d_lon / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_M * c


def forward_azimuth_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Forward azimuth / bearing in degrees [0, 360) from (lat1, lon1) to (lat2, lon2)."""
    r_lat1 = math.radians(lat1)
    r_lat2 = math.radians(lat2)
    d_lon = math.radians(lon2 - lon1)

    y = math.sin(d_lon) * math.cos(r_lat2)
    x = math.cos(r_lat1) * math.sin(r_lat2) - math.sin(r_lat1) * math.cos(r_lat2) * math.cos(d_lon)
    bearing = (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
    return round(bearing, 2)


def radar_horizon_distance_m(height_m: float) -> float:
    """
    Geometric distance in meters to radar horizon under 4/3 effective Earth refraction:
    d = sqrt(2 * R_eff * h)
    """
    if height_m <= 0.0:
        return 0.0
    return math.sqrt(2.0 * EFFECTIVE_EARTH_RADIUS_M * height_m)


def slant_range_and_elevation(
    surface_dist_m: float,
    h_radar_m: float,
    h_target_m: float,
) -> tuple[float, float]:
    """
    Computes slant range in meters and elevation angle in degrees
    taking into account Earth curvature under the 4/3 refraction model.
    """
    r_eff = EFFECTIVE_EARTH_RADIUS_M
    r_radar = r_eff + h_radar_m
    r_target = r_eff + h_target_m
    theta = surface_dist_m / r_eff

    # Law of cosines on effective spherical earth
    slant_sq = (r_radar ** 2) + (r_target ** 2) - (2.0 * r_radar * r_target * math.cos(theta))
    slant_range_m = math.sqrt(max(0.0, slant_sq))

    if slant_range_m < 1.0:
        return 0.0, 90.0

    # Elevation angle above local horizontal plane at radar antenna
    sin_elev = (r_target * math.cos(theta) - r_radar) / slant_range_m
    sin_elev = max(-1.0, min(1.0, sin_elev))
    elev_deg = math.degrees(math.asin(sin_elev))
    return slant_range_m, elev_deg


class RadarEngine:
    def __init__(self):
        self._stations: List[Dict[str, Any]] = []
        self.reload_config()

    def reload_config(self):
        """Loads radar stations from watchlists.json or falls back to defaults."""
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    stations = data.get("radar_stations", [])
                    if stations:
                        self._stations = stations
                        print(f"[RadarEngine] Loaded {len(self._stations)} radar stations from {CONFIG_PATH}")
                        return
        except Exception as e:
            print(f"[RadarEngine] Error loading config from {CONFIG_PATH}: {e}")

        self._stations = list(DEFAULT_RADAR_STATIONS)
        print(f"[RadarEngine] Using default radar stations ({len(self._stations)} stations)")

    def get_stations(self, enabled_only: bool = True) -> List[Dict[str, Any]]:
        """Returns radar stations enriched with calculated horizon distances."""
        results = []
        for st in self._stations:
            if enabled_only and not st.get("enabled", True):
                continue
            h_m = float(st.get("antenna_height_m", 50.0))
            d_radar_horiz_km = radar_horizon_distance_m(h_m) / 1000.0

            # Compute horizons at key standard altitudes (d_radar + d_target)
            horiz_ground_km = d_radar_horiz_km
            horiz_low_km = d_radar_horiz_km + (radar_horizon_distance_m(300.0) / 1000.0)      # 300m / 1000ft drone/low
            horiz_med_km = d_radar_horiz_km + (radar_horizon_distance_m(3000.0) / 1000.0)    # 3000m / 10,000ft approach
            horiz_fl330_km = d_radar_horiz_km + (radar_horizon_distance_m(10000.0) / 1000.0) # 10,000m / 33,000ft cruise

            results.append({
                "id": st["id"],
                "name": st["name"],
                "location": st.get("location", ""),
                "lat": st["lat"],
                "lon": st["lon"],
                "antenna_height_m": h_m,
                "max_range_km": float(st.get("max_range_km", 300.0)),
                "band": st.get("band", "Surveillance Radar"),
                "cone_of_silence_deg": float(st.get("cone_of_silence_deg", 35.0)),
                "enabled": st.get("enabled", True),
                "horizon_ground_km": round(horiz_ground_km, 1),
                "horizon_low_alt_300m_km": round(horiz_low_km, 1),
                "horizon_med_alt_3000m_km": round(horiz_med_km, 1),
                "horizon_high_alt_10000m_km": round(horiz_fl330_km, 1),
            })
        return results

    def evaluate_coverage(
        self,
        lat: float,
        lon: float,
        altitude_m: float = 1000.0,
        station_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Evaluates whether coordinates (lat, lon, altitude_m) are within
        line-of-sight radar coverage across stations.
        """
        stations = self.get_stations(enabled_only=True)
        if station_id:
            stations = [s for s in stations if s["id"].lower() == station_id.lower()]
            if not stations:
                return {
                    "status": "error",
                    "message": f"Radar station '{station_id}' not found.",
                }

        d_target_horiz_m = radar_horizon_distance_m(altitude_m)
        evaluations = []
        is_any_covered = False

        for st in stations:
            h_radar_m = st["antenna_height_m"]
            max_inst_range_m = st["max_range_km"] * 1000.0
            cone_angle = st["cone_of_silence_deg"]

            surface_dist_m = haversine_distance_m(st["lat"], st["lon"], lat, lon)
            azimuth = forward_azimuth_deg(st["lat"], st["lon"], lat, lon)
            slant_m, elev_deg = slant_range_and_elevation(surface_dist_m, h_radar_m, altitude_m)

            d_radar_horiz_m = radar_horizon_distance_m(h_radar_m)
            los_max_range_m = min(max_inst_range_m, d_radar_horiz_m + d_target_horiz_m)

            # Determine line-of-sight status
            if slant_m > max_inst_range_m:
                status = "OUT_OF_RANGE"
                reason = f"Slant range {slant_m / 1000.0:.1f} km exceeds instrumented range {st['max_range_km']:.0f} km"
            elif surface_dist_m > (d_radar_horiz_m + d_target_horiz_m):
                status = "RADAR_HORIZON_SHADOW"
                reason = f"Target below radar horizon shadow (max LOS {los_max_range_m / 1000.0:.1f} km at {altitude_m:.0f}m alt)"
            elif elev_deg > (90.0 - cone_angle):
                status = "CONE_OF_SILENCE"
                reason = f"Target inside overhead blind cone (elev {elev_deg:.1f}° > {90.0 - cone_angle:.1f}°)"
            else:
                status = "IN_COVERAGE"
                reason = f"Clear Line-of-Sight (LOS) radar track at {slant_m / 1000.0:.1f} km"
                is_any_covered = True

            margin_km = (los_max_range_m - surface_dist_m) / 1000.0

            evaluations.append({
                "station_id": st["id"],
                "station_name": st["name"],
                "status": status,
                "is_covered": status == "IN_COVERAGE",
                "surface_distance_km": round(surface_dist_m / 1000.0, 1),
                "slant_range_km": round(slant_m / 1000.0, 1),
                "azimuth_deg": azimuth,
                "elevation_angle_deg": round(elev_deg, 2),
                "max_los_range_km": round(los_max_range_m / 1000.0, 1),
                "los_margin_km": round(margin_km, 1),
                "reason": reason,
            })

        # Find best covering station (lowest slant range among covered)
        covered_stations = [e for e in evaluations if e["is_covered"]]
        best_station = min(covered_stations, key=lambda x: x["slant_range_km"]) if covered_stations else None

        return {
            "query": {
                "lat": lat,
                "lon": lon,
                "altitude_m": altitude_m,
            },
            "is_covered": is_any_covered,
            "best_station": best_station["station_name"] if best_station else None,
            "stations_evaluating_count": len(evaluations),
            "covered_stations_count": len(covered_stations),
            "evaluations": evaluations,
        }

    async def evaluate_entity_coverage(self, entity_id: str) -> Dict[str, Any]:
        """
        Looks up an active flight or vessel from Tile38 / TimescaleDB
        and evaluates its real-time radar coverage status.
        """
        from ..tools.entity_track import lookup_entity_anchor
        anchor = await lookup_entity_anchor(entity_id)
        if not anchor:
            return {
                "status": "error",
                "message": f"Entity '{entity_id}' not found in active hot cache or recent history.",
            }

        lat = anchor["lat"]
        lon = anchor["lon"]
        alt_m = anchor.get("altitude_m", 1000.0)

        res = self.evaluate_coverage(lat, lon, alt_m)
        res["entity_id"] = entity_id
        res["resolved_entity"] = {
            "entity_id": anchor.get("entity_id", entity_id),
            "layer": anchor.get("layer"),
            "speed_kts": anchor.get("speed_kts"),
            "heading_deg": anchor.get("heading_deg"),
            "is_live": anchor.get("is_live", False),
        }
        return res

    async def render_domes(self, enabled: bool = True, station_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Dispatches WebSocket command to Cesium UI to render 3D radar domes & range rings.
        """
        stations = self.get_stations(enabled_only=True)
        if station_id:
            stations = [s for s in stations if s["id"].lower() == station_id.lower()]

        cmd_res = await ws_copilot.broadcast_command(
            "render_radar_domes",
            {
                "enabled": enabled,
                "station_id": station_id,
                "stations": stations,
            }
        )
        return {
            "status": "dispatched",
            "enabled": enabled,
            "station_id": station_id,
            "stations_count": len(stations),
            "copilot_result": cmd_res,
        }


radar_engine = RadarEngine()
