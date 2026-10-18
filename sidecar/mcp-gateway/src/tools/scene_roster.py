"""
Deterministic 250km Contact Roster & Bearing/Distance Math.
Computes distance, compass bearing, and closing/receding vector status.
"""
import math
from typing import List, Dict, Any, Tuple, Optional

COMPASS_POINTS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate Great-Circle distance between two points in km."""
    R = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return R * c


def calculate_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> Tuple[float, str]:
    """Calculate initial compass bearing from (lat1, lon1) to (lat2, lon2)."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_lambda = math.radians(lon2 - lon1)

    y = math.sin(delta_lambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
    bearing_rad = math.atan2(y, x)
    bearing_deg = (math.degrees(bearing_rad) + 360.0) % 360.0

    # Map to 8-point compass
    idx = round(bearing_deg / 45.0) % 8
    compass = COMPASS_POINTS[idx]
    return round(bearing_deg, 1), compass


def calculate_vector_status(
    bearing_from_observer_deg: float, entity_heading_deg: Optional[float]
) -> str:
    """
    Determine if entity is closing in, receding, or stationary/crossing.
    Closing: Heading points roughly towards observer (bearing + 180 +/- 45 deg).
    """
    if entity_heading_deg is None:
        return "STATIONARY"

    # Angle pointing directly towards observer
    inbound_angle = (bearing_from_observer_deg + 180.0) % 360.0
    diff = abs((entity_heading_deg - inbound_angle + 180.0) % 360.0 - 180.0)

    if diff <= 45.0:
        return "CLOSING"
    elif diff >= 135.0:
        return "RECEDING"
    else:
        return "CROSSING"


def build_contact_roster(
    observer_lat: float,
    observer_lon: float,
    raw_contacts: List[Dict[str, Any]],
    radius_km: float = 250.0,
) -> List[Dict[str, Any]]:
    """Build distance-ranked contact roster with bearing and closing/receding vectors."""
    roster: List[Dict[str, Any]] = []

    for c in raw_contacts:
        c_lat = c.get("lat")
        c_lon = c.get("lon")
        if c_lat is None or c_lon is None:
            continue

        dist_km = haversine_distance_km(observer_lat, observer_lon, c_lat, c_lon)
        if dist_km > radius_km:
            continue

        bearing_deg, compass = calculate_bearing_deg(observer_lat, observer_lon, c_lat, c_lon)
        heading_deg = c.get("heading_deg")
        vector_status = calculate_vector_status(bearing_deg, heading_deg)

        roster.append({
            "entity_id": c.get("entity_id", "UNKNOWN"),
            "layer": c.get("layer", "unknown"),
            "distance_km": round(dist_km, 2),
            "bearing_deg": bearing_deg,
            "bearing_compass": compass,
            "vector_status": vector_status,
            "altitude_m": round(c.get("altitude_m", 0) or 0),
            "speed_kts": round(c.get("speed_kts", 0) or 0),
            "heading_deg": round(heading_deg, 1) if heading_deg is not None else None,
            "coordinates": {"lat": round(c_lat, 4), "lon": round(c_lon, 4)},
            "payload": c.get("payload", {}),
        })

    # Sort contacts by distance ascending (closest first)
    roster.sort(key=lambda x: x["distance_km"])
    return roster
