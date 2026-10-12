"""Owner: Duy. HTTP API that serves agent recommendations from saved models.

    python -m scripts.train_models           # once
    uvicorn src.api.app:app --port 8000

Endpoints:
    GET  /health            process is up
    GET  /ready             models are loaded (503 until they are)
    GET  /models            the saved models and their validation metrics
    GET  /hours             the recorded hours that can be replayed, and the days they fall on
    GET  /hours/{day}       the replayable hours of one day
    GET  /scenarios         named recorded hours picked by rule (sunniest, most overcast, windiest, night)
    POST /recommendations   one decision, as the same FrontendData the dashboard reads

When SOLAR_API_KEY is set, every endpoint except /health and /ready needs that
key in an X-API-Key header. With no key set the API is open, for local use.

The decision never waits on an LLM unless the request asks for explanations.
Requests that reach an outside service (live weather, LLM) have their own,
much lower rate limit. Errors use one envelope: {"error": {"code", "message"}}.
"""

from contextlib import asynccontextmanager
import hmac
import logging
import os
from pathlib import Path
from typing import Callable, Literal

from fastapi import Depends, FastAPI, HTTPException, Path as PathParameter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
import torch

from src.agents.reasoning import load_reasoner
from src.api.rate_limit import RateLimiter
from src.api.service import SCENARIO_WEATHER_FIELDS, InferenceService, RecordedHours, RecordedHoursUnavailable, UnknownHour, load_recorded_hours
from src.common.config import DEFAULT_CONFIG
from src.common.schema import ContractError
from src.common.tool_contracts import ToolError
from src.models.artifacts import DEFAULT_ARTIFACT_DIR, load_models


LOGGER = logging.getLogger(__name__)
MODEL_DIR_VARIABLE = "SOLAR_MODEL_DIR"
RATE_LIMIT_VARIABLE = "SOLAR_API_RATE_LIMIT_PER_MINUTE"
EXTERNAL_RATE_LIMIT_VARIABLE = "SOLAR_API_EXTERNAL_RATE_LIMIT_PER_MINUTE"
CORS_ORIGINS_VARIABLE = "SOLAR_API_CORS_ORIGINS"
API_KEY_VARIABLE = "SOLAR_API_KEY"
API_KEY_HEADER = "X-API-Key"
# A shorter key is refused at startup, so a guessable one is never accepted by mistake.
MIN_API_KEY_LENGTH = 24
DEFAULT_RATE_LIMIT_PER_MINUTE = 120
# Live weather and LLM explanations call outside services, one of which costs money per call.
DEFAULT_EXTERNAL_RATE_LIMIT_PER_MINUTE = 6
DEFAULT_CORS_ORIGINS = "http://localhost:3000"
DEFAULT_ANGLE_DEG = 35.0

ServiceLoader = Callable[[], InferenceService]


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["recorded", "live"] = Field("recorded", description="Replay a recorded test-window hour, or fetch the current forecast for the demo site.")
    timestamp: str | None = Field(None, max_length=40, description="Recorded hour to replay, as listed by /hours. Defaults to the sunniest hour. Not used in live mode.")
    current_angle_deg: float = Field(DEFAULT_ANGLE_DEG, ge=DEFAULT_CONFIG.min_angle_deg, le=DEFAULT_CONFIG.max_angle_deg, description="Current tilt of the controlled row.")
    explain: bool = Field(False, description="Add LLM-worded explanations to the agent log. Slower, and needs an API key on the server.")


def load_default_service() -> InferenceService:
    """Saved models from SOLAR_MODEL_DIR (default models/artifacts), their dataset's test hours, and the LLM if a key is set."""
    models = load_models(Path(os.environ.get(MODEL_DIR_VARIABLE) or DEFAULT_ARTIFACT_DIR))
    # Predictions are tiny; one thread each keeps concurrent requests from fighting over cores.
    torch.set_num_threads(1)
    return InferenceService(models, load_recorded_hours(models), DEFAULT_CONFIG, reasoner=load_reasoner())


def _limit(variable: str, default: int) -> int:
    return int(os.environ.get(variable) or default)


def _error(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status, headers=headers)


