from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import replace
from typing import Any

from src.agent.contracts import ScientificClaim, ToolProvenance, ToolResult


class EvidenceLedger:
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self._records: dict[str, dict[str, Any]] = {}
        self._claims: dict[str, ScientificClaim] = {}

    @staticmethod
    def output_digest(data: Any) -> str:
        """Hash JSON output using the decision/continuation wire representation."""
        active: set[int] = set()

        def validate(item: Any) -> None:
            kind = type(item)
            if item is None or kind in (str, bool, int):
                return
            if kind is float:
                if not math.isfinite(item):
                    raise ValueError("output digest requires finite JSON numbers")
                return
            if kind not in (list, dict):
                raise TypeError("output digest requires exact JSON builtin types")
            identity = id(item)
            if identity in active:
                raise ValueError("Circular reference in output digest JSON")
            active.add(identity)
            try:
                if kind is dict:
                    for key, child in item.items():
                        if type(key) is not str:
                            raise TypeError("output digest JSON keys must be strings")
                        validate(child)
                else:
                    for child in item:
                        validate(child)
            finally:
                active.remove(identity)

        validate(data)
        return hashlib.sha256(
            json.dumps(
                data,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()

    def prepare_provenance(
        self,
        input_digest: str,
        result: ToolResult,
    ) -> ToolProvenance:
        """Attach provenance without registering unvalidated result data."""
        provenance = result.provenance
        if provenance is None:
            provenance = ToolProvenance(
                tool_name=result.tool_name,
                tool_version=str(result.quality.get("tool_version") or "1"),
                model_name=result.quality.get("model"),
                model_version=result.quality.get("model_version"),
                demo_mode=bool(result.quality.get("demo_mode", False)),
                fallback_used=bool(result.quality.get("fallback_used", False)),
                input_digest=input_digest,
            )
        else:
            provenance = replace(
                provenance,
                tool_name=result.tool_name,
                input_digest=input_digest,
            )
        result.provenance = provenance
        return provenance

    def register_tool_result(
        self,
        step_id: str,
        input_digest: str,
        result: ToolResult,
    ) -> str:
        provenance = self.prepare_provenance(input_digest, result)
        payload = {
            "trace_id": self.trace_id,
            "step_id": step_id,
            "tool_name": result.tool_name,
            "status": result.status.value if result.status else None,
            "input_digest": input_digest,
            "provenance": provenance.to_dict(),
            "evidence": result.evidence,
            "artifacts": [item.to_dict() for item in result.artifacts],
            "scientific_usable": bool(
                result.success
                and not provenance.demo_mode
                and not provenance.fallback_used
            ),
        }
        if result.quality.get("request_input_digest") is not None:
            payload["input_binding"] = {
                name: deepcopy(result.quality.get(name))
                for name in (
                    "request_input_digest",
                    "input_evidence_ids",
                    "operation_key",
                )
            }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        evidence_id = f"evidence-{digest[:20]}"
        self._records[evidence_id] = deepcopy({"evidence_id": evidence_id, **payload})
        return evidence_id

    def get(self, evidence_id: str) -> dict[str, Any]:
        return deepcopy(self._records[evidence_id])

    def accept_claim(self, claim: ScientificClaim) -> None:
        missing = [
            evidence_id
            for evidence_id in claim.evidence_ids
            if evidence_id not in self._records
        ]
        if missing:
            raise ValueError(f"Unknown evidence ids: {missing}")
        unusable = [
            evidence_id
            for evidence_id in claim.evidence_ids
            if not self._records[evidence_id]["scientific_usable"]
        ]
        if unusable:
            raise ValueError(f"Scientifically unusable evidence ids: {unusable}")
        self._claims[claim.claim_id] = deepcopy(claim)

    def claims(self) -> dict[str, ScientificClaim]:
        return deepcopy(self._claims)

    def to_list(self) -> list[dict[str, Any]]:
        return [deepcopy(self._records[key]) for key in sorted(self._records)]
