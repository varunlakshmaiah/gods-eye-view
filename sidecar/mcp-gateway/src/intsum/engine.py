"""
Gods-Eye-View INTSUM (Intelligence Summary) Engine.
Synthesizes multi-domain spatio-temporal telemetry into executive situational dossiers:
- Air Domain (Traffic volume, low-altitude incursions, squawks)
- Maritime Domain (Vessels, cable proximity)
- Environmental Hazards (Seismic quakes, wildfires, weather)
- Critical Infrastructure Health (PostGIS permanent assets)
- Space Domain (Overhead orbital passes via SGP4)
"""
import os
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

from ..router import router
from ..sgp4_engine import predict_passes
from ..alerts.dispatcher import dispatcher, TacticalAlert
from ..alerts.engine import tripwire_engine


class IntsumEngine:
    def __init__(self):
        self.last_intsum: Optional[Dict[str, Any]] = None

    async def generate_summary(
        self,
        aoi_name: str = "Bangalore",
        window_hours: int = 12,
    ) -> Dict[str, Any]:
        """Synthesize multi-source operational INTSUM for a given AOI."""
        # 1. Resolve AOI parameters
        aois = tripwire_engine.get_aois()
        target_aoi = next((a for a in aois if a["name"].lower() == aoi_name.lower()), None)
        if not target_aoi and aois:
            target_aoi = aois[0]
        
        if not target_aoi:
            lat, lon, radius_km = 12.9716, 77.5946, 75.0
            aoi_name = "Bangalore"
        else:
            lat = target_aoi["lat"]
            lon = target_aoi["lon"]
            radius_km = target_aoi.get("radius_km", 75.0)
            aoi_name = target_aoi["name"]

        now = datetime.now(timezone.utc)
        start_time = now - timedelta(hours=window_hours)

        # 2. Query Recent Alerts from TimescaleDB
        recent_alerts = []
        threat_level = "GREEN (NOMINAL)"
        critical_count = 0
        warning_count = 0

        if router.pg_pool:
            try:
                async with router.pg_pool.acquire() as conn:
                    rows = await conn.fetch("""
                        SELECT id, ts, severity, category, title, message, entity_id
                        FROM tactical_alerts
                        WHERE ts >= $1 AND (aoi_id = $2 OR aoi_id IS NULL)
                        ORDER BY ts DESC
                        LIMIT 50;
                    """, start_time, target_aoi.get("id") if target_aoi else "aoi_bangalore")
                    for r in rows:
                        recent_alerts.append({
                            "id": r["id"],
                            "timestamp": r["ts"].isoformat() if r["ts"] else "",
                            "severity": r["severity"],
                            "category": r["category"],
                            "title": r["title"],
                            "message": r["message"],
                            "entity_id": r["entity_id"],
                        })
                        if r["severity"] == "CRITICAL":
                            critical_count += 1
                        elif r["severity"] == "WARNING":
                            warning_count += 1
            except Exception as e:
                print(f"[IntsumEngine] DB query error: {e}")

        if critical_count > 0:
            threat_level = "RED (CRITICAL ANOMALIES DETECTED)"
        elif warning_count >= 3:
            threat_level = "AMBER (ELEVATED CONTACT VOLUME)"

        # 3. Air Traffic Volume from Tile38 Hot Tier
        live_aircraft_count = 0
        if router.redis:
            try:
                nearby_res = await router.redis.execute_command(
                    "NEARBY", "flights", "COUNT", "POINT", lat, lon, int(radius_km * 1000)
                )
                if isinstance(nearby_res, (int, float)):
                    live_aircraft_count = int(nearby_res)
                elif isinstance(nearby_res, list) and len(nearby_res) > 1:
                    live_aircraft_count = int(nearby_res[1])
            except Exception:
                pass

        # 4. Critical Infrastructure in AOI
        infra_items = []
        try:
            infra_res = await router.query_static_infrastructure(lat, lon, radius_km, limit=10)
            infra_items = infra_res.get("features", [])
        except Exception:
            pass

        # 5. Overhead Orbital Passes (SGP4)
        passes_summary = []
        try:
            pass_res = predict_passes(lat, lon, start_time="now", duration_hours=min(window_hours, 12), min_elevation_deg=25.0)
            for p in pass_res[:5]:
                passes_summary.append({
                    "name": p.get("satellite_name"),
                    "norad_id": p.get("norad_id"),
                    "rise_time": p.get("rise_time"),
                    "max_elevation": p.get("max_elevation_deg"),
                })
        except Exception:
            pass

        # 6. Synthesize Markdown Dossier
        dossier_lines = [
            f"# 🛡️ SITUATION INTELLIGENCE DOSSIER (INTSUM)",
            f"**Area of Interest:** {aoi_name.upper()} ({lat:.4f}° N, {lon:.4f}° E)",
            f"**Operational Perimeter:** {radius_km:.1f} km Radius | **Window:** Past {window_hours} Hours",
            f"**Assessment Time:** {now.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**Threat Assessment Verdict:** `{threat_level}`",
            "",
            "---",
            "",
            "### 1. Air Domain & Perimeter Status",
            f"- **Live Aircraft Operating in AOI:** `{live_aircraft_count}` contacts currently tracked.",
            f"- **Perimeter Alerts Triggered:** `{len(recent_alerts)}` events ({critical_count} Critical, {warning_count} Warning).",
        ]

        if recent_alerts:
            dossier_lines.append("\n**Recent Flagged Incursions & Anomalies:**")
            for a in recent_alerts[:5]:
                dossier_lines.append(f"- `[{a['severity']}]` **{a['title']}** ({a['timestamp'][11:19]} UTC): {a['message']}")
        else:
            dossier_lines.append("- *No unauthorized incursions or emergency squawk transponders detected.*")

        dossier_lines.extend([
            "",
            "### 2. Critical Infrastructure Nodes",
            f"- **Monitored Strategic Facilities in Zone:** `{len(infra_items)}` mapped permanent assets.",
        ])
        for inf in infra_items[:4]:
            props = inf.get("properties", {})
            name = props.get("name", "Unnamed Facility")
            cat = props.get("category", "infrastructure")
            dist = props.get("distance_km", 0.0)
            dossier_lines.append(f"- **{name}** (`{cat}`) — {dist:.1f} km from AOI center.")

        dossier_lines.extend([
            "",
            "### 3. Overhead Space Domain (Next 12 Hours)",
            f"- **High-Elevation Orbital Passes ($\ge$ 25° Look-Angle):** `{len(passes_summary)}` passes.",
        ])
        for sat in passes_summary:
            rise_str = sat['rise_time'][11:19] if sat.get('rise_time') else 'N/A'
            dossier_lines.append(f"- **{sat['name']}** (NORAD #{sat['norad_id']}) — Peak Elevation: `{sat['max_elevation']:.1f}°` at `{rise_str} UTC`")

        dossier_lines.extend([
            "",
            "---",
            f"*Generated autonomously by Gods-Eye-View Personal C6ISR Sentinel v1.4.0*",
        ])

        markdown_report = "\n".join(dossier_lines)

        dossier = {
            "aoi_name": aoi_name,
            "threat_level": threat_level,
            "generated_at": now.isoformat(),
            "window_hours": window_hours,
            "live_aircraft": live_aircraft_count,
            "alerts_count": len(recent_alerts),
            "critical_count": critical_count,
            "infrastructure_count": len(infra_items),
            "satellite_passes": passes_summary,
            "markdown": markdown_report,
        }

        self.last_intsum = dossier
        return dossier

    async def dispatch_summary(self, aoi_name: str = "Bangalore", window_hours: int = 12) -> Dict[str, Any]:
        """Generate and dispatch INTSUM across all configured channels."""
        dossier = await self.generate_summary(aoi_name, window_hours)
        alert = TacticalAlert(
            id=f"intsum_{int(datetime.now(timezone.utc).timestamp())}",
            timestamp=datetime.now(timezone.utc).isoformat(),
            severity="INFO" if dossier["critical_count"] == 0 else "WARNING",
            category="INTSUM_DOSSIER",
            title=f"INTSUM: {aoi_name} Operational Briefing",
            message=f"Threat Level: {dossier['threat_level']}. {dossier['live_aircraft']} aircraft tracked, {dossier['alerts_count']} perimeter alerts.",
            aoi_name=aoi_name,
            lat=12.9716 if aoi_name == "Bangalore" else None,
            lon=77.5946 if aoi_name == "Bangalore" else None,
            metadata={"source": "intsum_engine", "dossier": dossier}
        )
        dispatch_results = await dispatcher.dispatch(alert)
        return {
            "dossier": dossier,
            "dispatch_results": dispatch_results
        }


intsum_engine = IntsumEngine()
