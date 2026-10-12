"""Owner: Duy. XGBoost adapter behind the stable model ID `boosting`."""

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import xgboost as xgb

from src.common.schema import CandidatePrediction, Metadata, WeatherFeatures, WeatherRow
from src.common.tool_contracts import ToolError
from src.models.advanced.features import at_candidate_angles, feature_matrix, feature_names, label_vector, split_for_early_stopping


MODEL_NAME = "boosting"
IMPLEMENTATION = "xgboost"
EARLY_STOPPING_SET_NAME = "early_stopping"


@dataclass(frozen=True)
class BoostingConfig:
    """Training hyperparameters; early stopping watches the tail of the train window."""

    # An upper limit; early stopping picks the actual count. Raised from 600 after the grid search in
    # scripts/tune_boosting.py: the model was still improving at 600 rounds on the full dataset.
    num_boost_round: int = 3000
    early_stopping_rounds: int = 40
    max_depth: int = 6
    learning_rate: float = 0.05
    subsample: float = 0.8
    colsample_bytree: float = 0.9
    min_child_weight: float = 2.0
    seed: int = 20261004
    # On by default: on the pipeline dataset they roughly halve validation RMSE and smooth the angle curve.
    # (On the noisy synthetic example data they lowered RMSE but made the curve bumpier; see features.py.)
    use_geometry_features: bool = True


DEFAULT_BOOSTING_CONFIG = BoostingConfig()


class BoostingPredictor:
    """`EnergyPredictor` over a trained booster; predictions are clipped at 0 kWh."""

    implementation = IMPLEMENTATION

    def __init__(self, booster: xgb.Booster, *, use_geometry_features: bool) -> None:
        self._booster, self._geometry = booster, use_geometry_features

    @property
    def use_geometry_features(self) -> bool:
        return self._geometry

    def save(self, path: Path) -> None:
        """Write the trained booster (including its early-stopping round) as XGBoost JSON."""
        try:
            self._booster.save_model(str(path))
        except xgb.core.XGBoostError as exc:
            raise ToolError(f"Boosting model could not be saved: {exc}") from exc

    def predict_kwh(self, weather: WeatherFeatures, candidate_angles_deg: tuple[float, ...], *, metadata: Metadata) -> list[CandidatePrediction]:
        candidates = at_candidate_angles(weather, candidate_angles_deg)
        try:
            raw = self._booster.predict(_to_dmatrix(candidates, geometry=self._geometry), iteration_range=(0, self._booster.best_iteration + 1))
        except xgb.core.XGBoostError as exc:
            raise ToolError(f"Boosting prediction failed: {exc}") from exc
        return [
            {"angle_deg": angle, "predicted_kwh": max(0.0, float(value))}
            for angle, value in zip(candidate_angles_deg, raw)
        ]


def train_boosting(train_rows: Sequence[WeatherRow], early_stopping_rows: Sequence[WeatherRow], config: BoostingConfig = DEFAULT_BOOSTING_CONFIG) -> BoostingPredictor:
    """Fit on `train_rows`; stop early on `early_stopping_rows`.

    Pass a slice of the train window (see `features.split_for_early_stopping`), not the
    validation window used for model selection, so the comparison stays fair.
    """
    if not train_rows or not early_stopping_rows:
        raise ToolError("Boosting needs non-empty fit and early-stopping windows")
    params = {
        "objective": "reg:squarederror",
        "eval_metric": "rmse",
        "tree_method": "hist",
        "max_depth": config.max_depth,
        "eta": config.learning_rate,
        "subsample": config.subsample,
        "colsample_bytree": config.colsample_bytree,
        "min_child_weight": config.min_child_weight,
        "seed": config.seed,
        "nthread": 1,
    }
    try:
        booster = xgb.train(
            params,
            _to_dmatrix(train_rows, label_vector(train_rows), geometry=config.use_geometry_features),
            num_boost_round=config.num_boost_round,
            evals=[(_to_dmatrix(early_stopping_rows, label_vector(early_stopping_rows), geometry=config.use_geometry_features), EARLY_STOPPING_SET_NAME)],
            early_stopping_rounds=config.early_stopping_rounds,
            verbose_eval=False,
        )
    except xgb.core.XGBoostError as exc:
        raise ToolError(f"Boosting training failed: {exc}") from exc
    return BoostingPredictor(booster, use_geometry_features=config.use_geometry_features)


def load_boosting(path: Path, *, use_geometry_features: bool) -> BoostingPredictor:
    """Restore a booster written by `BoostingPredictor.save`; it predicts exactly as it did when saved."""
    booster = xgb.Booster()
    try:
        booster.load_model(str(path))
        booster.best_iteration  # noqa: B018 - a model saved without its early-stopping round cannot be used
    except (xgb.core.XGBoostError, AttributeError, OSError) as exc:
        raise ToolError(f"Boosting model could not be loaded from {path.name}: {exc}") from exc
    return BoostingPredictor(booster, use_geometry_features=use_geometry_features)


def _to_dmatrix(rows: Sequence[WeatherFeatures], labels=None, *, geometry: bool) -> xgb.DMatrix:
    return xgb.DMatrix(feature_matrix(rows, geometry=geometry), label=labels, feature_names=list(feature_names(geometry=geometry)))
