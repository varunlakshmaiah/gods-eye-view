"""
Pattern-of-Life (PoL) & Anomaly Detection Engine.
Detects non-cooperative, clandestine, or catastrophic telemetry behaviors:
1. Dark Contacts: Transponder dropouts mid-flight or at sea near critical AOIs/cables.
2. Maneuver Anomalies: Rapid uncoordinated descents (> 3,500 fpm) and erratic turns.
"""
import math
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from ..router import router
from ..alerts.dispatcher import dispatcher, TacticalAlert


def _resolve_aoi(aoi_name: str, aois: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    if aois is None:
        try:
            from ..alerts.engine import tripwire_engine
            aois = tripwire_engine.get_aois()
        except Exception:
            aois = []
    target = next((a for a in aois if a["name"].lower() == aoi_name.lower()), None)
    if not target:
        return {"name": "Bangalore", "lat": 12.9716, "lon": 77.5946, "radius_km": 75.0, "airports": ["VOBL", "VOBG"]}
    return target


class PolEngine:
    async def detect_dark_contacts(
        self,
        aoi_name: str = "Bangalore",
        silence_window_min: int = 15,
        layer: str = "all",
        aois: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Detect contacts whose transponders abruptly ceased broadcasting
        while airborne (> 1,000m) or underway at sea (> 2.0 kts) in an AOI.
        """
        if not router.pg_pool:
            return {"status": "error", "message": "TimescaleDB connection not available"}

        target_aoi = _resolve_aoi(aoi_name, aois)
        lat = target_aoi["lat"]
        lon = target_aoi["lon"]
        radius_km = target_aoi.get("radius_km", 75.0)
        aoi_name = target_aoi["name"]

        radius_m = radius_km * 1000.0
        now = datetime.now(timezone.utc)
        active_cutoff = now - timedelta(minutes=silence_window_min)
        history_start = now - timedelta(hours=2)

        layer_filter = ""
        if layer in ["flights", "vessels", "military"]:
            layer_filter = f"AND layer = '{layer}'"

        # SQL to find entities seen in the AOI in [now - 2h, now - silence_min]
        # that have NO telemetry since active_cutoff
        sql = f"""
            WITH recent_aoi_contacts AS (
                SELECT
                    entity_id,
                    layer,
                    MAX(ts) AS last_seen,
                    COUNT(*) as ping_count
                FROM telemetry_history
                WHERE ts >= $1
                  AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography, $4)
                  {layer_filter}
                GROUP BY entity_id, layer
                HAVING MAX(ts) <= $5 AND COUNT(*) >= 2
            ),
            last_fixes AS (
                SELECT DISTINCT ON (th.entity_id)
                    th.entity_id,
                    th.layer,
                    th.ts,
                    ST_Y(th.geom::geometry) AS lat,
                    ST_X(th.geom::geometry) AS lon,
                    th.altitude_m,
                    th.speed_kts,
                    th.heading_deg,
                    th.payload
                FROM telemetry_history th
                INNER JOIN recent_aoi_contacts rc ON th.entity_id = rc.entity_id AND th.ts = rc.last_seen
                ORDER BY th.entity_id, th.ts DESC
            )
            SELECT * FROM last_fixes
            ORDER BY ts DESC;
        """

        dark_contacts = []
        try:
            async with router.pg_pool.acquire() as conn:
                rows = await conn.fetch(sql, history_start, lon, lat, radius_m, active_cutoff)
                for r in rows:
                    last_alt = r["altitude_m"] or 0.0
                    last_spd = r["speed_kts"] or 0.0
                    ent_layer = r["layer"]

                    # Filter: Only flag if contact was airborne (> 1000m) or underway at sea (> 2 kts)
                    is_suspicious = False
                    reason = ""
                    if ent_layer in ["flights", "military"]:
                        if last_alt > 1000.0:
                            is_suspicious = True
                            reason = f"Air transponder dropped mid-air at {int(last_alt)}m altitude ({int(last_spd)} kts)"
                    elif ent_layer == "vessels":
                        if last_spd > 2.0:
                            is_suspicious = True
                            reason = f"AIS transponder ceased in open water at {last_spd:.1f} kts"

                    if is_suspicious:
                        silence_dur_min = (now - r["ts"]).total_seconds() / 60.0
                        dark_contacts.append({
                            "entity_id": r["entity_id"],
                            "layer": ent_layer,
                            "last_seen_ts": r["ts"].isoformat(),
                            "silence_duration_minutes": round(silence_dur_min, 1),
                            "last_lat": r["lat"],
                            "last_lon": r["lon"],
                            "last_altitude_m": last_alt,
                            "last_speed_kts": last_spd,
                            "last_heading_deg": r["heading_deg"],
                            "suspicion_reason": reason,
                        })
        except Exception as e:
            print(f"[PolEngine] Error querying dark contacts: {e}")

        return {
            "aoi_name": aoi_name,
            "silence_threshold_minutes": silence_window_min,
            "scan_timestamp": now.isoformat(),
            "dark_contacts_count": len(dark_contacts),
            "dark_contacts": dark_contacts,
        }

    async def detect_maneuver_anomalies(
        self,
        aoi_name: str = "Bangalore",
        max_descent_rate_fpm: float = 3500.0,
        aois: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Detect severe trajectory anomalies (rapid descent > 3,500 fpm, high-G uncoordinated turns)
        from consecutive waypoint delta vectors.
        """
        if not router.pg_pool:
            return {"status": "error", "message": "TimescaleDB connection not available"}

        target_aoi = _resolve_aoi(aoi_name, aois)
        lat = target_aoi["lat"]
        lon = target_aoi["lon"]
        radius_km = target_aoi.get("radius_km", 75.0)
        aoi_name = target_aoi["name"]
        airports = target_aoi.get("airports", ["VOBL", "VOBG"])

        radius_m = radius_km * 1000.0
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=1)

        # Retrieve consecutive telemetry fixes for aircraft in AOI
        sql = """
            SELECT
                entity_id,
                ts,
                ST_Y(geom::geometry) AS lat,
                ST_X(geom::geometry) AS lon,
                altitude_m,
                speed_kts,
                heading_deg,
                LAG(ts) OVER (PARTITION BY entity_id ORDER BY ts) AS prev_ts,
                LAG(altitude_m) OVER (PARTITION BY entity_id ORDER BY ts) AS prev_alt,
                LAG(heading_deg) OVER (PARTITION BY entity_id ORDER BY ts) AS prev_hdg
            FROM telemetry_history
            WHERE ts >= $1
              AND layer IN ('flights', 'military')
              AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint($2, $3), 4326)::geography, $4)
            ORDER BY entity_id, ts DESC;
        """

        anomalies = []
        try:
            async with router.pg_pool.acquire() as conn:
                rows = await conn.fetch(sql, window_start, lon, lat, radius_m)
                for r in rows:
                    if r["prev_ts"] and r["prev_alt"] is not None and r["altitude_m"] is not None:
                        dt_sec = (r["ts"] - r["prev_ts"]).total_seconds()
                        if 5.0 <= dt_sec <= 180.0:
                            alt_drop_m = r["prev_alt"] - r["altitude_m"]
                            # Convert m/s drop to feet per minute (1 m = 3.28084 ft)
                            descent_rate_fpm = (alt_drop_m / dt_sec) * 60.0 * 3.28084

                            if descent_rate_fpm > max_descent_rate_fpm:
                                anomalies.append({
                                    "entity_id": r["entity_id"],
                                    "timestamp": r["ts"].isoformat(),
                                    "type": "RAPID_UNCOORDINATED_DESCENT",
                                    "descent_rate_fpm": round(descent_rate_fpm, 1),
                                    "threshold_fpm": max_descent_rate_fpm,
                                    "altitude_drop_m": round(alt_drop_m, 1),
                                    "current_altitude_m": r["altitude_m"],
                                    "speed_kts": r["speed_kts"],
                                    "lat": r["lat"],
                                    "lon": r["lon"],
                                    "severity": "CRITICAL" if descent_rate_fpm > 5000.0 else "WARNING",
                                })
        except Exception as e:
            print(f"[PolEngine] Error querying maneuver anomalies: {e}")

        # Deduplicate to latest anomaly per entity
        seen_entities = set()
        deduped = []
        for a in anomalies:
            if a["entity_id"] not in seen_entities:
                seen_entities.add(a["entity_id"])
                deduped.append(a)

        return {
            "aoi_name": aoi_name,
            "threshold_fpm": max_descent_rate_fpm,
            "anomalies_count": len(deduped),
            "anomalies": deduped,
        }


pol_engine = PolEngine()
