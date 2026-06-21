"""
Persistent storage layer for EcoTrack.

Provides :class:`DataStore` — a thin wrapper around an in-memory dict
that is periodically flushed to a JSON file on disk.  All mutations go
through an ``asyncio.Lock`` to prevent concurrent-write corruption.

A lightweight TTL cache for the ``/api/stats`` endpoint is also included.
"""

import asyncio
import datetime
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import DATA_FILE, STATS_CACHE_TTL_SEC, logger

# ─── Public API ───────────────────────────────────────────────────────────────

__all__ = ["DataStore"]


class DataStore:
    """Thread-safe, async-aware data store backed by a JSON file.

    Attributes:
        db: The in-memory data dictionary.
    """

    def __init__(self, data_file: Path = DATA_FILE) -> None:
        self._data_file = data_file
        self._write_lock = asyncio.Lock()

        # TTL cache state
        self._stats_cache: Dict[str, Any] = {}
        self._stats_cache_at: float = 0.0

        # In-memory chat sessions (keyed by session_id)
        self.chat_sessions: Dict[str, Any] = {}

        # Load persisted data
        self.db: Dict[str, Any] = self._load()
        if "activity_index" not in self.db:
            self.db["activity_index"] = {}

    # ── Persistence ───────────────────────────────────────────────────────

    def _load(self) -> Dict[str, Any]:
        """Load persisted data from JSON, returning a safe default on failure."""
        if self._data_file.exists():
            try:
                with open(self._data_file, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed to load data: %s", exc)
        return {
            "users": {},
            "activities": [],
            "leaderboard": [],
            "activity_index": {},
        }

    async def save(self) -> None:
        """Write the in-memory DB to disk under an async lock.

        The lock prevents two concurrent requests from corrupting the
        JSON file with interleaved writes.
        """
        async with self._write_lock:
            try:
                with open(self._data_file, "w", encoding="utf-8") as fh:
                    json.dump(self.db, fh, indent=2, default=str)
            except OSError as exc:
                logger.error("Failed to save data: %s", exc)

    # ── Stats Cache (TTL) ─────────────────────────────────────────────────

    def get_cached_stats(self) -> Optional[Dict[str, Any]]:
        """Return cached stats if still within the TTL window, else ``None``."""
        age = (
            datetime.datetime.now(datetime.timezone.utc).timestamp()
            - self._stats_cache_at
        )
        if self._stats_cache and age < STATS_CACHE_TTL_SEC:
            return self._stats_cache.copy()
        return None

    def set_stats_cache(self, stats: Dict[str, Any]) -> None:
        """Replace the cached stats and reset the TTL timer."""
        self._stats_cache = stats.copy()
        self._stats_cache_at = (
            datetime.datetime.now(datetime.timezone.utc).timestamp()
        )

    def invalidate_stats_cache(self) -> None:
        """Force the next ``get_cached_stats`` call to recompute."""
        self._stats_cache_at = 0.0

    # ── Convenience Accessors ─────────────────────────────────────────────

    @property
    def users(self) -> Dict[str, Any]:
        """Shorthand for ``self.db["users"]``."""
        return self.db["users"]

    @property
    def activities(self) -> List[Dict[str, Any]]:
        """Shorthand for ``self.db["activities"]``."""
        return self.db["activities"]

    @property
    def leaderboard(self) -> List[Dict[str, Any]]:
        """Shorthand for ``self.db["leaderboard"]``."""
        return self.db["leaderboard"]

    @property
    def activity_index(self) -> Dict[str, List[int]]:
        """Shorthand for ``self.db["activity_index"]``."""
        return self.db["activity_index"]
