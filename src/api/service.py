"""Owner: Duy. What the API serves: one agent recommendation from already-trained models.

Loads the saved models once, then runs the same Data -> Modeling ->
Optimization -> Manager pipeline as `scripts.run_recommendation` for each
request. Nothing is trained or re-scored here.
"""

from dataclasses import dataclass
from typing import Mapping
import uuid

from src.agents.reasoning import Reasoner
from src.common.config import DEMO_LATITUDE_DEG, DEMO_LONGITUDE_DEG, SimulationConfig
from src.common.schema import FrontendData, WeatherFeatures
from src.common.tool_contracts import WeatherTools
from src.models.advanced import lstm
from src.models.artifacts import LoadedModels
from src.models.data_loader import DatasetError, hourly_weather, load_dataset_split
from src.service.live_data_agent import current_hour_request, recommend_live
from src.service.recommendation_service import recommend_for_recorded_hour


CONTROL_TARGET_ID = "row-001"
# A "daylight" scenario needs the sun clearly up, so its predictions are not all near zero.
DAYLIGHT_MIN_ELEVATION_DEG = 10.0
# The weather a caller is shown beside each scenario, so they can see why it was picked.
SCENARIO_WEATHER_FIELDS = ("temperature_c", "cloud_cover_pct", "wind_speed_kmh", "wind_gust_kmh", "ghi_wm2", "sun_elevation_deg")


class UnknownHour(LookupError):
    """The requested recorded hour is not in the test window."""


class RecordedHoursUnavailable(RuntimeError):
    """The dataset the models were trained on is not present, so no recorded hour can be replayed."""


@dataclass(frozen=True)
class Scenario:
    """One recorded hour picked out by a rule over the data, never by hand."""

    id: str
    label: str
    timestamp: str


@dataclass(frozen=True)
class RecordedHours:
    """The test-window hours a recorded request may replay."""

    by_timestamp: Mapping[str, WeatherFeatures]
    default_timestamp: str
    first: str
    last: str
    # Hours whose gust is not below the wind speed; the dashboard's schema rejects the others.
    displayable: tuple[str, ...] = ()
    scenarios: tuple[Scenario, ...] = ()

    @property
    def days(self) -> list[str]:
        """Every UTC date that has at least one displayable hour, in order."""
        return sorted({timestamp[:10] for timestamp in self.displayable})

    def timestamps_on(self, day: str) -> list[str]:
        return [timestamp for timestamp in self.displayable if timestamp.startswith(day)]


def load_recorded_hours(models: LoadedModels) -> RecordedHours | None:
    """Test-window hours of the models' dataset, or None when that dataset is not on this machine."""
    try:
        hours = hourly_weather(load_dataset_split(models.source).test)
    except (DatasetError, ValueError, OSError):
        return None
    if not hours:
        return None
    # The dashboard rejects a gust below the wind speed, so the default avoids such hours when it can.
    consistent = [hour for hour in hours if hour["wind_gust_kmh"] >= hour["wind_speed_kmh"]] or hours
    default = max(consistent, key=lambda hour: hour["ghi_wm2"])
    return RecordedHours(
        {hour["timestamp"]: hour for hour in hours}, default["timestamp"], hours[0]["timestamp"], hours[-1]["timestamp"],
        displayable=tuple(hour["timestamp"] for hour in consistent), scenarios=pick_scenarios(consistent),
    )


def pick_scenarios(hours: list[WeatherFeatures]) -> tuple[Scenario, ...]:
    """Named hours chosen by fixed rules; a rule that matches no hour yields no scenario. Ties go to the earliest hour."""
    daylight = [hour for hour in hours if hour["sun_elevation_deg"] >= DAYLIGHT_MIN_ELEVATION_DEG]
    night = [hour for hour in hours if hour["sun_elevation_deg"] < 0]
    picks = [
        ("sunniest", "Sunniest hour", max(hours, key=lambda hour: hour["ghi_wm2"]) if hours else None),
        ("overcast", "Most overcast daylight hour", max(daylight, key=lambda hour: hour["cloud_cover_pct"]) if daylight else None),
        ("windiest", "Strongest wind gusts", max(hours, key=lambda hour: hour["wind_gust_kmh"]) if hours else None),
        ("night", "Night", night[0] if night else None),
    ]
    return tuple(Scenario(identifier, label, hour["timestamp"]) for identifier, label, hour in picks if hour is not None)


class InferenceService:
    def __init__(
        self, models: LoadedModels, hours: RecordedHours | None, config: SimulationConfig, *,
        reasoner: Reasoner | None = None, weather_tools: WeatherTools | None = None,
    ) -> None:
        self.models, self.hours, self.config = models, hours, config
        self._reasoner, self._weather_tools = reasoner, weather_tools

    @property
    def can_explain(self) -> bool:
        return self._reasoner is not None

    def recommend_recorded(self, *, timestamp: str | None, current_angle_deg: float, explain: bool) -> FrontendData:
        """Replay one recorded test-window hour with the row at the given angle."""
        if self.hours is None:
            raise RecordedHoursUnavailable(f"Dataset {self.models.info.dataset_name} is not available on this server")
        hour = self.hours.by_timestamp.get(timestamp or self.hours.default_timestamp)
        if hour is None:
            raise UnknownHour(f"No recorded hour {timestamp!r}; hours run from {self.hours.first} to {self.hours.last}")
        return recommend_for_recorded_hour(
            self.models.source, {**hour, "panel_angle_deg": current_angle_deg}, self.models.tools, self.config,
            control_target_id=CONTROL_TARGET_ID, run_id=_run_id(), reasoner=self._reasoner if explain else None,
        )

    def recommend_live(self, *, current_angle_deg: float, explain: bool) -> FrontendData:
        """Fetch the current hour's forecast for the demo site and decide on it.

        The LSTM is reported unavailable here: its stored validation metrics were
        measured with look-back weather history, which a single live fetch does not provide.
        """
        tools = self.models.tools.without(lstm.MODEL_NAME, implementation=lstm.IMPLEMENTATION)
        return recommend_live(
            self.models.source, current_hour_request(DEMO_LATITUDE_DEG, DEMO_LONGITUDE_DEG), tools, self.config,
            current_angle_deg=current_angle_deg, control_target_id=CONTROL_TARGET_ID, run_id=_run_id(),
            tools=self._weather_tools, reasoner=self._reasoner if explain else None,
        )


def _run_id() -> str:
    return f"api-{uuid.uuid4().hex[:12]}"
