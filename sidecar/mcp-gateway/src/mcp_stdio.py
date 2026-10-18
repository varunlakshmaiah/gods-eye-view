"""
Gods-Eye-View MCP STDIO Bridge for Claude Desktop.
Communicates with Claude Desktop over stdin/stdout, forwarding commands
to the running Uvicorn server (which holds the active browser WebSocket connection).
Dynamically discovers and federates all native + upstream tools from mcp-gateway.
"""
import sys
import builtins
import functools
import json
import httpx
import os
from typing import Optional, List, Dict, Any

# CRITICAL: In STDIO mode, all application logs must go to stderr.
# Stdout is strictly reserved for JSON-RPC messages.
_orig_print = builtins.print
builtins.print = functools.partial(_orig_print, file=sys.stderr)

import asyncio
from pydantic import ConfigDict
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools.base import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase

GATEWAY_HTTP_URL = os.getenv("MCP_GATEWAY_URL", "http://127.0.0.1:8000")
stdio_server = MCPServer("gods-eye-view")


class PermissiveArgModel(ArgModelBase):
    model_config = ConfigDict(extra="allow")

    def model_dump_one_level(self):
        res = dict(self.__dict__)
        if hasattr(self, "__pydantic_extra__") and self.__pydantic_extra__:
            res.update(self.__pydantic_extra__)
        return res


def make_forwarding_runner(tool_name: str):
    async def runner(**kwargs) -> str:
        async with httpx.AsyncClient(base_url=GATEWAY_HTTP_URL, timeout=60.0) as client:
            res = await client.post(f"/api/tools/{tool_name}", json=kwargs)
            if res.status_code != 200:
                return f"Error executing {tool_name}: HTTP {res.status_code} - {res.text}"
            data = res.json()
            if isinstance(data, dict) and "result" in data:
                result = data["result"]
            else:
                result = data
            if isinstance(result, str):
                return result
            return json.dumps(result, indent=2)

    return runner


def register_tool_definition(t_def: Dict[str, Any]):
    name = t_def["name"]
    desc = t_def.get("description", "")
    params = t_def.get("parameters", {"type": "object", "properties": {}})

    tool_obj = Tool.from_function(make_forwarding_runner(name), name=name, description=desc)
    tool_obj.fn_metadata.arg_model = PermissiveArgModel
    tool_obj.parameters = params
    stdio_server._tool_manager._tools[name] = tool_obj


async def init_stdio_tools():
    """Discover all tools from the running gateway and register them."""
    print("[mcp_stdio] Fetching tool registry from gateway...")
    async with httpx.AsyncClient(base_url=GATEWAY_HTTP_URL, timeout=10.0) as client:
        for attempt in range(15):
            try:
                res = await client.get("/api/mcp/tools")
                if res.status_code == 200:
                    tools = res.json().get("tools", [])
                    for t in tools:
                        register_tool_definition(t)
                    print(f"[mcp_stdio] Successfully registered {len(tools)} tools on STDIO bridge.")
                    return
            except Exception as e:
                print(f"[mcp_stdio] Gateway not ready yet (attempt {attempt+1}/15): {e}")
            await asyncio.sleep(1.0)
    print("[mcp_stdio] Warning: Could not connect to gateway /api/mcp/tools; registering fallback native tools.")
    _register_fallback_tools()


def _register_fallback_tools():
    fallbacks = [
        {"name": "resolve_anchor", "description": "Resolve place names, airport codes, callsigns, or MMSIs to exact coordinates."},
        {"name": "get_scene_context", "description": "Query 250km radius contact roster with bearings, closing vectors, weather, and overhead satellites."},
        {"name": "get_entity_trajectory", "description": "Reconstruct 4D historical waypoint trajectory for an aircraft or vessel from TimescaleDB."},
        {"name": "predict_satellite_passes", "description": "Calculate upcoming overhead satellite passes and look-angles (azimuth, elevation) via SGP4."},
        {"name": "control_visual_cockpit", "description": "Send camera flight, entity selection, layer toggle, or time replay to browser globe via WebSocket."},
    ]
    for fb in fallbacks:
        if fb["name"] not in stdio_server._tool_manager._tools:
            register_tool_definition(fb)


async def main():
    await init_stdio_tools()
    await stdio_server.run_stdio_async()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
