"""
Gods-Eye-View MCP Gateway & Spatial REST Server.
Exposes Model Context Protocol (MCP) and REST endpoints for AI Assistants.
"""
import os
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .geocoder import geocode_query
from .sgp4_engine import Sgp4Propagator, predict_passes, calculate_look_angles, teme_to_ecef, gmst_radians, datetime_to_jd_fr
from .router import router, parse_iso_or_relative_timestamp
from .ws_copilot import hub
from .tools.scene_roster import build_contact_roster
from .tools.entity_track import lookup_entity_anchor, get_trajectory
from .tools.environment import get_environmental_digest
from .tools.ui_control import execute_cockpit_control
from .upstream_tools import poll_and_federate, federate_upstream_tools
from .alerts.dispatcher import dispatcher, TacticalAlert
from .alerts.engine import tripwire_engine
from .intsum.engine import intsum_engine
from .kinematics.cpa import calculate_pairwise_cpa, calculate_aoi_intercept, scan_airspace_conflicts
from .pol.engine import pol_engine
from .radar.engine import radar_engine
from .sdr.engine import sdr_engine
from .cot.engine import cot_engine
from .meshtastic.engine import meshtastic_engine
from .escort.engine import escort_engine

# ----------------------------------------------------------------------------
# Lifespan Lifecycle
# ----------------------------------------------------------------------------
async def periodic_intsum_briefing():
    await asyncio.sleep(45)  # Wait for startup and initial telemetry flow
    while True:
        try:
            print("[IntsumEngine] Running scheduled 12-hour INTSUM situational briefing...")
            await intsum_engine.dispatch_summary(aoi_name="Bangalore", window_hours=12)
        except Exception as e:
            print(f"[IntsumEngine] Error in periodic INTSUM dispatch: {e}")
        await asyncio.sleep(43200)  # 12 hours

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[McpGateway] Initializing router connections...")
    await router.initialize()
    upstream_url = os.getenv("UPSTREAM_URL", "http://127.0.0.1:4173")
    asyncio.create_task(poll_and_federate(mcp_server, f"{upstream_url}/mcp"))
    asyncio.create_task(tripwire_engine.start())
    asyncio.create_task(periodic_intsum_briefing())
    asyncio.create_task(cot_engine.start())
    asyncio.create_task(meshtastic_engine.start())
    asyncio.create_task(escort_engine.start())
    yield
    print("[McpGateway] Closing router connections...")
    escort_engine.stop()
    meshtastic_engine.stop()
    cot_engine.stop()
    sdr_engine.stop()
    tripwire_engine.stop()
    await router.close()

app = FastAPI(
    title="Gods-Eye-View Spatio-Temporal MCP Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------------------------------------------------------
# Request Schemas
# ----------------------------------------------------------------------------
class ResolveAnchorRequest(BaseModel):
    query: str = Field(..., description="Place name, airport code, callsign, MMSI, or coordinates")
    timestamp: Optional[str] = Field(None, description="Optional ISO timestamp")

class SceneContextRequest(BaseModel):
    lat: float
    lon: float
    radius_km: float = 100.0
    timestamp: str = "now"
    layers: List[str] = ["all"]

class EntityTrajectoryRequest(BaseModel):
    entity_id: str
    start_time: str
    end_time: str

class SatellitePassesRequest(BaseModel):
    lat: float
    lon: float
    start_time: str = "now"
    duration_hours: int = 12
    min_elevation_deg: float = 15.0

class CockpitControlRequest(BaseModel):
    action: str
    params: Dict[str, Any] = {}

class StaticInfraRequest(BaseModel):
    lat: float
    lon: float
    radius_km: float = 50.0
    category: Optional[str] = None
    limit: int = 50

class AddAoiRequest(BaseModel):
    name: str = Field(..., description="Area name (e.g. 'Bangalore Urban')")
    lat: float = Field(..., description="Centroid Latitude")
    lon: float = Field(..., description="Centroid Longitude")
    radius_km: float = Field(50.0, description="Perimeter radius in kilometers")
    description: str = Field("", description="Description of the AOI")
    airports: List[str] = Field(default_factory=list, description="Expected airport codes within AOI to filter out normal landings")

class TestAlertRequest(BaseModel):
    title: str = Field("Tactical Test Alert", description="Alert title")
    severity: str = Field("INFO", description="Severity: INFO, LOW, MEDIUM, WARNING, HIGH, CRITICAL")
    message: str = Field("Test alert from Gods-Eye-View Sentinel", description="Alert body message")
    category: str = Field("SYSTEM", description="Category: AIRSPACE_INCURSION, SQUAWK_EMERGENCY, CABLE_ANCHOR_DRAG, DISASTER_EARTHQUAKE, SYSTEM")
    aoi_name: Optional[str] = Field("Bangalore", description="Optional AOI name")
    entity_id: Optional[str] = Field(None, description="Optional tracked entity ID")
    lat: Optional[float] = Field(None, description="Optional target latitude")
    lon: Optional[float] = Field(None, description="Optional target longitude")

class CpaRequest(BaseModel):
    entity_a_id: str = Field(..., description="First contact ID or callsign")
    entity_b_id: str = Field(..., description="Second contact ID or callsign")

class ScanConflictsRequest(BaseModel):
    lat: float = Field(..., description="Center latitude for radar scan")
    lon: float = Field(..., description="Center longitude for radar scan")
    radius_km: float = Field(100.0, description="Scan radius in km")
    min_cpa_km: float = Field(5.0, description="Minimum separation threshold in km")
    max_tcpa_min: float = Field(15.0, description="Maximum lookahead time in minutes")

class RadarCoverageRequest(BaseModel):
    lat: float = Field(..., description="Target latitude")
    lon: float = Field(..., description="Target longitude")
    altitude_m: float = Field(1000.0, description="Target altitude in meters")
    station_id: Optional[str] = Field(None, description="Optional radar station ID to evaluate")

class RadarRenderRequest(BaseModel):
    enabled: bool = Field(True, description="Enable or disable 3D radar domes rendering")
    station_id: Optional[str] = Field(None, description="Optional specific radar station ID")

class SdrConfigRequest(BaseModel):
    mode: Optional[str] = Field(None, description="Ingestion mode: synthetic_rf, tcp_sbs, or http_json")
    station_name: Optional[str] = Field(None, description="Station identifier")
    frequency_mhz: Optional[float] = Field(None, description="Tuned frequency in MHz (e.g. 1090.0 or 162.0)")
    source_host: Optional[str] = Field(None, description="Hostname/IP of SBS or readsb feed")
    source_port: Optional[int] = Field(None, description="Port number for SBS TCP feed (default 30003)")
    http_url: Optional[str] = Field(None, description="HTTP URL for aircraft.json polling")
    poll_interval_sec: Optional[float] = Field(None, description="Polling interval in seconds")

class CotConfigRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="Enable or disable CoT broadcast")
    target_host: Optional[str] = Field(None, description="Target host or broadcast IP (default 255.255.255.255)")
    target_port: Optional[int] = Field(None, description="Target UDP port (default 4242)")
    unicast_targets: Optional[List[str]] = Field(None, description="List of target phone or server IPs")
    broadcast_interval_sec: Optional[float] = Field(None, description="Broadcast interval in seconds")
    stale_offset_sec: Optional[int] = Field(None, description="Entity stale expiration offset in seconds")

