"""
Kinematic CPA / TCPA Collision & Intercept Analysis Engine.
Calculates Closest Point of Approach (CPA) and Time to CPA (TCPA) using 4D vector projection:
1. Contact-to-Contact pairwise collision prediction (TCAS / ARPA standards).
2. Contact-to-AOI perimeter intercept prediction (early boundary breach warning).
3. Airspace conflict scanner for high-density terminal radar service areas (TRSA).
"""
import math
from typing import Dict, Any, List, Optional, Tuple

from ..router import router


def lat_lon_to_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> Tuple[float, float]:
    """Convert relative lat/lon difference to local tangent plane (dx East, dy North) in meters."""
    avg_lat_rad = math.radians((lat1 + lat2) / 2.0)
    dx = math.radians(lon2 - lon1) * 6378137.0 * math.cos(avg_lat_rad)
    dy = math.radians(lat2 - lat1) * 6378137.0
    return dx, dy


def heading_speed_to_vx_vy(heading_deg: float, speed_mps: float) -> Tuple[float, float]:
    """Convert navigational heading (0° North, 90° East) and ground speed to vx (East), vy (North)."""
    rad = math.radians(heading_deg)
    vx = speed_mps * math.sin(rad)
    vy = speed_mps * math.cos(rad)
    return vx, vy


def calculate_pairwise_cpa(
    lat_a: float, lon_a: float, alt_m_a: float, speed_kts_a: float, heading_deg_a: float,
    lat_b: float, lon_b: float, alt_m_b: float, speed_kts_b: float, heading_deg_b: float,
    v_rate_fpm_a: float = 0.0,
    v_rate_fpm_b: float = 0.0,
) -> Dict[str, Any]:
    """
    Calculate 4D Closest Point of Approach (CPA) and Time to CPA (TCPA).
    Speed input in knots, converted to m/s (1 knot = 0.514444 m/s).
    Vertical rate in feet/min, converted to m/s (1 fpm = 0.00508 m/s).
    """
    speed_mps_a = speed_kts_a * 0.514444
    speed_mps_b = speed_kts_b * 0.514444
    v_rate_mps_a = v_rate_fpm_a * 0.00508
    v_rate_mps_b = v_rate_fpm_b * 0.00508

    # Relative position vector r = B - A
    rx, ry = lat_lon_to_meters(lat_a, lon_a, lat_b, lon_b)
    rz = alt_m_b - alt_m_a
    current_dist_m = math.sqrt(rx * rx + ry * ry + rz * rz)
    current_horiz_dist_km = math.sqrt(rx * rx + ry * ry) / 1000.0

    # Velocity vectors
    vax, vay = heading_speed_to_vx_vy(heading_deg_a, speed_mps_a)
    vbx, vby = heading_speed_to_vx_vy(heading_deg_b, speed_mps_b)

    # Relative velocity vector v = B - A
    vrx = vbx - vax
    vry = vby - vay
    vrz = v_rate_mps_b - v_rate_mps_a
    rel_speed_mps = math.sqrt(vrx * vrx + vry * vry + vrz * vrz)
    rel_speed_kts = rel_speed_mps / 0.514444

    # Calculate TCPA: t = -(r . v) / |v|^2
    if rel_speed_mps < 0.1:
        # Both contacts stationary or cruising on identical vector
        return {
            "converging": False,
            "tcpa_seconds": 0.0,
            "tcpa_minutes": 0.0,
            "cpa_distance_km": round(current_horiz_dist_km, 3),
            "cpa_vertical_separation_m": round(abs(rz), 1),
            "current_distance_km": round(current_horiz_dist_km, 3),
            "closing_speed_kts": 0.0,
            "cpa_lat": lat_a,
            "cpa_lon": lon_a,
        }

    dot_product = rx * vrx + ry * vry + rz * vrz
    tcpa_sec = -dot_product / (rel_speed_mps * rel_speed_mps)

    # Relative position at CPA
    cpa_rx = rx + vrx * tcpa_sec
    cpa_ry = ry + vry * tcpa_sec
    cpa_rz = rz + vrz * tcpa_sec
    cpa_dist_m = math.sqrt(cpa_rx * cpa_rx + cpa_ry * cpa_ry + cpa_rz * cpa_rz)
    cpa_horiz_km = math.sqrt(cpa_rx * cpa_rx + cpa_ry * cpa_ry) / 1000.0

    # Closing velocity (rate of closure)
    closing_speed_mps = -dot_product / current_dist_m if current_dist_m > 0 else 0.0
    closing_speed_kts = closing_speed_mps / 0.514444

    # Predicted latitude and longitude of Contact A at CPA
    cpa_lat_a = lat_a + math.degrees((vay * tcpa_sec) / 6378137.0)
    cpa_lon_a = lon_a + math.degrees((vax * tcpa_sec) / (6378137.0 * math.cos(math.radians(lat_a))))

    return {
        "converging": tcpa_sec > 0.0 and closing_speed_kts > 5.0,
        "tcpa_seconds": round(tcpa_sec, 1),
        "tcpa_minutes": round(tcpa_sec / 60.0, 2),
        "cpa_distance_km": round(cpa_horiz_km, 3),
        "cpa_vertical_separation_m": round(abs(cpa_rz), 1),
        "current_distance_km": round(current_horiz_dist_km, 3),
        "closing_speed_kts": round(closing_speed_kts, 1),
        "cpa_lat": round(cpa_lat_a, 5),
        "cpa_lon": round(cpa_lon_a, 5),
    }


