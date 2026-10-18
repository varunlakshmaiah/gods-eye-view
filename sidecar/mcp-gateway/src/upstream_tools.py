"""
Federate upstream God's Eye View MCP tools into mcp-gateway.
Discovers all tools from http://127.0.0.1:4173/mcp and dynamically registers them.
"""
import os
import httpx
import json
import asyncio
from typing import Optional, Dict, Any
from pydantic import ConfigDict
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.tools.base import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase


class PermissiveArgModel(ArgModelBase):
    model_config = ConfigDict(extra="allow")

    def model_dump_one_level(self):
        res = dict(self.__dict__)
        if hasattr(self, "__pydantic_extra__") and self.__pydantic_extra__:
            res.update(self.__pydantic_extra__)
        return res


async def federate_upstream_tools(server: MCPServer, upstream_mcp_url: str = "http://127.0.0.1:4173/mcp") -> bool:
    """Fetch tools from upstream MCP and register them on server."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                upstream_mcp_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"Host": "localhost:4173", "Content-Type": "application/json"},
            )
            if res.status_code != 200:
                print(f"[UpstreamFederation] Failed to list tools: HTTP {res.status_code}")
                return False

            data = res.json()
            tools = data.get("result", {}).get("tools", [])
            registered_count = 0

            for t_def in tools:
                name = t_def.get("name")
                desc = t_def.get("description", "")
                schema = t_def.get("inputSchema", {"type": "object", "properties": {}})

                # Don't overwrite sidecar native tools if already defined
                if name in server._tool_manager._tools:
                    continue

                def make_runner(tool_name):
                    async def runner(**kwargs) -> str:
                        async with httpx.AsyncClient(timeout=60.0) as call_client:
                            call_res = await call_client.post(
                                upstream_mcp_url,
                                json={
                                    "jsonrpc": "2.0",
                                    "id": 100,
                                    "method": "tools/call",
                                    "params": {"name": tool_name, "arguments": kwargs},
                                },
                                headers={"Host": "localhost:4173", "Content-Type": "application/json"},
                            )
                            if call_res.status_code != 200:
                                return f"Error from upstream MCP: HTTP {call_res.status_code} - {call_res.text}"
                            resp_json = call_res.json()
                            if "error" in resp_json:
                                return f"Upstream error: {resp_json['error']}"

                            res_obj = resp_json.get("result", {})
                            content = res_obj.get("content", [])
                            if content and isinstance(content, list):
                                parts = []
                                for item in content:
                                    if item.get("type") == "text":
                                        parts.append(item.get("text", ""))
                                    elif item.get("type") == "image":
                                        parts.append(f"[Image: {item.get('mimeType')}]")
                                if parts:
                                    return "\n\n".join(parts)
                            return json.dumps(res_obj, indent=2)

                    return runner

                tool_obj = Tool.from_function(make_runner(name), name=name, description=desc)
                tool_obj.fn_metadata.arg_model = PermissiveArgModel
                tool_obj.parameters = schema
                server._tool_manager._tools[name] = tool_obj
                registered_count += 1

            print(f"[UpstreamFederation] Successfully federated {registered_count} upstream tools into MCP Gateway!")
            return True
    except Exception as e:
        print(f"[UpstreamFederation] Note: Upstream tools could not be federated ({e}).")
        return False


async def poll_and_federate(server: MCPServer, upstream_mcp_url: str = "http://127.0.0.1:4173/mcp", max_retries: int = 20):
    """Background polling task to federate tools as soon as upstream Vite is listening."""
    for attempt in range(1, max_retries + 1):
        success = await federate_upstream_tools(server, upstream_mcp_url)
        if success:
            return
        await asyncio.sleep(2.0)
    print("[UpstreamFederation] Warning: Could not connect to upstream MCP server after max retries.")