class CotTestRequest(BaseModel):
    callsign: str = Field("TEST_TARGET", description="Target callsign")
    lat: float = Field(12.9716, description="Target latitude")
    lon: float = Field(77.5946, description="Target longitude")
    altitude_m: float = Field(1500.0, description="Altitude in meters")
    remarks: str = Field("Tactical CoT Test marker from Gods-Eye-View Sidecar", description="Remarks text")
    cot_type: str = Field("a-f-A-C-F", description="MIL-STD-2525 symbol code (e.g. a-f-A-C-F, a-u-A, t-x-d-a)")

class MeshtasticConfigRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="Enable or disable Meshtastic relay")
    mode: Optional[str] = Field(None, description="Operating mode: synthetic_mesh, tcp, or serial")
    region: Optional[str] = Field(None, description="LoRa region: IN_865, EU_868, US_915, etc.")
    channel_name: Optional[str] = Field(None, description="Primary channel name (default LongFast)")
    tcp_host: Optional[str] = Field(None, description="IP address of Wi-Fi/Ethernet Meshtastic node")
    tcp_port: Optional[int] = Field(None, description="TCP port for Meshtastic device (default 4403)")
    serial_port: Optional[str] = Field(None, description="Serial USB device path (e.g. /dev/ttyUSB0)")
    auto_forward_alerts: Optional[bool] = Field(None, description="Automatically forward high-priority alerts")
    broadcast_interval_sec: Optional[float] = Field(None, description="Telemetry update interval in seconds")

class MeshtasticSendRequest(BaseModel):
    text: str = Field(..., description="Alphanumeric text message to broadcast across the LoRa mesh")
    to_node: Optional[str] = Field("^all", description="Target node ID (e.g. '!4a18b01a' or '^all')")
    channel: Optional[int] = Field(0, description="Channel index (default 0)")

class EscortTagRequest(BaseModel):
    device_id: str = Field(..., description="Unique device/member identifier (e.g. 'varun_phone', 'convoy_1')")
    name: str = Field(..., description="Display label (e.g. 'Varun Mobile', 'Lead Patrol SUV')")
    device_type: Optional[str] = Field("person", description="Entity type: person, vehicle, asset, or vip")
    lat: Optional[float] = Field(None, description="Initial latitude")
    lon: Optional[float] = Field(None, description="Initial longitude")
    alt_m: Optional[float] = Field(None, description="Initial altitude in meters")
    bubble_radius_km: Optional[float] = Field(25.0, description="Protective bubble monitoring radius in km")
    source: Optional[str] = Field("api", description="Tracking source identifier")

class EscortPingRequest(BaseModel):
    device_id: str = Field(..., description="Target device/member ID")
    lat: float = Field(..., description="Current GPS latitude")
    lon: float = Field(..., description="Current GPS longitude")
    alt_m: Optional[float] = Field(0.0, description="Current altitude in meters")
    speed_kts: Optional[float] = Field(0.0, description="Speed in knots")
    heading_deg: Optional[float] = Field(0.0, description="Compass heading in degrees (0-360)")
    source: Optional[str] = Field("mobile_ping", description="Telemetry source")

class EscortQueryRequest(BaseModel):
    device_id: str = Field(..., description="Tagged member or vehicle device ID to query relative to")
    query_type: Optional[str] = Field("all", description="Query type: 'all', 'nearest_infrastructure', 'dam', 'military', 'datacenter', 'contacts', 'airspace'")
    category: Optional[str] = Field(None, description="Optional specific category filter")
    radius_km: Optional[float] = Field(None, description="Override scan radius in km")
    limit: Optional[int] = Field(5, description="Maximum results to return")

class EscortRouteRequest(BaseModel):
    device_id: str = Field(..., description="Origin device ID")
    dest_lat: float = Field(..., description="Destination latitude")
    dest_lon: float = Field(..., description="Destination longitude")
    corridor_width_km: Optional[float] = Field(15.0, description="Corridor buffer width in km")

# ----------------------------------------------------------------------------
# Tool Implementation Functions
# ----------------------------------------------------------------------------
async def tool_resolve_anchor(query: str, timestamp: Optional[str] = None) -> Dict[str, Any]:
    # 1. First attempt: place name or airport geocode
    geo = await geocode_query(query)
    if geo:
        lat, lon, label = geo
        return {
            "type": "geographic",
            "query": query,
            "resolved_name": label,
            "lat": lat,
            "lon": lon,
            "altitude_m": 0.0,
            "timestamp": timestamp or datetime.now(timezone.utc).isoformat(),
        }

    # 2. Second hop: Dynamic entity lookup (callsign, flight, MMSI, etc.)
    ent = await lookup_entity_anchor(query, timestamp)
    if ent:
        return {
            "type": "entity",
            "query": query,
            "entity_id": ent["entity_id"],
            "layer": ent["layer"],
            "lat": ent["lat"],
            "lon": ent["lon"],
            "altitude_m": ent["altitude_m"],
            "speed_kts": ent["speed_kts"],
            "heading_deg": ent["heading_deg"],
            "timestamp": ent["timestamp"],
            "payload": ent["payload"],
        }

    return {
        "type": "unresolved",
        "query": query,
        "message": f"Could not resolve '{query}' as a location, airport, callsign, or MMSI.",
    }


