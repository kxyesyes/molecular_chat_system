"""Shared prediction boundary for HTTP and Agent callers.

An explicit target never selects a legacy/global checkpoint. Scientific statuses
describe observations, not merely successful HTTP transport.
"""
from functools import lru_cache
import math
from threading import Lock

from .family_contract import LABEL_THRESHOLD, PROBABILITY_THRESHOLD


_factory_lock = Lock()


@lru_cache(maxsize=2)
def _family_predictor(directory):
    from .family_predictor import FamilyActivityPredictor
    from .model_registry import ActivityModelRegistry
    return FamilyActivityPredictor(ActivityModelRegistry(directory))


def get_family_predictor():
    from .trainer import get_activity_models_dir
    directory = str(get_activity_models_dir().resolve())
    # lru_cache alone allows duplicate construction during concurrent cold misses.
    # This lock guards construction only; each predictor owns its inference lock.
    with _factory_lock:
        return _family_predictor(directory)


def summarize_predictions(rows):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Invalid prediction result contract")
    def conflict(row):
        if row.get("status") == "failed" or row.get("execution_status") == "failed":
            return False
        if row.get("classification_regression_consistent") is False:
            return True
        probability, value = row.get("activity_probability"), row.get("predicted_pIC50")
        return (type(probability) in (int, float) and math.isfinite(probability)
                and 0 <= probability <= 1 and type(value) in (int, float) and math.isfinite(value)
                and (probability >= PROBABILITY_THRESHOLD) != (value >= LABEL_THRESHOLD))

    conflicts = [conflict(row) for row in rows]
    complete = [row for row, needs_review in zip(rows, conflicts) if not needs_review
                and row.get("execution_status", "passed") == "passed"
                and row.get("success") is True and row.get("status", "passed") == "passed"]
    observed = complete or any(conflicts) or any(row.get("status") == "partial"
                and row.get("execution_status") != "failed" for row in rows)
    status = "passed" if rows and len(complete) == len(rows) else "partial" if observed else "failed"
    warnings = list(dict.fromkeys(
        warning for row in rows
        if isinstance(row.get("warnings", []), list)
        for warning in row.get("warnings", [])
        if isinstance(warning, str)
    ))
    return {"success": status == "passed", "status": status,
            "results": rows, "warnings": warnings}


def predict_activity(smiles, *, target=None):
    if target is not None:
        rows = get_family_predictor().predict(smiles, target=target)
    else:
        from .predictor import get_predictor
        rows = get_predictor().predict(smiles)
    return summarize_predictions(rows)
