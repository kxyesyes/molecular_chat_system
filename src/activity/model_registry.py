from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional


logger = logging.getLogger(__name__)

REGISTRY_STATE_FILE = "registry_state.json"
REGISTRY_LOCK_FILE = "registry_state.lock"
REGISTRY_STATE_VERSION = 2
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
DEFAULT_LOCK_POLL_SECONDS = 0.05

_MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$")
_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
_REQUIRED_FIELDS = {
    "model_id",
    "weights_file",
    "task_type",
    "endpoint",
    "units",
    "dataset_sha256",
    "split_strategy",
    "random_seed",
    "model_config",
    "model_format",
    "weights_sha256",
    "metrics",
}
_LEGACY_PROVENANCE_FIELDS = {"model_format", "weights_sha256", "metrics"}
_LEGACY_REQUIRED_FIELDS = _REQUIRED_FIELDS - _LEGACY_PROVENANCE_FIELDS
_TASK_TYPES = {"regression", "classification"}
_MODEL_FORMATS = {"pytorch_state_dict"}
_INVALID_ENDPOINTS = {"unknown", "unspecified"}
_REGISTRY_THREAD_LOCK = threading.RLock()

_ENDPOINT_REQUIRED_FIELDS = frozenset({
    "target_id", "target_name", "endpoint_key", "label_transform",
    "prepared_dataset_sha256", "split_counts", "split_scaffold_counts",
    "test_metrics", "model_card_file", "model_card_sha256", "scientific_readiness",
})
# Trainer may merge only these extension fields, never overwrite base provenance.
ENDPOINT_METADATA_FIELDS = _ENDPOINT_REQUIRED_FIELDS | frozenset({
    "demo_mode", "fallback_used",
})


def _artifact_basename(value: Any, field: str) -> str:
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or any(c in value for c in '/\\:<>"|?*')
            or any(ord(c) < 32 for c in value)
            or value.endswith((".", " ")) or Path(value).name != value):
        raise ValueError(f"Invalid {field}")
    if value.split(".", 1)[0].rstrip().upper() in {
        "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
        *(f"COM{i}" for i in "123456789¹²³"),
        *(f"LPT{i}" for i in "123456789¹²³"),
    }:
        raise ValueError(f"Invalid {field}: reserved device name")
    return value


def _finite_metric(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, ValueError):
        return False


def _validate_validation_metrics(metrics: Any, task_type: str) -> None:
    """Validate claimed values without requiring new keys in minimal v2 cards."""
    if not isinstance(metrics, dict):
        raise ValueError("Invalid model card validation metrics")
    for name, value in metrics.items():
        if name == "confusion_matrix" and task_type == "classification":
            if (not isinstance(value, dict) or set(value) != {"tn", "fp", "fn", "tp"}
                    or any(type(n) is not int or n < 0 for n in value.values())):
                raise ValueError("Invalid model card validation metrics: confusion_matrix")
            continue
        if not _finite_metric(value):
            raise ValueError("Invalid model card validation metrics: expected finite numbers")
        if ((name in {"rmse", "mae"} and value < 0)
                or (name == "r2" and value > 1)
                or (name in {"roc_auc", "pr_auc", "balanced_accuracy"} and not 0 <= value <= 1)):
            raise ValueError("Invalid model card validation metrics: out of range")


