"""
API route handlers for EcoTrack.

All endpoints are registered on a single :class:`~fastapi.APIRouter`
which is mounted by the main ``app`` module.  This keeps route logic
separate from application bootstrapping (middleware, CORS, static
mounts, etc.).
"""

import asyncio
import datetime
import re
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ai_service import generate_offline_insights, get_ai_insights, get_gemini_response
from calculator import calculate_carbon
from config import MAPS_API_KEY, TREE_ABSORPTION_KG, logger
from models import ActivityLog, CarbonInput, ChatMessage, GoalInput
from storage import DataStore

# ─── Public API ───────────────────────────────────────────────────────────────

__all__ = ["router", "store"]

# ─── Constants ────────────────────────────────────────────────────────────────

_SESSION_ID_PATTERN: re.Pattern[str] = re.compile(r"^[\w\-]{1,128}$")
"""Regex for validating session_id path parameters."""

# ─── Shared State ─────────────────────────────────────────────────────────────
# Instantiated once at import time; the ``app`` module also holds a reference
# so that middleware and tests can access the same store.

store = DataStore()

# ─── Router ───────────────────────────────────────────────────────────────────

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_index() -> HTMLResponse:
    """Serve the main SPA (index.html)."""
    idx = Path("static/index.html")
    if idx.exists():
        return HTMLResponse(content=idx.read_text(encoding="utf-8"))
    return HTMLResponse(
        "<h1>Error: static/index.html not found.</h1>", status_code=500
    )


@router.post("/api/calculate", summary="Calculate annual carbon footprint")
async def calculate_footprint(data: CarbonInput) -> JSONResponse:
    """Compute the user's annual CO₂e footprint from lifestyle inputs.

    Persists the calculation result and returns a full breakdown with
    eco-score.
    """
    result = calculate_carbon(data)

    # Persist (non-blocking, fire-and-forget)
    entry_idx = len(store.activities)
    store.activities.append({
        "session_id": data.session_id,
        "type": "calculation",
        "data": result,
        "timestamp": result["timestamp"],
    })
    store.activity_index.setdefault(data.session_id, []).append(entry_idx)
    asyncio.create_task(store.save())

    logger.info(
        "Footprint calculated: session=%s... %.1f kg/yr score=%d",
        data.session_id[:8],
        result["total_kg_per_year"],
        result["eco_score"],
    )
    return JSONResponse(content=result)


