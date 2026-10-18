"""
Bi-directional WebSocket Co-Pilot Hub.
Bridges headless AI agent tools to active Cesium browser UI.
"""
import asyncio
import json
from typing import Set, Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect

class WsCopilotHub:
    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.latest_viewport: Dict[str, Any] = {
            "lat": 37.7749,
            "lon": -122.4194,
            "altitude_m": 15000.0,
            "heading_deg": 0.0,
            "pitch_deg": -45.0,
            "timestamp": None,
        }
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._req_counter = 0

    def is_browser_connected(self) -> bool:
        return len(self.active_connections) > 0

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.add(websocket)
        print(f"[WsCopilot] Browser UI connected. Active clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        self.active_connections.discard(websocket)
        print(f"[WsCopilot] Browser UI disconnected. Remaining: {len(self.active_connections)}")

    async def handle_message(self, websocket: WebSocket, text: str):
        try:
            data = json.loads(text)
            msg_type = data.get("type")

            if msg_type == "viewport_update":
                payload = data.get("payload", {})
                self.latest_viewport = {
                    "lat": payload.get("lat", self.latest_viewport["lat"]),
                    "lon": payload.get("lon", self.latest_viewport["lon"]),
                    "altitude_m": payload.get("alt", self.latest_viewport["altitude_m"]),
                    "heading_deg": payload.get("heading", self.latest_viewport["heading_deg"]),
                    "pitch_deg": payload.get("pitch", self.latest_viewport["pitch_deg"]),
                    "timestamp": payload.get("timestamp"),
                }
            elif msg_type == "response":
                req_id = data.get("req_id")
                if req_id and req_id in self._pending_requests:
                    future = self._pending_requests.pop(req_id)
                    if not future.done():
                        future.set_result(data.get("result"))
        except Exception as e:
            print(f"[WsCopilot] Error processing message: {e}")

    async def broadcast_command(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Broadcast command to all connected browser clients."""
        if not self.is_browser_connected():
            return {
                "status": "error",
                "message": "No active browser UI connected to Gods-Eye-View Co-Pilot bridge.",
            }

        self._req_counter += 1
        req_id = f"cmd_{self._req_counter}"
        message = {
            "type": "command",
            "req_id": req_id,
            "action": action,
            "params": params,
        }
        payload_text = json.dumps(message)

        dead_sockets = set()
        for ws in self.active_connections:
            try:
                await ws.send_text(payload_text)
            except Exception:
                dead_sockets.add(ws)

        for ws in dead_sockets:
            self.disconnect(ws)

        return {
            "status": "dispatched",
            "action": action,
            "params": params,
            "clients_count": len(self.active_connections),
        }

    async def get_active_viewport(self) -> Dict[str, Any]:
        """Get latest camera viewport from active browser."""
        if not self.is_browser_connected():
            return {
                "status": "offline",
                "message": "No active browser session. Returning last known viewport.",
                "viewport": self.latest_viewport,
            }
        return {
            "status": "live",
            "viewport": self.latest_viewport,
        }

hub = WsCopilotHub()
