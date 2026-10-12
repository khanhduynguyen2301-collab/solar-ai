"""Owner: Duy. LSTM adapter (PyTorch) behind the stable model ID `lstm`.

Each prediction reads a short window of hourly steps that ENDS at the hour being
predicted: the weather of up to `sequence_length - 1` directly preceding hours,
then the current hour. Every step carries the panel angle being evaluated, so a
window answers "what if the row sat at this angle through these hours". No hour
after the prediction hour is ever read, and the label `actual_kwh` is never an input.

Preceding hours that are not available (start of a data block, a gap, or a live
forecast with no stored history) are left as padding steps, marked by a validity
flag the network sees. Missing history is never filled with invented weather.
"""

import copy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import pickle
from typing import Mapping, Sequence

import numpy as np
import torch
from torch import nn

from src.common.schema import CandidatePrediction, Metadata, WeatherFeatures, WeatherRow
from src.common.tool_contracts import ToolError
from src.models.advanced.features import MODEL_FEATURE_NAMES, PANEL_ANGLE_COLUMN, at_candidate_angles, feature_matrix, label_vector


MODEL_NAME = "lstm"
IMPLEMENTATION = "pytorch"
STEP = timedelta(hours=1)
MIN_FEATURE_STD = 1e-6
# Model features plus one validity flag per step (1 = real hour, 0 = padding).
STEP_WIDTH = len(MODEL_FEATURE_NAMES) + 1

HourlyHistory = Mapping[datetime, WeatherFeatures]


@dataclass(frozen=True)
class LstmConfig:
    """Training hyperparameters; early stopping watches the tail of the train window."""

    sequence_length: int = 6
    hidden_size: int = 48
    max_epochs: int = 80
    patience: int = 10
    batch_size: int = 64
    learning_rate: float = 3e-3
    seed: int = 20261004
    # Share of training windows whose look-back is cut to a random shorter length, so the
    # network also learns to predict daytime hours with little or no stored history.
    history_dropout: float = 0.3


DEFAULT_LSTM_CONFIG = LstmConfig()


@dataclass(frozen=True)
class FeatureScaler:
    """Standardization fitted on the fit rows only; later rows never influence it."""

    mean: np.ndarray
    std: np.ndarray
    target_scale: float

    def scale(self, features: np.ndarray) -> np.ndarray:
        return (features - self.mean) / self.std


@dataclass(frozen=True)
class Windows:
    """`features[k]` holds the unscaled steps ending at target k; `valid[k]` flags its real steps."""

    features: np.ndarray
    valid: np.ndarray


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hourly_history(rows: Sequence[WeatherFeatures]) -> dict[datetime, WeatherFeatures]:
    """Weather by hour for look-back; rows sharing an hour differ only in panel angle."""
    history: dict[datetime, WeatherFeatures] = {}
    for row in rows:
        history.setdefault(parse_timestamp(row["timestamp"]), row)
    return history


def build_windows(targets: Sequence[WeatherFeatures], history: HourlyHistory, sequence_length: int) -> Windows:
    """One window per target: its directly preceding hours from `history`, then the target itself.

    Only hours strictly before a target are read, and only while they are
    consecutive; earlier steps stay as padding. History steps take the target's
    panel angle. Labels present on any row are ignored.
    """
    if sequence_length < 1:
        raise ValueError("sequence_length must be at least 1")
    steps: list[WeatherFeatures] = []
    valid = np.zeros((len(targets), sequence_length), dtype=bool)
    for index, target in enumerate(targets):
        try:
            instant = parse_timestamp(target["timestamp"])
        except (KeyError, ValueError, AttributeError) as exc:
            raise ToolError(f"LSTM cannot parse the prediction timestamp: {exc!r}") from exc
        preceding = _preceding_hours(instant, history, sequence_length - 1)
        padding = sequence_length - 1 - len(preceding)
        angle = target[PANEL_ANGLE_COLUMN]
        # Padding slots reuse the target row only to keep the matrix rectangular; they are zeroed by `valid`.
        steps.extend([target] * padding)
        steps.extend({**hour, PANEL_ANGLE_COLUMN: angle} for hour in preceding)
        steps.append(target)
        valid[index, padding:] = True
    features = feature_matrix(steps).reshape(len(targets), sequence_length, len(MODEL_FEATURE_NAMES))
    return Windows(features=features, valid=valid)


