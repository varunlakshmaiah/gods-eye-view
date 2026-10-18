"""
Sovereign Edge SDR Radio Ingestion Module
Connects to physical or networked RTL-SDR receivers (dump1090 / readsb / rtl-ais)
or provides synthetic Mode-S/AIS sovereign RF reception directly into Tile38 & TimescaleDB.
"""
from .engine import SdrEngine, sdr_engine

__all__ = ["SdrEngine", "sdr_engine"]
