"""
Pydantic models and typed dictionaries for the EcoTrack platform.

All request/response schemas live here so they can be imported by both
the route handlers and the business-logic modules without circular
dependencies.
"""

import uuid
from typing import Dict, Optional, TypedDict

from pydantic import BaseModel, Field

from config import MAX_MESSAGE_LENGTH

# ─── Public API ───────────────────────────────────────────────────────────────

__all__ = [
    "TransportBreakdown",
    "EnergyBreakdown",
    "FoodBreakdown",
    "LifestyleBreakdown",
    "BreakdownDict",
    "ComparisonDict",
    "CarbonResult",
    "ActionItem",
    "InsightsResult",
    "CarbonInput",
    "ChatMessage",
    "ActivityLog",
    "GoalInput",
]

# ─── TypedDict Definitions (structured return types) ──────────────────────────


class TransportBreakdown(TypedDict):
    """Breakdown of transport-related CO₂e emissions."""

    total: float
    car: float
    flights: float
    public_transport: float


class EnergyBreakdown(TypedDict):
    """Breakdown of home-energy CO₂e emissions."""

    total: float
    electricity: float
    gas: float


class FoodBreakdown(TypedDict):
    """Breakdown of food-related CO₂e emissions."""

    total: float
    diet: float
    waste: float


class LifestyleBreakdown(TypedDict):
    """Breakdown of lifestyle CO₂e emissions."""

    total: float
    clothing: float
    shopping: float
    streaming: float


class BreakdownDict(TypedDict):
    """Full emission breakdown across all four categories."""

    transport: TransportBreakdown
    energy: EnergyBreakdown
    food: FoodBreakdown
    lifestyle: LifestyleBreakdown


class ComparisonDict(TypedDict):
    """How the user's footprint compares to global/US averages."""

    vs_global_avg_pct: float
    vs_us_avg_pct: float
    global_avg_kg: float
    us_avg_kg: float


class CarbonResult(TypedDict):
    """Complete result returned by :func:`calculator.calculate_carbon`."""

    total_kg_per_year: float
    total_tonnes: float
    breakdown: BreakdownDict
    comparisons: ComparisonDict
    eco_score: int
    trees_to_offset: float
    session_id: str
    timestamp: str


class ActionItem(TypedDict):
    """A single recommended action in an AI insight plan."""

    action: str
    impact_kg: int
    difficulty: str
    timeframe: str


class InsightsResult(TypedDict):
    """Structured result returned by the AI insights generator."""

    summary: str
    top_actions: list[ActionItem]
    biggest_win: str
    quick_win: str
    yearly_goal_kg: int
    motivational_message: str


# ─── Pydantic Request Models ─────────────────────────────────────────────────


class CarbonInput(BaseModel):
    """User-submitted lifestyle data for carbon footprint calculation."""

    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))

    # Transport
    car_km_per_week: float = Field(default=0, ge=0, le=10_000)
    car_type: str = Field(
        default="petrol", pattern=r"^(petrol|diesel|electric|hybrid)$"
    )
    flights_per_year: int = Field(default=0, ge=0, le=365)
    flight_type: str = Field(
        default="short", pattern=r"^(short|medium|long)$"
    )
    public_transport_km: float = Field(default=0, ge=0, le=10_000)

    # Energy
    electricity_kwh: float = Field(default=0, ge=0, le=100_000)
    natural_gas_cubic_m: float = Field(default=0, ge=0, le=10_000)
    renewable_energy_pct: float = Field(default=0, ge=0, le=100)

    # Food
    diet_type: str = Field(
        default="omnivore",
        pattern=r"^(vegan|vegetarian|pescatarian|omnivore|heavy_meat)$",
    )
    food_waste_kg: float = Field(default=0, ge=0, le=500)

    # Lifestyle
    new_clothes_per_year: int = Field(default=0, ge=0, le=1_000)
    online_orders_per_month: int = Field(default=0, ge=0, le=500)
    streaming_hours_per_day: float = Field(default=0, ge=0, le=24)


class ChatMessage(BaseModel):
    """Payload for the ``/api/chat`` endpoint."""

    session_id: str = Field(..., min_length=1, max_length=128)
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)
    carbon_context: Optional[Dict] = None


class ActivityLog(BaseModel):
    """Payload for the ``/api/log-activity`` endpoint."""

    session_id: str = Field(..., min_length=1, max_length=128)
    activity_type: str = Field(
        ..., pattern=r"^(transport|energy|food|lifestyle)$"
    )
    description: str = Field(..., min_length=1, max_length=256)
    co2_saved_kg: float = Field(..., gt=0, le=10_000)
    date: Optional[str] = None


class GoalInput(BaseModel):
    """Payload for the ``/api/set-goal`` endpoint."""

    session_id: str = Field(..., min_length=1, max_length=128)
    target_reduction_pct: float = Field(..., ge=1, le=100)
    timeline_months: int = Field(..., ge=1, le=60)