def validate_endpoint_metadata(metadata: dict) -> dict:
    """Deep-copy and validate conditional endpoint evidence, without artifact I/O.

    endpoint/units describe model outputs (not pre-transform source labels).
    Ordinary legacy metadata is passed through unchanged. Artifact authenticity
    remains the registry's responsibility; this helper never loads weights.
    """
    if not isinstance(metadata, dict):
        raise ValueError("Invalid model metadata")
    result = copy.deepcopy(metadata)
    claim = bool((_ENDPOINT_REQUIRED_FIELDS - {"scientific_readiness"}) & result.keys())
    readiness = result.get("scientific_readiness", "legacy_unvalidated")
    if not claim and readiness == "legacy_unvalidated":
        return result
    if readiness != "endpoint_ready" or not _ENDPOINT_REQUIRED_FIELDS <= result.keys():
        raise ValueError("Incomplete endpoint_ready metadata")
    if result.get("split_strategy") != "scaffold":
        raise ValueError("Invalid split_strategy: endpoint_ready requires scaffold")

    # Reuse the dataset contract's canonicalization, but keep legacy reads light.
    from .dataset_contract import (
        _canonical_endpoint, _canonical_manifest_unit, _identity_component,
        _normalized_choice, _validate_metadata_string,
    )

    for field in ("target_id", "target_name", "endpoint_key", "label_transform", "task_type"):
        result[field] = _validate_metadata_string(result.get(field), field)
    result["target_id"] = _identity_component(result["target_id"], "target_id")
    result["endpoint"] = _canonical_endpoint(result.get("endpoint"), "endpoint")
    result["units"] = _canonical_manifest_unit(result.get("units"), "units")
    result["task_type"] = _normalized_choice(result["task_type"])
    result["label_transform"] = _normalized_choice(result["label_transform"])
    task, transform = result["task_type"], result["label_transform"]
    allowed = {"regression": {"identity", "molar_to_pactivity"},
               "classification": {"identity", "binary_threshold"}}
    if task not in allowed or transform not in allowed[task]:
        raise ValueError("Invalid label_transform or task_type")
    endpoint, units = result["endpoint"], result["units"]
    pactivity = {"pIC50", "pKi", "pEC50", "pKd"}
    if task == "regression":
        if units not in pactivity | {"M", "mM", "uM", "nM", "pM"}:
            raise ValueError("Invalid regression units")
        if (units in pactivity and endpoint != units) or (endpoint in pactivity and units != endpoint):
            raise ValueError("Inconsistent endpoint units")
        if transform == "molar_to_pactivity" and units not in pactivity:
            raise ValueError("Invalid molar_to_pactivity output units")
    elif units != ("probability" if transform == "binary_threshold" else "binary"):
        raise ValueError("Invalid classification output units")
    key = ":".join(_identity_component(result[field], field)
                   for field in ("target_id", "endpoint", "units", "task_type"))
    if _normalized_choice(result["endpoint_key"]) != key:
        raise ValueError("endpoint_key does not match model identity")
    result["endpoint_key"] = key
    for field, expected in (("output_endpoint", endpoint), ("output_units", units)):
        if field in result and result[field] != expected:
            raise ValueError(f"Inconsistent {field}")
    for field in ("dataset_sha256", "weights_sha256", "prepared_dataset_sha256", "model_card_sha256"):
        value = result.get(field)
        if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
            raise ValueError(f"Invalid {field}")
        result[field] = value.lower()
    card = _artifact_basename(result["model_card_file"], "model_card_file")
    if (card.casefold() in {REGISTRY_STATE_FILE, REGISTRY_LOCK_FILE, "active_model.json"}
            or card.casefold().endswith("_info.json")
            or card.casefold() == str(result.get("weights_file", "")).casefold()):
        raise ValueError("model_card_file collides with registry artifact")
    for field in ("demo_mode", "fallback_used"):
        if result.get(field, False) is not False:
            raise ValueError(f"Invalid {field}: endpoint_ready requires false")
    for field in ("split_counts", "split_scaffold_counts"):
        counts = result[field]
        if (not isinstance(counts, dict) or set(counts) != {"train", "validation", "test"}
                or any(type(n) is not int or n <= 0 for n in counts.values())):
            raise ValueError(f"Invalid {field}: expected positive split counts")
    if any(result["split_scaffold_counts"][s] > n for s, n in result["split_counts"].items()):
        raise ValueError("split_scaffold_counts exceeds rows")
    metrics = result["test_metrics"]
    required = {"rmse", "mae", "r2"} if task == "regression" else {"roc_auc", "pr_auc", "balanced_accuracy"}
    if not isinstance(metrics, dict) or not required <= metrics.keys():
        raise ValueError("Incomplete test_metrics")
    for name, value in metrics.items():
        if name == "confusion_matrix" and task == "classification":
            continue
        if not _finite_metric(value):
            raise ValueError("Invalid test_metrics: expected finite numbers")
    if task == "regression":
        if metrics["rmse"] < 0 or metrics["mae"] < 0 or metrics["r2"] > 1:
            raise ValueError("Invalid regression test_metrics")
    else:
        if any(not 0 <= metrics[name] <= 1 for name in required):
            raise ValueError("Invalid classification test_metrics")
        matrix = metrics.get("confusion_matrix")
        if (not isinstance(matrix, dict) or set(matrix) != {"tn", "fp", "fn", "tp"}
                or any(type(n) is not int or n < 0 for n in matrix.values())
                or sum(matrix.values()) != result["split_counts"]["test"]
                or matrix["tn"] + matrix["fp"] == 0 or matrix["fn"] + matrix["tp"] == 0):
            raise ValueError("Invalid classification confusion_matrix")
    try:
        json.dumps(result, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("Model metadata is not JSON serializable") from None
    return result


def _configured_positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
        return value
    except (TypeError, ValueError):
        return default


def _try_lock_file(lock_file) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(lock_file) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _interprocess_registry_lock(lock_path: Path) -> Iterator[None]:
    timeout_seconds = _configured_positive_float(
        "MEDCHAT_ACTIVITY_REGISTRY_LOCK_TIMEOUT_SECONDS",
        DEFAULT_LOCK_TIMEOUT_SECONDS,
    )
    poll_seconds = _configured_positive_float(
        "MEDCHAT_ACTIVITY_REGISTRY_LOCK_POLL_SECONDS",
        DEFAULT_LOCK_POLL_SECONDS,
    )
    deadline = time.monotonic() + timeout_seconds
    try:
        if lock_path.exists() and (
            lock_path.is_symlink()
            or not lock_path.resolve(strict=True).is_relative_to(lock_path.parent)
        ):
            raise OSError
        lock_file = lock_path.open("a+b", buffering=0)
    except (OSError, RuntimeError):
        raise RuntimeError("Unable to open activity model registry lock") from None
    acquired = False
    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
            os.fsync(lock_file.fileno())

        while not acquired:
            try:
                _try_lock_file(lock_file)
                acquired = True
            except (BlockingIOError, OSError) as error:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Timed out acquiring activity model registry lock"
                    ) from error
                time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        release_error = None
        if acquired:
            try:
                _unlock_file(lock_file)
            except OSError as error:
                release_error = RuntimeError(
                    "Failed to release activity model registry lock"
                )
                release_error.__cause__ = error
        lock_file.close()
        if release_error is not None:
            raise release_error


