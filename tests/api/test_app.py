"""HTTP API: serves validated decisions from saved models, rejects bad input, and fails closed."""

import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.rate_limit import WINDOW_SECONDS, RateLimiter
from src.api.service import InferenceService, load_recorded_hours
from src.common.config import DEFAULT_CONFIG
from src.common.schema import validate_frontend_data
from src.common.tool_contracts import ToolError
from src.models.artifacts import ArtifactError, load_models, save_models
from src.models.data_loader import EXAMPLE_DATASET_SOURCE
from src.pipeline.transform import transform_weather
from src.pipeline.validate import validate_weather
from src.service.recommendation_service import build_metadata, build_modeling_tools


RAW_WEATHER = {
    "source": "test-provider", "forecast_issued_at": None, "temperature_c": 22.0, "cloud_cover_pct": 15.0, "precipitation_mm": 0.0,
    "wind_speed_kmh": 14.0, "wind_gust_kmh": 24.0, "ghi_wm2": 850.0, "dni_wm2": 750.0, "dhi_wm2": 201.0,
}


class EchoReasoner:
    def explain(self, agent: str, facts: str) -> str:
        return f"The {agent} agent reviewed its tool results."


class FakeWeatherTools:
    """A provider that answers for whatever hour is asked, with Luan's real validation and transform."""

    def fetch_weather(self, request):
        return {**RAW_WEATHER, "timestamp": request.interval_start, "fetched_at": request.interval_start}

    def validate_weather(self, weather, *, now, config):
        return validate_weather(weather, now=now, config=config)

    def calculate_solar_position(self, request):
        return {"sun_elevation_deg": 55.0, "sun_azimuth_deg": 180.0}

    def transform_weather(self, weather, solar, *, panel_angle_deg):
        return transform_weather(weather, solar, panel_angle_deg=panel_angle_deg)

    def load_cached_weather(self, request):
        return None

    def store_weather(self, request, weather):
        return None


SHARED: dict = {}


def setUpModule() -> None:
    """Train once on the example data, save, and load: every test serves from that saved set."""
    source = EXAMPLE_DATASET_SOURCE
    metadata = build_metadata(source, interval_start="2026-01-01T00:00:00Z", control_target_id="row-001", config=DEFAULT_CONFIG)
    directory = tempfile.TemporaryDirectory()
    unittest.addModuleCleanup(directory.cleanup)
    save_models(build_modeling_tools(source, metadata=metadata), source, Path(directory.name))
    SHARED["models"] = load_models(Path(directory.name))
    SHARED["hours"] = load_recorded_hours(SHARED["models"])
    # The failure-path tests make the server log errors on purpose.
    logging.disable(logging.CRITICAL)
    unittest.addModuleCleanup(logging.disable, logging.NOTSET)


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.models, cls.hours = SHARED["models"], SHARED["hours"]

    def client(self, *, reasoner=None, hours="default", rate_limit=0, external_rate_limit=0, loader=None, api_key="", **options) -> TestClient:
        service = InferenceService(self.models, self.hours if hours == "default" else hours, DEFAULT_CONFIG, reasoner=reasoner, weather_tools=FakeWeatherTools())
        app = create_app(loader or (lambda: service), rate_limit_per_minute=rate_limit, external_rate_limit_per_minute=external_rate_limit, cors_origins=["http://localhost:3000"], api_key=api_key)
        client = TestClient(app, **options)
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def assert_error(self, response, status: int, code: str) -> dict:
        self.assertEqual(response.status_code, status)
        self.assertEqual(set(response.json()), {"error"})
        self.assertEqual(response.json()["error"]["code"], code)
        return response.json()["error"]


