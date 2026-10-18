"""
Sovereign Edge SDR Radio Feeder Daemon
Connects to RTL-SDR dongle / dump1090 daemon and streams Mode-S & AIS into Tile38 and TimescaleDB.
"""
import os
import sys
import json
import time
import asyncio
import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8000")
SDR_MODE = os.environ.get("SDR_MODE", "synthetic_rf")
STATION_NAME = os.environ.get("STATION_NAME", "Edge Sovereign SDR Node")

async def main():
    print(f"[SdrFeeder] Starting sovereign SDR feeder daemon (Mode: {SDR_MODE}, Station: {STATION_NAME})...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        # 1. Configure Gateway SDR engine
        try:
            res = await client.post(
                f"{GATEWAY_URL}/api/sdr/configure",
                json={
                    "mode": SDR_MODE,
                    "station_name": STATION_NAME,
                }
            )
            print(f"[SdrFeeder] Gateway configured: {res.status_code}")
        except Exception as e:
            print(f"[SdrFeeder] Waiting for gateway to become available: {e}")

        # 2. Start ingestion stream
        try:
            res = await client.post(f"{GATEWAY_URL}/api/sdr/start")
            print(f"[SdrFeeder] Ingestion stream started: {res.json()}")
        except Exception as e:
            print(f"[SdrFeeder] Failed to start SDR stream: {e}")

        # 3. Monitor health
        while True:
            await asyncio.sleep(15)
            try:
                res = await client.get(f"{GATEWAY_URL}/api/sdr/status")
                if res.status_code == 200:
                    status = res.json()
                    print(
                        f"[SdrFeeder] Heartbeat: {status['total_messages']} msgs, "
                        f"{status['active_rf_contacts_count']} active RF contacts, "
                        f"{status['message_rate_hz']} msg/s"
                    )
            except Exception as e:
                print(f"[SdrFeeder] Status check error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