def create_app(
    service_loader: ServiceLoader = load_default_service, *,
    rate_limit_per_minute: int | None = None, external_rate_limit_per_minute: int | None = None, cors_origins: list[str] | None = None,
    api_key: str | None = None,
) -> FastAPI:
    required_key = (os.environ.get(API_KEY_VARIABLE) or "") if api_key is None else api_key
    if required_key and len(required_key) < MIN_API_KEY_LENGTH:
        raise ValueError(f"{API_KEY_VARIABLE} must be at least {MIN_API_KEY_LENGTH} characters")
    limiter = RateLimiter(_limit(RATE_LIMIT_VARIABLE, DEFAULT_RATE_LIMIT_PER_MINUTE) if rate_limit_per_minute is None else rate_limit_per_minute)
    external_limiter = RateLimiter(_limit(EXTERNAL_RATE_LIMIT_VARIABLE, DEFAULT_EXTERNAL_RATE_LIMIT_PER_MINUTE) if external_rate_limit_per_minute is None else external_rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not required_key:
            LOGGER.warning("%s is not set: anyone who can reach this API can use it", API_KEY_VARIABLE)
        # A server with no usable models still starts, so /health and /ready can say what is wrong.
        try:
            app.state.service, app.state.unavailable_reason = service_loader(), None
        except ToolError as exc:
            LOGGER.error("Models could not be loaded: %s", exc)
            app.state.service, app.state.unavailable_reason = None, str(exc)
        yield

    app = FastAPI(title="Solar Farm AI recommendation API", version="0.1.0", lifespan=lifespan, description=__doc__)
    origins = cors_origins if cors_origins is not None else [origin.strip() for origin in (os.environ.get(CORS_ORIGINS_VARIABLE) or DEFAULT_CORS_ORIGINS).split(",") if origin.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["content-type", API_KEY_HEADER])

    # Starlette's own errors (unknown route, wrong method) are included, so every error uses the envelope.
    @app.exception_handler(StarletteHTTPException)
    async def http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
        return _error(exc.status_code, detail["code"], detail["message"], exc.headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = "; ".join(f"{'.'.join(str(part) for part in error['loc'] if part != 'body') or 'body'}: {error['msg']}" for error in exc.errors())
        return _error(422, "INVALID_REQUEST", problems)

    @app.exception_handler(Exception)
    async def unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # Details go to the server log only; the caller gets no internals.
        LOGGER.exception("Unhandled error while serving a request", exc_info=exc)
        return _error(500, "INTERNAL_ERROR", "The server could not complete the request.")

    def fail(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> HTTPException:
        return HTTPException(status, {"code": code, "message": message}, headers)

    key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False, description=f"Required when the server has {API_KEY_VARIABLE} set.")

    def require_key(supplied: str | None = Depends(key_header)) -> None:
        # Compared in constant time, so response timing does not reveal how much of a guess was right.
        if required_key and not (supplied and hmac.compare_digest(supplied.encode(), required_key.encode())):
            raise fail(401, "UNAUTHORIZED", f"A valid {API_KEY_HEADER} header is required")

    protected = [Depends(require_key)]

    def service_or_503(request: Request) -> InferenceService:
        service = request.app.state.service
        if service is None:
            raise fail(503, "MODELS_UNAVAILABLE", request.app.state.unavailable_reason or "Models are not loaded")
        return service

    def check_rate(request: Request, limiter_to_use: RateLimiter, what: str) -> None:
        # The connecting address is used as-is; behind a proxy, configure the proxy to rate-limit or pass a trusted address.
        wait = limiter_to_use.retry_after(request.client.host if request.client else "unknown")
        if wait is not None:
            raise fail(429, "RATE_LIMITED", f"Too many {what}; try again in {wait:.0f} s", {"Retry-After": str(max(1, round(wait)))})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(request: Request) -> dict:
        service = service_or_503(request)
        return {"status": "ready", "recorded_hours_available": service.hours is not None, "explanations_available": service.can_explain}

    @app.get("/models", dependencies=protected)
    def models(request: Request) -> dict:
        service = service_or_503(request)
        comparison = service.models.tools.evaluate_models()
        info = service.models.info
        return {
            "trained_at": info.created_at,
            "dataset": {"name": info.dataset_name, "dataset_kind": info.dataset_kind, "label_source": info.label_source},
            "selected_model": service.models.tools.select_best_model(comparison),
            "validation_comparison": comparison,
        }

    def hours_or_503(request: Request) -> RecordedHours:
        service = service_or_503(request)
        if service.hours is None:
            raise fail(503, "RECORDED_HOURS_UNAVAILABLE", f"Dataset {service.models.info.dataset_name} is not available on this server")
        return service.hours

    @app.get("/hours", dependencies=protected)
    def hours(request: Request) -> dict:
        recorded = hours_or_503(request)
        return {"count": len(recorded.by_timestamp), "first": recorded.first, "last": recorded.last, "default": recorded.default_timestamp, "days": recorded.days}

    @app.get("/hours/{day}", dependencies=protected)
    def hours_on(request: Request, day: str = PathParameter(pattern=r"^\d{4}-\d{2}-\d{2}$", description="A UTC date, YYYY-MM-DD.")) -> dict:
        timestamps = hours_or_503(request).timestamps_on(day)
        if not timestamps:
            raise fail(404, "UNKNOWN_DAY", f"No recorded hours on {day}")
        return {"day": day, "timestamps": timestamps}

    @app.get("/scenarios", dependencies=protected)
    def scenarios(request: Request) -> dict:
        recorded = hours_or_503(request)
        return {"scenarios": [
            {"id": scenario.id, "label": scenario.label, "timestamp": scenario.timestamp,
             "weather": {field: recorded.by_timestamp[scenario.timestamp][field] for field in SCENARIO_WEATHER_FIELDS}}
            for scenario in recorded.scenarios
        ]}

    @app.post("/recommendations", dependencies=protected)
    def recommend(body: RecommendationRequest, request: Request) -> dict:
        service = service_or_503(request)
        check_rate(request, limiter, "requests")
        if body.explain and not service.can_explain:
            raise fail(503, "EXPLANATIONS_UNAVAILABLE", "No LLM API key is configured on this server; send explain=false")
        if body.explain or body.mode == "live":
            check_rate(request, external_limiter, "live or explained requests")
        try:
            if body.mode == "live":
                return service.recommend_live(current_angle_deg=body.current_angle_deg, explain=body.explain)
            return service.recommend_recorded(timestamp=body.timestamp, current_angle_deg=body.current_angle_deg, explain=body.explain)
        except UnknownHour as exc:
            raise fail(404, "UNKNOWN_HOUR", str(exc)) from exc
        except RecordedHoursUnavailable as exc:
            raise fail(503, "RECORDED_HOURS_UNAVAILABLE", str(exc)) from exc
        except (ToolError, ContractError) as exc:
            # No safe decision could be produced; nothing partial is returned.
            LOGGER.error("Recommendation failed: %s", exc)
            raise fail(502, "RECOMMENDATION_FAILED", "The agents could not produce a decision for this request.") from exc

    return app


app = create_app()