async def tool_get_scene_context(
    lat: float,
    lon: float,
    radius_km: float = 100.0,
    timestamp: str = "now",
    layers: List[str] = ["all"],
) -> Dict[str, Any]:
    target_dt, is_live = parse_iso_or_relative_timestamp(timestamp)

    # 1. Query contact fixes (Tile38 if now, TimescaleDB if historical)
    raw_contacts = await router.query_contacts(
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        timestamp_str=timestamp,
        layers=layers,
    )

    # 2. Build 250km distance-ranked contact roster with bearing & vectors
    roster = build_contact_roster(lat, lon, raw_contacts, radius_km)

    # 3. Environmental and seismic digest
    env_digest = await get_environmental_digest(lat, lon, radius_km)

    # 4. Overhead satellite look-angles
    overhead_satellites = []
    if router.pg_pool:
        try:
            async with router.pg_pool.acquire() as conn:
                tle_rows = await conn.fetch(
                    "SELECT norad_id, name, line1, line2 FROM tle_archive ORDER BY fetched_at DESC LIMIT 50;"
                )
                for row in tle_rows:
                    sat = Sgp4Propagator(row["norad_id"], row["name"], row["line1"], row["line2"])
                    jd, fr = datetime_to_jd_fr(target_dt)
                    e, r, _ = sat.satrec.sgp4(jd, fr)
                    if e == 0:
                        theta = gmst_radians(jd, fr)
                        sat_ecef = teme_to_ecef(r, theta)
                        az, el, rng = calculate_look_angles(lat, lon, 0.0, sat_ecef)
                        if el >= 10.0:  # visible above 10 degrees
                            overhead_satellites.append({
                                "norad_id": sat.norad_id,
                                "name": sat.name,
                                "azimuth_deg": round(az, 1),
                                "elevation_deg": round(el, 1),
                                "range_km": round(rng, 1),
                            })
        except Exception as e:
            print(f"[SceneContext] Satellite look-angle calculation error: {e}")

    return {
        "anchor": {"lat": round(lat, 4), "lon": round(lon, 4), "radius_km": radius_km},
        "temporal": {
            "requested_timestamp": timestamp,
            "resolved_utc": target_dt.isoformat(),
            "mode": "live" if is_live else "historical",
        },
        "environment": env_digest,
        "contact_count": len(roster),
        "contact_roster": roster,
        "overhead_satellites": overhead_satellites,
    }


async def tool_get_entity_trajectory(
    entity_id: str, start_time: str, end_time: str
) -> Dict[str, Any]:
    return await get_trajectory(entity_id, start_time, end_time)


async def tool_predict_satellite_passes(
    lat: float,
    lon: float,
    start_time: str = "now",
    duration_hours: int = 12,
    min_elevation_deg: float = 15.0,
) -> Dict[str, Any]:
    start_dt, _ = parse_iso_or_relative_timestamp(start_time)
    satellites: List[Sgp4Propagator] = []

    if router.pg_pool:
        try:
            async with router.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (norad_id)
                        norad_id, name, line1, line2
                    FROM tle_archive
                    ORDER BY norad_id, epoch_ts DESC
                    LIMIT 100;
                    """
                )
                for r in rows:
                    satellites.append(
                        Sgp4Propagator(r["norad_id"], r["name"], r["line1"], r["line2"])
                    )
        except Exception as e:
            print(f"[PredictPasses] DB query error: {e}")

    passes = predict_passes(
        satellites=satellites,
        obs_lat=lat,
        obs_lon=lon,
        start_time=start_dt,
        duration_hours=duration_hours,
        min_elevation_deg=min_elevation_deg,
    )

    return {
        "observer": {"lat": lat, "lon": lon},
        "start_time": start_dt.isoformat(),
        "duration_hours": duration_hours,
        "min_elevation_deg": min_elevation_deg,
        "pass_count": len(passes),
        "passes": passes,
    }


async def tool_control_visual_cockpit(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return await execute_cockpit_control(action, params)


async def tool_get_active_alerts(limit: int = 20) -> List[Dict[str, Any]]:
    """Retrieve recent tactical alerts from database or in-memory ring buffer."""
    if router.pg_pool:
        try:
            async with router.pg_pool.acquire() as conn:
                rows = await conn.fetch("""
                    SELECT id, ts, severity, category, title, message, aoi_id, entity_id, lat, lon, metadata
                    FROM tactical_alerts
                    ORDER BY ts DESC
                    LIMIT $1;
                """, limit)
                if rows:
                    return [
                        {
                            "id": r["id"],
                            "timestamp": r["ts"].isoformat() if r["ts"] else "",
                            "severity": r["severity"],
                            "category": r["category"],
                            "title": r["title"],
                            "message": r["message"],
                            "aoi_id": r["aoi_id"],
                            "entity_id": r["entity_id"],
                            "lat": r["lat"],
                            "lon": r["lon"],
                            "metadata": r["metadata"] if isinstance(r["metadata"], dict) else {},
                        }
                        for r in rows
                    ]
        except Exception as e:
            print(f"[McpGateway] Error querying tactical_alerts table: {e}")
    return dispatcher.get_recent_alerts(limit)


async def tool_add_area_of_interest(
    name: str, lat: float, lon: float, radius_km: float = 50.0, description: str = "", airports: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Add or update an Area of Interest (AOI) tripwire zone."""
    return tripwire_engine.add_aoi(name, lat, lon, radius_km, description, airports)


