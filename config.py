"""
Configuration module for EcoTrack — Carbon Footprint Awareness Platform.

Centralises environment variables, tuneable constants, emission factors,
and logging setup so every other module imports from a single source of
truth.  Values are loaded once at import time via ``python-dotenv``.
"""

import logging
import os
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# ─── Public API ───────────────────────────────────────────────────────────────

__all__ = [
    "GEMINI_API_KEY",
    "MAPS_API_KEY",
    "PORT",
    "DATA_FILE",
    "ALLOWED_ORIGINS",
    "MAX_MESSAGE_LENGTH",
    "STATS_CACHE_TTL_SEC",
    "TREE_ABSORPTION_KG",
    "GLOBAL_AVG_KG",
    "US_AVG_KG",
    "CAR_FACTORS",
    "FLIGHT_FACTORS",
    "FLIGHT_DISTANCES",
    "DIET_FACTORS",
    "PT_FACTOR",
    "ELEC_FACTOR",
    "GAS_FACTOR",
    "WASTE_FACTOR",
    "CLOTHING_FACTOR",
    "ORDER_FACTOR",
    "STREAM_FACTOR",
    "logger",
]

# ─── Load .env ────────────────────────────────────────────────────────────────

load_dotenv()

# ─── Environment Variables ────────────────────────────────────────────────────

GEMINI_API_KEY: str = os.environ.get("GEMINI_API_KEY", "")
"""Google Gemini API key for AI chat and insights."""

MAPS_API_KEY: str = os.environ.get("MAPS_API_KEY", "")
"""Google Maps Platform key (used only for masked display in /api/config)."""

PORT: int = int(os.environ.get("PORT", "8000"))
"""HTTP port the server listens on."""

DATA_FILE: Path = Path(os.environ.get("DATA_FILE", "data/user_data.json"))
"""Path to the JSON file used for persistent storage."""

ALLOWED_ORIGINS: List[str] = [
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000",
    ).split(",")
    if origin.strip()
]
"""CORS allowed origins, parsed from a comma-separated env variable."""

# ─── Tuneable Constants ───────────────────────────────────────────────────────

MAX_MESSAGE_LENGTH: int = 1000
"""Maximum character length for a single chat message (prevents Gemini cost abuse)."""

STATS_CACHE_TTL_SEC: int = 30
"""TTL in seconds for the platform stats cache."""

TREE_ABSORPTION_KG: float = 21.0
"""Kilograms of CO₂ absorbed per tree per year (IPCC average)."""

GLOBAL_AVG_KG: float = 4_000.0
"""IPCC global average annual CO₂e footprint per capita (kg)."""

US_AVG_KG: float = 14_000.0
"""IEA US average annual CO₂e footprint per capita (kg)."""

# ─── Emission Factors (Sources: IPCC AR6 · IEA 2023 · Our World in Data) ─────

CAR_FACTORS: dict[str, float] = {
    "petrol":   0.21,   # kg CO₂e / km
    "diesel":   0.17,
    "hybrid":   0.11,
    "electric": 0.05,
}
"""Car emission factors in kg CO₂e per kilometre, keyed by fuel type."""

FLIGHT_FACTORS: dict[str, float] = {
    "short":  0.255,
    "medium": 0.195,
    "long":   0.150,
}
"""Flight emission factors in kg CO₂e per passenger-km, keyed by haul length."""

FLIGHT_DISTANCES: dict[str, float] = {
    "short":  500.0,
    "medium": 3_000.0,
    "long":   9_000.0,
}
"""Typical one-way flight distances in km, keyed by haul length."""

DIET_FACTORS: dict[str, float] = {
    "vegan":       1.5,   # kg CO₂e / day
    "vegetarian":  2.5,
    "pescatarian": 3.0,
    "omnivore":    5.0,
    "heavy_meat":  7.5,
}
"""Daily dietary emission factors in kg CO₂e, keyed by diet type."""

PT_FACTOR: float = 0.089
"""Public transport emission factor (kg CO₂e / km)."""

ELEC_FACTOR: float = 0.233
"""Grid electricity emission factor (kg CO₂e / kWh, global average)."""

GAS_FACTOR: float = 2.04
"""Natural gas emission factor (kg CO₂e / m³)."""

WASTE_FACTOR: float = 2.5
"""Food waste emission factor (kg CO₂e / kg food waste)."""

CLOTHING_FACTOR: float = 33.4
"""Embodied carbon per new garment (kg CO₂e)."""

ORDER_FACTOR: float = 0.5
"""Online delivery emission factor (kg CO₂e / order)."""

STREAM_FACTOR: float = 0.036
"""Video streaming emission factor (kg CO₂e / hour)."""

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger: logging.Logger = logging.getLogger("ecotrack")
"""Application-wide logger instance."""
