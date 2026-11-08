"""
Maps gateway REST routes and MCP tools to feature flags, and enforces them.
"""
import json
from typing import Dict, Optional, Tuple

from .features import FeatureRegistry

# Tools that stay available regardless of flags.
ALWAYS_ON_TOOLS = {"list_sidecar_features"}

TOOL_FEATURES: Dict[str, str] = {
    "resolve_anchor": "anchor_resolution",
    "get_scene_context": "scene_context",
    "get_entity_trajectory": "entity_trajectory",
    "predict_satellite_passes": "satellite_passes",
    "control_visual_cockpit": "visual_cockpit",
    "find_static_infrastructure": "static_infrastructure",
    "get_active_alerts": "alert_api",
    "test_tactical_alert": "alert_api",
    "add_area_of_interest": "aoi_management",
    "toggle_aoi_perimeters": "aoi_management",
    "generate_intelligence_summary": "intsum_reports",
    "dispatch_intelligence_summary": "intsum_reports",
    "calculate_cpa_intercept": "kinematics_cpa",
    "scan_proximity_conflicts": "kinematics_cpa",
    "detect_dark_contacts": "pattern_of_life",
    "detect_maneuver_anomalies": "pattern_of_life",
    "get_radar_stations": "radar_horizons",
    "evaluate_radar_coverage": "radar_horizons",
    "evaluate_entity_radar_coverage": "radar_horizons",
    "render_radar_domes": "radar_horizons",
    "get_sdr_status": "sdr_ingest",
    "configure_sdr_receiver": "sdr_ingest",
    "start_sdr_stream": "sdr_ingest",
    "stop_sdr_stream": "sdr_ingest",
    "get_sdr_contacts": "sdr_ingest",
    "get_cot_status": "cot_atak",
    "configure_cot_broadcaster": "cot_atak",
    "start_cot_stream": "cot_atak",
    "stop_cot_stream": "cot_atak",
    "broadcast_cot_test_marker": "cot_atak",
    "get_meshtastic_status": "meshtastic_relay",
    "get_mesh_nodes": "meshtastic_relay",
    "configure_meshtastic_relay": "meshtastic_relay",
    "send_meshtastic_message": "meshtastic_relay",
    "broadcast_tactical_alert_over_mesh": "meshtastic_relay",
    "tag_escort_device": "escort_bubbles",
    "untag_escort_device": "escort_bubbles",
    "update_escort_location": "escort_bubbles",
    "get_escort_status": "escort_bubbles",
    "query_relative_to_escort": "escort_bubbles",
    "evaluate_escort_route": "escort_bubbles",
    "tag_traffic_vehicle": "traffic_vehicle_tagging",
    "set_sidecar_feature": "feature_admin_mcp",
}

# Longest matching prefix wins, so list specific paths before their parents.
ROUTE_FEATURES: Tuple[Tuple[str, str], ...] = (
    ("/api/escort/owntracks", "escort_owntracks"),
    ("/api/escort", "escort_bubbles"),
    ("/api/alerts", "alert_api"),
    ("/api/aois", "aoi_management"),
    ("/api/intsum", "intsum_reports"),
    ("/api/kinematics", "kinematics_cpa"),
    ("/api/pol", "pattern_of_life"),
    ("/api/radar", "radar_horizons"),
    ("/api/sdr", "sdr_ingest"),
    ("/api/cot", "cot_atak"),
    ("/api/meshtastic", "meshtastic_relay"),
)


def feature_for_tool(name: str) -> Optional[str]:
    """Feature gating a tool. Unmapped tools are federated upstream tools."""
    if name in ALWAYS_ON_TOOLS:
        return None
    return TOOL_FEATURES.get(name, "upstream_tool_federation")


def feature_for_path(path: str) -> Optional[str]:
    if path.startswith("/api/tools/"):
        name = path[len("/api/tools/"):].strip("/")
        return feature_for_tool(name) if name else None
    for prefix, fid in ROUTE_FEATURES:
        if path == prefix or path.startswith(prefix + "/"):
            return fid
    return None


def disabled_payload(fid: str, registry: FeatureRegistry) -> Dict:
    info = registry.describe(fid)
    if not info["self_enabled"]:
        reason = f"disabled by {info['locked_by_env']}" if info["locked_by_env"] else "disabled in features.json"
    else:
        reason = f"requires disabled feature(s): {', '.join(info['blocked_by'])}"
    return {
        "error": "feature_disabled",
        "feature": fid,
        "message": f"Sidecar feature '{fid}' is disabled ({reason}).",
        "enable_with": f"PUT /api/features/{fid} {{\"enabled\": true}}",
    }


class FeatureGateMiddleware:
    """Pure ASGI middleware (safe for SSE/WebSocket) returning 403 for disabled features."""

    def __init__(self, app, registry: FeatureRegistry):
        self.app = app
        self.registry = registry

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            fid = feature_for_path(scope.get("path", ""))
            if fid and not self.registry.is_enabled(fid):
                body = json.dumps(disabled_payload(fid, self.registry)).encode()
                await send({
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                })
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)
