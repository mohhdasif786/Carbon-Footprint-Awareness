"""
Shared pytest fixtures for EcoTrack test suite.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    """Return a reusable FastAPI TestClient for the session."""
    from app import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture(autouse=True)
def reset_db(monkeypatch, tmp_path):
    """
    Before each test: swap the in-memory DB and DATA_FILE with fresh
    isolated state so tests never interfere with each other.
    """
    import app as app_module

    fresh: dict = {
        "users": {},
        "activities": [],
        "leaderboard": [],
        "activity_index": {},
    }
    monkeypatch.setattr(app_module, "DB", fresh)
    monkeypatch.setattr(app_module, "DATA_FILE", tmp_path / "test_data.json")
    # Also reset stats cache
    monkeypatch.setattr(app_module, "_stats_cache",    {})
    monkeypatch.setattr(app_module, "_stats_cache_at", 0.0)
    monkeypatch.setattr(app_module, "chat_sessions",   {})
