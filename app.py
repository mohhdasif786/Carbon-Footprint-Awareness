"""
Carbon Footprint Awareness Platform — EcoTrack
================================================
Run with: python app.py
Serves frontend + all APIs on http://localhost:8000

Architecture:
  FastAPI (ASGI) → serves static SPA + REST API
  Gemini 1.5 Flash → AI chat + personalized insights
  Google Maps Platform → EV stations, green routes
  Rate limiting (slowapi) → prevent API abuse
  Async file I/O with lock → thread-safe persistence
  TTL cache → efficient stats endpoint
"""

import os
import json
import uuid
import logging
import asyncio
import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from dotenv import load_dotenv
import google.generativeai as genai
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# ─── Environment ──────────────────────────────────────────────────────────────

load_dotenv()

GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY", "")
MAPS_API_KEY    = os.environ.get("MAPS_API_KEY", "")
PORT            = int(os.environ.get("PORT", "8000"))
DATA_FILE       = Path(os.environ.get("DATA_FILE", "data/user_data.json"))
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000"
    ).split(",")
    if origin.strip()
]

# Tuneable constants
MAX_MESSAGE_LENGTH: int   = 1000     # prevent Gemini cost abuse
STATS_CACHE_TTL_SEC: int  = 30       # platform stats refresh window
TREE_ABSORPTION_KG: float = 21.0     # kg CO₂ absorbed per tree per year
GLOBAL_AVG_KG: float      = 4_000.0  # IPCC global average
US_AVG_KG: float          = 14_000.0 # IEA US average

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ecotrack")

# ─── Gemini ───────────────────────────────────────────────────────────────────

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    _gemini_model = genai.GenerativeModel("gemini-1.5-flash")
    logger.info("✅ Gemini 1.5 Flash configured")
else:
    _gemini_model = None
    logger.warning("⚠️  GEMINI_API_KEY not set — AI features will use fallbacks")

# ─── Rate Limiter ─────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["300/hour"])

# ─── FastAPI ──────────────────────────────────────────────────────────────────

app = FastAPI(
    title="EcoTrack — Carbon Footprint Awareness Platform",
    description=(
        "AI-powered platform to help individuals understand, track, "
        "and reduce their carbon footprint through smart insights, "
        "interactive maps, and community engagement."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False if "*" in ALLOWED_ORIGINS else True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Inject HTTP security headers on every response."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"]         = "DENY"
    response.headers["X-XSS-Protection"]        = "1; mode=block"
    response.headers["Referrer-Policy"]          = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://www.googletagmanager.com https://www.google.com https://www.gstatic.com https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://unpkg.com; "
        "img-src 'self' data: https://*.tile.openstreetmap.org https://*.basemaps.cartocdn.com https://www.googletagmanager.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self' https://overpass-api.de https://nominatim.openstreetmap.org; "
        "frame-src 'self' https://www.googletagmanager.com;"
    )
    return response


# Mount static files
static_dir = Path("static")
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
DATA_FILE.parent.mkdir(exist_ok=True)

# ─── Thread-Safe Storage ──────────────────────────────────────────────────────

_write_lock = asyncio.Lock()


def load_data() -> Dict:
    """Load persisted data from JSON, returning a safe default on failure."""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.error("Failed to load data: %s", exc)
    return {"users": {}, "activities": [], "leaderboard": [], "activity_index": {}}


async def save_data_async(data: Dict) -> None:
    """Write data to disk under an async lock (prevents concurrent corruption)."""
    async with _write_lock:
        try:
            with open(DATA_FILE, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=str)
        except OSError as exc:
            logger.error("Failed to save data: %s", exc)


DB: Dict = load_data()
if "activity_index" not in DB:
    DB["activity_index"] = {}

# In-memory chat sessions (keyed by session_id)
chat_sessions: Dict[str, Any] = {}

# ─── Stats Cache (TTL) ────────────────────────────────────────────────────────

_stats_cache: Dict        = {}
_stats_cache_at: float    = 0.0


def get_cached_stats() -> Optional[Dict]:
    age = datetime.datetime.now(datetime.timezone.utc).timestamp() - _stats_cache_at
    return _stats_cache.copy() if (_stats_cache and age < STATS_CACHE_TTL_SEC) else None


def set_stats_cache(stats: Dict) -> None:
    global _stats_cache, _stats_cache_at
    _stats_cache    = stats.copy()
    _stats_cache_at = datetime.datetime.now(datetime.timezone.utc).timestamp()


def invalidate_stats_cache() -> None:
    global _stats_cache_at
    _stats_cache_at = 0.0

# ─── Emission Factors  (Sources: IPCC AR6 · IEA 2023 · Our World in Data) ────

