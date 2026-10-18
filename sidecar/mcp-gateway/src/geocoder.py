"""
Local place-name and airport code geocoder with offline fast-path.
"""
from typing import Optional, Tuple, Dict
import httpx
import re

# Offline database of major international airports and prominent global cities
OFFLINE_LOCATIONS: Dict[str, Tuple[float, float, str]] = {
    # Major Airports (ICAO / IATA)
    "KSFO": (37.6188, -122.3754, "San Francisco International Airport"),
    "SFO": (37.6188, -122.3754, "San Francisco International Airport"),
    "KLAX": (33.9425, -118.4081, "Los Angeles International Airport"),
    "LAX": (33.9425, -118.4081, "Los Angeles International Airport"),
    "KJFK": (40.6413, -73.7781, "John F. Kennedy International Airport"),
    "JFK": (40.6413, -73.7781, "John F. Kennedy International Airport"),
    "EGLL": (51.4700, -0.4543, "London Heathrow Airport"),
    "LHR": (51.4700, -0.4543, "London Heathrow Airport"),
    "LFPG": (49.0097, 2.5479, "Paris Charles de Gaulle Airport"),
    "CDG": (49.0097, 2.5479, "Paris Charles de Gaulle Airport"),
    "EDDF": (50.0379, 8.5622, "Frankfurt Airport"),
    "FRA": (50.0379, 8.5622, "Frankfurt Airport"),
    "RJTT": (35.5494, 139.7798, "Tokyo Haneda Airport"),
    "HND": (35.5494, 139.7798, "Tokyo Haneda Airport"),
    "OMDB": (25.2532, 55.3657, "Dubai International Airport"),
    "DXB": (25.2532, 55.3657, "Dubai International Airport"),
    "WSSS": (1.3644, 103.9915, "Singapore Changi Airport"),
    "SIN": (1.3644, 103.9915, "Singapore Changi Airport"),
    "YSSY": (-33.9461, 151.1772, "Sydney Kingsford Smith Airport"),
    "SYD": (-33.9461, 151.1772, "Sydney Kingsford Smith Airport"),

    # Major Cities
    "SAN FRANCISCO": (37.7749, -122.4194, "San Francisco, CA, USA"),
    "LOS ANGELES": (34.0522, -118.2437, "Los Angeles, CA, USA"),
    "NEW YORK": (40.7128, -74.0060, "New York, NY, USA"),
    "LONDON": (51.5074, -0.1278, "London, UK"),
    "PARIS": (48.8566, 2.3522, "Paris, France"),
    "BERLIN": (52.5200, 13.4050, "Berlin, Germany"),
    "TOKYO": (35.6762, 139.6503, "Tokyo, Japan"),
    "SINGAPORE": (1.3521, 103.8198, "Singapore"),
    "SYDNEY": (-33.8688, 151.2093, "Sydney, Australia"),
    "DUBAI": (25.2048, 55.2708, "Dubai, UAE"),
    "HONG KONG": (22.3193, 114.1694, "Hong Kong"),
    "TAIPEI": (25.0330, 121.5654, "Taipei, Taiwan"),
    "SEOUL": (37.5665, 126.9780, "Seoul, South Korea"),
    "UKRAINE": (48.3794, 31.1656, "Ukraine"),
    "KYIV": (50.4501, 30.5234, "Kyiv, Ukraine"),
    "TAIWAN STRAIT": (24.0, 119.5, "Taiwan Strait"),
    "STRAIT OF HORMUZ": (26.5667, 56.25, "Strait of Hormuz"),
    "PANAMA CANAL": (9.08, -79.68, "Panama Canal"),
    "SUEZ CANAL": (30.705, 32.344, "Suez Canal"),
}


async def geocode_query(query: str) -> Optional[Tuple[float, float, str]]:
    """
    Resolve a query string (city name, airport code, or coordinate pair) to (lat, lon, label).
    """
    cleaned = query.strip()
    
    # 1. Check for raw coordinates: "lat, lon"
    coord_match = re.match(r"^[-+]?([1-8]?\d(\.\d+)?|90(\.0+)?),\s*[-+]?(180(\.0+)?|((1[0-7]\d)|([1-9]?\d))(\.\d+)?)$", cleaned)
    if coord_match:
        parts = cleaned.split(",")
        lat, lon = float(parts[0].strip()), float(parts[1].strip())
        return lat, lon, f"Coordinates ({lat:.4f}, {lon:.4f})"

    # 2. Fast-path offline dictionary
    lookup_key = cleaned.upper()
    if lookup_key in OFFLINE_LOCATIONS:
        return OFFLINE_LOCATIONS[lookup_key]

    # Partial match in offline dictionary
    for k, v in OFFLINE_LOCATIONS.items():
        if k in lookup_key or lookup_key in k:
            return v

    # 3. Online fallback to OpenStreetMap Nominatim with strict timeout
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            headers = {"User-Agent": "GodsEyeView-Sidecar/1.0"}
            resp = await client.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": cleaned, "format": "json", "limit": "1"},
                headers=headers,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data and len(data) > 0:
                    lat = float(data[0]["lat"])
                    lon = float(data[0]["lon"])
                    display_name = data[0].get("display_name", cleaned)
                    return lat, lon, display_name
    except Exception as e:
        print(f"[Geocoder] Online geocoding error for '{query}': {e}")

    return None
