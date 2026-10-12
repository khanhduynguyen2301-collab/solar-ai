"""Owner: Duy. Train the models once and save them, so decisions can be served without retraining.

Trains on the default dataset (the full 2023-2025 set when present, otherwise
the committed seasonal sample), scores every model on the validation window,
and writes the trained adapters, those metrics and a checksummed manifest to
`models/artifacts/`. The API and `--models` runs load that directory.

Run from the repository root:  python -m scripts.train_models
"""

import argparse
import logging
import time
from pathlib import Path

from src.common.config import DEFAULT_CONFIG
from src.models.advanced import lstm
from src.models.artifacts import DEFAULT_ARTIFACT_DIR, save_models
from src.models.data_loader import default_dataset_source, load_dataset_split
from src.models.evaluation import ModelCandidate, selection_reason
from src.service.recommendation_service import build_metadata, build_modeling_tools


LOGGER = logging.getLogger(__name__)
CONTROL_TARGET_ID = "row-001"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-lstm", action="store_true", help="train boosting only; the LSTM is saved as unavailable")
    parser.add_argument("--output", type=Path, default=DEFAULT_ARTIFACT_DIR, help="directory to write the saved models to")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    source = default_dataset_source()
    split = load_dataset_split(source)
    LOGGER.info("Dataset %s (%s, labels %s): %d train, %d validation rows", source.path.name, source.dataset_kind, source.label_source, len(split.train), len(split.validation))
    skipped = [ModelCandidate(lstm.MODEL_NAME, lstm.IMPLEMENTATION, unavailable_reason="skipped at training time (--skip-lstm)")] if arguments.skip_lstm else []
    metadata = build_metadata(source, interval_start=split.validation[0]["timestamp"], control_target_id=CONTROL_TARGET_ID, config=DEFAULT_CONFIG)

    started = time.perf_counter()
    tools = build_modeling_tools(source, metadata=metadata, extra_candidates=skipped)
    LOGGER.info("Trained in %.0f s; scoring on the validation window...", time.perf_counter() - started)
    comparison = tools.evaluate_models()
    for entry in comparison:
        LOGGER.info("  %-18s %s", entry["model"], "unavailable" if entry["status"] == "UNAVAILABLE" else f"validation RMSE {entry['rmse']:.6g} kWh")
    LOGGER.info("%s", selection_reason(comparison, tools.select_best_model(comparison)))

    manifest = save_models(tools, source, arguments.output)
    LOGGER.info("Saved to %s in %.0f s total", manifest.parent, time.perf_counter() - started)


if __name__ == "__main__":
    main()