def calculate_aoi_intercept(
    contact_lat: float,
    contact_lon: float,
    contact_alt_m: float,
    speed_kts: float,
    heading_deg: float,
    aoi_lat: float,
    aoi_lon: float,
    aoi_radius_km: float,
) -> Dict[str, Any]:
    """
    Calculate trajectory intercept vector of a contact approaching an Area of Interest (AOI).
    Determines if contact will penetrate perimeter, predicted entry time, and minimum distance.
    """
    speed_mps = speed_kts * 0.514444
    rx, ry = lat_lon_to_meters(contact_lat, contact_lon, aoi_lat, aoi_lon)
    current_dist_km = math.sqrt(rx * rx + ry * ry) / 1000.0

    if speed_mps < 1.0:
        return {
            "on_intercept_course": False,
            "current_distance_km": round(current_dist_km, 2),
            "inside_aoi": current_dist_km <= aoi_radius_km,
            "tcpa_minutes": 0.0,
            "cpa_distance_km": round(current_dist_km, 2),
            "penetration_predicted": current_dist_km <= aoi_radius_km,
        }

    vx, vy = heading_speed_to_vx_vy(heading_deg, speed_mps)

    # Relative velocity of AOI relative to contact: -contact velocity
    vrx = -vx
    vry = -vy
    v_mag = math.sqrt(vrx * vrx + vry * vry)

    dot_product = rx * vrx + ry * vry
    tcpa_sec = -dot_product / (v_mag * v_mag)

    cpa_rx = rx + vrx * tcpa_sec
    cpa_ry = ry + vry * tcpa_sec
    cpa_dist_km = math.sqrt(cpa_rx * cpa_rx + cpa_ry * cpa_ry) / 1000.0

    closing_speed_kts = (-dot_product / (current_dist_km * 1000.0)) / 0.514444 if current_dist_km > 0 else 0.0

    will_penetrate = cpa_dist_km <= aoi_radius_km and tcpa_sec > 0.0
    time_to_entry_sec = None

    if will_penetrate and current_dist_km > aoi_radius_km:
        # Distance from contact along track to perimeter entry
        half_chord_m = math.sqrt((aoi_radius_km * 1000.0)**2 - (cpa_dist_km * 1000.0)**2)
        dist_to_cpa_m = math.sqrt((current_dist_km * 1000.0)**2 - (cpa_dist_km * 1000.0)**2)
        dist_to_entry_m = max(0.0, dist_to_cpa_m - half_chord_m)
        time_to_entry_sec = dist_to_entry_m / speed_mps

    return {
        "on_intercept_course": will_penetrate,
        "inside_aoi": current_dist_km <= aoi_radius_km,
        "current_distance_km": round(current_dist_km, 2),
        "cpa_distance_km": round(cpa_dist_km, 2),
        "tcpa_seconds": round(tcpa_sec, 1) if tcpa_sec > 0 else 0.0,
        "tcpa_minutes": round(tcpa_sec / 60.0, 1) if tcpa_sec > 0 else 0.0,
        "time_to_entry_minutes": round(time_to_entry_sec / 60.0, 1) if time_to_entry_sec is not None else (0.0 if current_dist_km <= aoi_radius_km else None),
        "closing_speed_kts": round(closing_speed_kts, 1),
        "penetration_predicted": will_penetrate,
    }


