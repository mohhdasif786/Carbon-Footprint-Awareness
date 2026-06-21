"""
Carbon Footprint Awareness Platform — EcoTrack
================================================
Run with: ``python app.py``
Serves frontend + all APIs on http://localhost:8000

Architecture
------------
FastAPI (ASGI) → serves static SPA + REST API
Gemini 1.5 Flash → AI chat + personalized insights
OpenStreetMap + Leaflet → EV stations, green routes (frontend)
Rate limiting (slowapi) → prevent API abuse
Async file I/O with lock → thread-safe persistence
TTL cache → efficient stats endpoint

Module Layout
-------------
- **config.py** — environment variables, emission factors, logging
- **models.py** — Pydantic request schemas & TypedDict return types
- **calculator.py** — pure carbon-footprint computation
- **ai_service.py** — Gemini chat, insights, offline fallbacks
- **storage.py** — async-safe JSON persistence & TTL cache
- **routes.py** — all API endpoint handlers
- **app.py** — *(this file)* — FastAPI composition root
"""

from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from config import ALLOWED_ORIGINS, DATA_FILE, PORT, logger
from routes import router, store

# ─── Public API ───────────────────────────────────────────────────────────────

__all__ = ["app"]

# ─── Rate Limiter ─────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["300/hour"])

# ─── FastAPI Application ─────────────────────────────────────────────────────

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
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(self), payment=()"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://www.googletagmanager.com https://www.google.com "
        "https://www.gstatic.com https://unpkg.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com "
        "https://unpkg.com; "
        "img-src 'self' data: https://*.tile.openstreetmap.org "
        "https://*.basemaps.cartocdn.com "
        "https://www.googletagmanager.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "connect-src 'self' https://overpass-api.de "
        "https://nominatim.openstreetmap.org; "
        "frame-src 'self' https://www.googletagmanager.com;"
    )
    return response


# ─── Static Files & Data Directory ───────────────────────────────────────────

static_dir = Path("static")
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
DATA_FILE.parent.mkdir(exist_ok=True)

# ─── Register Routes ─────────────────────────────────────────────────────────

app.include_router(router)

# ─── Backward-Compatibility Aliases ───────────────────────────────────────────
# Tests and any external code that imports directly from ``app`` will still
# find the symbols they expect.  This keeps the test suite working with
# **zero changes** to test files.

# Re-export models
from models import (  # noqa: E402, F401
    ActivityLog,
    CarbonInput,
    ChatMessage,
    GoalInput,
)

# Re-export business logic
from calculator import calculate_carbon  # noqa: E402, F401

# Re-export AI service functions
from ai_service import (  # noqa: E402, F401
    _gemini_model,
    generate_offline_insights,
    get_offline_response,
)

# Re-export config constants
from config import (  # noqa: E402, F401
    CAR_FACTORS,
    DIET_FACTORS,
    FLIGHT_FACTORS,
    GEMINI_API_KEY,
    GLOBAL_AVG_KG,
    TREE_ABSORPTION_KG,
    US_AVG_KG,
)

# Re-export storage primitives (tests access these directly)
DB = store.db
chat_sessions = store.chat_sessions
_stats_cache = store._stats_cache
_stats_cache_at = store._stats_cache_at


def load_data():
    """Delegate to store's internal loader (used by test_app.py)."""
    return store._load()


def get_cached_stats():
    """Delegate to store (used by test_app.py)."""
    return store.get_cached_stats()


def set_stats_cache(stats):
    """Delegate to store (used by test_app.py)."""
    store.set_stats_cache(stats)


def invalidate_stats_cache():
    """Delegate to store (used by test_app.py)."""
    store.invalidate_stats_cache()


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("🌱 EcoTrack — Carbon Footprint Awareness Platform")
    logger.info("=" * 60)
    logger.info("🚀  http://localhost:%d", PORT)
    logger.info("📖  http://localhost:%d/docs", PORT)
    logger.info("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=PORT, reload=False, log_level="info")
