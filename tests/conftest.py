"""
Shared pytest fixtures for the EcoTrack test suite.

Provides:
- A reusable ``TestClient`` scoped to the entire test session.
- An ``autouse`` fixture that resets all mutable state before every
  test so that tests are fully isolated.
"""

from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client() -> TestClient:
    """Return a reusable FastAPI TestClient for the session."""
    from app import app

    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def reset_db(monkeypatch, tmp_path) -> None:
    """Reset all mutable state before each test.

    Swaps the in-memory DB, DATA_FILE path, stats cache, and chat
    sessions with fresh isolated values so tests never interfere with
    each other.
    """
    import app as app_module
    from routes import store

    fresh: Dict[str, Any] = {
        "users": {},
        "activities": [],
        "leaderboard": [],
        "activity_index": {},
    }

    # Reset the DataStore's internal state
    monkeypatch.setattr(store, "db", fresh)
    monkeypatch.setattr(store, "_data_file", tmp_path / "test_data.json")
    monkeypatch.setattr(store, "_stats_cache", {})
    monkeypatch.setattr(store, "_stats_cache_at", 0.0)
    monkeypatch.setattr(store, "chat_sessions", {})

    # Keep backward-compat aliases in app module in sync
    monkeypatch.setattr(app_module, "DB", fresh)
    monkeypatch.setattr(app_module, "DATA_FILE", tmp_path / "test_data.json")
    monkeypatch.setattr(app_module, "_stats_cache", {})
    monkeypatch.setattr(app_module, "_stats_cache_at", 0.0)
    monkeypatch.setattr(app_module, "chat_sessions", {})
