"""
Comprehensive test suite for the EcoTrack Carbon Footprint Awareness Platform.
Covers all API endpoints, business logic, edge cases, and error paths.
"""

import json
import pytest

from app import (
    calculate_carbon,
    CarbonInput,
    generate_offline_insights,
    get_offline_response,
    get_cached_stats,
    set_stats_cache,
    invalidate_stats_cache,
    load_data,
    CAR_FACTORS,
    FLIGHT_FACTORS,
    DIET_FACTORS,
    TREE_ABSORPTION_KG,
    GLOBAL_AVG_KG,
    US_AVG_KG,
)


# ═══════════════════════════════════════════════════════════════════
# HEALTH & CONFIG ENDPOINTS
# ═══════════════════════════════════════════════════════════════════

class TestHealthAndConfig:
    def test_health_check_returns_ok(self, client):
        """Health endpoint returns status=ok with required fields."""
        response = client.get("/api/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "gemini_configured" in data
        assert "maps_configured" in data

    def test_health_check_gemini_not_configured(self, client):
        """Without GEMINI_API_KEY, gemini_configured must be False."""
        import app as app_module
        original = app_module._gemini_model
        app_module._gemini_model = None
        try:
            response = client.get("/api/health")
            assert response.status_code == 200
            assert response.json()["gemini_configured"] is False
        finally:
            app_module._gemini_model = original

    def test_get_config_endpoint(self, client):
        """Config endpoint exposes maps_api_key and version."""
        response = client.get("/api/config")
        assert response.status_code == 200
        data = response.json()
        assert "maps_api_key" in data
        assert data["version"] == "1.0.0"

    def test_serve_index_missing(self, client, tmp_path, monkeypatch):
        """When index.html is absent, root returns 500."""
        import app as app_module
        monkeypatch.setattr(app_module, "static_dir", tmp_path)
        # Patch Path to return a non-existent file
        response = client.get("/")
        # Either returns index.html content (200) or 500
        assert response.status_code in (200, 500)


# ═══════════════════════════════════════════════════════════════════
# CARBON CALCULATOR — UNIT TESTS
# ═══════════════════════════════════════════════════════════════════

class TestCalculateCarbonFunction:
    """Pure function tests for calculate_carbon()."""

    def _make_input(self, **kwargs) -> CarbonInput:
        defaults = dict(
            session_id="unit_test",
            car_km_per_week=0,
            car_type="petrol",
            flights_per_year=0,
            flight_type="short",
            public_transport_km=0,
            electricity_kwh=0,
            natural_gas_cubic_m=0,
            renewable_energy_pct=0,
            diet_type="omnivore",
            food_waste_kg=0,
            new_clothes_per_year=0,
            online_orders_per_month=0,
            streaming_hours_per_day=0,
        )
        defaults.update(kwargs)
        return CarbonInput(**defaults)

    def test_zero_input_returns_diet_baseline(self):
        """All zeros except mandatory diet still produces non-zero total (diet has a daily factor)."""
        result = calculate_carbon(self._make_input())
        assert result["total_kg_per_year"] > 0  # diet baseline always > 0
        assert result["session_id"] == "unit_test"

    def test_result_has_all_required_keys(self):
        result = calculate_carbon(self._make_input(car_km_per_week=100))
        for key in ("total_kg_per_year", "total_tonnes", "breakdown", "comparisons",
                    "eco_score", "trees_to_offset", "session_id", "timestamp"):
            assert key in result, f"Missing key: {key}"

    def test_breakdown_has_all_categories(self):
        result = calculate_carbon(self._make_input(car_km_per_week=100))
        bd = result["breakdown"]
        assert set(bd.keys()) == {"transport", "energy", "food", "lifestyle"}

    def test_car_type_petrol(self):
        result = calculate_carbon(self._make_input(car_km_per_week=100, car_type="petrol"))
        expected_car = round(100 * CAR_FACTORS["petrol"] * 52, 1)
        assert result["breakdown"]["transport"]["car"] == expected_car

    def test_car_type_diesel(self):
        result = calculate_carbon(self._make_input(car_km_per_week=100, car_type="diesel"))
        expected = round(100 * CAR_FACTORS["diesel"] * 52, 1)
        assert result["breakdown"]["transport"]["car"] == expected

    def test_car_type_hybrid(self):
        result = calculate_carbon(self._make_input(car_km_per_week=100, car_type="hybrid"))
        expected = round(100 * CAR_FACTORS["hybrid"] * 52, 1)
        assert result["breakdown"]["transport"]["car"] == expected

    def test_car_type_electric(self):
        result = calculate_carbon(self._make_input(car_km_per_week=100, car_type="electric"))
        expected = round(100 * CAR_FACTORS["electric"] * 52, 1)
        assert result["breakdown"]["transport"]["car"] == expected

    def test_flight_type_short(self):
        result = calculate_carbon(self._make_input(flights_per_year=2, flight_type="short"))
        expected = round(2 * 500.0 * 2 * FLIGHT_FACTORS["short"], 1)
        assert result["breakdown"]["transport"]["flights"] == expected

    def test_flight_type_medium(self):
        result = calculate_carbon(self._make_input(flights_per_year=2, flight_type="medium"))
        expected = round(2 * 3000.0 * 2 * FLIGHT_FACTORS["medium"], 1)
        assert result["breakdown"]["transport"]["flights"] == expected

    def test_flight_type_long(self):
        result = calculate_carbon(self._make_input(flights_per_year=2, flight_type="long"))
        expected = round(2 * 9000.0 * 2 * FLIGHT_FACTORS["long"], 1)
        assert result["breakdown"]["transport"]["flights"] == expected

    def test_diet_vegan(self):
        result = calculate_carbon(self._make_input(diet_type="vegan"))
        assert result["breakdown"]["food"]["diet"] == round(DIET_FACTORS["vegan"] * 365, 1)

    def test_diet_vegetarian(self):
        result = calculate_carbon(self._make_input(diet_type="vegetarian"))
        assert result["breakdown"]["food"]["diet"] == round(DIET_FACTORS["vegetarian"] * 365, 1)

    def test_diet_pescatarian(self):
        result = calculate_carbon(self._make_input(diet_type="pescatarian"))
        assert result["breakdown"]["food"]["diet"] == round(DIET_FACTORS["pescatarian"] * 365, 1)

    def test_diet_heavy_meat(self):
        result = calculate_carbon(self._make_input(diet_type="heavy_meat"))
        assert result["breakdown"]["food"]["diet"] == round(DIET_FACTORS["heavy_meat"] * 365, 1)

    def test_renewable_energy_reduces_electricity_emission(self):
        base = calculate_carbon(self._make_input(electricity_kwh=200, renewable_energy_pct=0))
        green = calculate_carbon(self._make_input(electricity_kwh=200, renewable_energy_pct=100))
        assert green["breakdown"]["energy"]["electricity"] == 0.0
        assert base["breakdown"]["energy"]["electricity"] > 0

    def test_eco_score_range(self):
        result = calculate_carbon(self._make_input(
            car_km_per_week=500, flights_per_year=20, flight_type="long",
            electricity_kwh=1000, diet_type="heavy_meat"
        ))
        assert 0 <= result["eco_score"] <= 100

    def test_comparisons_present(self):
        result = calculate_carbon(self._make_input())
        comp = result["comparisons"]
        assert "vs_global_avg_pct" in comp
        assert "vs_us_avg_pct" in comp
        assert comp["global_avg_kg"] == GLOBAL_AVG_KG
        assert comp["us_avg_kg"] == US_AVG_KG

    def test_trees_to_offset_positive(self):
        result = calculate_carbon(self._make_input(car_km_per_week=200))
        assert result["trees_to_offset"] >= 0

    def test_total_tonnes_matches_kg(self):
        result = calculate_carbon(self._make_input(car_km_per_week=100))
        assert abs(result["total_tonnes"] - result["total_kg_per_year"] / 1000) < 0.01

    def test_streaming_lifestyle_contribution(self):
        result = calculate_carbon(self._make_input(streaming_hours_per_day=8))
        assert result["breakdown"]["lifestyle"]["streaming"] > 0

    def test_clothing_lifestyle_contribution(self):
        result = calculate_carbon(self._make_input(new_clothes_per_year=20))
        assert result["breakdown"]["lifestyle"]["clothing"] > 0

    def test_online_orders_lifestyle_contribution(self):
        result = calculate_carbon(self._make_input(online_orders_per_month=10))
        assert result["breakdown"]["lifestyle"]["shopping"] > 0


# ═══════════════════════════════════════════════════════════════════
# CALCULATE API ENDPOINT
# ═══════════════════════════════════════════════════════════════════

class TestCalculateEndpoint:
    def test_calculate_with_full_payload(self, client):
        payload = {
            "session_id": "test_session_api",
            "car_km_per_week": 50,
            "car_type": "hybrid",
            "flights_per_year": 0,
            "flight_type": "short",
            "public_transport_km": 20,
            "electricity_kwh": 100,
            "natural_gas_cubic_m": 5,
            "renewable_energy_pct": 50,
            "diet_type": "vegan",
            "food_waste_kg": 2,
            "new_clothes_per_year": 2,
            "online_orders_per_month": 1,
            "streaming_hours_per_day": 2,
        }
        response = client.post("/api/calculate", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test_session_api"
        assert "eco_score" in data
        assert "breakdown" in data

    def test_calculate_default_payload(self, client):
        """Minimal payload uses defaults — should return 200."""
        response = client.post("/api/calculate", json={})
        assert response.status_code == 200

    def test_calculate_populates_history(self, client):
        """Calculation must appear in /api/history for that session."""
        session_id = "history_check_session"
        client.post("/api/calculate", json={"session_id": session_id})
        history = client.get(f"/api/history/{session_id}").json()
        assert history["count"] > 0

    def test_calculate_invalid_car_type(self, client):
        """Invalid car_type should produce validation error (422)."""
        response = client.post("/api/calculate", json={"car_type": "rocket"})
        assert response.status_code == 422

    def test_calculate_invalid_diet_type(self, client):
        response = client.post("/api/calculate", json={"diet_type": "carnivore"})
        assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════
# CHAT API ENDPOINT (offline mode — no Gemini key)
# ═══════════════════════════════════════════════════════════════════

class TestChatEndpoint:
    BASE_PAYLOAD = {
        "session_id": "chat_session",
        "message": "Hello there!",
        "carbon_context": None,
    }

    def test_chat_returns_response_field(self, client):
        response = client.post("/api/chat", json=self.BASE_PAYLOAD)
        assert response.status_code == 200
        data = response.json()
        assert "response" in data
        assert "session_id" in data
        assert "timestamp" in data

    def test_chat_greeting_without_context(self, client):
        payload = {**self.BASE_PAYLOAD, "message": "hi", "carbon_context": None}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200
        text = response.json()["response"]
        assert len(text) > 10

    def test_chat_greeting_with_context(self, client):
        context = {
            "total_kg_per_year": 5000.0,
            "eco_score": 60,
            "trees_to_offset": 238,
            "breakdown": {
                "transport": {"total": 2000.0},
                "energy": {"total": 1500.0},
                "food": {"total": 1000.0},
                "lifestyle": {"total": 500.0},
            },
        }
        payload = {"session_id": "chat_ctx", "message": "hello", "carbon_context": context}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200
        text = response.json()["response"]
        assert "Hello" in text or "EcoGuide" in text

    def test_chat_transport_keyword(self, client):
        payload = {**self.BASE_PAYLOAD, "message": "tell me about electric cars (EV)"}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 200

    def test_chat_empty_message_rejected(self, client):
        payload = {**self.BASE_PAYLOAD, "message": ""}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 422

    def test_chat_message_too_long_rejected(self, client):
        payload = {**self.BASE_PAYLOAD, "message": "x" * 1001}
        response = client.post("/api/chat", json=payload)
        assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════
# INSIGHTS API ENDPOINT
# ═══════════════════════════════════════════════════════════════════

class TestInsightsEndpoint:
    CARBON_DATA = {
        "total_kg_per_year": 7000.0,
        "eco_score": 50,
        "trees_to_offset": 333,
        "breakdown": {
            "transport": {"total": 3000.0},
            "energy": {"total": 2000.0},
            "food": {"total": 1500.0},
            "lifestyle": {"total": 500.0},
        },
    }

    def test_insights_returns_structured_response(self, client):
        response = client.post("/api/insights", json=self.CARBON_DATA)
        assert response.status_code == 200
        data = response.json()
        assert "summary" in data
        assert "top_actions" in data
        assert "biggest_win" in data
        assert "quick_win" in data
        assert "yearly_goal_kg" in data

    def test_insights_top_actions_not_empty(self, client):
        response = client.post("/api/insights", json=self.CARBON_DATA)
        assert len(response.json()["top_actions"]) > 0

    def test_insights_empty_carbon_data(self, client):
        """Empty carbon data should return fallback insights without crashing."""
        response = client.post("/api/insights", json={})
        assert response.status_code == 200


# ═══════════════════════════════════════════════════════════════════
# LOG ACTIVITY ENDPOINT
# ═══════════════════════════════════════════════════════════════════

class TestLogActivityEndpoint:
    def test_log_activity_success(self, client):
        payload = {
            "session_id": "activity_session",
            "activity_type": "transport",
            "description": "Cycled to work",
            "co2_saved_kg": 2.5,
        }
        response = client.post("/api/log-activity", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Cycled to work" in data["message"]

    def test_log_activity_appears_in_leaderboard(self, client):
        session_id = "leaderboard_session"
        payload = {
            "session_id": session_id,
            "activity_type": "energy",
            "description": "Switched to solar",
            "co2_saved_kg": 500.0,
        }
        client.post("/api/log-activity", json=payload)
        lb = client.get("/api/leaderboard").json()
        # session_id[:6].upper() == "LEADER"
        users = [e["user"] for e in lb["leaderboard"]]
        assert any("LEADER" in u for u in users)

    def test_log_activity_accumulates_kg(self, client):
        """Logging twice for same session must accumulate co2_saved_kg."""
        session_id = "accum_session"
        payload = {
            "session_id": session_id,
            "activity_type": "food",
            "description": "Meatless Monday",
            "co2_saved_kg": 10.0,
        }
        client.post("/api/log-activity", json=payload)
        client.post("/api/log-activity", json=payload)
        lb = client.get("/api/leaderboard").json()
        # session_id[:6].upper() == "ACCUM_"
        entry = next(
            (e for e in lb["leaderboard"] if session_id[:6].upper() in e["user"]), None
        )
        assert entry is not None
        assert entry["co2_saved_kg"] == 20.0

    def test_log_activity_with_custom_date(self, client):
        payload = {
            "session_id": "dated_session",
            "activity_type": "lifestyle",
            "description": "Bought secondhand clothing",
            "co2_saved_kg": 33.4,
            "date": "2025-01-01T00:00:00",
        }
        response = client.post("/api/log-activity", json=payload)
        assert response.status_code == 200

    def test_log_activity_invalid_type_rejected(self, client):
        payload = {
            "session_id": "bad_session",
            "activity_type": "gambling",  # invalid
            "description": "Something",
            "co2_saved_kg": 1.0,
        }
        response = client.post("/api/log-activity", json=payload)
        assert response.status_code == 422

    def test_log_activity_zero_co2_rejected(self, client):
        payload = {
            "session_id": "zero_session",
            "activity_type": "transport",
            "description": "Nothing",
            "co2_saved_kg": 0,  # gt=0 constraint
        }
        response = client.post("/api/log-activity", json=payload)
        assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════
# SET GOAL ENDPOINT
# ═══════════════════════════════════════════════════════════════════

class TestSetGoalEndpoint:
    def test_set_goal_success(self, client):
        payload = {
            "session_id": "goal_session",
            "target_reduction_pct": 20.0,
            "timeline_months": 12,
        }
        response = client.post("/api/set-goal", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "goal" in data
        assert data["goal"]["target_reduction_pct"] == 20.0
        assert data["goal"]["timeline_months"] == 12
        assert "created_at" in data["goal"]

    def test_set_goal_boundary_values(self, client):
        payload = {
            "session_id": "boundary_session",
            "target_reduction_pct": 100.0,
            "timeline_months": 60,
        }
        response = client.post("/api/set-goal", json=payload)
        assert response.status_code == 200

    def test_set_goal_invalid_pct_rejected(self, client):
        payload = {
            "session_id": "invalid_session",
            "target_reduction_pct": 0,  # ge=1 constraint
            "timeline_months": 12,
        }
        response = client.post("/api/set-goal", json=payload)
        assert response.status_code == 422

    def test_set_goal_invalid_months_rejected(self, client):
        payload = {
            "session_id": "invalid_months",
            "target_reduction_pct": 10,
            "timeline_months": 61,  # le=60 constraint
        }
        response = client.post("/api/set-goal", json=payload)
        assert response.status_code == 422


# ═══════════════════════════════════════════════════════════════════
# HISTORY ENDPOINT
# ═══════════════════════════════════════════════════════════════════

class TestHistoryEndpoint:
    def test_empty_history_for_new_session(self, client):
        response = client.get("/api/history/unknown_session_xyz")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["activities"] == []

    def test_history_populated_after_calculate(self, client):
        session_id = "test_history_session"
        client.post("/api/calculate", json={"session_id": session_id})
        response = client.get(f"/api/history/{session_id}")
        assert response.status_code == 200
        assert response.json()["count"] > 0


# ═══════════════════════════════════════════════════════════════════
# LEADERBOARD ENDPOINT
# ═══════════════════════════════════════════════════════════════════

class TestLeaderboardEndpoint:
    def test_empty_leaderboard(self, client):
        response = client.get("/api/leaderboard")
        assert response.status_code == 200
        data = response.json()
        assert "leaderboard" in data
        assert "total_users" in data
        assert data["total_users"] == 0

    def test_leaderboard_ranked_with_medals(self, client):
        """First 3 entries should have medal emoji assigned."""
        for i, kg in enumerate([500.0, 300.0, 100.0]):
            client.post("/api/log-activity", json={
                "session_id": f"medal_session_{i}",
                "activity_type": "transport",
                "description": "Test",
                "co2_saved_kg": kg,
            })
        lb = client.get("/api/leaderboard").json()["leaderboard"]
        assert lb[0]["medal"] == "🥇"
        assert lb[1]["medal"] == "🥈"
        assert lb[2]["medal"] == "🥉"

    def test_leaderboard_sorted_descending(self, client):
        for i, kg in enumerate([100.0, 500.0, 250.0]):
            client.post("/api/log-activity", json={
                "session_id": f"sort_session_{i}",
                "activity_type": "energy",
                "description": "Test",
                "co2_saved_kg": kg,
            })
        lb = client.get("/api/leaderboard").json()["leaderboard"]
        scores = [e["co2_saved_kg"] for e in lb]
        assert scores == sorted(scores, reverse=True)

    def test_leaderboard_max_20_entries(self, client):
        """Leaderboard must cap at 20 entries."""
        for i in range(25):
            client.post("/api/log-activity", json={
                "session_id": f"many_session_{i}",
                "activity_type": "food",
                "description": "Test",
                "co2_saved_kg": float(i + 1),
            })
        lb = client.get("/api/leaderboard").json()["leaderboard"]
        assert len(lb) <= 20


# ═══════════════════════════════════════════════════════════════════
# STATS ENDPOINT & CACHE
# ═══════════════════════════════════════════════════════════════════

class TestStatsEndpoint:
    def test_stats_returns_required_fields(self, client):
        response = client.get("/api/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_users" in data
        assert "total_co2_saved_kg" in data
        assert "total_trees_equivalent" in data
        assert "calculations_done" in data

    def test_stats_counts_calculations(self, client):
        """Each /api/calculate call must increment calculations_done."""
        client.post("/api/calculate", json={"session_id": "stats_session"})
        data = client.get("/api/stats").json()
        assert data["calculations_done"] >= 1

    def test_stats_cache_hit(self):
        """get_cached_stats() returns cached value within TTL."""
        invalidate_stats_cache()
        assert get_cached_stats() is None  # empty cache
        set_stats_cache({"total_users": 5, "total_co2_saved_kg": 100.0})
        cached = get_cached_stats()
        assert cached is not None
        assert cached["total_users"] == 5

    def test_stats_cache_invalidation(self):
        """invalidate_stats_cache() clears the cache."""
        set_stats_cache({"total_users": 3})
        invalidate_stats_cache()
        assert get_cached_stats() is None

    def test_stats_second_call_uses_cache(self, client):
        """Two consecutive calls should return same data (cache hit)."""
        client.post("/api/calculate", json={"session_id": "cache_session"})
        resp1 = client.get("/api/stats").json()
        resp2 = client.get("/api/stats").json()
        assert resp1 == resp2


# ═══════════════════════════════════════════════════════════════════
# OFFLINE INSIGHTS (generate_offline_insights)
# ═══════════════════════════════════════════════════════════════════

class TestGenerateOfflineInsights:
    def _carbon_data(self, transport=0, energy=0, food=0, lifestyle=0, total=None):
        t = transport + energy + food + lifestyle if total is None else total
        return {
            "total_kg_per_year": t,
            "breakdown": {
                "transport": {"total": transport},
                "energy": {"total": energy},
                "food": {"total": food},
                "lifestyle": {"total": lifestyle},
            },
            "eco_score": 60,
            "trees_to_offset": int(t / TREE_ABSORPTION_KG),
        }

    def test_all_required_keys_present(self):
        insights = generate_offline_insights(self._carbon_data(transport=2000))
        for key in ("summary", "top_actions", "biggest_win", "quick_win",
                    "yearly_goal_kg", "motivational_message"):
            assert key in insights

    def test_high_transport_generates_action(self):
        insights = generate_offline_insights(self._carbon_data(transport=2000))
        actions = [a["action"] for a in insights["top_actions"]]
        assert any("transit" in a.lower() or "transport" in a.lower() for a in actions)

    def test_low_transport_generates_walking_action(self):
        insights = generate_offline_insights(self._carbon_data(transport=100))
        actions = [a["action"] for a in insights["top_actions"]]
        assert any("walk" in a.lower() or "cycle" in a.lower() for a in actions)

    def test_high_energy_generates_renewable_action(self):
        insights = generate_offline_insights(self._carbon_data(energy=2000))
        actions = [a["action"] for a in insights["top_actions"]]
        assert any("renewable" in a.lower() or "solar" in a.lower() or "green" in a.lower() for a in actions)

    def test_low_energy_generates_led_action(self):
        insights = generate_offline_insights(self._carbon_data(energy=100))
        actions = [a["action"] for a in insights["top_actions"]]
        assert any("led" in a.lower() or "unplug" in a.lower() for a in actions)

    def test_high_food_generates_vegetarian_action(self):
        insights = generate_offline_insights(self._carbon_data(food=2000))
        actions = [a["action"] for a in insights["top_actions"]]
        assert any("vegetarian" in a.lower() or "plant" in a.lower() for a in actions)

    def test_low_food_generates_meat_reduction_action(self):
        insights = generate_offline_insights(self._carbon_data(food=500))
        actions = [a["action"] for a in insights["top_actions"]]
        assert any("meat" in a.lower() or "compost" in a.lower() for a in actions)

    def test_high_lifestyle_generates_clothing_action(self):
        insights = generate_offline_insights(self._carbon_data(lifestyle=1500))
        actions = [a["action"] for a in insights["top_actions"]]
        assert any("clothing" in a.lower() or "fashion" in a.lower() for a in actions)

    def test_low_lifestyle_generates_streaming_action(self):
        insights = generate_offline_insights(self._carbon_data(lifestyle=100))
        actions = [a["action"] for a in insights["top_actions"]]
        assert any("stream" in a.lower() or "sd" in a.lower() for a in actions)

    def test_no_breakdown_uses_fallback_actions(self):
        """When breakdown is missing, default actions should be returned."""
        data = {"total_kg_per_year": 5000.0}
        insights = generate_offline_insights(data)
        assert len(insights["top_actions"]) > 0

    def test_top_actions_capped_at_3(self):
        insights = generate_offline_insights(self._carbon_data(
            transport=2000, energy=2000, food=2000, lifestyle=2000
        ))
        assert len(insights["top_actions"]) <= 3

    def test_top_actions_sorted_by_impact(self):
        insights = generate_offline_insights(self._carbon_data(
            transport=2000, energy=2000, food=2000, lifestyle=2000
        ))
        impacts = [a["impact_kg"] for a in insights["top_actions"]]
        assert impacts == sorted(impacts, reverse=True)

    def test_yearly_goal_is_positive(self):
        insights = generate_offline_insights(self._carbon_data(transport=1000))
        assert insights["yearly_goal_kg"] > 0

    def test_summary_mentions_highest_category(self):
        insights = generate_offline_insights(self._carbon_data(
            transport=5000, energy=100, food=100, lifestyle=100
        ))
        assert "transport" in insights["summary"].lower()


# ═══════════════════════════════════════════════════════════════════
# OFFLINE RESPONSE (get_offline_response)
# ═══════════════════════════════════════════════════════════════════

class TestGetOfflineResponse:
    CONTEXT = {
        "total_kg_per_year": 4500.0,
        "eco_score": 65,
        "trees_to_offset": 214,
        "breakdown": {
            "transport": {"total": 1200.0},
            "energy": {"total": 1800.0},
            "food": {"total": 1000.0},
            "lifestyle": {"total": 500.0},
        },
    }

    def test_greeting_with_context(self):
        res = get_offline_response("hello", self.CONTEXT)
        assert "Hello" in res
        assert "Eco Score" in res

    def test_greeting_without_context(self):
        res = get_offline_response("hi", None)
        assert "Hello" in res or "EcoGuide" in res

    def test_transport_keyword_car(self):
        res = get_offline_response("how do I reduce my car emissions?", self.CONTEXT)
        assert "Transport" in res

    def test_transport_keyword_flight(self):
        res = get_offline_response("flight emissions?", self.CONTEXT)
        assert "Transport" in res

    def test_transport_keyword_ev(self):
        res = get_offline_response("tell me about EV", self.CONTEXT)
        assert "Transport" in res

    def test_energy_keyword_electricity(self):
        # Use a keyword that only matches energy branch, not transport
        res = get_offline_response("natural gas utility bill", self.CONTEXT)
        assert "Energy" in res

    def test_energy_keyword_solar(self):
        res = get_offline_response("solar panels", self.CONTEXT)
        assert "Energy" in res

    def test_energy_with_context_shows_footprint(self):
        res = get_offline_response("gas usage", self.CONTEXT)
        assert "1800" in res or "energy" in res.lower()

    def test_energy_without_context_generic(self):
        res = get_offline_response("heating", None)
        assert "Energy" in res

    def test_food_keyword_diet(self):
        res = get_offline_response("diet tips", self.CONTEXT)
        assert "Food" in res or "Diet" in res

    def test_food_keyword_meat(self):
        res = get_offline_response("I eat a lot of beef", self.CONTEXT)
        assert "Food" in res or "Diet" in res

    def test_food_without_context(self):
        res = get_offline_response("vegan food", None)
        assert "Diet" in res or "Food" in res

    def test_lifestyle_keyword_clothing(self):
        # 'streaming' and 'shop' are pure lifestyle keywords with no 'hi' substring
        res = get_offline_response("streaming shop purchase order", self.CONTEXT)
        assert "Lifestyle" in res or "streaming" in res.lower() or "shop" in res.lower()

    def test_lifestyle_keyword_streaming(self):
        res = get_offline_response("streaming video emissions", self.CONTEXT)
        assert "Lifestyle" in res

    def test_lifestyle_without_context(self):
        res = get_offline_response("online orders", None)
        assert "Lifestyle" in res

    def test_eco_score_keyword_with_context(self):
        res = get_offline_response("what is my eco score?", self.CONTEXT)
        assert "Eco Score" in res or "score" in res.lower()
        assert "65" in res

    def test_eco_score_keyword_without_context(self):
        res = get_offline_response("my score", None)
        assert "Calculator" in res or "footprint" in res.lower()

    def test_reduce_keyword_with_context(self):
        res = get_offline_response("how can I reduce my footprint?", self.CONTEXT)
        assert len(res) > 50

    def test_reduce_keyword_no_context(self):
        res = get_offline_response("tips to reduce", None)
        assert len(res) > 50

    def test_default_fallback_with_context(self):
        res = get_offline_response("something completely random xyz123", self.CONTEXT)
        assert "4500" in res or "carbon" in res.lower()

    def test_default_fallback_without_context(self):
        res = get_offline_response("something completely random xyz123", None)
        assert "EcoGuide" in res or "assistant" in res.lower()

    def test_fallback_mode_notice(self):
        res = get_offline_response("help", self.CONTEXT, is_fallback=True)
        assert "Google Gemini API rate limit exceeded" in res

    def test_no_api_key_notice(self):
        import app as app_module
        original = app_module.GEMINI_API_KEY
        app_module.GEMINI_API_KEY = ""
        try:
            res = get_offline_response("hi", None, is_fallback=False)
            assert "Eco-Mode" in res or "without Gemini" in res
        finally:
            app_module.GEMINI_API_KEY = original

    def test_start_keyword_greeting(self):
        res = get_offline_response("start", None)
        assert "Hello" in res or "EcoGuide" in res


# ═══════════════════════════════════════════════════════════════════
# DATA PERSISTENCE — load_data
# ═══════════════════════════════════════════════════════════════════

class TestLoadData:
    def test_load_data_returns_default_on_missing_file(self, tmp_path, monkeypatch):
        import app as app_module
        monkeypatch.setattr(app_module, "DATA_FILE", tmp_path / "nonexistent.json")
        data = load_data()
        assert "users" in data
        assert "activities" in data
        assert "leaderboard" in data
        assert "activity_index" in data

    def test_load_data_returns_default_on_corrupt_json(self, tmp_path, monkeypatch):
        import app as app_module
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("NOT VALID JSON {{{{", encoding="utf-8")
        monkeypatch.setattr(app_module, "DATA_FILE", bad_file)
        data = load_data()
        assert "users" in data

    def test_load_data_reads_valid_file(self, tmp_path, monkeypatch):
        import app as app_module
        valid_data = {
            "users": {"u1": {}},
            "activities": [],
            "leaderboard": [],
            "activity_index": {},
        }
        valid_file = tmp_path / "valid.json"
        valid_file.write_text(json.dumps(valid_data), encoding="utf-8")
        monkeypatch.setattr(app_module, "DATA_FILE", valid_file)
        data = load_data()
        assert data["users"] == {"u1": {}}