async def tool_test_tactical_alert(
    title: str = "Tactical Test Alert",
    severity: str = "INFO",
    message: str = "Test alert from Gods-Eye-View Sentinel",
    category: str = "SYSTEM",
    aoi_name: Optional[str] = "Bangalore",
    entity_id: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
) -> Dict[str, Any]:
    """Manually dispatch a tactical alert across all enabled channels (HUD, Desktop, Telegram, Discord, Slack, DB)."""
    import uuid
    alert = TacticalAlert(
        id=f"alert_test_{int(datetime.now(timezone.utc).timestamp())}_{uuid.uuid4().hex[:6]}",
        timestamp=datetime.now(timezone.utc).isoformat(),
        severity=severity.upper(),
        category=category,
        title=title,
        message=message,
        aoi_name=aoi_name,
        entity_id=entity_id,
        lat=lat if lat is not None else (12.9716 if aoi_name == "Bangalore" else None),
        lon=lon if lon is not None else (77.5946 if aoi_name == "Bangalore" else None),
        metadata={"source": "manual_test"}
    )
    results = await dispatcher.dispatch(alert)
    return {
        "alert": alert.to_dict(),
        "dispatch_results": results
    }


async def tool_calculate_pairwise_cpa(entity_a_id: str, entity_b_id: str) -> Dict[str, Any]:
    """Look up both entities from Tile38 / router and compute 4D CPA / TCPA."""
    anchor_a = await lookup_entity_anchor(entity_a_id)
    anchor_b = await lookup_entity_anchor(entity_b_id)
    if not anchor_a or not anchor_b:
        raise HTTPException(
            status_code=404,
            detail=f"Could not resolve both entities: '{entity_a_id}' found: {bool(anchor_a)}, '{entity_b_id}' found: {bool(anchor_b)}"
        )
    
    speed_a = anchor_a.get("speed_kts", 0.0)
    hdg_a = anchor_a.get("heading_deg", 0.0)
    speed_b = anchor_b.get("speed_kts", 0.0)
    hdg_b = anchor_b.get("heading_deg", 0.0)
    
    res = calculate_pairwise_cpa(
        lat_a=anchor_a["lat"], lon_a=anchor_a["lon"], alt_m_a=anchor_a.get("altitude_m", 0.0),
        speed_kts_a=speed_a, heading_deg_a=hdg_a,
        lat_b=anchor_b["lat"], lon_b=anchor_b["lon"], alt_m_b=anchor_b.get("altitude_m", 0.0),
        speed_kts_b=speed_b, heading_deg_b=hdg_b,
    )
    res["entity_a"] = {"id": entity_a_id, "lat": anchor_a["lat"], "lon": anchor_a["lon"], "alt_m": anchor_a.get("altitude_m", 0.0), "speed_kts": speed_a, "heading": hdg_a}
    res["entity_b"] = {"id": entity_b_id, "lat": anchor_b["lat"], "lon": anchor_b["lon"], "alt_m": anchor_b.get("altitude_m", 0.0), "speed_kts": speed_b, "heading": hdg_b}
    return res


async def tool_scan_proximity_conflicts(
    lat: float, lon: float, radius_km: float = 100.0, min_cpa_km: float = 5.0, max_tcpa_min: float = 15.0
) -> Dict[str, Any]:
    """Scan local airspace for converging collision hazards and AOI perimeter intercepts."""
    aois = tripwire_engine.get_aois()
    return await scan_airspace_conflicts(
        redis_client=router.redis,
        lat=lat,
        lon=lon,
        radius_km=radius_km,
        min_cpa_km=min_cpa_km,
        max_tcpa_min=max_tcpa_min,
        aois=aois,
    )

# ----------------------------------------------------------------------------
# REST API Endpoints
# ----------------------------------------------------------------------------
@app.get("/health")
async def health():
    return {
        "service": "mcp-gateway",
        "status": "ok",
        "browser_connected": hub.is_browser_connected(),
        "tile38_connected": router.redis is not None,
        "timescaledb_connected": router.pg_pool is not None,
    }

@app.post("/api/tools/resolve_anchor")
async def api_resolve_anchor(req: ResolveAnchorRequest):
    return await tool_resolve_anchor(req.query, req.timestamp)

@app.post("/api/tools/get_scene_context")
async def api_get_scene_context(req: SceneContextRequest):
    return await tool_get_scene_context(req.lat, req.lon, req.radius_km, req.timestamp, req.layers)

@app.post("/api/tools/get_entity_trajectory")
async def api_get_entity_trajectory(req: EntityTrajectoryRequest):
    return await tool_get_entity_trajectory(req.entity_id, req.start_time, req.end_time)

@app.post("/api/tools/predict_satellite_passes")
async def api_predict_satellite_passes(req: SatellitePassesRequest):
    return await tool_predict_satellite_passes(
        req.lat, req.lon, req.start_time, req.duration_hours, req.min_elevation_deg
    )

@app.post("/api/tools/control_visual_cockpit")
async def api_control_visual_cockpit(req: CockpitControlRequest):
    return await tool_control_visual_cockpit(req.action, req.params)

@app.post("/api/tools/find_static_infrastructure")
async def api_find_static_infrastructure(req: StaticInfraRequest):
    return await router.query_static_infrastructure(
        req.lat, req.lon, req.radius_km, req.category, req.limit
    )

@app.get("/api/alerts/active")
async def api_get_active_alerts(limit: int = 20):
    return await tool_get_active_alerts(limit)

@app.post("/api/alerts/test")
async def api_test_alert(req: TestAlertRequest):
    return await tool_test_tactical_alert(
        title=req.title,
        severity=req.severity,
        message=req.message,
        category=req.category,
        aoi_name=req.aoi_name,
        entity_id=req.entity_id,
        lat=req.lat,
        lon=req.lon,
    )

@app.get("/api/aois")
async def api_get_aois():
    return {"aois": tripwire_engine.get_aois()}

@app.post("/api/aois")
async def api_add_aoi(req: AddAoiRequest):
    res = tripwire_engine.add_aoi(req.name, req.lat, req.lon, req.radius_km, req.description, req.airports)
    # Broadcast updated AOIs to all connected Cesium browser viewports
    await hub.broadcast_json({"type": "aoi_sync", "aois": tripwire_engine.get_aois()})
    return res

