"""
AI service layer for EcoTrack.

Wraps Google Gemini 1.5 Flash to provide:

* **Chat** — context-aware conversation via ``get_gemini_response``
* **Insights** — structured JSON action plans via ``get_ai_insights``
* **Offline fallbacks** — deterministic, data-driven responses when the
  API is unavailable or rate-limited.

All public functions accept plain dicts so they stay decoupled from
FastAPI's request lifecycle.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

import google.generativeai as genai

from config import GEMINI_API_KEY, TREE_ABSORPTION_KG, logger

# ─── Public API ───────────────────────────────────────────────────────────────

__all__ = [
    "generate_offline_insights",
    "get_offline_response",
    "get_gemini_response",
    "get_ai_insights",
]

# ─── Gemini Model Initialisation ──────────────────────────────────────────────

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    _gemini_model = genai.GenerativeModel("gemini-1.5-flash")
    logger.info("✅ Gemini 1.5 Flash configured")
else:
    _gemini_model = None
    logger.warning("⚠️  GEMINI_API_KEY not set — AI features will use fallbacks")

# ─── System Prompt ────────────────────────────────────────────────────────────

_SYSTEM_PROMPT: str = (
    "You are EcoGuide, an expert AI sustainability assistant for the "
    "EcoTrack Carbon Footprint Awareness Platform.\n"
    "Help users understand their carbon footprint and motivate them to "
    "take meaningful action.\n\n"
    "Principles:\n"
    "- Be encouraging, never preachy\n"
    "- Give specific, quantified advice "
    '("switching to an EV saves ~1,200 kg CO₂/year")\n'
    "- Tailor advice to the user's highest-emission category when "
    "carbon data is provided\n"
    "- Keep responses to 2–4 concise paragraphs\n"
    "- Use emojis sparingly but effectively\n"
    "- Always close with ONE concrete action the user can take TODAY\n"
    "Use metric units (kg, km) with imperial equivalents where helpful."
)

# ─── Shared Helpers ───────────────────────────────────────────────────────────

# Mapping from category name to (high-emission tip, quick-win tip)
_WIN_MAP: Dict[str, Tuple[str, str]] = {
    "transport": (
        "Choosing active/public transit instead of a personal vehicle.",
        "Inflate tires regularly to improve car mileage.",
    ),
    "energy": (
        "Installing solar panels or switching to a renewable tariff.",
        "Turn down the heating thermostat by 1°C.",
    ),
    "food": (
        "Adopting a plant-based diet (vegan or vegetarian).",
        "Plan your grocery shopping to reduce food waste.",
    ),
    "lifestyle": (
        "Buying pre-owned items and avoiding fast fashion.",
        "Unsubscribe from unnecessary cloud storage and stream in SD.",
    ),
}

_DEFAULT_ACTIONS: List[Dict[str, Any]] = [
    {
        "action": "Switch to LED light bulbs",
        "impact_kg": 150,
        "difficulty": "easy",
        "timeframe": "immediate",
    },
    {
        "action": "Reduce meat intake by half",
        "impact_kg": 400,
        "difficulty": "medium",
        "timeframe": "weekly",
    },
    {
        "action": "Carpool or use transit twice a week",
        "impact_kg": 300,
        "difficulty": "medium",
        "timeframe": "weekly",
    },
]


def _extract_category_totals(
    carbon_data: Dict[str, Any],
) -> Dict[str, float]:
    """Extract per-category totals from a carbon result or context dict.

    This helper de-duplicates the breakdown-extraction logic that was
    previously repeated in ``generate_offline_insights``,
    ``get_offline_response``, and ``get_gemini_response``.

    Args:
        carbon_data: A dict containing at least a ``breakdown`` key whose
            value maps category names to sub-dicts with a ``total`` field.

    Returns:
        A dict mapping ``{"transport", "energy", "food", "lifestyle"}``
        to their respective annual kg CO₂e totals (defaulting to 0).
    """
    bd = carbon_data.get("breakdown", {})
    return {
        "transport": bd.get("transport", {}).get("total", 0.0) if bd else 0.0,
        "energy": bd.get("energy", {}).get("total", 0.0) if bd else 0.0,
        "food": bd.get("food", {}).get("total", 0.0) if bd else 0.0,
        "lifestyle": bd.get("lifestyle", {}).get("total", 0.0) if bd else 0.0,
    }


def _highest_category(cats: Dict[str, float]) -> Tuple[str, float]:
    """Return the (name, value) of the highest-emitting category.

    Args:
        cats: Mapping of category name → annual kg CO₂e.

    Returns:
        A ``(name, value)`` tuple.  Falls back to ``("energy", 0.0)``
        when all values are zero.
    """
    if not cats or all(v == 0.0 for v in cats.values()):
        return "energy", 0.0
    name = max(cats, key=cats.get)  # type: ignore[arg-type]
    return name, cats[name]


def _build_category_actions(cats: Dict[str, float]) -> List[Dict[str, Any]]:
    """Generate contextual action items based on per-category emissions.

    High-emission categories get aggressive reduction advice; lower ones
    get lighter "easy win" suggestions.

    Args:
        cats: Mapping of category name → annual kg CO₂e.

    Returns:
        A list of action dicts sorted by ``impact_kg`` descending,
        capped at 3 items.
    """
    actions: List[Dict[str, Any]] = []

    transport = cats.get("transport", 0.0)
    if transport > 1500:
        actions.append({
            "action": "Commute via public transit 3 days/week",
            "impact_kg": int(transport * 0.25),
            "difficulty": "medium",
            "timeframe": "weekly",
        })
    elif transport > 0:
        actions.append({
            "action": "Walk or cycle for trips under 3 km",
            "impact_kg": 120,
            "difficulty": "easy",
            "timeframe": "immediate",
        })

    energy = cats.get("energy", 0.0)
    if energy > 1500:
        actions.append({
            "action": "Switch to a 100% green/renewable energy provider",
            "impact_kg": int(energy * 0.45),
            "difficulty": "easy",
            "timeframe": "weekly",
        })
    elif energy > 0:
        actions.append({
            "action": "Unplug standby electronics & install LED bulbs",
            "impact_kg": 90,
            "difficulty": "easy",
            "timeframe": "immediate",
        })

    food = cats.get("food", 0.0)
    if food > 1800:
        actions.append({
            "action": "Transition to a vegetarian diet style",
            "impact_kg": 800,
            "difficulty": "medium",
            "timeframe": "monthly",
        })
    elif food > 0:
        actions.append({
            "action": "Reduce meat consumption by half & compost waste",
            "impact_kg": 400,
            "difficulty": "easy",
            "timeframe": "weekly",
        })

    lifestyle = cats.get("lifestyle", 0.0)
    if lifestyle > 1000:
        actions.append({
            "action": "Reduce new clothing purchases by 50%",
            "impact_kg": int(lifestyle * 0.3),
            "difficulty": "easy",
            "timeframe": "monthly",
        })
    elif lifestyle > 0:
        actions.append({
            "action": "Stream videos in Standard Definition (SD)",
            "impact_kg": 50,
            "difficulty": "easy",
            "timeframe": "immediate",
        })

    # Sort by impact, keep top 3
    actions.sort(key=lambda a: a["impact_kg"], reverse=True)
    return actions[:3]


# ─── Offline Insights ─────────────────────────────────────────────────────────


def generate_offline_insights(carbon_data: Dict[str, Any]) -> Dict[str, Any]:
    """Generate personalised insights without calling the Gemini API.

    Produces the same JSON structure as the Gemini-backed version so
    the frontend can render either interchangeably.

    Args:
        carbon_data: A carbon-result dictionary (or a subset of one).

    Returns:
        An insights dict with ``summary``, ``top_actions``,
        ``biggest_win``, ``quick_win``, ``yearly_goal_kg``, and
        ``motivational_message``.
    """
    total_kg = carbon_data.get("total_kg_per_year", 0.0)
    cats = _extract_category_totals(carbon_data)
    highest_cat, highest_val = _highest_category(cats)

    summary = (
        f"Your annual carbon footprint is {total_kg:,.1f} kg CO₂e. "
        f"Your largest source of emissions is {highest_cat} "
        f"({highest_val:,.1f} kg CO₂e), which should be your primary "
        f"target for reduction."
    )

    top_actions = _build_category_actions(cats) or _DEFAULT_ACTIONS
    biggest_win, quick_win = _WIN_MAP.get(
        highest_cat,
        ("Reducing overall energy demand", "Turn off lights when leaving empty rooms"),
    )
    yearly_goal_kg = max(500, int(total_kg * 0.15))

    return {
        "summary": summary,
        "top_actions": top_actions,
        "biggest_win": biggest_win,
        "quick_win": quick_win,
        "yearly_goal_kg": yearly_goal_kg,
        "motivational_message": (
            f"Every action counts! Focus on your {highest_cat} emissions "
            f"for the fastest progress towards your target. 🌟"
        ),
    }


# ─── Offline Chat Response ────────────────────────────────────────────────────


def get_offline_response(
    message: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    is_fallback: bool = False,
) -> str:
    """Generate a structured, context-aware answer without calling Gemini.

    The function matches keywords in *message* to one of several
    topic branches (transport, energy, food, lifestyle, score, goals)
    and returns a rich Markdown response.

    Args:
        message:     The user's chat message.
        context:     Optional carbon-result dict for personalisation.
        is_fallback: ``True`` when Gemini was available but rate-limited.

    Returns:
        A Markdown-formatted reply string.
    """
    msg = message.lower().strip()

    # Build status notice
    notice = ""
    if is_fallback:
        notice = (
            "**Note:** Google Gemini API rate limit exceeded. "
            "EcoGuide is running in **Eco-Fallback Mode** to answer "
            "your questions. ⚡🌱\n\n"
        )
    elif not GEMINI_API_KEY:
        notice = (
            "**Eco-Mode Active:** Running locally without Gemini API "
            "key. 🌱\n\n"
        )

    # Extract context stats
    total_kg = context.get("total_kg_per_year", 0.0) if context else 0.0
    eco_score = context.get("eco_score", 0) if context else 0
    trees = context.get("trees_to_offset", 0) if context else 0

    cats = _extract_category_totals(context) if context else {}
    transport_total = cats.get("transport", 0.0)
    energy_total = cats.get("energy", 0.0)
    food_total = cats.get("food", 0.0)
    lifestyle_total = cats.get("lifestyle", 0.0)

    highest_cat, highest_val = (
        _highest_category(cats) if cats else ("none", 0.0)
    )

    # 1. Greetings
    if any(k in msg for k in ["hi", "hello", "hey", "greetings", "start"]):
        return _greet(notice, context, eco_score, total_kg, highest_cat, highest_val)

    # 2. Transport
    if any(
        k in msg
        for k in [
            "car", "drive", "flight", "plane", "transport", "travel",
            "vehicle", "fuel", "petrol", "diesel", "electric", "ev",
            "bus", "train", "commute", "subway",
        ]
    ):
        return _topic_transport(notice, transport_total)

    # 3. Energy
    if any(
        k in msg
        for k in [
            "electricity", "gas", "energy", "solar", "power",
            "heating", "ac", "utilities", "utility",
        ]
    ):
        return _topic_energy(notice, energy_total)

    # 4. Food / Diet
    if any(
        k in msg
        for k in [
            "food", "diet", "eat", "meat", "beef", "pork", "chicken",
            "vegan", "vegetarian", "pescatarian", "waste", "compost",
        ]
    ):
        return _topic_food(notice, food_total)

    # 5. Lifestyle / Shopping / Streaming
    if any(
        k in msg
        for k in [
            "lifestyle", "clothing", "clothes", "shop", "streaming",
            "stream", "order", "purchase", "online",
        ]
    ):
        return _topic_lifestyle(notice, lifestyle_total)

    # 6. Score
    if any(
        k in msg
        for k in ["score", "eco_score", "eco score", "performance", "rating"]
    ):
        return _topic_score(
            notice, context, eco_score, total_kg,
            transport_total, energy_total, food_total, lifestyle_total,
            highest_cat,
        )

    # 7. Goals / General tips
    if any(
        k in msg
        for k in ["reduce", "offset", "tree", "goal", "tips", "action", "improve", "help"]
    ):
        return _topic_goals(
            notice, trees, total_kg, highest_cat, highest_val,
        )

    # 8. Default fallback
    return _default_fallback(notice, context, total_kg, eco_score)


# ── Private topic formatters ──────────────────────────────────────────────────


def _greet(
    notice: str,
    context: Optional[Dict],
    eco_score: int,
    total_kg: float,
    highest_cat: str,
    highest_val: float,
) -> str:
    """Build a greeting response, personalised if context is available."""
    if context:
        return (
            f"{notice}Hello! I am EcoGuide, your AI sustainability assistant.\n\n"
            f"I see you have calculated your carbon footprint: your Eco Score is "
            f"**{eco_score}/100** and your annual emissions are **{total_kg:,} kg "
            f"CO₂e**. Your highest emission category is **{highest_cat.title()}** "
            f"({highest_val:,} kg).\n\n"
            f"How can I help you reduce your footprint today? Ask me about "
            f"transport, energy, food, or lifestyle! 💬"
        )
    return (
        f"{notice}Hello! I am EcoGuide, your AI sustainability assistant.\n\n"
        f"To get personalized insights, please go to the **Carbon Calculator** "
        f"section first and calculate your footprint!\n\n"
        f"Otherwise, feel free to ask me general questions about carbon "
        f"footprint reduction. How can I help you? 💬"
    )


def _topic_transport(notice: str, transport_total: float) -> str:
    """Build a transport-tips response."""
    resp = (
        f"{notice}### 🚗 Transport Emission Reduction Tips\n\n"
        "Transport is often the largest source of personal greenhouse gas "
        "emissions. Here is how you can reduce it:\n\n"
        "• **Switch to Public Transit:** Trains and buses emit significantly "
        "less per passenger-km (approx. 0.089 kg CO₂/km) compared to "
        "conventional petrol cars (0.21 kg/km).\n"
        "• **Drive Efficiently or Switch to EV:** Electric vehicles have an "
        "average factor of just 0.05 kg CO₂/km. If driving a petrol/diesel "
        "car, combine trips and maintain tire pressure to reduce consumption.\n"
        "• **Limit Flying:** Short-haul flights emit 0.255 kg CO₂/km per "
        "passenger. For distances under 500 km, prefer high-speed rail when "
        "available.\n\n"
    )
    if transport_total > 0:
        resp += (
            f"Your current annual transport footprint is "
            f"**{transport_total:,} kg CO₂e**. Try targeting a 10% reduction "
            f"this month by walking or cycling for short trips! 🚲"
        )
    else:
        resp += (
            "Calculate your footprint to see your specific transport "
            "emissions and get tailored targets! 📈"
        )
    return resp


def _topic_energy(notice: str, energy_total: float) -> str:
    """Build a home-energy tips response."""
    resp = (
        f"{notice}### 💡 Home Energy Efficiency Tips\n\n"
        "Home energy usage from electricity and heating plays a massive role "
        "in global emissions. Try these key improvements:\n\n"
        "• **Increase Renewable Energy:** Swapping to green energy tariffs or "
        "installing solar panels directly cuts down your electricity footprint.\n"
        "• **Smart Thermostats & Temperature Adjustments:** Lowering your "
        "thermostat by 1°C in winter can cut heating bills and emissions by "
        "up to 10%.\n"
        "• **Upgrade to LEDs & Efficient Appliances:** LED bulbs use up to "
        "85% less energy than incandescent lightbulbs and last 25 times "
        "longer.\n\n"
    )
    if energy_total > 0:
        resp += (
            f"Your current annual home energy footprint is "
            f"**{energy_total:,} kg CO₂e**. Switch off standby appliances "
            f"and turn off lights to start saving today! 🔌"
        )
    else:
        resp += (
            "Use the calculator to log your utility bills and track your "
            "home energy impact! 📊"
        )
    return resp


def _topic_food(notice: str, food_total: float) -> str:
    """Build a diet & food-waste tips response."""
    resp = (
        f"{notice}### 🍎 Diet & Food Waste Tips\n\n"
        "What we eat and how much we throw away has a substantial carbon "
        "cost:\n\n"
        "• **Transition to Plant-Based Eating:** Heavy meat diets average "
        "7.5 kg CO₂/day, while vegetarian (2.5 kg/day) and vegan (1.5 "
        "kg/day) diets are significantly lower.\n"
        "• **Stop Food Waste:** Food waste in landfills produces methane, a "
        "potent greenhouse gas. Plan meals, freeze leftovers, and compost "
        "waste to avoid emissions (each kg of food waste adds 2.5 kg CO₂e).\n"
        "• **Eat Seasonal & Local:** Reduce transportation emissions by "
        "choosing local produce that is in season.\n\n"
    )
    if food_total > 0:
        resp += (
            f"Your current annual food footprint is "
            f"**{food_total:,} kg CO₂e**. Try a 'Meatless Monday' challenge "
            f"to lower your daily emissions! 🥗"
        )
    else:
        resp += (
            "Calculate your footprint to see how your diet type and food "
            "waste contribute to your total score! 📉"
        )
    return resp


def _topic_lifestyle(notice: str, lifestyle_total: float) -> str:
    """Build a sustainable-lifestyle tips response."""
    resp = (
        f"{notice}### 🛍️ Sustainable Lifestyle & Consumer Habits\n\n"
        "Every item we buy and every digital service we consume carries an "
        "embodied carbon footprint:\n\n"
        "• **Mindful Fashion:** The fashion industry is responsible for "
        "significant global emissions. A new clothing garment averages "
        "33.4 kg CO₂e. Buy secondhand, repair old clothes, or choose "
        "high-quality items.\n"
        "• **Consolidate Online Shipments:** Online deliveries average "
        "0.5 kg CO₂ per order. Try to consolidate orders and avoid rush "
        "shipping.\n"
        "• **Digital Carbon Footprint:** Video streaming averages 0.036 kg "
        "CO₂ per hour. Streaming in Standard Definition (SD) or turning off "
        "auto-play can help reduce data center energy usage.\n\n"
    )
    if lifestyle_total > 0:
        resp += (
            f"Your current annual lifestyle footprint is "
            f"**{lifestyle_total:,} kg CO₂e**. Small choices like renting "
            f"or borrowing items add up over time! ♻️"
        )
    else:
        resp += (
            "Try the carbon calculator to estimate your daily habits, "
            "streaming hours, and clothing purchases! 👗"
        )
    return resp


def _topic_score(
    notice: str,
    context: Optional[Dict],
    eco_score: int,
    total_kg: float,
    transport_total: float,
    energy_total: float,
    food_total: float,
    lifestyle_total: float,
    highest_cat: str,
) -> str:
    """Build an eco-score analysis response."""
    if context:
        return (
            f"{notice}### 🏆 Your Eco Score Analysis\n\n"
            f"Your Eco Score is **{eco_score}/100**.\n\n"
            f"• **Score Interpretation:** A score closer to 100 means you "
            f"are closer to or below the global sustainable carbon budget. "
            f"A lower score indicates higher emissions.\n"
            f"• **Current Footprint:** Your annual footprint is "
            f"**{total_kg:,} kg CO₂e**.\n"
            f"• **Category Breakdown:**\n"
            f"  - Transport: {transport_total:,} kg\n"
            f"  - Home Energy: {energy_total:,} kg\n"
            f"  - Food & Diet: {food_total:,} kg\n"
            f"  - Lifestyle: {lifestyle_total:,} kg\n\n"
            f"To improve your score, focus on reducing your highest "
            f"category: **{highest_cat}**! 🚀"
        )
    return (
        f"{notice}You haven't calculated your footprint yet! Head over to "
        f"the **Carbon Calculator** section and fill in your details to get "
        f"your Eco Score and a personalized rating. 📈"
    )


def _topic_goals(
    notice: str,
    trees: float,
    total_kg: float,
    highest_cat: str,
    highest_val: float,
) -> str:
    """Build a goals / general tips response."""
    resp = (
        f"{notice}### 🌱 Top Actions to Reduce Your Footprint Today\n\n"
        "Here are the most impactful actions you can take to lower your "
        "emissions:\n\n"
        "1. **Switch to 100% Renewable Tariff:** Instantly removes "
        "electricity emissions (easy, high impact).\n"
        "2. **Reduce Beef/Lamb Intake:** These meats have the highest "
        "carbon intensity (medium difficulty, high impact).\n"
        "3. **Carpool, Walk, or Cycle:** Swap short car trips for active "
        "transit to save significant fuel emissions (easy, medium impact).\n"
        f"4. **Offset Remaining Emissions:** It takes about "
        f"**{trees if trees else 50:.0f} trees** to offset "
        f"{total_kg if total_kg else 1000:,.0f} kg CO₂/year. Consider "
        f"supporting verified forestry or conservation projects.\n\n"
    )
    if highest_cat != "none":
        resp += (
            f"Since your highest emission category is **{highest_cat}** "
            f"({highest_val:,} kg), prioritizing reductions in this area "
            f"will yield the fastest results! 🎯"
        )
    return resp


def _default_fallback(
    notice: str,
    context: Optional[Dict],
    total_kg: float,
    eco_score: int,
) -> str:
    """Build a generic fallback when no keyword matched."""
    if context:
        return (
            f"{notice}Thank you for your question. I want to help you "
            f"achieve your sustainability goals!\n\n"
            f"Your current carbon footprint is **{total_kg:,} kg CO₂e/year** "
            f"with an Eco Score of **{eco_score}/100**.\n\n"
            f"Based on your profile, here is one concrete action you can "
            f"take today: **Try to reduce energy waste or substitute one "
            f"drive with public transit.**\n\n"
            f"Ask me about any specific category: **Transport**, **Energy**, "
            f"**Food & Diet**, or **Lifestyle** for detailed tips! 🌿"
        )
    return (
        f"{notice}Thank you for reaching out! I am EcoGuide, your AI "
        f"assistant.\n\nIf you want to receive personalized advice, please "
        f"complete your carbon calculation first. In the meantime, feel free "
        f"to ask me about topics like EV benefits, diet footprints, green "
        f"energy tariffs, or fashion waste! 🌍"
    )


# ─── Gemini-Backed Chat ──────────────────────────────────────────────────────


async def get_gemini_response(
    session_id: str,
    message: str,
    context: Optional[Dict[str, Any]] = None,
    *,
    chat_sessions: Optional[Dict[str, Any]] = None,
) -> str:
    """Return a context-aware Gemini AI response for the chat interface.

    Maintains per-session conversation history.  Falls back to
    :func:`get_offline_response` on any error.

    Args:
        session_id:    Unique session identifier.
        message:       The user's chat message.
        context:       Optional carbon-result dict for personalisation.
        chat_sessions: Mutable dict storing Gemini chat objects.

    Returns:
        The AI's reply as a plain string.
    """
    if chat_sessions is None:
        chat_sessions = {}

    if not _gemini_model:
        return get_offline_response(message, context)

    try:
        if session_id not in chat_sessions:
            chat_sessions[session_id] = _gemini_model.start_chat(history=[])

        chat = chat_sessions[session_id]

        if context:
            bd = context.get("breakdown", {})
            biggest = (
                max(
                    bd.items(),
                    key=lambda x: x[1].get("total", 0) if isinstance(x[1], dict) else 0,
                )[0]
                if bd
                else "unknown"
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
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: chat.send_message(full_prompt)
        )
        logger.info("Gemini chat: session=%s...", session_id[:8])
        return response.text

    except Exception as exc:
        logger.error("Gemini chat error: %s", exc)
        return get_offline_response(message, context, is_fallback=True)


# ─── Gemini-Backed Insights ──────────────────────────────────────────────────


async def get_ai_insights(carbon_data: Dict[str, Any]) -> Dict[str, Any]:
    """Call Gemini to generate a structured JSON action plan.

    Falls back to :func:`generate_offline_insights` on any error.

    Args:
        carbon_data: A carbon-result dictionary.

    Returns:
        A structured insights dict consumable by the frontend.
    """
    if not _gemini_model:
        return generate_offline_insights(carbon_data)

    try:
        bd = carbon_data.get("breakdown", {})
        prompt = (
            "Analyze this carbon footprint and respond with ONLY a valid "
            "JSON object (no markdown, no code fences).\n\n"
            f"Total  : {carbon_data.get('total_kg_per_year')} kg CO₂e/yr\n"
            f"Transport : {bd.get('transport', {}).get('total', 0)} kg\n"
            f"Energy    : {bd.get('energy', {}).get('total', 0)} kg\n"
            f"Food      : {bd.get('food', {}).get('total', 0)} kg\n"
            f"Lifestyle : {bd.get('lifestyle', {}).get('total', 0)} kg\n"
            f"Eco Score : {carbon_data.get('eco_score')}/100\n\n"
            'Required JSON structure:\n'
            '{"summary":"<2 sentences>","top_actions":[{"action":"<title>",'
            '"impact_kg":<int>,"difficulty":"easy|medium|hard",'
            '"timeframe":"immediate|weekly|monthly"},'
            '{"action":"<title>","impact_kg":<int>,'
            '"difficulty":"easy|medium|hard",'
            '"timeframe":"immediate|weekly|monthly"},'
            '{"action":"<title>","impact_kg":<int>,'
            '"difficulty":"easy|medium|hard",'
            '"timeframe":"immediate|weekly|monthly"}],'
            '"biggest_win":"<string>","quick_win":"<string>",'
            '"yearly_goal_kg":<int>,'
            '"motivational_message":"<string>"}'
        )
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None, lambda: _gemini_model.generate_content(prompt)
        )
        text = response.text.strip()

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
