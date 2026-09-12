"""Shared prediction boundary for HTTP and Agent callers.

An explicit target never selects a legacy/global checkpoint. Scientific statuses
describe observations, not merely successful HTTP transport.
"""
from functools import lru_cache
from threading import Lock


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
    complete = [row for row in rows if row.get("success") is True
                and row.get("status", "passed") == "passed"]
    observed = complete or any(row.get("status") == "partial" for row in rows)
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
