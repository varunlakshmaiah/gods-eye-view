"""
Unified Tactical Alert Dispatcher.
Dispatches critical alerts across:
1. Browser 3D Cesium HUD & Native macOS Desktop Notification (via WebSocket /ws/copilot)
2. Telegram Bot API (direct push to mobile phone)
3. Discord / Slack Webhooks
4. Persistent PostgreSQL tactical_alerts log
"""
import os
import json
import uuid
import httpx
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List

from ..ws_copilot import hub
from ..router import router


@dataclass
class TacticalAlert:
    id: str = field(default_factory=lambda: f"alt_{uuid.uuid4().hex[:12]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str = "WARNING"  # CRITICAL, WARNING, INFO
    category: str = "AOI_INCURSION"  # SQUAWK_EMERGENCY, AOI_INCURSION, DISASTER_EARTHQUAKE, DISASTER_FIRE, CABLE_THREAT, TEST
    title: str = "Tactical Alert"
    message: str = ""
    aoi_id: Optional[str] = None
    aoi_name: Optional[str] = None
    entity_id: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AlertDispatcher:
    def __init__(self, config_path: str = "/app/config/watchlists.json"):
        self.config_path = config_path
        self._recent_alerts: List[TacticalAlert] = []
        self._max_in_memory = 100

    def _get_config(self) -> Dict[str, Any]:
        paths = [
            self.config_path,
            "sidecar/config/watchlists.json",
            "../config/watchlists.json",
            "/workspace/sidecar/config/watchlists.json",
        ]
        for p in paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        return json.load(f)
                except Exception as e:
                    print(f"[AlertDispatcher] Error loading config {p}: {e}")
        return {}

    async def dispatch(self, alert: TacticalAlert) -> Dict[str, bool]:
        """Dispatch alert across all configured channels."""
        self._recent_alerts.insert(0, alert)
        if len(self._recent_alerts) > self._max_in_memory:
            self._recent_alerts.pop()

        cfg = self._get_config().get("notifications", {})
        results = {
            "browser_hud": False,
            "telegram": False,
            "discord": False,
            "slack": False,
            "db_logged": False,
        }

        # 1. Dispatch to Browser HUD & macOS Desktop Notification
        try:
            await hub.broadcast_json({
                "type": "tactical_alert",
                "alert": alert.to_dict()
            })
            results["browser_hud"] = True
        except Exception as e:
            print(f"[AlertDispatcher] Browser HUD dispatch error: {e}")

        # 2. Dispatch to Telegram
        tg_cfg = cfg.get("telegram", {})
        tg_token = os.environ.get("TELEGRAM_BOT_TOKEN") or tg_cfg.get("bot_token")
        tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID") or tg_cfg.get("chat_id")
        if (tg_cfg.get("enabled", False) or os.environ.get("TELEGRAM_BOT_TOKEN")) and tg_token and tg_chat_id:
            try:
                results["telegram"] = await self._send_telegram(tg_token, tg_chat_id, alert)
            except Exception as e:
                print(f"[AlertDispatcher] Telegram dispatch error: {e}")

        # 3. Dispatch to Discord Webhook
        dc_cfg = cfg.get("discord_webhook", {})
        dc_url = os.environ.get("DISCORD_WEBHOOK_URL") or dc_cfg.get("webhook_url")
        if (dc_cfg.get("enabled", False) or os.environ.get("DISCORD_WEBHOOK_URL")) and dc_url:
            try:
                results["discord"] = await self._send_discord(dc_url, alert)
            except Exception as e:
                print(f"[AlertDispatcher] Discord dispatch error: {e}")

        # 4. Dispatch to Slack Webhook
        sl_cfg = cfg.get("slack_webhook", {})
        sl_url = os.environ.get("SLACK_WEBHOOK_URL") or sl_cfg.get("webhook_url")
        if (sl_cfg.get("enabled", False) or os.environ.get("SLACK_WEBHOOK_URL")) and sl_url:
            try:
                results["slack"] = await self._send_slack(sl_url, alert)
            except Exception as e:
                print(f"[AlertDispatcher] Slack dispatch error: {e}")

        # 5. Log to PostgreSQL tactical_alerts
        try:
            results["db_logged"] = await self._log_to_db(alert)
        except Exception as e:
            print(f"[AlertDispatcher] DB log error: {e}")

        # 6. Broadcast to ATAK via Cursor-on-Target (CoT)
        try:
            from ..cot.engine import cot_engine
            cot_engine.broadcast_alert(alert.to_dict())
            results["atak_cot"] = True
        except Exception as e:
            print(f"[AlertDispatcher] CoT dispatch error: {e}")
            results["atak_cot"] = False

        # 7. Broadcast to Meshtastic LoRa Tactical Mesh
        try:
            from ..meshtastic.engine import meshtastic_engine
            meshtastic_engine.broadcast_alert(alert.to_dict())
            results["meshtastic_lora"] = True
        except Exception as e:
            print(f"[AlertDispatcher] Meshtastic dispatch error: {e}")
            results["meshtastic_lora"] = False

        print(f"[AlertDispatcher] Dispatched [{alert.severity}] '{alert.title}' -> {results}")
        return results

    async def _send_telegram(self, token: str, chat_id: str, alert: TacticalAlert) -> bool:
        emoji = "🚨" if alert.severity == "CRITICAL" else ("⚠️" if alert.severity == "WARNING" else "ℹ️")
        globe_link = ""
        if alert.lat is not None and alert.lon is not None:
            globe_link = f"\n\n🌐 <a href=\"http://localhost:8080/#v=2&lat={alert.lat:.4f}&lon={alert.lon:.4f}&alt=15000&heading=0&pitch=-90&roll=0\">Open Target in 3D Cockpit</a>"

        text = (
            f"<b>{emoji} TACTICAL ALERT: {alert.title}</b>\n"
            f"<b>Severity:</b> <code>{alert.severity}</code>\n"
            f"<b>Category:</b> <code>{alert.category}</code>\n"
            f"<b>Time:</b> {alert.timestamp}\n\n"
            f"{alert.message}"
            f"{globe_link}"
        )

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            })
            return res.status_code == 200

    async def _send_discord(self, webhook_url: str, alert: TacticalAlert) -> bool:
        color = 0xFF0000 if alert.severity == "CRITICAL" else (0xFFA500 if alert.severity == "WARNING" else 0x00FFCC)
        fields = [
            {"name": "Severity", "value": alert.severity, "inline": True},
            {"name": "Category", "value": alert.category, "inline": True},
        ]
        if alert.aoi_name:
            fields.append({"name": "Area of Interest", "value": alert.aoi_name, "inline": True})
        if alert.lat is not None and alert.lon is not None:
            fields.append({"name": "Coordinates", "value": f"{alert.lat:.4f}, {alert.lon:.4f}", "inline": True})
            fields.append({"name": "Globe Link", "value": f"[Open 3D Cockpit](http://localhost:8080/#v=2&lat={alert.lat:.4f}&lon={alert.lon:.4f}&alt=15000)", "inline": False})

        embed = {
            "title": f"🚨 {alert.title}",
            "description": alert.message,
            "color": color,
            "timestamp": alert.timestamp,
            "fields": fields,
            "footer": {"text": "Gods-Eye-View Personal Intelligence Sentinel"}
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(webhook_url, json={"embeds": [embed]})
            return res.status_code in (200, 204)

    async def _send_slack(self, webhook_url: str, alert: TacticalAlert) -> bool:
        text = f"🚨 *TACTICAL ALERT: {alert.title}* [{alert.severity}]\n{alert.message}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(webhook_url, json={"text": text})
            return res.status_code == 200

    async def _log_to_db(self, alert: TacticalAlert) -> bool:
        if not router.pg_pool:
            return False
        async with router.pg_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO tactical_alerts (id, ts, severity, category, title, message, aoi_id, entity_id, lat, lon, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb)
                ON CONFLICT (id) DO NOTHING;
            """, alert.id, datetime.fromisoformat(alert.timestamp), alert.severity, alert.category,
                 alert.title, alert.message, alert.aoi_id, alert.entity_id, alert.lat, alert.lon, json.dumps(alert.metadata))
            return True

    def get_recent_alerts(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [a.to_dict() for a in self._recent_alerts[:limit]]


dispatcher = AlertDispatcher()
