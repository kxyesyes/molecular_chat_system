from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

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
}
_TASK_TYPES = {"regression", "classification"}


class ActivityModelRegistry:
    """Confined registry for locally trained activity-model checkpoints."""

    def __init__(self, models_dir: Path | str):
        directory = Path(models_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.models_dir = directory.resolve(strict=True)
        self._active_path = self.models_dir / "active_model.json"

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

    def _metadata_path(self, model_id: str) -> Path:
        safe_model_id = self._validate_model_id(model_id)
        return self.models_dir / f"{safe_model_id}_info.json"

    def _resolve_weights_name(self, weights_file: Any) -> Path:
        if not isinstance(weights_file, str) or not weights_file:
            raise ValueError("Invalid weights_file")
        if (
            Path(weights_file).is_absolute()
            or Path(weights_file).name != weights_file
            or "/" in weights_file
            or "\\" in weights_file
        ):
            raise ValueError("Invalid weights_file")

        candidate = self.models_dir / weights_file
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
        self._resolve_weights_name(metadata.get("weights_file"))

        if metadata.get("task_type") not in _TASK_TYPES:
            raise ValueError("Invalid task_type")
        for field in ("endpoint", "units", "split_strategy"):
            self._require_non_empty_string(metadata, field)
        if not isinstance(metadata.get("dataset_sha256"), str) or not _SHA256_PATTERN.fullmatch(
            metadata["dataset_sha256"]
        ):
            raise ValueError("Invalid dataset_sha256")
        random_seed = metadata.get("random_seed")
        if isinstance(random_seed, bool) or not isinstance(random_seed, int):
            raise ValueError("Invalid random_seed")
        if not isinstance(metadata.get("model_config"), dict) or not metadata["model_config"]:
            raise ValueError("Invalid model_config")

        try:
            json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError):
            raise ValueError("Model metadata is not JSON serializable") from None
        return dict(metadata)

    def _atomic_write_json(self, destination: Path, payload: Dict[str, Any]) -> None:
        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.models_dir,
                prefix=".activity-registry-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, destination)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def _load_metadata_path(
        self, metadata_path: Path, expected_model_id: Optional[str] = None
    ) -> Dict[str, Any]:
        try:
            resolved_metadata = metadata_path.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ValueError("Model is not registered") from None
        if (
            not resolved_metadata.is_relative_to(self.models_dir)
            or not resolved_metadata.is_file()
            or metadata_path.is_symlink()
        ):
            raise ValueError("Invalid model registry record")

        try:
            with resolved_metadata.open("r", encoding="utf-8") as handle:
                metadata = json.load(handle)
        except (OSError, json.JSONDecodeError):
            raise ValueError("Invalid model registry record") from None

        validated = self._validate_metadata(metadata)
        model_id = validated["model_id"]
        if expected_model_id is not None and model_id != expected_model_id:
            raise ValueError("Invalid model registry record")
        if metadata_path.name != f"{model_id}_info.json":
            raise ValueError("Invalid model registry record")
        return validated

    def register(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        validated = self._validate_metadata(metadata)
        destination = self._metadata_path(validated["model_id"])
        self._atomic_write_json(destination, validated)
        return validated

    def get(self, model_id: str) -> Dict[str, Any]:
        safe_model_id = self._validate_model_id(model_id)
        return self._load_metadata_path(
            self._metadata_path(safe_model_id), expected_model_id=safe_model_id
        )

    def list(self) -> List[Dict[str, Any]]:
        models: List[Dict[str, Any]] = []
        for metadata_path in self.models_dir.glob("*_info.json"):
            try:
                models.append(self._load_metadata_path(metadata_path))
            except ValueError as exc:
                logger.warning(
                    "Skipping invalid activity model registry record %s: %s",
                    metadata_path.name,
                    exc,
                )

        def created_at(metadata: Dict[str, Any]) -> float:
            try:
                return float(metadata.get("created_at", 0))
            except (TypeError, ValueError):
                return 0.0

        models.sort(key=created_at, reverse=True)
        return models

    def resolve_weights(self, model_id: str) -> Path:
        metadata = self.get(model_id)
        return self._resolve_weights_name(metadata["weights_file"])

    def select(self, model_id: str) -> Dict[str, Any]:
        metadata = self.get(model_id)
        self._atomic_write_json(self._active_path, {"model_id": metadata["model_id"]})
        return metadata

    def get_active_model_id(self) -> Optional[str]:
        if not self._active_path.exists():
            return None
        try:
            resolved_active = self._active_path.resolve(strict=True)
            if (
                not resolved_active.is_relative_to(self.models_dir)
                or not resolved_active.is_file()
                or self._active_path.is_symlink()
            ):
                raise ValueError("Invalid active model record")
            with resolved_active.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict) or set(payload) != {"model_id"}:
                raise ValueError("Invalid active model record")
            model_id = self._validate_model_id(payload["model_id"])
            self.get(model_id)
            return model_id
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("Ignoring invalid active activity model selection: %s", exc)
            return None

    def get_active(self) -> Optional[Dict[str, Any]]:
        model_id = self.get_active_model_id()
        return self.get(model_id) if model_id is not None else None

    def delete(self, model_id: str) -> bool:
        metadata = self.get(model_id)
        weights_path = self._resolve_weights_name(metadata["weights_file"])
        metadata_path = self._metadata_path(metadata["model_id"])
        was_active = self.get_active_model_id() == metadata["model_id"]

        weights_path.unlink()
        metadata_path.unlink()
        if was_active and self._active_path.exists():
            self._active_path.unlink()
        return True