async def scan_airspace_conflicts(
    lat: float,
    lon: float,
    radius_km: float = 100.0,
    min_cpa_km: float = 5.0,
    max_tcpa_min: float = 15.0,
    max_vert_sep_m: float = 600.0,
    aois: Optional[List[Dict[str, Any]]] = None,
    redis_client=None,
) -> Dict[str, Any]:
    """
    Scan local airspace for kinematic conflicts:
    1. Pairwise aircraft convergence violating horizontal & vertical separation minimums (TCAS style).
    2. Inbound aircraft on an intercept course toward monitored AOIs (early warning).
    """
    try:
        raw_contacts = await router.query_contacts(lat, lon, radius_km, "now", ["flights", "military"])
    except Exception as e:
        return {"status": "error", "message": f"Contact query failed: {e}"}

    contacts = []
    for c in raw_contacts:
        contacts.append({
            "id": c.get("entity_id"),
            "lat": c.get("lat"),
            "lon": c.get("lon"),
            "alt_m": c.get("altitude_m", 0.0),
            "heading": c.get("heading_deg", 0.0),
            "speed_kts": c.get("speed_kts", 0.0),
        })

    pairwise_conflicts = []
    n = len(contacts)
    for i in range(n):
        for j in range(i + 1, n):
            a = contacts[i]
            b = contacts[j]
            cpa_res = calculate_pairwise_cpa(
                lat_a=a["lat"], lon_a=a["lon"], alt_m_a=a["alt_m"], speed_kts_a=a["speed_kts"], heading_deg_a=a["heading"],
                lat_b=b["lat"], lon_b=b["lon"], alt_m_b=b["alt_m"], speed_kts_b=b["speed_kts"], heading_deg_b=b["heading"],
            )
            if (
                cpa_res["converging"]
                and 0 < cpa_res["tcpa_minutes"] <= max_tcpa_min
                and cpa_res["cpa_distance_km"] <= min_cpa_km
                and cpa_res["cpa_vertical_separation_m"] <= max_vert_sep_m
            ):
                pairwise_conflicts.append({
                    "entity_a": a["id"],
                    "entity_b": b["id"],
                    "tcpa_seconds": cpa_res["tcpa_seconds"],
                    "tcpa_minutes": cpa_res["tcpa_minutes"],
                    "cpa_distance_km": cpa_res["cpa_distance_km"],
                    "cpa_vertical_separation_m": cpa_res["cpa_vertical_separation_m"],
                    "current_distance_km": cpa_res["current_distance_km"],
                    "closing_speed_kts": cpa_res["closing_speed_kts"],
                    "conflict_lat": cpa_res["cpa_lat"],
                    "conflict_lon": cpa_res["cpa_lon"],
                    "severity": "CRITICAL" if cpa_res["cpa_distance_km"] < 2.0 and cpa_res["cpa_vertical_separation_m"] < 300 else "WARNING",
                })

    aoi_intercepts = []
    if aois:
        for aoi in aois:
            if not aoi.get("enabled", True):
                continue
            a_lat = aoi["lat"]
            a_lon = aoi["lon"]
            a_rad = aoi.get("radius_km", 50.0)
            a_name = aoi["name"]
            for c in contacts:
                aoi_res = calculate_aoi_intercept(
                    c["lat"], c["lon"], c["alt_m"], c["speed_kts"], c["heading"],
                    a_lat, a_lon, a_rad
                )
                if aoi_res["on_intercept_course"] and not aoi_res["inside_aoi"]:
                    entry_min = aoi_res.get("time_to_entry_minutes")
                    if entry_min is not None and 0 < entry_min <= max_tcpa_min:
                        aoi_intercepts.append({
                            "entity_id": c["id"],
                            "aoi_name": a_name,
                            "time_to_entry_minutes": entry_min,
                            "cpa_distance_km": aoi_res["cpa_distance_km"],
                            "closing_speed_kts": aoi_res["closing_speed_kts"],
                            "current_distance_km": aoi_res["current_distance_km"],
                            "altitude_m": c["alt_m"],
                        })

    pairwise_conflicts.sort(key=lambda x: x["tcpa_seconds"])
    aoi_intercepts.sort(key=lambda x: x["time_to_entry_minutes"])

    return {
        "contacts_analyzed": len(contacts),
        "radius_km": radius_km,
        "pairwise_conflicts_count": len(pairwise_conflicts),
        "pairwise_conflicts": pairwise_conflicts,
        "aoi_intercepts_count": len(aoi_intercepts),
        "aoi_intercepts": aoi_intercepts,
    }