def _preceding_hours(instant: datetime, history: HourlyHistory, limit: int) -> list[WeatherFeatures]:
    """Up to `limit` consecutive hours ending just before `instant`, oldest first."""
    preceding: list[WeatherFeatures] = []
    for back in range(1, limit + 1):
        hour = history.get(instant - STEP * back)
        if hour is None:
            break
        preceding.append(hour)
    return preceding[::-1]


class _Network(nn.Module):
    def __init__(self, step_width: int, hidden_size: int) -> None:
        super().__init__()
        self.lstm = nn.LSTM(step_width, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, 1)

    def forward(self, windows: torch.Tensor) -> torch.Tensor:
        output, _ = self.lstm(windows)
        return self.head(output[:, -1, :]).squeeze(-1)


class LstmPredictor:
    """`EnergyPredictor` over a trained network; predictions are clipped at 0 kWh."""

    implementation = IMPLEMENTATION

    def __init__(self, network: _Network, scaler: FeatureScaler, history: HourlyHistory, sequence_length: int) -> None:
        self._network, self._scaler, self._history, self._sequence_length = network, scaler, history, sequence_length

    def save(self, weights_path: Path) -> dict:
        """Write the network weights and return everything else needed to rebuild the adapter, as plain JSON data."""
        torch.save(self._network.state_dict(), weights_path)
        return {
            "sequence_length": self._sequence_length,
            "hidden_size": self._network.lstm.hidden_size,
            "scaler": {"mean": self._scaler.mean.tolist(), "std": self._scaler.std.tolist(), "target_scale": self._scaler.target_scale},
            # Weather only, never labels: the hours the adapter may look back on at prediction time.
            "history": [self._history[instant] for instant in sorted(self._history)],
        }

    def without_history(self) -> "LstmPredictor":
        """The same trained network with an empty history store, as when no preceding hours are stored."""
        return LstmPredictor(self._network, self._scaler, {}, self._sequence_length)

    def predict_kwh(self, weather: WeatherFeatures, candidate_angles_deg: tuple[float, ...], *, metadata: Metadata) -> list[CandidatePrediction]:
        windows = build_windows(at_candidate_angles(weather, candidate_angles_deg), self._history, self._sequence_length)
        predicted = _predict(self._network, self._scaler, windows)
        return [{"angle_deg": angle, "predicted_kwh": max(0.0, float(value))} for angle, value in zip(candidate_angles_deg, predicted)]


def train_lstm(
    train_rows: Sequence[WeatherRow],
    early_stopping_rows: Sequence[WeatherRow],
    history_rows: Sequence[WeatherFeatures],
    config: LstmConfig = DEFAULT_LSTM_CONFIG,
) -> LstmPredictor:
    """Fit on `train_rows`, stop early on `early_stopping_rows` (which must follow them in time).

    Training windows look back only into the train and early-stopping rows.
    `history_rows` supplies the weather (never labels) the adapter may look back
    on at prediction time; only hours before the predicted hour are read.
    """
    if not train_rows or not early_stopping_rows:
        raise ToolError("LSTM needs non-empty fit and early-stopping windows")
    torch.manual_seed(config.seed)
    scaler = _fit_scaler(train_rows)
    training_history = hourly_history([*train_rows, *early_stopping_rows])
    fit_windows = _shorten_history(build_windows(train_rows, training_history, config.sequence_length), config)
    fit_inputs = _to_inputs(scaler, fit_windows)
    stop_inputs = _to_inputs(scaler, build_windows(early_stopping_rows, training_history, config.sequence_length))
    fit_targets = _to_tensor(label_vector(train_rows) / scaler.target_scale)
    stop_targets = _to_tensor(label_vector(early_stopping_rows) / scaler.target_scale)
    network = _fit_network(fit_inputs, fit_targets, stop_inputs, stop_targets, config)
    return LstmPredictor(network, scaler, hourly_history(history_rows), config.sequence_length)


