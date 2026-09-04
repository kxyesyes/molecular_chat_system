from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.agent.orchestrators.base import WorkflowStep


@dataclass(frozen=True)
class SemanticDecision:
    allowed: bool
    requirement: str | None = None
    reason: str | None = None
    evidence_digest: str | None = None


class SemanticInputValidator:
    """Validate scientific meaning before a tool is allowed to start."""

    def validate(self, step: WorkflowStep, input_data: Any) -> SemanticDecision:
        for requirement in step.preconditions:
            if requirement == "target_evidence":
                records = self._target_records(input_data)
                usable = [
                    dict(item)
                    for item in records
                    if self._usable_target_record(item)
                ]
                if not usable:
                    return SemanticDecision(
                        False,
                        requirement,
                        "target_evidence_missing",
                    )
                digest = hashlib.sha256(
                    json.dumps(
                        usable,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()
                return SemanticDecision(
                    True,
                    requirement,
                    evidence_digest=digest,
                )
        return SemanticDecision(True)

    @classmethod
    def _target_records(cls, input_data: Any) -> list[Mapping[str, Any]]:
        if isinstance(input_data, (list, tuple)):
            return [item for item in input_data if isinstance(item, Mapping)]
        if not isinstance(input_data, Mapping):
            return []
        if all(
            key in input_data for key in ("query", "metadata", "outputs")
        ):
            outputs = input_data.get("outputs")
            if not isinstance(outputs, Mapping):
                return []
            return cls._target_records(outputs.get("target"))
        for key in ("targets", "records", "results", "data"):
            value = input_data.get(key)
            if isinstance(value, (list, tuple)):
                return [item for item in value if isinstance(item, Mapping)]
        return [input_data]

    @classmethod
    def _usable_target_record(cls, record: Mapping[str, Any]) -> bool:
        if cls._has_untrusted_mode(record):
            return False
        identifiers = (
            "gene_symbol",
            "target_identifier",
            "uniprot_id",
            "target_name",
        )
        evidence_fields = (
            "source",
            "database",
            "uniprot_id",
            "recommended_structures",
            "structures",
            "artifacts",
            "evidence",
        )
        return any(cls._meaningful(record.get(key)) for key in identifiers) and any(
            cls._meaningful(record.get(key)) for key in evidence_fields
        )

    @classmethod
    def _has_untrusted_mode(cls, value: Any) -> bool:
        if isinstance(value, Mapping):
            for key in ("demo_mode", "fallback_used", "is_demo", "fallback"):
                if cls._enabled_flag(value.get(key)):
                    return True
            quality = value.get("quality")
            if isinstance(quality, Mapping) and cls._has_untrusted_mode(quality):
                return True
        return False

    @staticmethod
    def _enabled_flag(value: Any) -> bool:
        if value is True:
            return True
        return isinstance(value, str) and value.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @classmethod
    def _meaningful(cls, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().lower() not in {
                "",
                "none",
                "null",
                "n/a",
                "unknown",
            }
        if isinstance(value, Mapping):
            return bool(value) and not cls._has_untrusted_mode(value)
        if isinstance(value, (list, tuple, set)):
            return any(cls._meaningful(item) for item in value)
        return bool(value)


__all__ = ["SemanticDecision", "SemanticInputValidator"]