class ActivityModelRegistry:
    """Atomic, confined registry for locally trained activity checkpoints."""

    def __init__(self, models_dir: Path | str):
        directory = Path(models_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.models_dir = directory.resolve(strict=True)
        self.state_path = self.models_dir / REGISTRY_STATE_FILE
        self._lock_path = self.models_dir / REGISTRY_LOCK_FILE
        self._legacy_active_path = self.models_dir / "active_model.json"
        with self._transaction():
            self._ensure_state_unlocked()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with _REGISTRY_THREAD_LOCK:
            with _interprocess_registry_lock(self._lock_path):
                yield

    @staticmethod
    def _validate_model_id(model_id: Any) -> str:
        if not isinstance(model_id, str) or not _MODEL_ID_PATTERN.fullmatch(model_id):
            raise ValueError("Invalid model_id")
        return model_id

    @staticmethod
    def _require_non_empty_string(metadata: Dict[str, Any], field: str) -> None:
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Invalid {field}")

    @staticmethod
    def _sha256_file(file_path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with file_path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            raise ValueError("Unable to hash registered weights") from None
        return digest.hexdigest()

    def _metadata_path(self, model_id: str) -> Path:
        safe_model_id = self._validate_model_id(model_id)
        return self.models_dir / f"{safe_model_id}_info.json"

    @staticmethod
    def _validate_weights_basename(weights_file: Any) -> str:
        return _artifact_basename(weights_file, "weights_file")

    def _resolve_weights_name(self, weights_file: Any) -> Path:
        safe_weights_file = self._validate_weights_basename(weights_file)
        candidate = self.models_dir / safe_weights_file
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("Registered weights file is missing") from None

        if (
            not resolved.is_relative_to(self.models_dir)
            or not resolved.is_file()
            or candidate.is_symlink()
        ):
            raise ValueError("Invalid weights_file")
        return resolved

    def _validate_metadata(self, metadata: Any) -> Dict[str, Any]:
        if not isinstance(metadata, dict) or not _REQUIRED_FIELDS.issubset(metadata):
            raise ValueError("Incomplete model metadata")
        metadata = validate_endpoint_metadata(metadata)

        self._validate_model_id(metadata.get("model_id"))
        weights_path = self._resolve_weights_name(metadata.get("weights_file"))

        if metadata.get("task_type") not in _TASK_TYPES:
            raise ValueError("Invalid task_type")
        for field in ("endpoint", "units", "split_strategy"):
            self._require_non_empty_string(metadata, field)
        if metadata["endpoint"].strip().lower() in _INVALID_ENDPOINTS:
            raise ValueError("Invalid endpoint")
        if not isinstance(metadata.get("dataset_sha256"), str) or not _SHA256_PATTERN.fullmatch(
            metadata["dataset_sha256"]
        ):
            raise ValueError("Invalid dataset_sha256")
        if not isinstance(metadata.get("weights_sha256"), str) or not _SHA256_PATTERN.fullmatch(
            metadata["weights_sha256"]
        ):
            raise ValueError("Invalid weights_sha256")
        if self._sha256_file(weights_path) != metadata["weights_sha256"].lower():
            raise ValueError("weights_sha256 does not match registered weights")
        random_seed = metadata.get("random_seed")
        if isinstance(random_seed, bool) or not isinstance(random_seed, int):
            raise ValueError("Invalid random_seed")
        if not isinstance(metadata.get("model_config"), dict) or not metadata["model_config"]:
            raise ValueError("Invalid model_config")
        if metadata.get("model_format") not in _MODEL_FORMATS:
            raise ValueError("Invalid model_format")
        if not isinstance(metadata.get("metrics"), dict):
            raise ValueError("Invalid metrics")

        try:
            json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("Model metadata is not JSON serializable") from None
        if metadata.get("scientific_readiness") == "endpoint_ready":
            self._verify_model_card(metadata)
        return dict(metadata)

    def _resolve_card(self, name: str) -> Path:
        _artifact_basename(name, "model_card_file")
        candidate = self.models_dir / name
        try:
            resolved = candidate.resolve(strict=True)
            if (candidate.is_symlink() or resolved.parent != self.models_dir
                    or not resolved.is_file() or resolved.stat().st_nlink != 1):
                raise ValueError("Invalid model_card_file")
            return resolved
        except (OSError, RuntimeError):
            raise ValueError("Model card is missing or unavailable") from None

    def _verify_model_card(self, metadata: dict) -> None:
        card_path = self._resolve_card(metadata["model_card_file"])
        try:
            content = card_path.read_bytes()
            if hashlib.sha256(content).hexdigest() != metadata["model_card_sha256"]:
                raise ValueError("model_card_sha256 mismatch")
            from .dataset_contract import _reject_duplicate_object_members

            card = json.loads(content, object_pairs_hook=_reject_duplicate_object_members)
            if not isinstance(card, dict):
                raise ValueError("Invalid model card")
            for field in ("model_card_file", "model_card_sha256"):
                if field in card and card[field] != metadata[field]:
                    raise ValueError(f"Model card {field} does not match metadata")
            # Validate identity with the same pure rules without requiring the
            # impossible self-referential card digest in the card itself.
            normalized = validate_endpoint_metadata(dict(
                card, model_card_file=metadata["model_card_file"],
                model_card_sha256=metadata["model_card_sha256"],
            ))
            fields = ("model_id", "weights_file", "target_id", "target_name", "endpoint_key", "endpoint",
                      "units", "task_type", "label_transform", "weights_sha256",
                      "prepared_dataset_sha256", "dataset_sha256", "split_counts",
                      "split_scaffold_counts", "test_metrics", "scientific_readiness",
                      "random_seed", "split_strategy", "model_config", "model_format", "metrics")
            if any(normalized.get(field) != metadata.get(field) for field in fields):
                raise ValueError("Model card content does not match metadata")
            # build_model_card copies provenance, but adds validation_metrics
            # and limitations; publish_model attaches card paths/hash afterwards.
            # All other optional claims must be present and equal on both sides.
            special = {"model_card_file", "model_card_sha256", "validation_metrics",
                       "limitations", "demo_mode", "fallback_used"}
            missing = object()
            for field in (normalized.keys() | metadata.keys()) - special:
                if normalized.get(field, missing) != metadata.get(field, missing):
                    raise ValueError(f"Model card {field} does not match metadata")
            for field in ("validation_metrics", "limitations"):
                if field in metadata and card.get(field, missing) != metadata[field]:
                    raise ValueError(f"Model card {field} does not match metadata")
            for record in (metadata, card):
                for field in ("metrics", "validation_metrics", "best_metrics"):
                    if field in record:
                        _validate_validation_metrics(record[field], metadata["task_type"])
                        if record[field] != metadata["metrics"]:
                            raise ValueError(f"Model card {field} does not match validation metrics")
            if any(card.get(field) is not False for field in ("demo_mode", "fallback_used")):
                raise ValueError("Model card demo/fallback flags must be false")
        except (OSError, UnicodeError, json.JSONDecodeError):
            raise ValueError("Invalid model card") from None

    def _validate_artifact_ownership(self, models: dict) -> None:
        """Reserve paths symmetrically, including records added after a card."""
        reserved = (REGISTRY_STATE_FILE, REGISTRY_LOCK_FILE, "active_model.json")
        owners = {name.casefold() for name in reserved}

        def resolved_key(name: str) -> str:
            try:
                resolved = (self.models_dir / name).resolve(strict=False)
            except (OSError, RuntimeError):
                raise ValueError("Invalid registry artifact path") from None
            if resolved.parent != self.models_dir:
                raise ValueError("Registry artifact path escapes model directory")
            return str(resolved).casefold()

        resolved_owners = {resolved_key(name) for name in reserved}
        for model_id, record in models.items():
            names = [f"{model_id}_info.json", record["weights_file"]]
            if "model_card_file" in record:
                names.append(_artifact_basename(record["model_card_file"], "model_card_file"))
            for name in names:
                key = name.casefold()
                if key in owners:
                    raise ValueError("Registry artifact path collision")
                actual_key = resolved_key(name)
                if actual_key in resolved_owners:
                    raise ValueError("Registry resolved artifact path collision")
                owners.add(key)
                resolved_owners.add(actual_key)

    @staticmethod
    def _new_state() -> Dict[str, Any]:
        return {
            "version": REGISTRY_STATE_VERSION,
            "models": {},
            "active_model_id": None,
            "active_models_by_endpoint": {},
        }

    def _atomic_write_json_unlocked(
        self, destination: Path, payload: Dict[str, Any]
    ) -> None:
        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.models_dir,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, destination)
        except OSError:
            raise RuntimeError("Failed to write activity model registry file") from None
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    logger.warning("Failed to clean activity registry temp file")

    def _load_state_unlocked(self) -> Dict[str, Any]:
        try:
            resolved_state = self.state_path.resolve(strict=True)
            if (
                not resolved_state.is_relative_to(self.models_dir)
                or not resolved_state.is_file()
                or self.state_path.is_symlink()
            ):
                raise ValueError
            with resolved_state.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, RuntimeError, json.JSONDecodeError, ValueError):
            raise ValueError("Invalid activity model registry state") from None

        if (
            not isinstance(payload, dict)
            or type(payload.get("version")) is not int
            or payload.get("version") not in {1, REGISTRY_STATE_VERSION}
            or not isinstance(payload.get("models"), dict)
        ):
            raise ValueError("Invalid activity model registry state")

        models = payload["models"]
        seen_weights: set[str] = set()
        for model_id, metadata in models.items():
            self._validate_model_id(model_id)
            if not isinstance(metadata, dict) or metadata.get("model_id") != model_id:
                raise ValueError("Invalid activity model registry state")
            weights_file = self._validate_weights_basename(metadata.get("weights_file"))
            if weights_file in seen_weights:
                raise ValueError("Invalid activity model registry state")
            seen_weights.add(weights_file)

        self._validate_artifact_ownership(models)

        active_model_id = payload.get("active_model_id")
        if active_model_id is not None:
            self._validate_model_id(active_model_id)
            if active_model_id not in models:
                raise ValueError("Invalid activity model registry state")
        mapping = {} if payload["version"] == 1 else payload.get("active_models_by_endpoint")
        if not isinstance(mapping, dict):
            raise ValueError("Invalid active_models_by_endpoint")
        for key, model_id in mapping.items():
            if (not isinstance(key, str) or not isinstance(model_id, str)
                    or model_id not in models):
                raise ValueError("Invalid active_models_by_endpoint")
            record = validate_endpoint_metadata(models[model_id])
            if record.get("scientific_readiness") != "endpoint_ready" or record.get("endpoint_key") != key:
                raise ValueError("Invalid active_models_by_endpoint endpoint_key")
        state = {
            "version": REGISTRY_STATE_VERSION,
            "models": dict(models),
            "active_model_id": active_model_id,
            "active_models_by_endpoint": dict(mapping),
        }
        if payload["version"] == 1:
            self._atomic_write_json_unlocked(self.state_path, state)
        return state

    def _load_legacy_active_unlocked(self, models: Dict[str, Dict[str, Any]]) -> Optional[str]:
        if not self._legacy_active_path.exists() or self._legacy_active_path.is_symlink():
            return None
        try:
            resolved = self._legacy_active_path.resolve(strict=True)
            if not resolved.is_relative_to(self.models_dir) or not resolved.is_file():
                return None
            with resolved.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            model_id = payload.get("model_id") if isinstance(payload, dict) else None
            return model_id if model_id in models else None
        except (OSError, RuntimeError, json.JSONDecodeError):
            return None

    def _upgrade_legacy_sidecar(self, metadata: Any) -> Any:
        if not isinstance(metadata, dict):
            return metadata
        missing_fields = _LEGACY_PROVENANCE_FIELDS - metadata.keys()
        if not missing_fields:
            return metadata
        if not _LEGACY_REQUIRED_FIELDS.issubset(metadata):
            return metadata

        upgraded = dict(metadata)
        model_id = self._validate_model_id(upgraded.get("model_id"))
        expected_weights_file = f"model_rgmpnn_{model_id}.pt"
        if upgraded.get("weights_file") != expected_weights_file:
            raise ValueError("Legacy sidecar does not match trainer checkpoint naming")
        weights_path = self._resolve_weights_name(expected_weights_file)

        if "model_format" in missing_fields:
            upgraded["model_format"] = "pytorch_state_dict"
        if "weights_sha256" in missing_fields:
            upgraded["weights_sha256"] = self._sha256_file(weights_path)
        if "metrics" in missing_fields:
            best_metrics = upgraded.get("best_metrics")
            if not isinstance(best_metrics, dict):
                raise ValueError("Legacy sidecar has no valid metrics")
            upgraded["metrics"] = dict(best_metrics)
        return upgraded

    def _migrate_sidecars_unlocked(self) -> Dict[str, Any]:
        state = self._new_state()
        seen_weights: set[str] = set()
        for metadata_path in sorted(self.models_dir.glob("*_info.json")):
            try:
                if metadata_path.is_symlink():
                    raise ValueError("Invalid model registry sidecar")
                resolved = metadata_path.resolve(strict=True)
                if not resolved.is_relative_to(self.models_dir) or not resolved.is_file():
                    raise ValueError("Invalid model registry sidecar")
                with resolved.open("r", encoding="utf-8") as handle:
                    legacy_metadata = self._upgrade_legacy_sidecar(json.load(handle))
                    metadata = self._validate_metadata(legacy_metadata)
                model_id = metadata["model_id"]
                if metadata_path.name != f"{model_id}_info.json":
                    raise ValueError("Invalid model registry sidecar")
                if metadata["weights_file"] in seen_weights:
                    raise ValueError("Duplicate weights_file in model registry sidecars")
                self._validate_artifact_ownership({**state["models"], model_id: metadata})
                state["models"][model_id] = metadata
                seen_weights.add(metadata["weights_file"])
            except (OSError, json.JSONDecodeError, ValueError):
                logger.warning(
                    "Skipping invalid activity model registry record %s: invalid or unavailable",
                    metadata_path.name,
                )
        state["active_model_id"] = self._load_legacy_active_unlocked(state["models"])
        return state

    def _ensure_state_unlocked(self) -> None:
        if self.state_path.exists():
            self._load_state_unlocked()
            return
        state = self._migrate_sidecars_unlocked()
        self._atomic_write_json_unlocked(self.state_path, state)

    def _get_validated_unlocked(
        self, state: Dict[str, Any], model_id: str
    ) -> Dict[str, Any]:
        safe_model_id = self._validate_model_id(model_id)
        metadata = state["models"].get(safe_model_id)
        if metadata is None:
            raise ValueError("Model is not registered")
        try:
            return self._validate_metadata(metadata)
        except ValueError:
            raise ValueError("Registered model record is invalid") from None

    def _write_sidecar_best_effort(self, metadata: Dict[str, Any]) -> None:
        try:
            self._atomic_write_json_unlocked(
                self._metadata_path(metadata["model_id"]), metadata
            )
        except (OSError, RuntimeError):
            logger.warning(
                "Failed to write activity model sidecar %s",
                f"{metadata['model_id']}_info.json",
            )

    def register(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        with self._transaction():
            state = self._load_state_unlocked()
            validated = self._validate_metadata(metadata)
            model_id = validated["model_id"]
            if model_id in state["models"]:
                raise ValueError("Model is already registered")
            if any(
                item.get("weights_file") == validated["weights_file"]
                for item in state["models"].values()
            ):
                raise ValueError("weights_file is already registered")
            self._validate_artifact_ownership({**state["models"], model_id: validated})
            state["models"][model_id] = validated
            self._atomic_write_json_unlocked(self.state_path, state)
            self._write_sidecar_best_effort(validated)
            return validated

    def get(self, model_id: str) -> Dict[str, Any]:
        with self._transaction():
            state = self._load_state_unlocked()
            return self._get_validated_unlocked(state, model_id)

    def _warn_unmanaged_sidecars_unlocked(self, state: Dict[str, Any]) -> None:
        registered_names = {
            f"{model_id}_info.json" for model_id in state["models"]
        }
        for metadata_path in self.models_dir.glob("*_info.json"):
            if metadata_path.name not in registered_names:
                logger.warning(
                    "Skipping invalid activity model registry record %s: not present in registry state",
                    metadata_path.name,
                )

    def list(self) -> List[Dict[str, Any]]:
        with self._transaction():
            state = self._load_state_unlocked()
            self._warn_unmanaged_sidecars_unlocked(state)
            models: List[Dict[str, Any]] = []
            for model_id in state["models"]:
                try:
                    models.append(self._get_validated_unlocked(state, model_id))
                except ValueError as exc:
                    logger.warning(
                        "Skipping invalid activity model registry record %s: %s",
                        f"{model_id}_info.json",
                        exc,
                    )

            def created_at(item: Dict[str, Any]) -> float:
                try:
                    return float(item.get("created_at", 0))
                except (TypeError, ValueError):
                    return 0.0

            models.sort(key=created_at, reverse=True)
            return models

    def resolve_weights(self, model_id: str) -> Path:
        with self._transaction():
            state = self._load_state_unlocked()
            metadata = self._get_validated_unlocked(state, model_id)
            return self._resolve_weights_name(metadata["weights_file"])

    def select(self, model_id: str) -> Dict[str, Any]:
        with self._transaction():
            state = self._load_state_unlocked()
            metadata = self._get_validated_unlocked(state, model_id)
            state["active_model_id"] = metadata["model_id"]
            self._atomic_write_json_unlocked(self.state_path, state)
            return metadata

    def get_active_model_id(self) -> Optional[str]:
        with self._transaction():
            state = self._load_state_unlocked()
            model_id = state["active_model_id"]
            if model_id is None:
                return None
            try:
                self._get_validated_unlocked(state, model_id)
                return model_id
            except ValueError as exc:
                logger.warning("Ignoring invalid active activity model selection: %s", exc)
                return None

    def select_for_endpoint(self, endpoint_key: str, model_id: str) -> Dict[str, Any]:
        with self._transaction():
            state = self._load_state_unlocked()
            metadata = self._get_validated_unlocked(state, model_id)
            if (metadata.get("scientific_readiness") != "endpoint_ready"
                    or metadata.get("endpoint_key") != endpoint_key):
                raise ValueError("endpoint_key does not match endpoint_ready model")
            state["active_models_by_endpoint"][endpoint_key] = metadata["model_id"]
            self._atomic_write_json_unlocked(self.state_path, state)
            return metadata

    def get_active_for_endpoint(self, endpoint_key: str) -> Optional[Dict[str, Any]]:
        with self._transaction():
            state = self._load_state_unlocked()
            model_id = state["active_models_by_endpoint"].get(endpoint_key) if isinstance(endpoint_key, str) else None
            if model_id is None:
                return None
            try:
                return self._get_validated_unlocked(state, model_id)
            except ValueError:
                logger.warning("Ignoring invalid endpoint activity model: unavailable")
                return None

    def get_active(self) -> Optional[Dict[str, Any]]:
        with self._transaction():
            state = self._load_state_unlocked()
            model_id = state["active_model_id"]
            if model_id is None:
                return None
            try:
                return self._get_validated_unlocked(state, model_id)
            except ValueError as exc:
                logger.warning("Ignoring invalid active activity model selection: %s", exc)
                return None

    def _cleanup_artifact_best_effort(self, artifact: Path) -> None:
        try:
            artifact.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "Failed to clean activity model artifact %s",
                artifact.name,
            )

    def delete(self, model_id: str) -> bool:
        with self._transaction():
            state = self._load_state_unlocked()
            metadata = self._get_validated_unlocked(state, model_id)
            weights_path = self._resolve_weights_name(metadata["weights_file"])
            sidecar_path = self._metadata_path(metadata["model_id"])
            card_path = (self._resolve_card(metadata["model_card_file"])
                         if metadata.get("scientific_readiness") == "endpoint_ready" else None)
            was_active = state["active_model_id"] == metadata["model_id"]
            del state["models"][metadata["model_id"]]
            if was_active:
                state["active_model_id"] = None
            state["active_models_by_endpoint"] = {
                key: selected for key, selected in state["active_models_by_endpoint"].items()
                if selected != metadata["model_id"]
            }
            self._atomic_write_json_unlocked(self.state_path, state)
            self._cleanup_artifact_best_effort(sidecar_path)
            self._cleanup_artifact_best_effort(weights_path)
            if card_path is not None:
                self._cleanup_artifact_best_effort(card_path)
            if was_active:
                self._cleanup_artifact_best_effort(self._legacy_active_path)
        return True