class StatusEndpointsTest(ApiTestCase):
    def test_health_reports_the_process_is_up(self) -> None:
        self.assertEqual(self.client().get("/health").json(), {"status": "ok"})

    def test_ready_reports_what_the_server_can_do(self) -> None:
        response = self.client().get("/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ready", "recorded_hours_available": True, "explanations_available": False})

    def test_models_lists_the_stored_validation_metrics_and_the_selected_model(self) -> None:
        body = self.client().get("/models").json()
        available = [entry for entry in body["validation_comparison"] if entry["status"] != "UNAVAILABLE"]

        self.assertEqual(body["dataset"], {"name": "example_weather.csv", "dataset_kind": "MOCK", "label_source": "mock"})
        self.assertEqual(body["selected_model"], min(available, key=lambda entry: entry["rmse"])["model"])
        self.assertEqual({entry["model"] for entry in available}, {"boosting", "lstm"})

    def test_hours_describes_the_replayable_window(self) -> None:
        body = self.client().get("/hours").json()

        self.assertEqual(body["count"], len(self.hours.by_timestamp))
        self.assertIn(body["default"], self.hours.by_timestamp)
        self.assertLessEqual(body["first"], body["last"])

    def test_a_server_without_models_starts_and_says_why_it_is_not_ready(self) -> None:
        def no_models():
            raise ArtifactError("No saved models: run `python -m scripts.train_models` first")

        client = self.client(loader=no_models)

        self.assertEqual(client.get("/health").status_code, 200)
        for response in (client.get("/ready"), client.get("/models"), client.post("/recommendations", json={})):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "MODELS_UNAVAILABLE")
            self.assertIn("train_models", response.json()["error"]["message"])


class RecommendationTest(ApiTestCase):
    def test_default_request_returns_a_valid_dashboard_payload(self) -> None:
        response = self.client().post("/recommendations", json={})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        validate_frontend_data(payload)
        self.assertEqual(payload["metadata"]["interval_start"], self.hours.default_timestamp)
        self.assertEqual(payload["optimization"]["current_angle_deg"], 35.0)
        self.assertIn(payload["decision"]["action"], ("ROTATE", "HOLD", "STOW"))

    def test_request_chooses_the_hour_and_the_current_angle(self) -> None:
        timestamp = self.hours.default_timestamp

        payload = self.client().post("/recommendations", json={"timestamp": timestamp, "current_angle_deg": 60}).json()

        self.assertEqual((payload["metadata"]["interval_start"], payload["optimization"]["current_angle_deg"]), (timestamp, 60.0))
        self.assertIn(60.0, [entry["angle_deg"] for entry in payload["candidate_predictions"]])

    def test_same_request_gives_the_same_decision(self) -> None:
        client, body = self.client(), {"current_angle_deg": 60}

        first, second = client.post("/recommendations", json=body).json(), client.post("/recommendations", json=body).json()

        self.assertEqual((first["decision"], first["optimization"]), (second["decision"], second["optimization"]))

    def test_no_llm_text_unless_asked(self) -> None:
        payload = self.client(reasoner=EchoReasoner()).post("/recommendations", json={}).json()

        self.assertNotIn("llm_reasoning", [entry["action"] for entry in payload["agent_log"]])

    def test_explain_adds_llm_text_without_changing_the_decision(self) -> None:
        client = self.client(reasoner=EchoReasoner())
        plain = client.post("/recommendations", json={}).json()

        explained = client.post("/recommendations", json={"explain": True}).json()

        self.assertEqual([entry["agent"] for entry in explained["agent_log"] if entry["action"] == "llm_reasoning"], ["modeling", "optimization", "manager"])
        self.assertEqual((explained["decision"], explained["optimization"], explained["safety"]), (plain["decision"], plain["optimization"], plain["safety"]))

    def test_explain_without_a_configured_llm_is_refused(self) -> None:
        response = self.client().post("/recommendations", json={"explain": True})

        self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "EXPLANATIONS_UNAVAILABLE"))

    def test_live_request_uses_fetched_weather_and_holds_when_freshness_is_unknown(self) -> None:
        response = self.client().post("/recommendations", json={"mode": "live", "current_angle_deg": 40})

        payload = response.json()
        validate_frontend_data(payload)
        self.assertEqual(payload["data_agent"]["source"], "test-provider")
        self.assertEqual((payload["data_agent"]["status"], payload["decision"]["action"]), ("INVALID", "HOLD"))
        self.assertEqual({entry["model"]: entry["status"] for entry in payload["model_comparison"]}["lstm"], "UNAVAILABLE")
        self.assertEqual(payload["selected_model"], "boosting")

    def test_recorded_request_without_the_dataset_is_unavailable_but_live_still_works(self) -> None:
        client = self.client(hours=None)

        recorded, live = client.post("/recommendations", json={}), client.post("/recommendations", json={"mode": "live"})

        self.assertEqual((recorded.status_code, recorded.json()["error"]["code"]), (503, "RECORDED_HOURS_UNAVAILABLE"))
        self.assertEqual(client.get("/hours").status_code, 503)
        self.assertEqual(live.status_code, 200)


class RejectedRequestsTest(ApiTestCase):
    def test_unknown_hour_is_not_found(self) -> None:
        error = self.assert_error(self.client().post("/recommendations", json={"timestamp": "1999-01-01T00:00:00Z"}), 404, "UNKNOWN_HOUR")

        self.assertIn(self.hours.first, error["message"])

    def test_angle_outside_the_panel_range_is_rejected(self) -> None:
        for angle in (-1, 90.5, "steep"):
            error = self.assert_error(self.client().post("/recommendations", json={"current_angle_deg": angle}), 422, "INVALID_REQUEST")
            self.assertIn("current_angle_deg", error["message"])

    def test_unknown_field_is_rejected(self) -> None:
        self.assert_error(self.client().post("/recommendations", json={"target_angle_deg": 45}), 422, "INVALID_REQUEST")

    def test_unknown_mode_and_overlong_timestamp_are_rejected(self) -> None:
        self.assert_error(self.client().post("/recommendations", json={"mode": "forecast"}), 422, "INVALID_REQUEST")
        self.assert_error(self.client().post("/recommendations", json={"timestamp": "2" * 41}), 422, "INVALID_REQUEST")

    def test_body_that_is_not_json_is_rejected(self) -> None:
        self.assert_error(self.client().post("/recommendations", content=b"{not json", headers={"content-type": "application/json"}), 422, "INVALID_REQUEST")

    def test_unknown_route_uses_the_error_envelope(self) -> None:
        self.assert_error(self.client().get("/nowhere"), 404, "HTTP_ERROR")

    def test_unexpected_failure_reveals_no_internals(self) -> None:
        class ExplodingService(InferenceService):
            def recommend_recorded(self, **_kwargs):
                raise RuntimeError("secret path C:/models and a stack trace")

        service = ExplodingService(self.models, self.hours, DEFAULT_CONFIG)
        client = self.client(loader=lambda: service, raise_server_exceptions=False)

        error = self.assert_error(client.post("/recommendations", json={}), 500, "INTERNAL_ERROR")
        self.assertNotIn("secret", error["message"])

    def test_tool_failure_returns_no_partial_decision(self) -> None:
        class FailingService(InferenceService):
            def recommend_recorded(self, **_kwargs):
                raise ToolError("safety evaluation failed at /internal/path")

        error = self.assert_error(self.client(loader=lambda: FailingService(self.models, self.hours, DEFAULT_CONFIG)).post("/recommendations", json={}), 502, "RECOMMENDATION_FAILED")
        self.assertNotIn("internal", error["message"])


class RateLimitTest(ApiTestCase):
    def test_requests_over_the_limit_are_refused_with_a_retry_time(self) -> None:
        client = self.client(rate_limit=2)

        statuses = [client.post("/recommendations", json={}).status_code for _ in range(3)]
        refused = client.post("/recommendations", json={})

        self.assertEqual(statuses, [200, 200, 429])
        self.assertEqual(refused.json()["error"]["code"], "RATE_LIMITED")
        self.assertGreaterEqual(int(refused.headers["retry-after"]), 1)

    def test_live_and_explained_requests_have_their_own_lower_limit(self) -> None:
        client = self.client(reasoner=EchoReasoner(), rate_limit=0, external_rate_limit=1)

        first, second = client.post("/recommendations", json={"mode": "live"}), client.post("/recommendations", json={"explain": True})
        plain = client.post("/recommendations", json={})

        self.assertEqual((first.status_code, second.status_code, plain.status_code), (200, 429, 200))

    def test_status_endpoints_are_not_rate_limited(self) -> None:
        client = self.client(rate_limit=1)

        self.assertEqual([client.get("/health").status_code for _ in range(5)], [200] * 5)

    def test_window_slides_and_clients_are_counted_separately(self) -> None:
        now = [0.0]
        limiter = RateLimiter(2, clock=lambda: now[0])

        self.assertEqual([limiter.retry_after("a"), limiter.retry_after("a")], [None, None])
        self.assertAlmostEqual(limiter.retry_after("a"), WINDOW_SECONDS)
        self.assertIsNone(limiter.retry_after("b"))
        now[0] = WINDOW_SECONDS + 1
        self.assertIsNone(limiter.retry_after("a"))

    def test_zero_turns_the_limiter_off_and_negative_is_invalid(self) -> None:
        limiter = RateLimiter(0)

        self.assertEqual([limiter.retry_after("a") for _ in range(50)], [None] * 50)
        with self.assertRaises(ValueError):
            RateLimiter(-1)


class CorsTest(ApiTestCase):
    def test_only_configured_origins_are_allowed(self) -> None:
        client = self.client()

        allowed = client.get("/health", headers={"origin": "http://localhost:3000"})
        other = client.get("/health", headers={"origin": "https://evil.example"})

        self.assertEqual(allowed.headers.get("access-control-allow-origin"), "http://localhost:3000")
        self.assertNotIn("access-control-allow-origin", other.headers)


if __name__ == "__main__":
    unittest.main()


API_KEY = "test-key-0123456789-abcdefghij"


