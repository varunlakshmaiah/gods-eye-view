"""
SGP4 Headless Orbital Math & Pass Predictor.
Propagates satellite TLEs at any past, present, or future timestamp.
"""
import math
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
from sgp4.api import Satrec, jday

# WGS84 Constants
WGS84_A = 6378.137  # Earth semi-major axis (km)
WGS84_F = 1.0 / 298.257223563  # Flattening
WGS84_B = WGS84_A * (1.0 - WGS84_F)  # Semi-minor axis (km)
WGS84_E2 = 2.0 * WGS84_F - WGS84_F * WGS84_F  # Eccentricity squared


def datetime_to_jd_fr(dt: datetime) -> Tuple[float, float]:
    """Convert UTC datetime to Julian Date and Fraction."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    sec_with_micro = dt.second + dt.microsecond / 1e6
    return jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, sec_with_micro)


def gmst_radians(jd: float, fr: float) -> float:
    """Calculate Greenwich Mean Sidereal Time in radians."""
    d = (jd - 2451545.0) + fr
    gmst_hours = 18.697374558 + 24.06570982441908 * d
    gmst_rad = (gmst_hours % 24.0) * (2.0 * math.pi / 24.0)
    return gmst_rad


def teme_to_ecef(r_teme: Tuple[float, float, float], theta_gmst: float) -> Tuple[float, float, float]:
    """Rotate TEME (True Equator, Mean Equinox) vector to ECEF via GMST angle."""
    x, y, z = r_teme
    cos_t = math.cos(theta_gmst)
    sin_t = math.sin(theta_gmst)
    x_ecef = x * cos_t + y * sin_t
    y_ecef = -x * sin_t + y * cos_t
    z_ecef = z
    return (x_ecef, y_ecef, z_ecef)


def ecef_to_geodetic(x: float, y: float, z: float) -> Tuple[float, float, float]:
    """Convert ECEF (km) to Geodetic coordinates: (lat_deg, lon_deg, alt_km)."""
    p = math.sqrt(x * x + y * y)
    lon_rad = math.atan2(y, x)

    # Iterative Bowring method
    lat_rad = math.atan2(z, p * (1.0 - WGS84_E2))
    for _ in range(5):
        sin_lat = math.sin(lat_rad)
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        lat_rad = math.atan2(z + WGS84_E2 * n * sin_lat, p)

    sin_lat = math.sin(lat_rad)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    alt_km = p / math.cos(lat_rad) - n

    return math.degrees(lat_rad), math.degrees(lon_rad), alt_km


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_km: float) -> Tuple[float, float, float]:
    """Convert Geodetic coordinates (lat, lon, alt_km) to ECEF (km)."""
    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)

    x = (n + alt_km) * cos_lat * math.cos(lon_rad)
    y = (n + alt_km) * cos_lat * math.sin(lon_rad)
    z = (n * (1.0 - WGS84_E2) + alt_km) * sin_lat
    return x, y, z


def calculate_look_angles(
    obs_lat: float, obs_lon: float, obs_alt_km: float,
    sat_ecef: Tuple[float, float, float]
) -> Tuple[float, float, float]:
    """
    Compute Azimuth (deg), Elevation (deg), and Slant Range (km)
    from observer to satellite.
    """
    obs_x, obs_y, obs_z = geodetic_to_ecef(obs_lat, obs_lon, obs_alt_km)
    dx = sat_ecef[0] - obs_x
    dy = sat_ecef[1] - obs_y
    dz = sat_ecef[2] - obs_z

    range_km = math.sqrt(dx * dx + dy * dy + dz * dz)
    if range_km < 1e-6:
        return 0.0, 90.0, 0.0

    lat_rad = math.radians(obs_lat)
    lon_rad = math.radians(obs_lon)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)

    # Topocentric horizon coordinate vector (East, North, Up)
    east = -sin_lon * dx + cos_lon * dy
    north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    up = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz

    elevation_rad = math.asin(max(-1.0, min(1.0, up / range_km)))
    azimuth_rad = math.atan2(east, north)
    if azimuth_rad < 0:
        azimuth_rad += 2.0 * math.pi

    return math.degrees(azimuth_rad), math.degrees(elevation_rad), range_km


class Sgp4Propagator:
    """Wrapper around SGP4 satellite records."""

    def __init__(self, norad_id: int, name: str, line1: str, line2: str):
        self.norad_id = norad_id
        self.name = name
        self.line1 = line1
        self.line2 = line2
        self.satrec = Satrec.twoline2rv(line1, line2)

    def propagate(self, dt: datetime) -> Optional[Dict[str, Any]]:
        """Propagate to a specific datetime UTC."""
        jd, fr = datetime_to_jd_fr(dt)
        e, r, v = self.satrec.sgp4(jd, fr)
        if e != 0:
            return None  # SGP4 error code (e.g., decay)

        theta = gmst_radians(jd, fr)
        ecef = teme_to_ecef(r, theta)
        lat, lon, alt_km = ecef_to_geodetic(ecef[0], ecef[1], ecef[2])
        vel_kms = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)

        return {
            "norad_id": self.norad_id,
            "name": self.name,
            "timestamp": dt.isoformat(),
            "lat": lat,
            "lon": lon,
            "altitude_km": alt_km,
            "velocity_kms": vel_kms,
            "ecef": ecef,
        }


def predict_passes(
    satellites: List[Sgp4Propagator],
    obs_lat: float,
    obs_lon: float,
    start_time: datetime,
    duration_hours: int = 12,
    min_elevation_deg: float = 15.0,
    step_seconds: int = 30,
) -> List[Dict[str, Any]]:
    """Predict satellite overhead passes for an observer location."""
    end_time = start_time + timedelta(hours=duration_hours)
    passes: List[Dict[str, Any]] = []

    for sat in satellites:
        current_time = start_time
        in_pass = False
        current_pass: Optional[Dict[str, Any]] = None

        while current_time <= end_time:
            jd, fr = datetime_to_jd_fr(current_time)
            e, r, _ = sat.satrec.sgp4(jd, fr)
            if e == 0:
                theta = gmst_radians(jd, fr)
                sat_ecef = teme_to_ecef(r, theta)
                az, el, rng = calculate_look_angles(obs_lat, obs_lon, 0.0, sat_ecef)

                if el >= min_elevation_deg:
                    if not in_pass:
                        in_pass = True
                        current_pass = {
                            "norad_id": sat.norad_id,
                            "name": sat.name,
                            "rise_time": current_time.isoformat(),
                            "rise_azimuth_deg": round(az, 1),
                            "max_elevation_deg": round(el, 1),
                            "max_elevation_time": current_time.isoformat(),
                            "set_time": None,
                            "set_azimuth_deg": None,
                            "min_range_km": round(rng, 1),
                        }
                    else:
                        if el > current_pass["max_elevation_deg"]:
                            current_pass["max_elevation_deg"] = round(el, 1)
                            current_pass["max_elevation_time"] = current_time.isoformat()
                            current_pass["min_range_km"] = min(current_pass["min_range_km"], round(rng, 1))
                else:
                    if in_pass and current_pass:
                        in_pass = False
                        current_pass["set_time"] = current_time.isoformat()
                        current_pass["set_azimuth_deg"] = round(az, 1)
                        passes.append(current_pass)
                        current_pass = None

            current_time += timedelta(seconds=step_seconds)

        if in_pass and current_pass:
            current_pass["set_time"] = end_time.isoformat()
            passes.append(current_pass)

    # Sort passes by rise time
    passes.sort(key=lambda p: p["rise_time"])
    return passes
