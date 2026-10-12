"""Owner: Duy. Save trained models once and load them without retraining.

A saved set is a directory holding the trained adapters, the validation
metrics computed when they were trained, and a manifest that names the dataset
they came from and a checksum for every file. Loading verifies the checksums,
so a truncated or altered file is refused rather than served.

Nothing is recomputed on load: the metrics a loaded set reports are the ones
measured at training time, and the manifest says when and on which data.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Mapping

from src.common.schema import MODEL_NAMES, ModelMetrics, ModelName
from src.common.tool_contracts import EnergyPredictor, ToolError
from src.models.advanced import boosting, lstm
from src.models.data_loader import ROOT, DatasetSource
from src.models.evaluation import EvaluatedModelingTools, select_best_model


ARTIFACT_VERSION = 1
DEFAULT_ARTIFACT_DIR = ROOT / "models" / "artifacts"
MANIFEST_FILE = "manifest.json"
BOOSTING_FILE = "boosting.json"
LSTM_WEIGHTS_FILE = "lstm.pt"
LSTM_SETTINGS_FILE = "lstm_settings.json"


class ArtifactError(ToolError):
    """A saved model set is missing, incomplete, altered, or from an unsupported version."""


@dataclass(frozen=True)
class ArtifactInfo:
    """What a saved set says about itself."""

    created_at: str
    dataset_name: str
    dataset_kind: str
    label_source: str
    available_models: tuple[ModelName, ...]


class SavedModelingTools:
    """`ModelingTools` over loaded adapters and the validation metrics stored with them."""

    def __init__(self, predictors: Mapping[ModelName, EnergyPredictor], comparison: list[ModelMetrics]) -> None:
        self._predictors, self._comparison = dict(predictors), tuple(comparison)

    def evaluate_models(self) -> list[ModelMetrics]:
        """The comparison measured at training time; it is not recomputed."""
        return [dict(entry) for entry in self._comparison]

    def select_best_model(self, comparison: list[ModelMetrics]) -> ModelName:
        return select_best_model(comparison)

    def get_predictor(self, model: ModelName) -> EnergyPredictor:
        predictor = self._predictors.get(model)
        if predictor is None:
            raise ToolError(f"No predictor available for model {model!r}")
        return predictor

    def without(self, model: ModelName, *, implementation: str) -> "SavedModelingTools":
        """The same set with one model reported unavailable, for requests that model cannot serve."""
        comparison = [
            {"model": model, "implementation": implementation, "status": "UNAVAILABLE", "mae": None, "rmse": None, "r2": None} if entry["model"] == model else dict(entry)
            for entry in self._comparison
        ]
        return SavedModelingTools({name: predictor for name, predictor in self._predictors.items() if name != model}, comparison)


@dataclass(frozen=True)
class LoadedModels:
    tools: SavedModelingTools
    source: DatasetSource
    info: ArtifactInfo


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def save_models(tools: EvaluatedModelingTools, source: DatasetSource, directory: Path = DEFAULT_ARTIFACT_DIR) -> Path:
    """Write every available trained adapter, the validation comparison and a manifest. Returns the manifest path."""
    comparison = tools.evaluate_models()
    available = [entry["model"] for entry in comparison if entry["status"] != "UNAVAILABLE"]
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    use_geometry_features = None
    if boosting.MODEL_NAME in available:
        predictor = tools.get_predictor(boosting.MODEL_NAME)
        predictor.save(directory / BOOSTING_FILE)
        use_geometry_features = predictor.use_geometry_features
        files[BOOSTING_FILE] = _sha256(directory / BOOSTING_FILE)
    if lstm.MODEL_NAME in available:
        settings = tools.get_predictor(lstm.MODEL_NAME).save(directory / LSTM_WEIGHTS_FILE)
        (directory / LSTM_SETTINGS_FILE).write_text(json.dumps(settings), encoding="utf-8")
        files[LSTM_WEIGHTS_FILE] = _sha256(directory / LSTM_WEIGHTS_FILE)
        files[LSTM_SETTINGS_FILE] = _sha256(directory / LSTM_SETTINGS_FILE)
    unsaved = [model for model in available if model not in (boosting.MODEL_NAME, lstm.MODEL_NAME)]
    if unsaved:
        raise ArtifactError(f"No save format is defined for {unsaved}")
    manifest = {
        "artifact_version": ARTIFACT_VERSION,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dataset": {"path": _portable_path(source.path), "name": source.path.name, "dataset_kind": source.dataset_kind, "label_source": source.label_source},
        "validation_comparison": comparison,
        "unavailable_reasons": tools.unavailable_reasons(),
        "boosting": {"use_geometry_features": use_geometry_features},
        "files": files,
    }
    manifest_path = directory / MANIFEST_FILE
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def _portable_path(path: Path) -> str:
    """Relative to the repository when inside it, so a saved set can move between machines and into a container."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def load_models(directory: Path = DEFAULT_ARTIFACT_DIR) -> LoadedModels:
    """Load a saved set after checking its version and every file's checksum."""
    manifest_path = directory / MANIFEST_FILE
    if not manifest_path.is_file():
        raise ArtifactError(f"No saved models in {directory}: run `python -m scripts.train_models` first")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["artifact_version"] != ARTIFACT_VERSION:
            raise ArtifactError(f"Saved models are version {manifest['artifact_version']}; this code reads version {ARTIFACT_VERSION}. Retrain them.")
        for name, expected in manifest["files"].items():
            path = directory / name
            if not path.is_file():
                raise ArtifactError(f"Saved model file is missing: {name}")
            if _sha256(path) != expected:
                raise ArtifactError(f"Saved model file does not match its checksum and was not loaded: {name}")
        comparison = manifest["validation_comparison"]
        if {entry["model"] for entry in comparison} != set(MODEL_NAMES):
            raise ArtifactError("Saved comparison does not list exactly the contract models")
        predictors: dict[ModelName, EnergyPredictor] = {}
        if BOOSTING_FILE in manifest["files"]:
            predictors[boosting.MODEL_NAME] = boosting.load_boosting(directory / BOOSTING_FILE, use_geometry_features=bool(manifest["boosting"]["use_geometry_features"]))
        if LSTM_WEIGHTS_FILE in manifest["files"]:
            settings = json.loads((directory / LSTM_SETTINGS_FILE).read_text(encoding="utf-8"))
            predictors[lstm.MODEL_NAME] = lstm.load_lstm(directory / LSTM_WEIGHTS_FILE, settings)
        available = tuple(entry["model"] for entry in comparison if entry["status"] != "UNAVAILABLE")
        if set(available) != set(predictors):
            raise ArtifactError(f"Saved comparison lists {sorted(available)} as available but files exist for {sorted(predictors)}")
        dataset = manifest["dataset"]
        stored_path = Path(dataset["path"])
        source = DatasetSource(path=stored_path if stored_path.is_absolute() else ROOT / stored_path, dataset_kind=dataset["dataset_kind"], label_source=dataset["label_source"])
        info = ArtifactInfo(manifest["created_at"], dataset["name"], dataset["dataset_kind"], dataset["label_source"], available)
    except ArtifactError:
        raise
    except ToolError as exc:
        raise ArtifactError(str(exc)) from exc
    except (KeyError, TypeError, ValueError, OSError) as exc:
        raise ArtifactError(f"Saved models in {directory} are unreadable: {exc!r}") from exc
    return LoadedModels(SavedModelingTools(predictors, comparison), source, info)
