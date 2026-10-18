"""
Two-hop entity location and historical 4D trajectory reconstruction.
"""
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
from dateutil.parser import parse
from ..router import router
from .scene_roster import haversine_distance_km


async def lookup_entity_anchor(
    query: str, timestamp_str: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Two-hop lookup: Map a callsign, flight name, MMSI, or hex to exact coordinates.
    Checks live Tile38 hot cache first, then falls back to TimescaleDB history.
    """
    clean_q = query.strip()

    # Step 1: Check live Tile38 hot tier first for real-time contact
    if router.redis:
        for layer in ["flights", "vessels", "military"]:
            try:
                res = await router.redis.execute_command("GET", layer, clean_q, "WITHFIELDS")
                if res:
                    import json
                    data = json.loads(res) if isinstance(res, (str, bytes)) else res
                    if data.get("ok") and "object" in data:
                        coords = data["object"].get("coordinates", [])
                        fields = data.get("fields", {})
                        if len(coords) >= 2:
                            return {
                                "entity_id": clean_q,
                                "layer": layer,
                                "lat": float(coords[1]),
                                "lon": float(coords[0]),
                                "altitude_m": float(fields.get("alt", 0.0)),
                                "speed_kts": float(fields.get("spd", 0.0)),
                                "heading_deg": float(fields.get("hdg", 0.0)),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "is_live": True,
                            }
            except Exception:
                pass

    if not router.pg_pool:
        return None

    # Step 2: Query TimescaleDB for matching entity_id or payload callsign/name
    sql = """
        SELECT
            entity_id, layer, ts,
            ST_X(geom::geometry) AS lon,
            ST_Y(geom::geometry) AS lat,
            altitude_m, speed_kts, heading_deg, payload
        FROM telemetry_history
        WHERE entity_id ILIKE $1
           OR payload->>'callsign' ILIKE $1
           OR payload->>'flight' ILIKE $1
           OR payload->>'name' ILIKE $1
        ORDER BY ts DESC
        LIMIT 1;
    """

    try:
        async with router.pg_pool.acquire() as conn:
            row = await conn.fetchrow(sql, clean_q)
            if row:
                return {
                    "entity_id": row["entity_id"],
                    "layer": row["layer"],
                    "lat": row["lat"],
                    "lon": row["lon"],
                    "altitude_m": row["altitude_m"],
                    "speed_kts": row["speed_kts"],
                    "heading_deg": row["heading_deg"],
                    "timestamp": row["ts"].isoformat(),
                    "payload": row["payload"],
                }
    except Exception as e:
        print(f"[EntityTrack] Lookup error for '{query}': {e}")

    return None


async def get_trajectory(
    entity_id: str,
    start_time_str: str,
    end_time_str: str,
) -> Dict[str, Any]:
    """
    Reconstruct full 4D path/trajectory for an entity over [start_time, end_time].
    """
    if not router.pg_pool:
        return {"error": "TimescaleDB connection unavailable", "waypoints": []}

    try:
        start_dt = parse(start_time_str).astimezone(timezone.utc)
        end_dt = parse(end_time_str).astimezone(timezone.utc)
    except Exception as e:
        return {"error": f"Invalid timestamp format: {e}", "waypoints": []}

    sql = """
        SELECT
            ts,
            ST_X(geom::geometry) AS lon,
            ST_Y(geom::geometry) AS lat,
            altitude_m,
            speed_kts,
            heading_deg
        FROM telemetry_history
        WHERE entity_id = $1
          AND ts >= $2 AND ts <= $3
        ORDER BY ts ASC;
    """

    waypoints: List[Dict[str, Any]] = []
    total_dist_km = 0.0

    try:
        async with router.pg_pool.acquire() as conn:
            rows = await conn.fetch(sql, entity_id, start_dt, end_dt)
            prev_lat, prev_lon = None, None

            for r in rows:
                c_lat = r["lat"]
                c_lon = r["lon"]
                if prev_lat is not None and prev_lon is not None:
                    total_dist_km += haversine_distance_km(prev_lat, prev_lon, c_lat, c_lon)

                waypoints.append({
                    "timestamp": r["ts"].isoformat(),
                    "lat": round(c_lat, 5),
                    "lon": round(c_lon, 5),
                    "altitude_m": round(r["altitude_m"] or 0),
                    "speed_kts": round(r["speed_kts"] or 0),
                    "heading_deg": round(r["heading_deg"] or 0, 1),
                })
                prev_lat, prev_lon = c_lat, c_lon

        return {
            "entity_id": entity_id,
            "start_time": start_dt.isoformat(),
            "end_time": end_dt.isoformat(),
            "waypoint_count": len(waypoints),
            "total_distance_km": round(total_dist_km, 2),
            "waypoints": waypoints,
        }
    except Exception as e:
        print(f"[EntityTrack] Trajectory query error: {e}")
        return {"error": str(e), "waypoints": []}
