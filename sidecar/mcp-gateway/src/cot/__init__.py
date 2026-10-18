"""
Cursor-on-Target (CoT) & ATAK Tactical Data Link Module
Serializes 4D spatial contacts, radar horizons, and tactical tripwire alerts into
MIL-STD-2525 Cursor-on-Target XML events and broadcasts over UDP port 4242 to ATAK/WinTAK/iTAK clients.
"""
from .engine import CotEngine, cot_engine

__all__ = ["CotEngine", "cot_engine"]