CAR_FACTORS: Dict[str, float] = {
    "petrol":   0.21,   # kg CO₂e / km
    "diesel":   0.17,
    "hybrid":   0.11,
    "electric": 0.05,
}
FLIGHT_FACTORS: Dict[str, float]    = {"short": 0.255, "medium": 0.195, "long": 0.150}
FLIGHT_DISTANCES: Dict[str, float]  = {"short": 500.0, "medium": 3_000.0, "long": 9_000.0}
DIET_FACTORS: Dict[str, float]      = {
    "vegan":        1.5,    # kg CO₂e / day
    "vegetarian":   2.5,
    "pescatarian":  3.0,
    "omnivore":     5.0,
    "heavy_meat":   7.5,
}
PT_FACTOR:        float = 0.089   # public transport, kg CO₂e / km
ELEC_FACTOR:      float = 0.233   # kg CO₂e / kWh (global grid)
GAS_FACTOR:       float = 2.04    # kg CO₂e / m³
WASTE_FACTOR:     float = 2.5     # kg CO₂e / kg food waste
CLOTHING_FACTOR:  float = 33.4    # kg CO₂e / new garment
ORDER_FACTOR:     float = 0.5     # kg CO₂e / online delivery
STREAM_FACTOR:    float = 0.036   # kg CO₂e / streaming-hour

# ─── Pydantic Models ──────────────────────────────────────────────────────────

class CarbonInput(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Transport
    car_km_per_week:       float = Field(default=0, ge=0, le=10_000)
    car_type:              str   = Field(default="petrol",   pattern=r"^(petrol|diesel|electric|hybrid)$")
    flights_per_year:      int   = Field(default=0, ge=0, le=365)
    flight_type:           str   = Field(default="short",    pattern=r"^(short|medium|long)$")
    public_transport_km:   float = Field(default=0, ge=0, le=10_000)
    # Energy
    electricity_kwh:       float = Field(default=0, ge=0, le=100_000)
    natural_gas_cubic_m:   float = Field(default=0, ge=0, le=10_000)
    renewable_energy_pct:  float = Field(default=0, ge=0, le=100)
    # Food
    diet_type:             str   = Field(default="omnivore", pattern=r"^(vegan|vegetarian|pescatarian|omnivore|heavy_meat)$")
    food_waste_kg:         float = Field(default=0, ge=0, le=500)
    # Lifestyle
    new_clothes_per_year:      int   = Field(default=0, ge=0, le=1_000)
    online_orders_per_month:   int   = Field(default=0, ge=0, le=500)
    streaming_hours_per_day:   float = Field(default=0, ge=0, le=24)


class ChatMessage(BaseModel):
    session_id:      str           = Field(..., min_length=1, max_length=128)
    message:         str           = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)
    carbon_context:  Optional[Dict] = None


class ActivityLog(BaseModel):
    session_id:    str   = Field(..., min_length=1, max_length=128)
    activity_type: str   = Field(..., pattern=r"^(transport|energy|food|lifestyle)$")
    description:   str   = Field(..., min_length=1, max_length=256)
    co2_saved_kg:  float = Field(..., gt=0, le=10_000)
    date:          Optional[str] = None


class GoalInput(BaseModel):
    session_id:           str   = Field(..., min_length=1, max_length=128)
    target_reduction_pct: float = Field(..., ge=1, le=100)
    timeline_months:      int   = Field(..., ge=1, le=60)

# ─── Carbon Calculator ────────────────────────────────────────────────────────

