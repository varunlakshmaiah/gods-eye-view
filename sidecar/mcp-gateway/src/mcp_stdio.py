"""
Gods-Eye-View MCP STDIO Bridge for Claude Desktop.
Communicates with Claude Desktop over stdin/stdout, forwarding commands
to the running Uvicorn server (which holds the active browser WebSocket connection).
"""
import sys
import builtins
import functools
import json
import httpx
from typing import Optional, List, Dict, Any

# CRITICAL: In STDIO mode, all application logs must go to stderr.
# Stdout is strictly reserved for JSON-RPC messages.
_orig_print = builtins.print
builtins.print = functools.partial(_orig_print, file=sys.stderr)

import asyncio
from mcp.server.mcpserver import MCPServer

GATEWAY_HTTP_URL = "http://127.0.0.1:8000"
stdio_server = MCPServer("gods-eye-view")


@stdio_server.tool()
async def resolve_anchor(query: str, timestamp: Optional[str] = None) -> str:
    """Resolve place names, airport codes (ICAO/IATA), callsigns, or MMSIs to exact coordinates."""
    async with httpx.AsyncClient(base_url=GATEWAY_HTTP_URL, timeout=30.0) as client:
        res = await client.post("/api/tools/resolve_anchor", json={"query": query, "timestamp": timestamp})
        return json.dumps(res.json(), indent=2)


@stdio_server.tool()
async def get_scene_context(
    lat: float, lon: float, radius_km: float = 100.0, timestamp: str = "now", layers: Optional[List[str]] = None
) -> str:
    """Query 250km radius contact roster with bearings, closing vectors, weather, and overhead satellites."""
    async with httpx.AsyncClient(base_url=GATEWAY_HTTP_URL, timeout=30.0) as client:
        res = await client.post(
            "/api/tools/get_scene_context",
            json={
                "lat": lat,
                "lon": lon,
                "radius_km": radius_km,
                "timestamp": timestamp,
                "layers": layers or ["all"],
            },
        )
        return json.dumps(res.json(), indent=2)


@stdio_server.tool()
async def get_entity_trajectory(entity_id: str, start_time: str, end_time: str) -> str:
    """Reconstruct 4D historical waypoint trajectory for an aircraft or vessel from TimescaleDB."""
    async with httpx.AsyncClient(base_url=GATEWAY_HTTP_URL, timeout=30.0) as client:
        res = await client.post(
            "/api/tools/get_entity_trajectory",
            json={"entity_id": entity_id, "start_time": start_time, "end_time": end_time},
        )
        return json.dumps(res.json(), indent=2)


@stdio_server.tool()
async def predict_satellite_passes(
    lat: float, lon: float, start_time: str = "now", duration_hours: int = 12, min_elevation_deg: float = 15.0
) -> str:
    """Calculate upcoming overhead satellite passes and look-angles (azimuth, elevation) via SGP4."""
    async with httpx.AsyncClient(base_url=GATEWAY_HTTP_URL, timeout=30.0) as client:
        res = await client.post(
            "/api/tools/predict_satellite_passes",
            json={
                "lat": lat,
                "lon": lon,
                "start_time": start_time,
                "duration_hours": duration_hours,
                "min_elevation_deg": min_elevation_deg,
            },
        )
        return json.dumps(res.json(), indent=2)


@stdio_server.tool()
async def control_visual_cockpit(action: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Control active 3D Cesium globe (fly_to_coordinate, select_entity, toggle_layer, set_time_replay_mode, get_active_viewport)."""
    async with httpx.AsyncClient(base_url=GATEWAY_HTTP_URL, timeout=30.0) as client:
        res = await client.post(
            "/api/tools/control_visual_cockpit",
            json={"action": action, "params": params or {}},
        )
        return json.dumps(res.json(), indent=2)


async def main():
    await stdio_server.run_stdio_async()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
