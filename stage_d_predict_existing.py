#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a release forecast from an already-trained Stage D model.

Unlike stage_d_weighted_forecast.py --input-json, this script does NOT rebuild
weights from Stage C. It loads output_stage_d/stage_d_model.json and applies it
to the Actual/Forecast pairs exported by stage_d_cache_watcher.py.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from stage_d_weighted_forecast import load_model, predict_from_actuals, format_telegram_message

LOG = logging.getLogger("stage_d_predict_existing")


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a pre-trained Stage D model to release Actual/Forecast data.")
    parser.add_argument("--model", required=True, type=Path, help="Existing Stage D stage_d_model.json")
    parser.add_argument("--input-json", required=True, type=Path, help="JSON produced by stage_d_cache_watcher.py")
    parser.add_argument("--output-dir", required=True, type=Path, help="Directory for prediction JSON and Telegram HTML")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    model = load_model(args.model)
    with args.input_json.open("r", encoding="utf-8") as f:
        actuals = json.load(f)

    prediction = predict_from_actuals(model, actuals)
    message = format_telegram_message(prediction)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    prediction_path = args.output_dir / "stage_d_prediction.json"
    message_path = args.output_dir / "stage_d_telegram_message.html"

    atomic_write(prediction_path, json.dumps(prediction, ensure_ascii=False, indent=2) + "\n")
    atomic_write(message_path, message + "\n")

    print("\n" + message.replace("<b>", "").replace("</b>", ""))
    LOG.info("Loaded existing model: %s", args.model)
    LOG.info("Reports received: %s/8", prediction.get("reports_received", 0))
    LOG.info("Prediction saved: %s", prediction_path)
    LOG.info("Telegram HTML saved: %s", message_path)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
        LOG.exception("Prediction failed: %s", exc)
        raise SystemExit(1)