def calculate_carbon(data: CarbonInput) -> Dict:
    """
    Pure function: compute annual CO₂e footprint across four lifestyle categories.

    Returns a typed dict with:
      - total_kg_per_year, total_tonnes
      - breakdown (transport / energy / food / lifestyle)
      - comparisons (vs global & US averages)
      - eco_score (0–100, higher = greener)
      - trees_to_offset, session_id, timestamp
    """
    # ── Transport ──────────────────────────────────────────────────────────────
    car_annual     = data.car_km_per_week * CAR_FACTORS.get(data.car_type, 0.21) * 52
    flight_annual  = (
        data.flights_per_year
        * FLIGHT_DISTANCES.get(data.flight_type, 500.0)
        * 2                                                 # round-trip
        * FLIGHT_FACTORS.get(data.flight_type, 0.255)
    )
    pt_annual      = data.public_transport_km * 52 * PT_FACTOR
    transport_total = car_annual + flight_annual + pt_annual

    # ── Energy ─────────────────────────────────────────────────────────────────
    renewable_mult     = 1.0 - (data.renewable_energy_pct / 100.0)
    electricity_annual = data.electricity_kwh * 12 * ELEC_FACTOR * renewable_mult
    gas_annual         = data.natural_gas_cubic_m * 12 * GAS_FACTOR
    energy_total       = electricity_annual + gas_annual

    # ── Food ───────────────────────────────────────────────────────────────────
    diet_annual  = DIET_FACTORS.get(data.diet_type, 5.0) * 365
    waste_annual = data.food_waste_kg * 12 * WASTE_FACTOR
    food_total   = diet_annual + waste_annual

    # ── Lifestyle ──────────────────────────────────────────────────────────────
    clothing_annual  = data.new_clothes_per_year * CLOTHING_FACTOR
    shopping_annual  = data.online_orders_per_month * 12 * ORDER_FACTOR
    streaming_annual = data.streaming_hours_per_day * 365 * STREAM_FACTOR
    lifestyle_total  = clothing_annual + shopping_annual + streaming_annual

    total = transport_total + energy_total + food_total + lifestyle_total

    # ── Score & Comparisons ────────────────────────────────────────────────────
    eco_score  = max(0, min(100, int(100 - (total / US_AVG_KG) * 50)))
    vs_global  = round((total / GLOBAL_AVG_KG - 1.0) * 100, 1)
    vs_us      = round((total / US_AVG_KG     - 1.0) * 100, 1)

    return {
        "total_kg_per_year": round(total, 1),
        "total_tonnes":      round(total / 1_000, 2),
        "breakdown": {
            "transport": {
                "total":            round(transport_total, 1),
                "car":              round(car_annual, 1),
                "flights":          round(flight_annual, 1),
                "public_transport": round(pt_annual, 1),
            },
            "energy": {
                "total":       round(energy_total, 1),
                "electricity": round(electricity_annual, 1),
                "gas":         round(gas_annual, 1),
            },
            "food": {
                "total": round(food_total, 1),
                "diet":  round(diet_annual, 1),
                "waste": round(waste_annual, 1),
            },
            "lifestyle": {
                "total":     round(lifestyle_total, 1),
                "clothing":  round(clothing_annual, 1),
                "shopping":  round(shopping_annual, 1),
                "streaming": round(streaming_annual, 1),
            },
        },
        "comparisons": {
            "vs_global_avg_pct": vs_global,
            "vs_us_avg_pct":     vs_us,
            "global_avg_kg":     GLOBAL_AVG_KG,
            "us_avg_kg":         US_AVG_KG,
        },
        "eco_score":       eco_score,
        "trees_to_offset": round(total / TREE_ABSORPTION_KG, 0),
        "session_id":      data.session_id,
        "timestamp":       datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

# ─── Gemini AI ────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """You are EcoGuide, an expert AI sustainability assistant for the EcoTrack Carbon Footprint Awareness Platform.
Help users understand their carbon footprint and motivate them to take meaningful action.

Principles:
- Be encouraging, never preachy
- Give specific, quantified advice ("switching to an EV saves ~1,200 kg CO₂/year")
- Tailor advice to the user's highest-emission category when carbon data is provided
- Keep responses to 2–4 concise paragraphs
- Use emojis sparingly but effectively
- Always close with ONE concrete action the user can take TODAY
Use metric units (kg, km) with imperial equivalents where helpful."""

def generate_offline_insights(carbon_data: Dict) -> Dict:
    """
    Generate dynamic, personalized insights offline when the Gemini API is rate-limited or disabled.
    """
    total_kg = carbon_data.get("total_kg_per_year", 0.0)
    bd = carbon_data.get("breakdown", {})
    
    transport_total = bd.get("transport", {}).get("total", 0.0) if bd else 0.0
    energy_total = bd.get("energy", {}).get("total", 0.0) if bd else 0.0
    food_total = bd.get("food", {}).get("total", 0.0) if bd else 0.0
    lifestyle_total = bd.get("lifestyle", {}).get("total", 0.0) if bd else 0.0

    cats = {
        "transport": transport_total,
        "energy": energy_total,
        "food": food_total,
        "lifestyle": lifestyle_total
    }
    highest_cat = max(cats, key=cats.get) if bd else "energy"
    highest_val = cats[highest_cat] if bd else 0.0

    summary = (
        f"Your annual carbon footprint is {total_kg:,.1f} kg CO₂e. "
        f"Your largest source of emissions is {highest_cat} ({highest_val:,.1f} kg CO₂e), which should be your primary target for reduction."
    )

    top_actions = []
    
    # Custom actions based on categories
    if transport_total > 1500:
        top_actions.append({
            "action": "Commute via public transit 3 days/week",
            "impact_kg": int(transport_total * 0.25),
            "difficulty": "medium",
            "timeframe": "weekly"
        })
    elif transport_total > 0:
        top_actions.append({
            "action": "Walk or cycle for trips under 3 km",
            "impact_kg": 120,
            "difficulty": "easy",
            "timeframe": "immediate"
        })

    if energy_total > 1500:
        top_actions.append({
            "action": "Switch to a 100% green/renewable energy provider",
            "impact_kg": int(energy_total * 0.45),
            "difficulty": "easy",
            "timeframe": "weekly"
        })
    elif energy_total > 0:
        top_actions.append({
            "action": "Unplug standby electronics & install LED bulbs",
            "impact_kg": 90,
            "difficulty": "easy",
            "timeframe": "immediate"
        })

    if food_total > 1800:
        top_actions.append({
            "action": "Transition to a vegetarian diet style",
            "impact_kg": 800,
            "difficulty": "medium",
            "timeframe": "monthly"
        })
    elif food_total > 0:
        top_actions.append({
            "action": "Reduce meat consumption by half & compost waste",
            "impact_kg": 400,
            "difficulty": "easy",
            "timeframe": "weekly"
        })

    if lifestyle_total > 1000:
        top_actions.append({
            "action": "Reduce new clothing purchases by 50%",
            "impact_kg": int(lifestyle_total * 0.3),
            "difficulty": "easy",
            "timeframe": "monthly"
        })
    elif lifestyle_total > 0:
        top_actions.append({
            "action": "Stream videos in Standard Definition (SD)",
            "impact_kg": 50,
            "difficulty": "easy",
            "timeframe": "immediate"
        })

    # Sort by impact, select top 3
    top_actions = sorted(top_actions, key=lambda x: x["impact_kg"], reverse=True)[:3]
    if not top_actions:
        top_actions = [
            {"action": "Switch to LED light bulbs", "impact_kg": 150, "difficulty": "easy", "timeframe": "immediate"},
            {"action": "Reduce meat intake by half", "impact_kg": 400, "difficulty": "medium", "timeframe": "weekly"},
            {"action": "Carpool or use transit twice a week", "impact_kg": 300, "difficulty": "medium", "timeframe": "weekly"}
        ]

    # Map categories to wins
    win_map = {
        "transport": ("Choosing active/public transit instead of a personal vehicle.", "Inflate tires regularly to improve car mileage."),
        "energy": ("Installing solar panels or switching to a renewable tariff.", "Turn down the heating thermostat by 1°C."),
        "food": ("Adopting a plant-based diet (vegan or vegetarian).", "Plan your grocery shopping to reduce food waste."),
        "lifestyle": ("Buying pre-owned items and avoiding fast fashion.", "Unsubscribe from unnecessary cloud storage and stream in SD."),
    }
    biggest_win, quick_win = win_map.get(highest_cat, ("Reducing overall energy demand", "Turn off lights when leaving empty rooms"))

    yearly_goal_kg = max(500, int(total_kg * 0.15))

    return {
        "summary": summary,
        "top_actions": top_actions,
        "biggest_win": biggest_win,
        "quick_win": quick_win,
        "yearly_goal_kg": yearly_goal_kg,
        "motivational_message": f"Every action counts! Focus on your {highest_cat} emissions for the fastest progress towards your target. 🌟",
    }


def get_offline_response(message: str, context: Optional[Dict] = None, is_fallback: bool = False) -> str:
    """
    Generate structured, context-aware answers offline when the Gemini API is unavailable.
    """
    msg = message.lower().strip()
    
    notice = ""
    if is_fallback:
        notice = "**Note:** Google Gemini API rate limit exceeded. EcoGuide is running in **Eco-Fallback Mode** to answer your questions. ⚡🌱\n\n"
    elif not GEMINI_API_KEY:
        notice = "**Eco-Mode Active:** Running locally without Gemini API key. 🌱\n\n"

    # Extract context stats if available
    total_kg = context.get("total_kg_per_year", 0.0) if context else 0.0
    eco_score = context.get("eco_score", 0) if context else 0
    trees = context.get("trees_to_offset", 0) if context else 0
    breakdown = context.get("breakdown", {}) if context else {}
    
    transport_total = breakdown.get("transport", {}).get("total", 0.0) if breakdown else 0.0
    energy_total = breakdown.get("energy", {}).get("total", 0.0) if breakdown else 0.0
    food_total = breakdown.get("food", {}).get("total", 0.0) if breakdown else 0.0
    lifestyle_total = breakdown.get("lifestyle", {}).get("total", 0.0) if breakdown else 0.0

    # Determine highest category
    highest_cat = "none"
    highest_val = 0.0
    if breakdown:
        cats = {
            "transport": transport_total,
            "energy": energy_total,
            "food": food_total,
            "lifestyle": lifestyle_total
        }
        highest_cat = max(cats, key=cats.get)
        highest_val = cats[highest_cat]

    # 1. Greetings
    if any(k in msg for k in ["hi", "hello", "hey", "greetings", "start"]):
        if context:
            return (
                f"{notice}Hello! I am EcoGuide, your AI sustainability assistant.\n\n"
                f"I see you have calculated your carbon footprint: your Eco Score is **{eco_score}/100** and your annual emissions are **{total_kg:,} kg CO₂e**. "
                f"Your highest emission category is **{highest_cat.title()}** ({highest_val:,} kg).\n\n"
                f"How can I help you reduce your footprint today? Ask me about transport, energy, food, or lifestyle! 💬"
            )
        else:
            return (
                f"{notice}Hello! I am EcoGuide, your AI sustainability assistant.\n\n"
                f"To get personalized insights, please go to the **Carbon Calculator** section first and calculate your footprint!\n\n"
                f"Otherwise, feel free to ask me general questions about carbon footprint reduction. How can I help you? 💬"
            )

    # 2. Transport
    if any(k in msg for k in ["car", "drive", "flight", "plane", "transport", "travel", "vehicle", "fuel", "petrol", "diesel", "electric", "ev", "bus", "train", "commute", "subway"]):
        resp = (
            f"{notice}### 🚗 Transport Emission Reduction Tips\n\n"
            "Transport is often the largest source of personal greenhouse gas emissions. Here is how you can reduce it:\n\n"
            "• **Switch to Public Transit:** Trains and buses emit significantly less per passenger-km (approx. 0.089 kg CO₂/km) compared to conventional petrol cars (0.21 kg/km).\n"
            "• **Drive Efficiently or Switch to EV:** Electric vehicles have an average factor of just 0.05 kg CO₂/km. If driving a petrol/diesel car, combine trips and maintain tire pressure to reduce consumption.\n"
            "• **Limit Flying:** Short-haul flights emit 0.255 kg CO₂/km per passenger. For distances under 500 km, prefer high-speed rail when available.\n\n"
        )
        if transport_total > 0:
            resp += f"Your current annual transport footprint is **{transport_total:,} kg CO₂e**. Try targeting a 10% reduction this month by walking or cycling for short trips! 🚲"
        else:
            resp += "Calculate your footprint to see your specific transport emissions and get tailored targets! 📈"
        return resp

    # 3. Energy
    if any(k in msg for k in ["electricity", "gas", "energy", "solar", "power", "heating", "ac", "utilities", "utility"]):
        resp = (
            f"{notice}### 💡 Home Energy Efficiency Tips\n\n"
            "Home energy usage from electricity and heating plays a massive role in global emissions. Try these key improvements:\n\n"
            "• **Increase Renewable Energy:** Swapping to green energy tariffs or installing solar panels directly cuts down your electricity footprint.\n"
            "• **Smart Thermostats & Temperature Adjustments:** Lowering your thermostat by 1°C in winter can cut heating bills and emissions by up to 10%.\n"
            "• **Upgrade to LEDs & Efficient Appliances:** LED bulbs use up to 85% less energy than incandescent lightbulbs and last 25 times longer.\n\n"
        )
        if energy_total > 0:
            resp += f"Your current annual home energy footprint is **{energy_total:,} kg CO₂e**. Switch off standby appliances and turn off lights to start saving today! 🔌"
        else:
            resp += "Use the calculator to log your utility bills and track your home energy impact! 📊"
        return resp

    # 4. Food / Diet
    if any(k in msg for k in ["food", "diet", "eat", "meat", "beef", "pork", "chicken", "vegan", "vegetarian", "pescatarian", "waste", "compost"]):
        resp = (
            f"{notice}### 🍎 Diet & Food Waste Tips\n\n"
            "What we eat and how much we throw away has a substantial carbon cost:\n\n"
            "• **Transition to Plant-Based Eating:** Heavy meat diets average 7.5 kg CO₂/day, while vegetarian (2.5 kg/day) and vegan (1.5 kg/day) diets are significantly lower.\n"
            "• **Stop Food Waste:** Food waste in landfills produces methane, a potent greenhouse gas. Plan meals, freeze leftovers, and compost waste to avoid emissions (each kg of food waste adds 2.5 kg CO₂e).\n"
            "• **Eat Seasonal & Local:** Reduce transportation emissions by choosing local produce that is in season.\n\n"
        )
        if food_total > 0:
            resp += f"Your current annual food footprint is **{food_total:,} kg CO₂e**. Try a 'Meatless Monday' challenge to lower your daily emissions! 🥗"
        else:
            resp += "Calculate your footprint to see how your diet type and food waste contribute to your total score! 📉"
        return resp

    # 5. Lifestyle / Shopping / Streaming
    if any(k in msg for k in ["lifestyle", "clothing", "clothes", "shop", "streaming", "stream", "order", "purchase", "online"]):
        resp = (
            f"{notice}### 🛍️ Sustainable Lifestyle & Consumer Habits\n\n"
            "Every item we buy and every digital service we consume carries an embodied carbon footprint:\n\n"
            "• **Mindful Fashion:** The fashion industry is responsible for significant global emissions. A new clothing garment averages 33.4 kg CO₂e. Buy secondhand, repair old clothes, or choose high-quality items.\n"
            "• **Consolidate Online Shipments:** Online deliveries average 0.5 kg CO₂ per order. Try to consolidate orders and avoid rush shipping.\n"
            "• **Digital Carbon Footprint:** Video streaming averages 0.036 kg CO₂ per hour. Streaming in Standard Definition (SD) or turning off auto-play can help reduce data center energy usage.\n\n"
        )
        if lifestyle_total > 0:
            resp += f"Your current annual lifestyle footprint is **{lifestyle_total:,} kg CO₂e**. Small choices like renting or borrowing items add up over time! ♻️"
        else:
            resp += "Try the carbon calculator to estimate your daily habits, streaming hours, and clothing purchases! 👗"
        return resp

    # 6. Score
    if any(k in msg for k in ["score", "eco_score", "eco score", "performance", "rating"]):
        if context:
            return (
                f"{notice}### 🏆 Your Eco Score Analysis\n\n"
                f"Your Eco Score is **{eco_score}/100**.\n\n"
                f"• **Score Interpretation:** A score closer to 100 means you are closer to or below the global sustainable carbon budget. A lower score indicates higher emissions.\n"
                f"• **Current Footprint:** Your annual footprint is **{total_kg:,} kg CO₂e**.\n"
                f"• **Category Breakdown:**\n"
                f"  - Transport: {transport_total:,} kg\n"
                f"  - Home Energy: {energy_total:,} kg\n"
                f"  - Food & Diet: {food_total:,} kg\n"
                f"  - Lifestyle: {lifestyle_total:,} kg\n\n"
                f"To improve your score, focus on reducing your highest category: **{highest_cat}**! 🚀"
            )
        else:
            return (
                f"{notice}You haven't calculated your footprint yet! Head over to the **Carbon Calculator** section and fill in your details to get your Eco Score and a personalized rating. 📈"
            )

    # 7. Goal / General tips
    if any(k in msg for k in ["reduce", "offset", "tree", "goal", "tips", "action", "improve", "help"]):
        resp = (
            f"{notice}### 🌱 Top Actions to Reduce Your Footprint Today\n\n"
            "Here are the most impactful actions you can take to lower your emissions:\n\n"
            "1. **Switch to 100% Renewable Tariff:** Instantly removes electricity emissions (easy, high impact).\n"
            "2. **Reduce Beef/Lamb Intake:** These meats have the highest carbon intensity (medium difficulty, high impact).\n"
            "3. **Carpool, Walk, or Cycle:** Swap short car trips for active transit to save significant fuel emissions (easy, medium impact).\n"
            "4. **Offset Remaining Emissions:** It takes about **{trees if trees else 50:.0f} trees** to offset {total_kg if total_kg else 1000:,.0f} kg CO₂/year. Consider supporting verified forestry or conservation projects.\n\n"
        )
        if highest_cat != "none":
            resp += f"Since your highest emission category is **{highest_cat}** ({highest_val:,} kg), prioritizing reductions in this area will yield the fastest results! 🎯"
        return resp

    # 8. Default fallback response
    if context:
        return (
            f"{notice}Thank you for your question. I want to help you achieve your sustainability goals!\n\n"
            f"Your current carbon footprint is **{total_kg:,} kg CO₂e/year** with an Eco Score of **{eco_score}/100**.\n\n"
            f"Based on your profile, here is one concrete action you can take today: "
            f"**Try to reduce energy waste or substitute one drive with public transit.**\n\n"
            f"Ask me about any specific category: **Transport**, **Energy**, **Food & Diet**, or **Lifestyle** for detailed tips! 🌿"
        )
    else:
        return (
            f"{notice}Thank you for reaching out! I am EcoGuide, your AI assistant.\n\n"
            "If you want to receive personalized advice, please complete your carbon calculation first. "
            "In the meantime, feel free to ask me about topics like EV benefits, diet footprints, green energy tariffs, or fashion waste! 🌍"
        )


async def get_gemini_response(session_id: str, message: str, context: Optional[Dict] = None) -> str:
    """
    Return a context-aware Gemini AI response for the chat interface.
    Maintains per-session conversation history. Falls back gracefully.
    """
    if not _gemini_model:
        return get_offline_response(message, context)

    try:
        if session_id not in chat_sessions:
            chat_sessions[session_id] = _gemini_model.start_chat(history=[])

        chat = chat_sessions[session_id]

        if context:
            bd = context.get("breakdown", {})
            biggest = (
                max(bd.items(), key=lambda x: x[1].get("total", 0) if isinstance(x[1], dict) else 0)[0]
                if bd else "unknown"
            )
            enriched = (
                f"[User Carbon Profile]\n"
                f"Annual footprint : {context.get('total_kg_per_year')} kg CO₂e\n"
                f"Eco Score        : {context.get('eco_score')}/100\n"
                f"Biggest source   : {biggest}\n"
                f"Trees to offset  : {context.get('trees_to_offset')}\n\n"
                f"[Question]\n{message}"
            )
        else:
            enriched = message

        full_prompt = f"{_SYSTEM_PROMPT}\n\n{enriched}"
        loop     = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: chat.send_message(full_prompt))
        logger.info("Gemini chat: session=%s...", session_id[:8])
        return response.text

    except Exception as exc:
        logger.error("Gemini chat error: %s", exc)
        return get_offline_response(message, context, is_fallback=True)


async def get_ai_insights(carbon_data: Dict) -> Dict:
    """
    Call Gemini to generate a structured JSON action plan.
    Falls back to generate_offline_insights on any error.
    """
    if not _gemini_model:
        return generate_offline_insights(carbon_data)

    try:
        bd     = carbon_data.get("breakdown", {})
        prompt = (
            "Analyze this carbon footprint and respond with ONLY a valid JSON object "
            "(no markdown, no code fences).\n\n"
            f"Total  : {carbon_data.get('total_kg_per_year')} kg CO₂e/yr\n"
            f"Transport : {bd.get('transport', {}).get('total', 0)} kg\n"
            f"Energy    : {bd.get('energy', {}).get('total', 0)} kg\n"
            f"Food      : {bd.get('food', {}).get('total', 0)} kg\n"
            f"Lifestyle : {bd.get('lifestyle', {}).get('total', 0)} kg\n"
            f"Eco Score : {carbon_data.get('eco_score')}/100\n\n"
            'Required JSON structure:\n'
            '{"summary":"<2 sentences>","top_actions":[{"action":"<title>",'
            '"impact_kg":<int>,"difficulty":"easy|medium|hard","timeframe":"immediate|weekly|monthly"},'
            '{"action":"<title>","impact_kg":<int>,"difficulty":"easy|medium|hard","timeframe":"immediate|weekly|monthly"},'
            '{"action":"<title>","impact_kg":<int>,"difficulty":"easy|medium|hard","timeframe":"immediate|weekly|monthly"}],'
            '"biggest_win":"<string>","quick_win":"<string>","yearly_goal_kg":<int>,'
            '"motivational_message":"<string>"}'
        )
        loop     = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, lambda: _gemini_model.generate_content(prompt))
        text     = response.text.strip()

        # Strip accidental markdown fences
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip().rstrip("`")

        logger.info("Gemini insights generated successfully")
        return json.loads(text)

    except Exception as exc:
        logger.error("Gemini insights error: %s", exc)
        return generate_offline_insights(carbon_data)

# ─── API Routes ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_index():
    """Serve the main SPA (index.html)."""
    idx = Path("static/index.html")
    if idx.exists():
        return HTMLResponse(content=idx.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Error: static/index.html not found.</h1>", status_code=500)


@app.post("/api/calculate", summary="Calculate annual carbon footprint")
async def calculate_footprint(data: CarbonInput):
    """
    Compute the user's annual CO₂e footprint from lifestyle inputs.
    Persists the calculation and returns a full breakdown with eco score.
    """
    result = calculate_carbon(data)

    # Persist (non-blocking, fire-and-forget)
    entry_idx = len(DB["activities"])
    DB["activities"].append({"session_id": data.session_id, "type": "calculation",
                              "data": result, "timestamp": result["timestamp"]})
    DB["activity_index"].setdefault(data.session_id, []).append(entry_idx)
    asyncio.create_task(save_data_async(DB))

    logger.info("Footprint calculated: session=%s... %.1f kg/yr score=%d",
                data.session_id[:8], result["total_kg_per_year"], result["eco_score"])
    return JSONResponse(content=result)


@app.post("/api/chat", summary="Chat with EcoGuide AI assistant")
@limiter.limit("15/minute")
async def chat_with_ai(request: Request, msg: ChatMessage):
    """
    Send a message to EcoGuide (Gemini 1.5 Flash).
    Rate-limited to 15 requests/minute per IP to prevent abuse.
    """
    reply = await get_gemini_response(msg.session_id, msg.message, msg.carbon_context)
    return {"response": reply, "session_id": msg.session_id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat()}


@app.post("/api/insights", summary="Generate AI-powered personalized insights")
@limiter.limit("10/minute")
async def get_insights(request: Request, data: dict):
    """
    Generate a structured action plan from carbon calculation results.
    Rate-limited to 10 requests/minute per IP.
    """
    return await get_ai_insights(data)


@app.post("/api/log-activity", summary="Log a completed green action")
async def log_activity(activity: ActivityLog):
    """Record an eco-friendly action and update the community leaderboard."""
    if not activity.date:
        activity.date = datetime.datetime.now(datetime.timezone.utc).isoformat()

    entry_idx = len(DB["activities"])
    DB["activities"].append(activity.model_dump())
    DB["activity_index"].setdefault(activity.session_id, []).append(entry_idx)

    existing = next((x for x in DB["leaderboard"] if x["session_id"] == activity.session_id), None)
    if existing:
        existing["co2_saved_kg"] = round(existing["co2_saved_kg"] + activity.co2_saved_kg, 2)
        existing["actions"]      = existing.get("actions", 0) + 1
    else:
        DB["leaderboard"].append({
            "session_id":   activity.session_id,
            "co2_saved_kg": round(activity.co2_saved_kg, 2),
            "actions":      1,
            "joined":       activity.date,
        })

    invalidate_stats_cache()
    asyncio.create_task(save_data_async(DB))

    logger.info("Activity logged: '%s' (%.2f kg saved)", activity.description, activity.co2_saved_kg)
    return {"success": True, "message": f"Logged: {activity.description}"}


@app.post("/api/set-goal", summary="Save a carbon reduction goal")
async def set_goal(goal: GoalInput):
    """Persist the user's reduction target and timeline."""
    DB["users"].setdefault(goal.session_id, {})["goal"] = {
        "target_reduction_pct": goal.target_reduction_pct,
        "timeline_months":      goal.timeline_months,
        "created_at":           datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    asyncio.create_task(save_data_async(DB))
    logger.info("Goal set: session=%s... %.0f%% over %d months",
                goal.session_id[:8], goal.target_reduction_pct, goal.timeline_months)
    return {"success": True, "goal": DB["users"][goal.session_id]["goal"]}


@app.get("/api/history/{session_id}", summary="Get activity history for a session")
async def get_history(session_id: str):
    """
    O(k) lookup using the activity index (k = activities for this session).
    Avoids scanning the entire activities list.
    """
    indices    = DB["activity_index"].get(session_id, [])
    activities = [DB["activities"][i] for i in indices if i < len(DB["activities"])]
    return {"activities": activities, "count": len(activities)}


@app.get("/api/leaderboard", summary="Community CO₂-saving leaderboard")
async def get_leaderboard():
    """Return top-20 users ranked by total CO₂ saved (session IDs anonymised)."""
    sorted_lb = sorted(DB["leaderboard"], key=lambda x: x.get("co2_saved_kg", 0), reverse=True)
    medals    = ["🥇", "🥈", "🥉"]
    return {
        "leaderboard": [
            {
                "rank":         i + 1,
                "medal":        medals[i] if i < 3 else "",
                "user":         f"EcoHero #{e['session_id'][:6].upper()}",
                "co2_saved_kg": e["co2_saved_kg"],
                "actions":      e.get("actions", 0),
            }
            for i, e in enumerate(sorted_lb[:20])
        ],
        "total_users": len(DB["leaderboard"]),
    }


@app.get("/api/stats", summary="Platform-wide statistics (TTL-cached)")
async def get_platform_stats():
    """Aggregate stats with a 30-second TTL cache to reduce computation."""
    cached = get_cached_stats()
    if cached:
        return cached

    total_saved = sum(x.get("co2_saved_kg", 0) for x in DB["leaderboard"])
    calcs_done  = sum(1 for a in DB["activities"] if a.get("type") == "calculation")
    stats = {
        "total_users":            len(DB["leaderboard"]),
        "total_co2_saved_kg":     round(total_saved, 1),
        "total_trees_equivalent": round(total_saved / TREE_ABSORPTION_KG, 0),
        "calculations_done":      calcs_done,
    }
    set_stats_cache(stats)
    return stats


@app.get("/api/config", include_in_schema=False)
async def get_config():
    """Expose runtime configuration required by the frontend."""
    # Mask API key to prevent public configuration leakage (Leaflet.js is used on client side)
    masked_key = f"{MAPS_API_KEY[:4]}...{MAPS_API_KEY[-4:]}" if len(MAPS_API_KEY) > 8 else "configured" if MAPS_API_KEY else ""
    return {"maps_api_key": masked_key, "version": "1.0.0"}


@app.get("/api/health", summary="Liveness / readiness check")
async def health_check():
    """Return server health and integration status."""
    return {
        "status":            "ok",
        "timestamp":         datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "gemini_configured": _gemini_model is not None,
        "maps_configured":   bool(MAPS_API_KEY),
    }

# ─── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🌱 EcoTrack — Carbon Footprint Awareness Platform")
    logger.info("=" * 60)
    logger.info("🚀  http://localhost:%d", PORT)
    logger.info("📖  http://localhost:%d/docs", PORT)
    logger.info("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False, log_level="info")
