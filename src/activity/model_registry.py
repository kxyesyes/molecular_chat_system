from __future__ import annotations

from contextlib import contextmanager
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
REGISTRY_STATE_VERSION = 1
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
        if not isinstance(weights_file, str) or not weights_file:
            raise ValueError("Invalid weights_file")
        if (
            Path(weights_file).is_absolute()
            or Path(weights_file).name != weights_file
            or "/" in weights_file
            or "\\" in weights_file
        ):
            raise ValueError("Invalid weights_file")
        return weights_file

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
        return dict(metadata)

    @staticmethod
    def _new_state() -> Dict[str, Any]:
        return {
            "version": REGISTRY_STATE_VERSION,
            "models": {},
            "active_model_id": None,
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
            or payload.get("version") != REGISTRY_STATE_VERSION
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

        active_model_id = payload.get("active_model_id")
        if active_model_id is not None:
            self._validate_model_id(active_model_id)
            if active_model_id not in models:
                raise ValueError("Invalid activity model registry state")
        return {
            "version": REGISTRY_STATE_VERSION,
            "models": dict(models),
            "active_model_id": active_model_id,
        }

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
            was_active = state["active_model_id"] == metadata["model_id"]
            del state["models"][metadata["model_id"]]
            if was_active:
                state["active_model_id"] = None
            self._atomic_write_json_unlocked(self.state_path, state)
            self._cleanup_artifact_best_effort(sidecar_path)
            self._cleanup_artifact_best_effort(weights_path)
            if was_active:
                self._cleanup_artifact_best_effort(self._legacy_active_path)
        return True