def load_lstm(weights_path: Path, settings: dict) -> LstmPredictor:
    """Restore an adapter from the weights file and the settings returned by `LstmPredictor.save`."""
    try:
        network = _Network(STEP_WIDTH, int(settings["hidden_size"]))
        # weights_only: the file may hold tensors and nothing else, so loading it cannot run code.
        network.load_state_dict(torch.load(weights_path, map_location="cpu", weights_only=True))
        network.eval()
        scaler = FeatureScaler(
            mean=np.asarray(settings["scaler"]["mean"], dtype=np.float64), std=np.asarray(settings["scaler"]["std"], dtype=np.float64),
            target_scale=float(settings["scaler"]["target_scale"]),
        )
        return LstmPredictor(network, scaler, hourly_history(settings["history"]), int(settings["sequence_length"]))
    except (KeyError, TypeError, ValueError, RuntimeError, OSError, pickle.UnpicklingError) as exc:
        raise ToolError(f"LSTM model could not be loaded from {weights_path.name}: {exc}") from exc


def _shorten_history(windows: Windows, config: LstmConfig) -> Windows:
    """Turn a random prefix of some windows into padding; the target step is always kept."""
    rng = np.random.default_rng(config.seed)
    count, length = windows.valid.shape
    shortened = rng.random(count) < config.history_dropout
    # Number of leading steps to hide: between 1 and all of the look-back steps.
    hidden = np.where(shortened, rng.integers(1, max(length, 2), size=count), 0)
    keep = np.arange(length)[np.newaxis, :] >= hidden[:, np.newaxis]
    keep[:, -1] = True
    return Windows(features=windows.features, valid=windows.valid & keep)


def _fit_scaler(train_rows: Sequence[WeatherRow]) -> FeatureScaler:
    features, labels = feature_matrix(train_rows), label_vector(train_rows)
    std = features.std(axis=0)
    return FeatureScaler(mean=features.mean(axis=0), std=np.where(std < MIN_FEATURE_STD, 1.0, std), target_scale=max(float(labels.max()), MIN_FEATURE_STD))


def _to_inputs(scaler: FeatureScaler, windows: Windows) -> torch.Tensor:
    """Scaled steps with padding zeroed, plus the validity flag as the last channel."""
    valid = windows.valid[..., np.newaxis].astype(np.float64)
    return _to_tensor(np.concatenate([scaler.scale(windows.features) * valid, valid], axis=-1))


def _fit_network(fit_inputs: torch.Tensor, fit_targets: torch.Tensor, stop_inputs: torch.Tensor, stop_targets: torch.Tensor, config: LstmConfig) -> _Network:
    network = _Network(STEP_WIDTH, config.hidden_size)
    optimizer = torch.optim.Adam(network.parameters(), lr=config.learning_rate)
    loss_function = nn.MSELoss()
    shuffle = torch.Generator().manual_seed(config.seed)
    best_loss, best_state, epochs_without_improvement = float("inf"), copy.deepcopy(network.state_dict()), 0
    for _ in range(config.max_epochs):
        network.train()
        for batch in torch.randperm(len(fit_inputs), generator=shuffle).split(config.batch_size):
            optimizer.zero_grad()
            loss_function(network(fit_inputs[batch]), fit_targets[batch]).backward()
            optimizer.step()
        network.eval()
        with torch.no_grad():
            stop_loss = loss_function(network(stop_inputs), stop_targets).item()
        if stop_loss < best_loss:
            best_loss, best_state, epochs_without_improvement = stop_loss, copy.deepcopy(network.state_dict()), 0
        else:
            epochs_without_improvement += 1
            if epochs_without_improvement >= config.patience:
                break
    network.load_state_dict(best_state)
    network.eval()
    return network


def _predict(network: _Network, scaler: FeatureScaler, windows: Windows) -> np.ndarray:
    if not len(windows.features):
        return np.empty(0)
    with torch.no_grad():
        return network(_to_inputs(scaler, windows)).numpy() * scaler.target_scale


def _to_tensor(values: np.ndarray) -> torch.Tensor:
    return torch.tensor(values, dtype=torch.float32)
