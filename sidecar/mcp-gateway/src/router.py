"""
Temporal Router directing queries between Tile38 (Hot Live State)
and TimescaleDB (Cold Historical Archive).
"""
import os
import json
import math
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple
import redis.asyncio as aioredis
import asyncpg
import h3

def parse_iso_or_relative_timestamp(ts_str: Optional[str]) -> Tuple[datetime, bool]:
    """
    Parse a timestamp string. Returns (datetime_utc, is_now).
    is_now is True if ts_str is 'now', None, or within 60s of current UTC.
    """
    now = datetime.now(timezone.utc)
    if not ts_str or ts_str.lower() == "now":
        return now, True

    try:
        from dateutil.parser import parse
        dt = parse(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)

        diff_seconds = abs((now - dt).total_seconds())
        return dt, (diff_seconds <= 60)
    except Exception:
        return now, True


class TemporalRouter:
    def __init__(self):
        self.tile38_host = os.environ.get("TILE38_HOST", "tile38")
        self.tile38_port = int(os.environ.get("TILE38_PORT", "9851"))
        self.db_url = os.environ.get(
            "DATABASE_URL",
            "postgresql://postgres:postgres@timescaledb:5432/gods_eye_sidecar"
        )
        self.redis: Optional[aioredis.Redis] = None
        self.pg_pool: Optional[asyncpg.Pool] = None

    async def initialize(self):
        # Tile38 connection
        try:
            self.redis = aioredis.Redis(
                host=self.tile38_host,
                port=self.tile38_port,
                decode_responses=True,
                protocol=2,
                socket_connect_timeout=2.0
            )
            await self.redis.ping()
            print(f"[TemporalRouter] Connected to Tile38 at {self.tile38_host}:{self.tile38_port}")
        except Exception as e:
            print(f"[TemporalRouter] Tile38 initial connection warning: {e}")

        # TimescaleDB connection
        try:
            self.pg_pool = await asyncpg.create_pool(
                self.db_url,
                min_size=1,
                max_size=10,
                command_timeout=10.0
            )
            print(f"[TemporalRouter] Connected to TimescaleDB pool")
        except Exception as e:
            print(f"[TemporalRouter] TimescaleDB initial connection warning: {e}")

    async def close(self):
        if self.redis:
            await self.redis.close()
        if self.pg_pool:
            await self.pg_pool.close()

    async def query_contacts(
        self,
        lat: float,
        lon: float,
        radius_km: float = 100.0,
        timestamp_str: Optional[str] = "now",
        layers: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        target_dt, is_live = parse_iso_or_relative_timestamp(timestamp_str)
        layers_to_check = layers or ["flights", "military", "vessels", "firms"]
        if "all" in layers_to_check:
            layers_to_check = ["flights", "military", "vessels", "firms"]

        if is_live:
            return await self._query_tile38_live(lat, lon, radius_km, layers_to_check)
        else:
            return await self._query_timescaledb_historical(lat, lon, radius_km, target_dt, layers_to_check)

    async def _query_tile38_live(
        self, lat: float, lon: float, radius_km: float, layers: List[str]
    ) -> List[Dict[str, Any]]:
        contacts: List[Dict[str, Any]] = []
        if not self.redis:
            return contacts

        radius_m = radius_km * 1000.0

        for layer in layers:
            try:
                # Tile38: NEARBY <layer> POINT <lat> <lon> <radius_m>
                raw = await self.redis.execute_command(
                    "NEARBY", layer, "POINT", lat, lon, radius_m
                )
                if not raw or not isinstance(raw, list) or len(raw) < 2:
                    continue

                items = raw[1]
                for item in items:
                    if isinstance(item, list) and len(item) >= 2:
                        entity_id = item[0]
                        point_data = item[1]
                        ent_lat, ent_lon = None, None
                        fields: Dict[str, Any] = {}

                        # Parse coordinates
                        if isinstance(point_data, str):
                            try:
                                geo = json.loads(point_data)
                                if geo.get("type") == "Point" and "coordinates" in geo:
                                    coords = geo["coordinates"]
                                    ent_lon, ent_lat = float(coords[0]), float(coords[1])
                            except Exception:
                                pass
                        elif isinstance(point_data, list):
                            if len(point_data) == 2 and isinstance(point_data[0], (int, float)):
                                ent_lat, ent_lon = float(point_data[0]), float(point_data[1])
                            elif len(point_data) >= 2 and point_data[0] == "POINT":
                                coords = point_data[1]
                                ent_lat, ent_lon = float(coords[0]), float(coords[1])

                        # Parse optional fields (alt, spd, hdg)
                        if len(item) > 2 and isinstance(item[2], list):
                            flist = item[2]
                            for fi in range(0, len(flist), 2):
                                if fi + 1 < len(flist):
                                    fname = str(flist[fi])
                                    try:
                                        fields[fname] = float(flist[fi + 1])
                                    except (ValueError, TypeError):
                                        fields[fname] = flist[fi + 1]

                        if ent_lat is not None and ent_lon is not None:
                            contacts.append({
                                "entity_id": str(entity_id),
                                "layer": layer,
                                "lat": ent_lat,
                                "lon": ent_lon,
                                "altitude_m": fields.get("alt", 0),
                                "speed_kts": fields.get("spd", 0),
                                "heading_deg": fields.get("hdg", 0),
                                "is_live": True,
                            })
            except Exception as e:
                print(f"[TemporalRouter] Tile38 NEARBY error on layer '{layer}': {e}")

        return contacts

    async def _query_timescaledb_historical(
        self, lat: float, lon: float, radius_km: float, target_dt: datetime, layers: List[str]
    ) -> List[Dict[str, Any]]:
        contacts: List[Dict[str, Any]] = []
        if not self.pg_pool:
            return contacts

        # H3 Spatial Partition Pruning: Get H3 res 5 ring around center point
        center_hex = h3.geo_to_h3(lat, lon, 5)
        k_dist = max(1, math.ceil(radius_km / 120.0))
        h3_cells = h3.k_ring(center_hex, k_dist)
        h3_bigints = [int(cell, 16) for cell in h3_cells]

        window_start = target_dt - timedelta(minutes=15)
        radius_m = radius_km * 1000.0

        query = """
            SELECT DISTINCT ON (entity_id)
                entity_id, layer, ts,
                ST_X(geom::geometry) AS lon,
                ST_Y(geom::geometry) AS lat,
                altitude_m, speed_kts, heading_deg, payload
            FROM telemetry_history
            WHERE layer = ANY($1)
              AND h3_res5 = ANY($2)
              AND ts >= $3 AND ts <= $4
              AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint($5, $6, 0), 4326)::geography, $7)
            ORDER BY entity_id, ts DESC;
        """

        try:
            async with self.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    query,
                    layers,
                    h3_bigints,
                    window_start,
                    target_dt,
                    lon,
                    lat,
                    radius_m,
                )
                for r in rows:
                    contacts.append({
                        "entity_id": r["entity_id"],
                        "layer": r["layer"],
                        "lat": r["lat"],
                        "lon": r["lon"],
                        "altitude_m": r["altitude_m"] or 0,
                        "speed_kts": r["speed_kts"] or 0,
                        "heading_deg": r["heading_deg"] or 0,
                        "timestamp": r["ts"].isoformat(),
                        "is_live": False,
                        "payload": json.loads(r["payload"]) if r["payload"] else {},
                    })
        except Exception as e:
            print(f"[TemporalRouter] TimescaleDB historical query error: {e}")

        return contacts

    async def query_static_infrastructure(
        self,
        lat: float,
        lon: float,
        radius_km: float = 50.0,
        category: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Query permanent fixed infrastructure (datacenters, dams, military bases, submarine cables)."""
        if not self.pg_pool:
            return []

        radius_m = radius_km * 1000.0
        results = []
        try:
            async with self.pg_pool.acquire() as conn:
                if category:
                    rows = await conn.fetch(
                        """
                        SELECT id, category, name, operator, capacity, country_code,
                               round((ST_Distance(geom, ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography) / 1000)::numeric, 1) AS distance_km,
                               ST_Y(ST_Centroid(geom::geometry)) AS lat,
                               ST_X(ST_Centroid(geom::geometry)) AS lon,
                               metadata
                        FROM static_infrastructure
                        WHERE category = $4
                          AND ST_DWithin(geom, ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography, $3)
                        ORDER BY distance_km ASC
                        LIMIT $5
                        """,
                        lat, lon, radius_m, category, limit
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT id, category, name, operator, capacity, country_code,
                               round((ST_Distance(geom, ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography) / 1000)::numeric, 1) AS distance_km,
                               ST_Y(ST_Centroid(geom::geometry)) AS lat,
                               ST_X(ST_Centroid(geom::geometry)) AS lon,
                               metadata
                        FROM static_infrastructure
                        WHERE ST_DWithin(geom, ST_SetSRID(ST_MakePoint($2, $1), 4326)::geography, $3)
                        ORDER BY distance_km ASC
                        LIMIT $4
                        """,
                        lat, lon, radius_m, limit
                    )

                for r in rows:
                    meta = json.loads(r["metadata"]) if isinstance(r["metadata"], str) else (r["metadata"] or {})
                    results.append({
                        "id": r["id"],
                        "category": r["category"],
                        "name": r["name"],
                        "distance_km": float(r["distance_km"]),
                        "lat": float(r["lat"]) if r["lat"] is not None else None,
                        "lon": float(r["lon"]) if r["lon"] is not None else None,
                        "operator": r["operator"],
                        "capacity": r["capacity"],
                        "country_code": r["country_code"],
                        "details": meta
                    })
        except Exception as e:
            print(f"[TemporalRouter] Static infrastructure query error: {e}")

        return results

router = TemporalRouter()
