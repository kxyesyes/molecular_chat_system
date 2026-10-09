"""Run a redacted, real-weight ADMET-AI smoke/soak check.

This script never writes a report and never prints model outputs.  It only
reports status, row identity, endpoint counts, model identity and latency.
Set ``ADMET_AI_PYTHON`` to a Python 3.10 environment containing
``admet-ai==1.4.0`` before running it from the MedChat environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from time import perf_counter

# Executing a script by path makes ``scripts/`` the first import directory.
# Add the repository root explicitly so this probe reports model-environment
# failures instead of masking them as ``No module named 'src'``.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.admet.predictor import ADMETPredictor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat must be positive")
    if not os.environ.get("ADMET_AI_PYTHON"):
        raise SystemExit("ADMET_AI_PYTHON is required; no real model environment was selected")

    initialization_started = perf_counter()
    predictor = ADMETPredictor()
    # Force the lazy backend handshake outside the measured warm predictions.
    availability_probe = {"message": "", "reasoning": ""}
    if not predictor._check_adme_backend(availability_probe):
        raise SystemExit(availability_probe.get("message") or "ADMET-AI backend unavailable")
    initialization_ms = round((perf_counter() - initialization_started) * 1000, 2)
    observations = []
    try:
        for iteration in range(1, args.repeat + 1):
            started = perf_counter()
            result = predictor.execute({
                "smiles": ["CCO", "CC(=O)Oc1ccccc1C(=O)O"],
                "molecule_ids": ["real-001", "real-002"],
            })
            elapsed_ms = round((perf_counter() - started) * 1000, 2)
            rows = result.get("data") if isinstance(result, dict) else None
            rows = rows if isinstance(rows, list) else []
            successful = [row for row in rows if row.get("status") == "succeeded"]
            observations.append({
                "iteration": iteration,
                "success": result.get("success") is True,
                "status": result.get("status"),
                "latency_ms": elapsed_ms,
                "row_ids": [row.get("molecule_id") for row in rows],
                "successful_rows": len(successful),
                "endpoint_counts": [
                    len(row.get("admet", {}).get("endpoints", {}))
                    for row in successful
                ],
                "methods": sorted({
                    row.get("admet", {}).get("prediction_method")
                    for row in successful
                    if row.get("admet", {}).get("prediction_method")
                }),
                "model_versions": sorted({
                    row.get("admet", {}).get("model_version")
                    for row in successful
                    if row.get("admet", {}).get("model_version")
                }),
                "weights_ids": sorted({
                    row.get("admet", {}).get("weights_id")
                    for row in successful
                    if row.get("admet", {}).get("weights_id")
                }),
                "error": result.get("message") if result.get("success") is not True else None,
            })
    finally:
        predictor.close()

    print(json.dumps({
        "api_key_present": bool(os.environ.get("OPENAI_COMPATIBLE_API_KEY")),
        "backend_initialization_ms": initialization_ms,
        "repeat": args.repeat,
        "observations": observations,
    }, ensure_ascii=False, indent=2))
    return 0 if all(item["success"] for item in observations) else 1


if __name__ == "__main__":
    raise SystemExit(main())
