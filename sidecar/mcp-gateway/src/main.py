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
    yield
    print("[McpGateway] Closing router connections...")
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

# Mount SSE app on FastAPI: exposes /mcp/sse and /mcp/messages
app.mount("/mcp", mcp_server.sse_app())
