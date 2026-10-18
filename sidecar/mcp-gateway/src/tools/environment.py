"""
Environmental & Seismic state resolution for spatio-temporal queries.
Fetches atmospheric conditions and nearby seismic events.
"""
from typing import Dict, Any, List, Optional
import httpx
from .scene_roster import haversine_distance_km


async def get_weather_conditions(lat: float, lon: float) -> Dict[str, Any]:
    """Fetch atmospheric weather conditions at lat, lon."""
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
        "timezone": "UTC",
    }
    try:
        async with httpx.AsyncClient(timeout=3.5) as client:
            resp = await client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json().get("current", {})
                return {
                    "temperature_c": data.get("temperature_2m"),
                    "apparent_temp_c": data.get("apparent_temperature"),
                    "humidity_percent": data.get("relative_humidity_2m"),
                    "precipitation_mm": data.get("precipitation"),
                    "wind_speed_kmh": data.get("wind_speed_10m"),
                    "wind_direction_deg": data.get("wind_direction_10m"),
                    "weather_code": data.get("weather_code"),
                }
    except Exception as e:
        print(f"[Environment] Weather fetch error: {e}")

    return {
        "temperature_c": None,
        "wind_speed_kmh": None,
        "weather_code": None,
        "status": "unavailable",
    }


async def get_seismic_events(
    lat: float, lon: float, radius_km: float = 500.0
) -> List[Dict[str, Any]]:
    """Fetch USGS earthquakes occurring within radius_km in trailing 24 hours."""
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/all_day.geojson"
    events: List[Dict[str, Any]] = []

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                features = resp.json().get("features", [])
                for f in features:
                    props = f.get("properties", {})
                    geom = f.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    if len(coords) >= 2:
                        eq_lon, eq_lat = coords[0], coords[1]
                        depth_km = coords[2] if len(coords) > 2 else 0.0
                        dist = haversine_distance_km(lat, lon, eq_lat, eq_lon)
                        if dist <= radius_km:
                            events.append({
                                "id": f.get("id"),
                                "place": props.get("place"),
                                "magnitude": props.get("mag"),
                                "distance_km": round(dist, 1),
                                "depth_km": depth_km,
                                "time": props.get("time"),
                                "lat": eq_lat,
                                "lon": eq_lon,
                            })
    except Exception as e:
        print(f"[Environment] Seismic fetch error: {e}")

    events.sort(key=lambda x: x["distance_km"])
    return events


async def get_environmental_digest(
    lat: float, lon: float, radius_km: float = 250.0
) -> Dict[str, Any]:
    """Compile weather and seismic digest for a given geographic anchor."""
    weather = await get_weather_conditions(lat, lon)
    seismic = await get_seismic_events(lat, lon, radius_km)

    return {
        "location": {"lat": round(lat, 4), "lon": round(lon, 4)},
        "weather": weather,
        "nearby_seismic_count": len(seismic),
        "recent_earthquakes": seismic[:5],  # top 5 closest
    }