@router.post("/api/chat", summary="Chat with EcoGuide AI assistant")
async def chat_with_ai(request: Request, msg: ChatMessage) -> Dict:
    """Send a message to EcoGuide (Gemini 1.5 Flash).

    Rate-limited to 15 requests/minute per IP to prevent abuse.
    """
    reply = await get_gemini_response(
        msg.session_id,
        msg.message,
        msg.carbon_context,
        chat_sessions=store.chat_sessions,
    )
    return {
        "response": reply,
        "session_id": msg.session_id,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


@router.post(
    "/api/insights",
    summary="Generate AI-powered personalized insights",
)
async def get_insights(request: Request, data: dict) -> Dict:
    """Generate a structured action plan from carbon calculation results.

    Rate-limited to 10 requests/minute per IP.
    """
    return await get_ai_insights(data)


@router.post("/api/log-activity", summary="Log a completed green action")
async def log_activity(activity: ActivityLog) -> Dict:
    """Record an eco-friendly action and update the community leaderboard."""
    if not activity.date:
        activity.date = datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat()

    entry_idx = len(store.activities)
    store.activities.append(activity.model_dump())
    store.activity_index.setdefault(activity.session_id, []).append(entry_idx)

    # Update or create leaderboard entry
    existing = next(
        (x for x in store.leaderboard if x["session_id"] == activity.session_id),
        None,
    )
    if existing:
        existing["co2_saved_kg"] = round(
            existing["co2_saved_kg"] + activity.co2_saved_kg, 2
        )
        existing["actions"] = existing.get("actions", 0) + 1
    else:
        store.leaderboard.append({
            "session_id": activity.session_id,
            "co2_saved_kg": round(activity.co2_saved_kg, 2),
            "actions": 1,
            "joined": activity.date,
        })

    store.invalidate_stats_cache()
    asyncio.create_task(store.save())

    logger.info(
        "Activity logged: '%s' (%.2f kg saved)",
        activity.description,
        activity.co2_saved_kg,
    )
    return {"success": True, "message": f"Logged: {activity.description}"}


@router.post("/api/set-goal", summary="Save a carbon reduction goal")
async def set_goal(goal: GoalInput) -> Dict:
    """Persist the user's reduction target and timeline."""
    store.users.setdefault(goal.session_id, {})["goal"] = {
        "target_reduction_pct": goal.target_reduction_pct,
        "timeline_months": goal.timeline_months,
        "created_at": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
    }
    asyncio.create_task(store.save())

    logger.info(
        "Goal set: session=%s... %.0f%% over %d months",
        goal.session_id[:8],
        goal.target_reduction_pct,
        goal.timeline_months,
    )
    return {"success": True, "goal": store.users[goal.session_id]["goal"]}


@router.get(
    "/api/history/{session_id}",
    summary="Get activity history for a session",
)
async def get_history(session_id: str) -> Dict:
    """O(k) lookup using the activity index (k = activities for this session).

    Avoids scanning the entire activities list.

    Raises:
        HTTPException: 400 if session_id format is invalid.
    """
    if not _SESSION_ID_PATTERN.match(session_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid session_id format. Use alphanumeric, hyphens, or underscores (1-128 chars).",
        )
    indices = store.activity_index.get(session_id, [])
    activities = [
        store.activities[i]
        for i in indices
        if i < len(store.activities)
    ]
    return {"activities": activities, "count": len(activities)}


@router.get("/api/leaderboard", summary="Community CO₂-saving leaderboard")
async def get_leaderboard() -> Dict:
    """Return top-20 users ranked by total CO₂ saved (session IDs anonymised)."""
    sorted_lb = sorted(
        store.leaderboard,
        key=lambda x: x.get("co2_saved_kg", 0),
        reverse=True,
    )
    medals = ["🥇", "🥈", "🥉"]
    return {
        "leaderboard": [
            {
                "rank": i + 1,
                "medal": medals[i] if i < 3 else "",
                "user": f"EcoHero #{entry['session_id'][:6].upper()}",
                "co2_saved_kg": entry["co2_saved_kg"],
                "actions": entry.get("actions", 0),
            }
            for i, entry in enumerate(sorted_lb[:20])
        ],
        "total_users": len(store.leaderboard),
    }


@router.get("/api/stats", summary="Platform-wide statistics (TTL-cached)")
async def get_platform_stats() -> Dict:
    """Aggregate stats with a 30-second TTL cache to reduce computation."""
    cached = store.get_cached_stats()
    if cached:
        return cached

    total_saved = sum(x.get("co2_saved_kg", 0) for x in store.leaderboard)
    calcs_done = sum(
        1 for a in store.activities if a.get("type") == "calculation"
    )
    stats = {
        "total_users": len(store.leaderboard),
        "total_co2_saved_kg": round(total_saved, 1),
        "total_trees_equivalent": round(total_saved / TREE_ABSORPTION_KG, 0),
        "calculations_done": calcs_done,
    }
    store.set_stats_cache(stats)
    return stats


@router.get("/api/config", include_in_schema=False)
async def get_config() -> Dict:
    """Expose runtime configuration required by the frontend.

    The Maps API key is masked to prevent leakage in public responses.
    """
    if len(MAPS_API_KEY) > 8:
        masked_key = f"{MAPS_API_KEY[:4]}...{MAPS_API_KEY[-4:]}"
    elif MAPS_API_KEY:
        masked_key = "configured"
    else:
        masked_key = ""
    return {"maps_api_key": masked_key, "version": "1.0.0"}


@router.get("/api/health", summary="Liveness / readiness check")
async def health_check() -> Dict:
    """Return server health and integration status."""
    from ai_service import _gemini_model  # deferred to avoid circular ref

    return {
        "status": "ok",
        "timestamp": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "gemini_configured": _gemini_model is not None,
        "maps_configured": bool(MAPS_API_KEY),
    }
