"""Shared prediction boundary for HTTP and Agent callers.

An explicit target never selects a legacy/global checkpoint. Scientific statuses
describe observations, not merely successful HTTP transport.
"""
from collections.abc import Mapping
from functools import lru_cache
import math
import re
from threading import Lock

from .family_contract import LABEL_THRESHOLD, PROBABILITY_THRESHOLD


_factory_lock = Lock()
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


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


def _is_complete_family_prediction(row):
    """Return whether a row contains a complete, pinned scientific result.

    The predictor and Agent validator both use this family-row contract.  The
    summary boundary must not promote a transport-shaped ``success`` flag when
    its numeric values or model provenance are absent.
    """
    if not isinstance(row, Mapping):
        return False
    if (row.get("success") is not True or row.get("status") != "passed"
            or row.get("execution_status", "passed") != "passed"
            or row.get("units") != "pIC50"
            or row.get("label_threshold") != LABEL_THRESHOLD
            or row.get("probability_threshold") != PROBABILITY_THRESHOLD
            or row.get("classification_regression_consistent") is not True):
        return False
    probability = row.get("activity_probability")
    value = row.get("predicted_pIC50")
    if (type(probability) not in (int, float) or not math.isfinite(float(probability))
            or not 0 <= probability <= 1
            or type(value) not in (int, float) or not math.isfinite(float(value))):
        return False
    if row.get("activity_class") != ("有活性" if probability >= PROBABILITY_THRESHOLD else "无活性"):
        return False
    if not isinstance(row.get("errors"), Mapping) or row["errors"]:
        return False
    provenance = row.get("provenance")
    if (not isinstance(provenance, Mapping)
            or not provenance.get("bundle_id")
            or provenance.get("bundle_id") != row.get("bundle_id")):
        return False
    models = provenance.get("models")
    if not isinstance(models, Mapping):
        return False
    request = provenance.get("request")
    if (not isinstance(request, Mapping)
            or request.get("family_id") != row.get("family_id")
            or request.get("endpoint") != "pIC50"
            or request.get("units") != "pIC50"
            or request.get("validation") != "endpoint_ready"
            or not isinstance(request.get("identity"), str)
            or not request["identity"].strip()):
        return False
    for task in ("classification", "regression"):
        model = models.get(task)
        if (not isinstance(model, Mapping)
                or not isinstance(model.get("model_id"), str)
                or not model["model_id"].strip()
                or model.get("task_type") != task
                or model.get("target_id") != row.get("family_id")
                or any(not isinstance(model.get(field), str)
                       or _SHA256_PATTERN.fullmatch(model[field]) is None
                       for field in ("weights_sha256", "model_card_sha256", "prepared_dataset_sha256"))
                or model.get("demo_mode") is not False
                or model.get("fallback_used") is not False):
            return False
    return True


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
    complete = [row for row, needs_review in zip(rows, conflicts)
                if not needs_review and _is_complete_family_prediction(row)]
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


def _target_required_row(smiles):
    return {
        "smiles": smiles,
        "requested_target": None,
        "family_id": None,
        "bundle_id": None,
        "success": False,
        "status": "failed",
        "execution_status": "failed",
        "activity_class": None,
        "activity_probability": None,
        "predicted_pIC50": None,
        "units": "pIC50",
        "label_threshold": LABEL_THRESHOLD,
        "probability_threshold": PROBABILITY_THRESHOLD,
        "classification_regression_consistent": None,
        "warnings": ["必须明确选择 PDE 或 BuChE 靶点后再进行预测"],
        "errors": {"target": "explicit_target_required"},
        "provenance": {},
    }


def predict_activity(smiles, *, target=None, model_request=None):
    if target is None:
        inputs = [smiles] if isinstance(smiles, str) else list(smiles)
        return summarize_predictions([_target_required_row(value) for value in inputs])
    predictor = get_family_predictor()
    if model_request is None:
        rows = predictor.predict(smiles, target=target)
    else:
        rows = predictor.predict(smiles, target=target, model_request=model_request)
    return summarize_predictions(rows)