@app.get("/api/intsum/generate")
async def api_generate_intsum(aoi: str = "Bangalore", hours: int = 12):
    return await intsum_engine.generate_summary(aoi, hours)

@app.post("/api/intsum/dispatch")
async def api_dispatch_intsum(aoi: str = "Bangalore", hours: int = 12):
    return await intsum_engine.dispatch_summary(aoi, hours)

@app.post("/api/kinematics/cpa")
async def api_calculate_cpa(req: CpaRequest):
    return await tool_calculate_pairwise_cpa(req.entity_a_id, req.entity_b_id)

@app.post("/api/kinematics/conflicts")
async def api_scan_conflicts(req: ScanConflictsRequest):
    return await tool_scan_proximity_conflicts(
        req.lat, req.lon, req.radius_km, req.min_cpa_km, req.max_tcpa_min
    )

@app.get("/api/pol/dark_contacts")
async def api_detect_dark_contacts(aoi: str = "Bangalore", silence_min: int = 15, layer: str = "all"):
    return await pol_engine.detect_dark_contacts(aoi, silence_min, layer)

@app.get("/api/pol/maneuvers")
async def api_detect_maneuver_anomalies(aoi: str = "Bangalore", max_fpm: float = 3500.0):
    return await pol_engine.detect_maneuver_anomalies(aoi, max_fpm)

@app.get("/api/radar/stations")
async def api_get_radar_stations(enabled_only: bool = True):
    return {"stations": radar_engine.get_stations(enabled_only)}

@app.post("/api/radar/coverage")
async def api_evaluate_radar_coverage(req: RadarCoverageRequest):
    return radar_engine.evaluate_coverage(req.lat, req.lon, req.altitude_m, req.station_id)

@app.get("/api/radar/entity_coverage")
async def api_evaluate_entity_radar_coverage(entity_id: str):
    return await radar_engine.evaluate_entity_coverage(entity_id)

@app.post("/api/radar/render_domes")
async def api_render_radar_domes(req: RadarRenderRequest):
    return await radar_engine.render_domes(req.enabled, req.station_id)

@app.get("/api/sdr/status")
async def api_get_sdr_status():
    return sdr_engine.get_status()

@app.post("/api/sdr/configure")
async def api_configure_sdr(req: SdrConfigRequest):
    return sdr_engine.configure(
        mode=req.mode,
        station_name=req.station_name,
        frequency_mhz=req.frequency_mhz,
        source_host=req.source_host,
        source_port=req.source_port,
        http_url=req.http_url,
        poll_interval_sec=req.poll_interval_sec,
    )

@app.post("/api/sdr/start")
async def api_start_sdr():
    return await sdr_engine.start()

@app.post("/api/sdr/stop")
async def api_stop_sdr():
    return sdr_engine.stop()

@app.get("/api/sdr/contacts")
async def api_get_sdr_contacts():
    return {"contacts": sdr_engine.get_contacts()}

@app.get("/api/cot/status")
async def api_get_cot_status():
    return cot_engine.get_status()

@app.post("/api/cot/configure")
async def api_configure_cot(req: CotConfigRequest):
    return cot_engine.configure(
        enabled=req.enabled,
        target_host=req.target_host,
        target_port=req.target_port,
        unicast_targets=req.unicast_targets,
        broadcast_interval_sec=req.broadcast_interval_sec,
        stale_offset_sec=req.stale_offset_sec,
    )

@app.post("/api/cot/start")
async def api_start_cot():
    return await cot_engine.start()

@app.post("/api/cot/stop")
async def api_stop_cot():
    return cot_engine.stop()

@app.post("/api/cot/broadcast_test")
async def api_broadcast_cot_test(req: CotTestRequest):
    from .cot.engine import build_cot_xml
    xml = build_cot_xml(
        uid=f"TEST_{req.callsign}",
        cot_type=req.cot_type,
        lat=req.lat,
        lon=req.lon,
        hae_m=req.altitude_m,
        callsign=req.callsign,
        remarks=req.remarks,
        stale_sec=120,
    )
    cot_engine.send_cot_packet(xml)
    return {"status": "broadcast_sent", "callsign": req.callsign, "lat": req.lat, "lon": req.lon, "xml": xml}

@app.get("/api/meshtastic/status")
async def api_get_meshtastic_status():
    return meshtastic_engine.get_status()

@app.get("/api/meshtastic/nodes")
async def api_get_meshtastic_nodes():
    return meshtastic_engine.get_nodes()

@app.post("/api/meshtastic/configure")
async def api_configure_meshtastic(req: MeshtasticConfigRequest):
    return meshtastic_engine.configure(
        enabled=req.enabled,
        mode=req.mode,
        region=req.region,
        channel_name=req.channel_name,
        tcp_host=req.tcp_host,
        tcp_port=req.tcp_port,
        serial_port=req.serial_port,
        auto_forward_alerts=req.auto_forward_alerts,
        broadcast_interval_sec=req.broadcast_interval_sec,
    )

@app.post("/api/meshtastic/send")
async def api_send_meshtastic_message(req: MeshtasticSendRequest):
    return meshtastic_engine.send_message(
        text=req.text,
        to_node=req.to_node or "^all",
        channel=req.channel or 0,
    )

@app.post("/api/meshtastic/start")
async def api_start_meshtastic():
    return await meshtastic_engine.start()

@app.post("/api/meshtastic/stop")
async def api_stop_meshtastic():
    return meshtastic_engine.stop()

@app.get("/api/escort/devices")
async def api_get_escort_devices():
    return {"devices": escort_engine.list_devices()}

@app.get("/api/escort/device/{device_id}")
async def api_get_escort_device(device_id: str):
    dev = escort_engine.get_device(device_id)
    if not dev:
        raise HTTPException(status_code=404, detail=f"Escort device '{device_id}' not found")
    return dev

