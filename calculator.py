"""
Carbon footprint calculator — pure business logic.

Contains the core ``calculate_carbon`` function which turns a user's
lifestyle inputs into an annualised CO₂e footprint with category
breakdowns, comparison metrics, and an eco-score.

This module has **no side-effects** — it only performs arithmetic and
returns typed dictionaries.
"""

import datetime

from config import (
    CAR_FACTORS,
    CLOTHING_FACTOR,
    DIET_FACTORS,
    ELEC_FACTOR,
    FLIGHT_DISTANCES,
    FLIGHT_FACTORS,
    GAS_FACTOR,
    GLOBAL_AVG_KG,
    ORDER_FACTOR,
    PT_FACTOR,
    STREAM_FACTOR,
    TREE_ABSORPTION_KG,
    US_AVG_KG,
    WASTE_FACTOR,
)
from models import CarbonInput, CarbonResult

# ─── Public API ───────────────────────────────────────────────────────────────

__all__ = ["calculate_carbon"]


def calculate_carbon(data: CarbonInput) -> CarbonResult:
    """Compute annual CO₂e footprint across four lifestyle categories.

    This is a **pure function**: given the same *data* it always produces
    the same result (except for the ``timestamp`` field).

    Args:
        data: Validated lifestyle inputs from the user.

    Returns:
        A :class:`CarbonResult` dictionary containing:

        - ``total_kg_per_year`` / ``total_tonnes`` — aggregate totals
        - ``breakdown`` — per-category sub-totals and line items
        - ``comparisons`` — vs global & US averages
        - ``eco_score`` — 0–100 (higher = greener)
        - ``trees_to_offset`` — trees needed to neutralise emissions
        - ``session_id`` / ``timestamp`` — provenance metadata
    """
    # ── Transport ─────────────────────────────────────────────────────────
    car_annual = data.car_km_per_week * CAR_FACTORS.get(data.car_type, 0.21) * 52
    flight_annual = (
        data.flights_per_year
        * FLIGHT_DISTANCES.get(data.flight_type, 500.0)
        * 2  # round-trip
        * FLIGHT_FACTORS.get(data.flight_type, 0.255)
    )
    pt_annual = data.public_transport_km * 52 * PT_FACTOR
    transport_total = car_annual + flight_annual + pt_annual

    # ── Energy ────────────────────────────────────────────────────────────
    renewable_mult = 1.0 - (data.renewable_energy_pct / 100.0)
    electricity_annual = data.electricity_kwh * 12 * ELEC_FACTOR * renewable_mult
    gas_annual = data.natural_gas_cubic_m * 12 * GAS_FACTOR
    energy_total = electricity_annual + gas_annual

    # ── Food ──────────────────────────────────────────────────────────────
    diet_annual = DIET_FACTORS.get(data.diet_type, 5.0) * 365
    waste_annual = data.food_waste_kg * 12 * WASTE_FACTOR
    food_total = diet_annual + waste_annual

    # ── Lifestyle ─────────────────────────────────────────────────────────
    clothing_annual = data.new_clothes_per_year * CLOTHING_FACTOR
    shopping_annual = data.online_orders_per_month * 12 * ORDER_FACTOR
    streaming_annual = data.streaming_hours_per_day * 365 * STREAM_FACTOR
    lifestyle_total = clothing_annual + shopping_annual + streaming_annual

    total = transport_total + energy_total + food_total + lifestyle_total

    # ── Score & Comparisons ───────────────────────────────────────────────
    eco_score = max(0, min(100, int(100 - (total / US_AVG_KG) * 50)))
    vs_global = round((total / GLOBAL_AVG_KG - 1.0) * 100, 1)
    vs_us = round((total / US_AVG_KG - 1.0) * 100, 1)

    return CarbonResult(
        total_kg_per_year=round(total, 1),
        total_tonnes=round(total / 1_000, 2),
        breakdown={
            "transport": {
                "total": round(transport_total, 1),
                "car": round(car_annual, 1),
                "flights": round(flight_annual, 1),
                "public_transport": round(pt_annual, 1),
            },
            "energy": {
                "total": round(energy_total, 1),
                "electricity": round(electricity_annual, 1),
                "gas": round(gas_annual, 1),
            },
            "food": {
                "total": round(food_total, 1),
                "diet": round(diet_annual, 1),
                "waste": round(waste_annual, 1),
            },
            "lifestyle": {
                "total": round(lifestyle_total, 1),
                "clothing": round(clothing_annual, 1),
                "shopping": round(shopping_annual, 1),
                "streaming": round(streaming_annual, 1),
            },
        },
        comparisons={
            "vs_global_avg_pct": vs_global,
            "vs_us_avg_pct": vs_us,
            "global_avg_kg": GLOBAL_AVG_KG,
            "us_avg_kg": US_AVG_KG,
        },
        eco_score=eco_score,
        trees_to_offset=round(total / TREE_ABSORPTION_KG, 0),
        session_id=data.session_id,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )
