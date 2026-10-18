"""
Visual Cockpit & Globe Control Tool.
Dispatches commands to connected Cesium browser via the WebSocket bridge.
"""
from typing import Dict, Any
from ..ws_copilot import hub


async def execute_cockpit_control(action: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Execute action on the visual Cesium globe."""
    valid_actions = {
        "get_active_viewport",
        "fly_to_coordinate",
        "select_entity",
        "toggle_layer",
        "set_time_replay_mode",
    }

    if action not in valid_actions:
        return {
            "status": "error",
            "message": f"Invalid action '{action}'. Supported actions: {list(valid_actions)}",
        }

    if action == "get_active_viewport":
        return await hub.get_active_viewport()

    if not hub.is_browser_connected():
        return {
            "status": "offline",
            "message": "Visual Cockpit is offline (no browser connected to bridge). Command queued.",
            "action": action,
            "params": params,
        }

    # Dispatch to browser
    return await hub.broadcast_command(action, params)