@app.post("/api/escort/tag")
async def api_tag_escort_device(req: EscortTagRequest):
    return escort_engine.tag_device(
        device_id=req.device_id,
        name=req.name,
        device_type=req.device_type or "person",
        lat=req.lat,
        lon=req.lon,
        alt_m=req.alt_m,
        bubble_radius_km=req.bubble_radius_km,
        source=req.source or "api",
    )

@app.post("/api/escort/ping")
async def api_ping_escort_location(req: EscortPingRequest):
    return escort_engine.update_location(
        device_id=req.device_id,
        lat=req.lat,
        lon=req.lon,
        alt_m=req.alt_m or 0.0,
        speed_kts=req.speed_kts or 0.0,
        heading_deg=req.heading_deg or 0.0,
        source=req.source or "mobile_ping",
    )

@app.post("/api/escort/query")
async def api_query_relative_to_escort(req: EscortQueryRequest):
    return await escort_engine.query_relative(
        device_id=req.device_id,
        query_type=req.query_type or "all",
        category=req.category,
        radius_km=req.radius_km,
        limit=req.limit or 5,
    )

@app.post("/api/escort/route")
async def api_evaluate_escort_route(req: EscortRouteRequest):
    return await escort_engine.evaluate_safe_route(
        device_id=req.device_id,
        dest_lat=req.dest_lat,
        dest_lon=req.dest_lon,
        corridor_width_km=req.corridor_width_km or 15.0,
    )

@app.delete("/api/escort/device/{device_id}")
async def api_delete_escort_device(device_id: str):
    success = escort_engine.remove_device(device_id)
    return {"status": "removed" if success else "not_found", "device_id": device_id}

@app.get("/api/mcp/tools")
async def api_list_all_tools():
    """List all registered tools (both native and federated) with their schemas."""
    tools = []
    for name, tool_obj in mcp_server._tool_manager._tools.items():
        tools.append({
            "name": name,
            "description": tool_obj.description or "",
            "parameters": getattr(tool_obj, "parameters", {"type": "object", "properties": {}}),
        })
    return {"tools": tools}

