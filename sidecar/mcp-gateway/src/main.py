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

# ----------------------------------------------------------------------------
# Lifespan Lifecycle
# ----------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[McpGateway] Initializing router connections...")
    await router.initialize()
    upstream_url = os.getenv("UPSTREAM_URL", "http://127.0.0.1:4173")
    asyncio.create_task(poll_and_federate(mcp_server, f"{upstream_url}/mcp"))
    yield
    print("[McpGateway] Closing router connections...")
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

# Mount SSE app on FastAPI: exposes /mcp/sse and /mcp/messages
app.mount("/mcp", mcp_server.sse_app())
