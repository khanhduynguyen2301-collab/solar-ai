"""Saved models: a loaded set predicts exactly as the trained one did, and a damaged set is refused."""

import json
import tempfile
import unittest
from pathlib import Path

from src.common.config import DEFAULT_CONFIG
from src.common.schema import MODEL_NAMES
from src.common.tool_contracts import ModelingTools, ToolError
from src.models.artifacts import (
    ARTIFACT_VERSION, BOOSTING_FILE, LSTM_SETTINGS_FILE, LSTM_WEIGHTS_FILE, MANIFEST_FILE, ArtifactError, load_models, save_models,
)
from src.models.data_loader import EXAMPLE_DATASET_SOURCE, load_dataset_split
from src.models.evaluation import to_features
from src.service.recommendation_service import build_metadata, build_modeling_tools


ANGLES = DEFAULT_CONFIG.candidate_angles_deg


class SavedModelsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = EXAMPLE_DATASET_SOURCE
        split = load_dataset_split(cls.source)
        cls.weather = to_features(max(split.test, key=lambda row: row["ghi_wm2"]))
        cls.metadata = build_metadata(cls.source, interval_start=cls.weather["timestamp"], control_target_id="row-001", config=DEFAULT_CONFIG)
        cls.trained = build_modeling_tools(cls.source, metadata=cls.metadata)
        cls.directory = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.directory.cleanup)
        cls.path = Path(cls.directory.name)
        cls.manifest_path = save_models(cls.trained, cls.source, cls.path)
        cls.loaded = load_models(cls.path)

    def copy_of_saved_set(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        for file in self.path.iterdir():
            (Path(directory.name) / file.name).write_bytes(file.read_bytes())
        return Path(directory.name)

    def test_loaded_tools_implement_the_modeling_contract(self) -> None:
        self.assertIsInstance(self.loaded.tools, ModelingTools)

    def test_loaded_boosting_predicts_exactly_as_trained(self) -> None:
        expected = self.trained.get_predictor("boosting").predict_kwh(self.weather, ANGLES, metadata=self.metadata)

        self.assertEqual(self.loaded.tools.get_predictor("boosting").predict_kwh(self.weather, ANGLES, metadata=self.metadata), expected)

    def test_loaded_lstm_predicts_exactly_as_trained(self) -> None:
        expected = self.trained.get_predictor("lstm").predict_kwh(self.weather, ANGLES, metadata=self.metadata)

        self.assertEqual(self.loaded.tools.get_predictor("lstm").predict_kwh(self.weather, ANGLES, metadata=self.metadata), expected)

    def test_stored_metrics_are_the_ones_measured_at_training_time(self) -> None:
        self.assertEqual(self.loaded.tools.evaluate_models(), self.trained.evaluate_models())
        self.assertEqual(self.loaded.tools.select_best_model(self.loaded.tools.evaluate_models()), self.trained.select_best_model(self.trained.evaluate_models()))

    def test_undelivered_models_stay_unavailable_with_null_metrics(self) -> None:
        by_model = {entry["model"]: entry for entry in self.loaded.tools.evaluate_models()}

        self.assertEqual(set(by_model), set(MODEL_NAMES))
        self.assertEqual((by_model["linear_regression"]["status"], by_model["linear_regression"]["rmse"]), ("UNAVAILABLE", None))
        with self.assertRaises(ToolError):
            self.loaded.tools.get_predictor("linear_regression")

    def test_manifest_records_the_dataset_and_its_labels(self) -> None:
        self.assertEqual((self.loaded.source.dataset_kind, self.loaded.source.label_source), ("MOCK", "mock"))
        self.assertEqual(self.loaded.source.path, self.source.path)
        self.assertEqual(self.loaded.info.available_models, ("boosting", "lstm"))
        self.assertEqual(self.loaded.info.dataset_name, self.source.path.name)

    def test_manifest_stores_a_repository_relative_dataset_path(self) -> None:
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(manifest["dataset"]["path"], "data/example/example_weather.csv")

    def test_weights_file_holds_no_energy_labels(self) -> None:
        settings = json.loads((self.path / LSTM_SETTINGS_FILE).read_text(encoding="utf-8"))

        self.assertTrue(settings["history"])
        self.assertTrue(all("actual_kwh" not in hour for hour in settings["history"]))

    def test_without_a_model_reports_it_unavailable_and_selects_another(self) -> None:
        reduced = self.loaded.tools.without("lstm", implementation="pytorch")
        by_model = {entry["model"]: entry for entry in reduced.evaluate_models()}

        self.assertEqual((by_model["lstm"]["status"], by_model["lstm"]["rmse"]), ("UNAVAILABLE", None))
        self.assertEqual(reduced.select_best_model(reduced.evaluate_models()), "boosting")
        with self.assertRaises(ToolError):
            reduced.get_predictor("lstm")
        self.assertEqual({entry["model"]: entry["status"] for entry in self.loaded.tools.evaluate_models()}["lstm"], "MOCK")

    def test_missing_directory_says_how_to_create_the_models(self) -> None:
        with self.assertRaisesRegex(ArtifactError, "train_models"):
            load_models(self.path / "nowhere")

    def test_altered_model_file_is_refused(self) -> None:
        copy = self.copy_of_saved_set()
        with (copy / BOOSTING_FILE).open("ab") as handle:
            handle.write(b" ")

        with self.assertRaisesRegex(ArtifactError, "checksum"):
            load_models(copy)

    def test_missing_model_file_is_refused(self) -> None:
        copy = self.copy_of_saved_set()
        (copy / LSTM_WEIGHTS_FILE).unlink()

        with self.assertRaisesRegex(ArtifactError, "missing"):
            load_models(copy)

    def test_other_artifact_version_is_refused(self) -> None:
        copy = self.copy_of_saved_set()
        manifest = json.loads((copy / MANIFEST_FILE).read_text(encoding="utf-8"))
        (copy / MANIFEST_FILE).write_text(json.dumps({**manifest, "artifact_version": ARTIFACT_VERSION + 1}), encoding="utf-8")

        with self.assertRaisesRegex(ArtifactError, "Retrain"):
            load_models(copy)

    def test_unreadable_manifest_is_refused(self) -> None:
        copy = self.copy_of_saved_set()
        (copy / MANIFEST_FILE).write_text("{not json", encoding="utf-8")

        with self.assertRaises(ArtifactError):
            load_models(copy)


if __name__ == "__main__":
    unittest.main()