@app.post("/api/tools/{tool_name}")
async def api_execute_tool(tool_name: str, payload: Dict[str, Any] = Body(default={})):
    """Generic tool invocation endpoint for both native and federated tools."""
    if tool_name not in mcp_server._tool_manager._tools:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_name}' not found")
    tool_obj = mcp_server._tool_manager._tools[tool_name]
    try:
        res = await tool_obj.fn(**payload)
        return {"result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ----------------------------------------------------------------------------
# WebSocket Hub Endpoint for Browser Co-Pilot Bridge
# ----------------------------------------------------------------------------
@app.websocket("/ws/copilot")
async def websocket_copilot_endpoint(websocket: WebSocket):
    await hub.connect(websocket)
    try:
        import json
        await websocket.send_text(json.dumps({
            "type": "aoi_sync",
            "aois": tripwire_engine.get_aois()
        }))
    except Exception:
        pass
    try:
        while True:
            text = await websocket.receive_text()
            await hub.handle_message(websocket, text)
    except WebSocketDisconnect:
        hub.disconnect(websocket)
    except Exception as e:
        hub.disconnect(websocket)

# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# MCPServer Registration (mcp 2.x - SSE and STDIO support)
# ----------------------------------------------------------------------------
from mcp.server.mcpserver import MCPServer

mcp_server = MCPServer("gods-eye-view")

@mcp_server.tool()
async def resolve_anchor(query: str, timestamp: Optional[str] = None) -> str:
    """Resolve place names, airport codes (ICAO/IATA), callsigns, or MMSIs to exact coordinates."""
    res = await tool_resolve_anchor(query, timestamp)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def get_scene_context(
    lat: float, lon: float, radius_km: float = 100.0, timestamp: str = "now", layers: Optional[List[str]] = None
) -> str:
    """Query 250km radius contact roster with bearings, closing vectors, weather, and overhead satellites."""
    res = await tool_get_scene_context(lat, lon, radius_km, timestamp, layers or ["all"])
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def get_entity_trajectory(entity_id: str, start_time: str, end_time: str) -> str:
    """Reconstruct 4D historical waypoint trajectory for an aircraft or vessel from TimescaleDB."""
    res = await tool_get_entity_trajectory(entity_id, start_time, end_time)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def predict_satellite_passes(
    lat: float, lon: float, start_time: str = "now", duration_hours: int = 12, min_elevation_deg: float = 15.0
) -> str:
    """Calculate upcoming overhead satellite passes and look-angles (azimuth, elevation) via SGP4."""
    res = await tool_predict_satellite_passes(lat, lon, start_time, duration_hours, min_elevation_deg)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def control_visual_cockpit(action: str, params: Dict[str, Any]) -> str:
    """Send camera flight, entity selection, layer toggle, or time replay to browser globe via WebSocket."""
    res = await tool_control_visual_cockpit(action, params)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def find_static_infrastructure(
    lat: float, lon: float, radius_km: float = 50.0, category: Optional[str] = None, limit: int = 50
) -> str:
    """Query permanently persisted global infrastructure in local PostGIS (datacenters, dams, military bases, submarine cables, landing stations) in sub-milliseconds."""
    res = await router.query_static_infrastructure(lat, lon, radius_km, category, limit)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def get_active_alerts(limit: int = 20) -> str:
    """Retrieve recent tactical alerts and perimeter tripwire triggers from database."""
    res = await tool_get_active_alerts(limit)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def add_area_of_interest(
    name: str, lat: float, lon: float, radius_km: float = 50.0, description: str = ""
) -> str:
    """Register or update an Area of Interest (AOI) perimeter tripwire for real-time threat monitoring."""
    res = await tool_add_area_of_interest(name, lat, lon, radius_km, description)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def test_tactical_alert(
    title: str = "Tactical Test Alert",
    severity: str = "INFO",
    message: str = "Test alert from Gods-Eye-View Sentinel",
    category: str = "SYSTEM"
) -> str:
    """Simulate and dispatch a tactical alert across HUD banners, OS desktop notifications, and webhooks."""
    res = await tool_test_tactical_alert(title=title, severity=severity, message=message, category=category)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def generate_intelligence_summary(aoi_name: str = "Bangalore", window_hours: int = 12) -> str:
    """Generate an executive multi-domain Intelligence Summary (INTSUM) dossier for an AOI (air traffic, incursions, hazards, infrastructure, upcoming satellite passes)."""
    dossier = await intsum_engine.generate_summary(aoi_name, window_hours)
    return dossier["markdown"]

@mcp_server.tool()
async def dispatch_intelligence_summary(aoi_name: str = "Bangalore", window_hours: int = 12) -> str:
    """Generate and dispatch an INTSUM situational briefing across all notification channels (Browser HUD, Telegram, Discord, Slack, DB)."""
    res = await intsum_engine.dispatch_summary(aoi_name, window_hours)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def toggle_aoi_perimeters(visible: Optional[bool] = None) -> str:
    """Toggle or set visibility of 3D Area of Interest perimeter rings and tactical geofence beacons in the Cesium browser cockpit."""
    res = await hub.broadcast_command("toggle_aoi_visuals", {"visible": visible})
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def calculate_cpa_intercept(entity_a_id: str, entity_b_id: str) -> str:
    """Calculate 4D Closest Point of Approach (CPA), Time to CPA (TCPA), and closing velocity between two aircraft or vessels."""
    res = await tool_calculate_pairwise_cpa(entity_a_id, entity_b_id)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def scan_proximity_conflicts(
    lat: float, lon: float, radius_km: float = 100.0, min_cpa_km: float = 5.0, max_tcpa_min: float = 15.0
) -> str:
    """Radar conflict scanner: detect converging aircraft violating separation minimums or inbound on AOI intercept trajectories."""
    res = await tool_scan_proximity_conflicts(lat, lon, radius_km, min_cpa_km, max_tcpa_min)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def detect_dark_contacts(
    aoi_name: str = "Bangalore", silence_window_min: int = 15, layer: str = "all"
) -> str:
    """Identify non-cooperative dark contacts whose transponders ceased broadcasting while airborne or underway at sea."""
    res = await pol_engine.detect_dark_contacts(aoi_name, silence_window_min, layer)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def detect_maneuver_anomalies(
    aoi_name: str = "Bangalore", max_descent_rate_fpm: float = 3500.0
) -> str:
    """Detect severe kinematic flight anomalies such as rapid uncoordinated descents (> 3,500 fpm) and high-G deviations."""
    res = await pol_engine.detect_maneuver_anomalies(aoi_name, max_descent_rate_fpm)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def get_radar_stations(enabled_only: bool = True) -> str:
    """List operational military and civilian surveillance radar stations with antenna heights and calculated horizon envelopes."""
    res = radar_engine.get_stations(enabled_only)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def evaluate_radar_coverage(
    lat: float, lon: float, altitude_m: float = 1000.0, station_id: Optional[str] = None
) -> str:
    """Evaluate line-of-sight radar coverage, horizon shadow, slant range, elevation angle, and blind cone status for any target coordinate."""
    res = radar_engine.evaluate_coverage(lat, lon, altitude_m, station_id)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def evaluate_entity_radar_coverage(entity_id: str) -> str:
    """Evaluate real-time line-of-sight radar visibility and horizon shadow for an active tracked aircraft or vessel."""
    res = await radar_engine.evaluate_entity_coverage(entity_id)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def render_radar_domes(enabled: bool = True, station_id: Optional[str] = None) -> str:
    """Render 3D hemispherical radar coverage envelopes, range rings, and azimuth radials in the Cesium browser globe."""
    res = await radar_engine.render_domes(enabled, station_id)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def get_sdr_status() -> str:
    """Check sovereign edge SDR receiver status, active frequency, message rates, and contact counts."""
    res = sdr_engine.get_status()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def configure_sdr_receiver(
    mode: Optional[str] = None,
    station_name: Optional[str] = None,
    frequency_mhz: Optional[float] = None,
    source_host: Optional[str] = None,
    source_port: Optional[int] = None,
    http_url: Optional[str] = None,
    poll_interval_sec: Optional[float] = None,
) -> str:
    """Configure sovereign edge SDR receiver modes ('synthetic_rf', 'tcp_sbs', 'http_json'), host, port, or frequency."""
    res = sdr_engine.configure(
        mode=mode,
        station_name=station_name,
        frequency_mhz=frequency_mhz,
        source_host=source_host,
        source_port=source_port,
        http_url=http_url,
        poll_interval_sec=poll_interval_sec,
    )
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def start_sdr_stream() -> str:
    """Start local sovereign edge SDR receiver ingestion (Mode-S 1090MHz ADS-B / 162MHz AIS)."""
    res = await sdr_engine.start()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def stop_sdr_stream() -> str:
    """Stop local sovereign edge SDR receiver ingestion stream."""
    res = sdr_engine.stop()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def get_sdr_contacts() -> str:
    """Retrieve all contacts received via local sovereign edge RF transponder signals with signal strength (RSSI)."""
    res = sdr_engine.get_contacts()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def get_cot_status() -> str:
    """Check Cursor-on-Target (CoT) / ATAK broadcaster metrics, transmission rate, and network targets."""
    res = cot_engine.get_status()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def configure_cot_broadcaster(
    enabled: Optional[bool] = None,
    target_host: Optional[str] = None,
    target_port: Optional[int] = None,
    unicast_targets: Optional[List[str]] = None,
    broadcast_interval_sec: Optional[float] = None,
    stale_offset_sec: Optional[int] = None,
) -> str:
    """Configure CoT / ATAK broadcaster UDP target host, port (default 4242), unicast phone IPs, or interval."""
    res = cot_engine.configure(
        enabled=enabled,
        target_host=target_host,
        target_port=target_port,
        unicast_targets=unicast_targets,
        broadcast_interval_sec=broadcast_interval_sec,
        stale_offset_sec=stale_offset_sec,
    )
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def start_cot_stream() -> str:
    """Start streaming live CoT XML packets over UDP port 4242 to ATAK, WinTAK, and iTAK clients."""
    res = await cot_engine.start()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def stop_cot_stream() -> str:
    """Stop CoT / ATAK UDP broadcast stream."""
    res = cot_engine.stop()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def broadcast_cot_test_marker(
    callsign: str = "TEST_PIN",
    lat: float = 12.9716,
    lon: float = 77.5946,
    altitude_m: float = 1500.0,
    remarks: str = "Test marker from Gods-Eye-View Sidecar",
    cot_type: str = "a-f-A-C-F",
) -> str:
    """Broadcast an immediate test CoT marker to all ATAK/WinTAK phones and devices on the network."""
    from .cot.engine import build_cot_xml
    xml = build_cot_xml(
        uid=f"TEST_{callsign}",
        cot_type=cot_type,
        lat=lat,
        lon=lon,
        hae_m=altitude_m,
        callsign=callsign,
        remarks=remarks,
        stale_sec=180,
    )
    cot_engine.send_cot_packet(xml)
    import json
    return json.dumps({
        "status": "broadcast_sent",
        "callsign": callsign,
        "lat": lat,
        "lon": lon,
        "cot_type": cot_type,
        "xml_sample": xml,
    }, indent=2)

@mcp_server.tool()
async def get_meshtastic_status() -> str:
    """Check Meshtastic / LoRa off-grid tactical relay status, active channel, modem preset, and packet statistics."""
    res = meshtastic_engine.get_status()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def get_mesh_nodes() -> str:
    """Discover active Meshtastic field nodes, mobile rovers, team positions, battery levels, and SNR signal quality."""
    res = meshtastic_engine.get_nodes()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def configure_meshtastic_relay(
    enabled: Optional[bool] = None,
    mode: Optional[str] = None,
    region: Optional[str] = None,
    channel_name: Optional[str] = None,
    tcp_host: Optional[str] = None,
    tcp_port: Optional[int] = None,
    serial_port: Optional[str] = None,
    auto_forward_alerts: Optional[bool] = None,
    broadcast_interval_sec: Optional[float] = None,
) -> str:
    """Configure Meshtastic relay parameters (region e.g. IN_865/EU_868/US_915, channel, TCP host/port, or serial port)."""
    res = meshtastic_engine.configure(
        enabled=enabled,
        mode=mode,
        region=region,
        channel_name=channel_name,
        tcp_host=tcp_host,
        tcp_port=tcp_port,
        serial_port=serial_port,
        auto_forward_alerts=auto_forward_alerts,
        broadcast_interval_sec=broadcast_interval_sec,
    )
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def send_meshtastic_message(
    text: str,
    to_node: str = "^all",
    channel: int = 0,
) -> str:
    """Broadcast an alphanumeric message across the decentralized LoRa mesh to field teams, rovers, or base stations."""
    res = meshtastic_engine.send_message(text=text, to_node=to_node, channel=channel)
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def broadcast_tactical_alert_over_mesh(
    title: str = "Tactical Alert",
    severity: str = "CRITICAL",
    message: str = "Airspace perimeter breach detected",
) -> str:
    """Compress and broadcast an emergency high-priority tactical alert across the off-grid LoRa radio mesh."""
    meshtastic_engine.broadcast_alert({
        "title": title,
        "severity": severity,
        "message": message,
    })
    import json
    return json.dumps({
        "status": "alert_broadcast_sent",
        "title": title,
        "severity": severity,
        "message": message,
    }, indent=2)

@mcp_server.tool()
async def tag_escort_device(
    device_id: str,
    name: str,
    device_type: str = "person",
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    alt_m: Optional[float] = None,
    bubble_radius_km: float = 25.0,
) -> str:
    """Tag and register a personal phone, team member, VIP, or vehicle with a dynamic protective guardian bubble."""
    res = escort_engine.tag_device(
        device_id=device_id,
        name=name,
        device_type=device_type,
        lat=lat,
        lon=lon,
        alt_m=alt_m,
        bubble_radius_km=bubble_radius_km,
        source="mcp_tool",
    )
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def update_escort_location(
    device_id: str,
    lat: float,
    lon: float,
    alt_m: float = 0.0,
    speed_kts: float = 0.0,
    heading_deg: float = 0.0,
) -> str:
    """Update live GPS location, speed, and heading for a tagged person or vehicle (moves their protective bubble)."""
    res = escort_engine.update_location(
        device_id=device_id,
        lat=lat,
        lon=lon,
        alt_m=alt_m,
        speed_kts=speed_kts,
        heading_deg=heading_deg,
        source="mcp_update",
    )
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def get_escort_status(device_id: Optional[str] = None) -> str:
    """List all monitored members, vehicles, their live coordinates, safety verdicts (SAFE/ADVISORY/ALERT), and active threats."""
    if device_id:
        res = escort_engine.get_device(device_id)
    else:
        res = escort_engine.list_devices()
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def query_relative_to_escort(
    device_id: str,
    query_type: str = "all",
    category: Optional[str] = None,
    radius_km: Optional[float] = None,
    limit: int = 5,
) -> str:
    """Spatial Q&A relative to member's live moving location: 'What is the nearest dam/military base/hospital?', local contacts, or weather."""
    res = await escort_engine.query_relative(
        device_id=device_id,
        query_type=query_type,
        category=category,
        radius_km=radius_km,
        limit=limit,
    )
    import json
    return json.dumps(res, indent=2)

@mcp_server.tool()
async def evaluate_escort_route(
    device_id: str,
    dest_lat: float,
    dest_lon: float,
    corridor_width_km: float = 15.0,
) -> str:
    """Evaluate travel corridor from a member's moving location to a destination for intersecting infrastructure, military, or environmental hazards."""
    res = await escort_engine.evaluate_safe_route(
        device_id=device_id,
        dest_lat=dest_lat,
        dest_lon=dest_lon,
        corridor_width_km=corridor_width_km,
    )
    import json
    return json.dumps(res, indent=2)

# Mount SSE app on FastAPI: exposes /mcp/sse and /mcp/messages
app.mount("/mcp", mcp_server.sse_app())