class ApiKeyTest(ApiTestCase):
    def test_protected_endpoints_refuse_a_request_with_no_key(self) -> None:
        client = self.client(api_key=API_KEY)

        for response in (client.get("/models"), client.get("/hours"), client.post("/recommendations", json={})):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "UNAUTHORIZED")

    def test_a_wrong_key_is_refused_and_the_real_key_is_not_echoed(self) -> None:
        response = self.client(api_key=API_KEY).post("/recommendations", json={}, headers={"X-API-Key": API_KEY[:-1] + "x"})

        self.assertEqual(response.status_code, 401)
        self.assertNotIn(API_KEY, response.text)

    def test_the_right_key_gets_a_decision(self) -> None:
        client = self.client(api_key=API_KEY)
        headers = {"X-API-Key": API_KEY}

        response = client.post("/recommendations", json={}, headers=headers)

        self.assertEqual(response.status_code, 200)
        validate_frontend_data(response.json())
        self.assertEqual(client.get("/models", headers=headers).status_code, 200)
        self.assertEqual(client.get("/hours", headers=headers).status_code, 200)

    def test_health_and_ready_stay_open_so_a_host_can_probe_them(self) -> None:
        client = self.client(api_key=API_KEY)

        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/ready").status_code, 200)

    def test_an_unauthorised_request_does_not_use_up_the_rate_limit(self) -> None:
        client = self.client(api_key=API_KEY, rate_limit=1)

        self.assertEqual(client.post("/recommendations", json={}).status_code, 401)
        self.assertEqual(client.post("/recommendations", json={}, headers={"X-API-Key": API_KEY}).status_code, 200)

    def test_with_no_key_configured_the_api_is_open(self) -> None:
        self.assertEqual(self.client().post("/recommendations", json={}).status_code, 200)

    def test_a_line_break_stored_after_the_key_does_not_lock_every_caller_out(self) -> None:
        client = self.client(api_key=API_KEY + "\n")

        self.assertEqual(client.post("/recommendations", json={}, headers={"X-API-Key": API_KEY}).status_code, 200)
        self.assertEqual(client.post("/recommendations", json={}).status_code, 401)

    def test_a_short_key_stops_the_server_from_starting(self) -> None:
        with self.assertRaisesRegex(ValueError, "SOLAR_API_KEY must be at least"):
            create_app(lambda: None, api_key="too-short")

    def test_a_browser_may_send_the_key_header(self) -> None:
        response = self.client(api_key=API_KEY).options("/recommendations", headers={
            "origin": "http://localhost:3000", "access-control-request-method": "POST", "access-control-request-headers": "x-api-key",
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn("x-api-key", response.headers["access-control-allow-headers"].lower())


class ScenarioEndpointsTest(ApiTestCase):
    def test_hours_lists_the_days_that_can_be_replayed(self) -> None:
        days = self.client().get("/hours").json()["days"]

        self.assertEqual(days, sorted(set(days)))
        self.assertEqual(days, sorted({timestamp[:10] for timestamp in self.hours.displayable}))

    def test_one_day_lists_only_its_displayable_hours(self) -> None:
        day = self.hours.default_timestamp[:10]

        body = self.client().get(f"/hours/{day}").json()

        self.assertEqual(body["day"], day)
        self.assertIn(self.hours.default_timestamp, body["timestamps"])
        for timestamp in body["timestamps"]:
            hour = self.hours.by_timestamp[timestamp]
            self.assertTrue(timestamp.startswith(day))
            self.assertGreaterEqual(hour["wind_gust_kmh"], hour["wind_speed_kmh"])

    def test_a_day_with_no_hours_is_not_found(self) -> None:
        self.assert_error(self.client().get("/hours/1999-01-01"), 404, "UNKNOWN_DAY")

    def test_a_malformed_day_is_rejected(self) -> None:
        self.assert_error(self.client().get("/hours/next-tuesday"), 422, "INVALID_REQUEST")

    def test_every_scenario_is_a_replayable_hour_that_matches_its_rule(self) -> None:
        scenarios = {scenario["id"]: scenario for scenario in self.client().get("/scenarios").json()["scenarios"]}
        displayable = [self.hours.by_timestamp[timestamp] for timestamp in self.hours.displayable]

        self.assertEqual(scenarios["sunniest"]["weather"]["ghi_wm2"], max(hour["ghi_wm2"] for hour in displayable))
        self.assertEqual(scenarios["windiest"]["weather"]["wind_gust_kmh"], max(hour["wind_gust_kmh"] for hour in displayable))
        self.assertLess(scenarios["night"]["weather"]["sun_elevation_deg"], 0)
        self.assertGreaterEqual(scenarios["overcast"]["weather"]["sun_elevation_deg"], 10)
        for scenario in scenarios.values():
            self.assertIn(scenario["timestamp"], self.hours.displayable)
            self.assertEqual(scenario["weather"]["ghi_wm2"], self.hours.by_timestamp[scenario["timestamp"]]["ghi_wm2"])

    def test_a_scenario_can_be_replayed(self) -> None:
        client = self.client()
        scenario = client.get("/scenarios").json()["scenarios"][0]

        response = client.post("/recommendations", json={"timestamp": scenario["timestamp"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["metadata"]["interval_start"], scenario["timestamp"])

    def test_a_rule_that_matches_no_hour_yields_no_scenario(self) -> None:
        from src.api.service import pick_scenarios

        always_day = [{**hour, "sun_elevation_deg": 40.0} for hour in list(self.hours.by_timestamp.values())[:5]]

        self.assertNotIn("night", [scenario.id for scenario in pick_scenarios(always_day)])
        self.assertEqual(pick_scenarios([]), ())

    def test_scenarios_and_days_need_the_key_and_the_dataset(self) -> None:
        keyed = self.client(api_key=API_KEY)
        without_dataset = self.client(hours=None)

        self.assertEqual(keyed.get("/scenarios").status_code, 401)
        self.assertEqual(keyed.get("/hours/2025-01-15").status_code, 401)
        self.assert_error(without_dataset.get("/scenarios"), 503, "RECORDED_HOURS_UNAVAILABLE")
        self.assert_error(without_dataset.get("/hours/2025-01-15"), 503, "RECORDED_HOURS_UNAVAILABLE")
