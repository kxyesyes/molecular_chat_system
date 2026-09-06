from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import re
from typing import Any

from src.agent.contracts.generation_request import (
    preserve_generation_trust_envelope,
)


class BindingResolutionError(ValueError):
    pass


class BindingResolver:
    OUTPUT = re.compile(r"^\$\.outputs\.([A-Za-z0-9_-]+)$")
    REQUEST_QUERY = "$.request.query"
    WORKFLOW = "$.workflow"
    SUPPORTED_TRANSFORMS = frozenset({"identity", "smiles_text"})

    @classmethod
    def derive_selector(cls, input_binding: Any, input_from: Any) -> str | None:
        if input_binding is not None and not isinstance(input_binding, str):
            raise BindingResolutionError("Invalid input binding selector")
        if input_from is not None and not isinstance(input_from, str):
            raise BindingResolutionError("Invalid input_from selector")
        if input_binding:
            return input_binding
        if input_from:
            return f"$.outputs.{input_from}"
        if input_binding == "":
            raise BindingResolutionError("Invalid empty input binding selector")
        if input_from == "":
            raise BindingResolutionError("Invalid empty input_from selector")
        return None

    @classmethod
    def validate_transform(cls, transform: Any) -> str:
        if not isinstance(transform, str) or transform not in cls.SUPPORTED_TRANSFORMS:
            raise BindingResolutionError(
                f"Unsupported input transform: {transform!r}"
            )
        return transform

    @classmethod
    def normalize_transform(
        cls,
        input_binding: Any,
        input_from: Any,
        transform: Any,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        """Normalize the historical input_from shape contract explicitly."""
        normalized = cls.validate_transform(transform)
        if input_binding is None and input_from and normalized == "identity":
            if not isinstance(metadata, Mapping) or metadata.get("input_mode") != "raw":
                return "smiles_text"
        return normalized

    @classmethod
    def output_key(cls, selector: str) -> str:
        match = cls.OUTPUT.fullmatch(selector)
        if not match:
            raise BindingResolutionError(f"Unsupported binding selector: {selector}")
        return match.group(1)

    def resolve(
        self,
        selector: str,
        transform: str,
        request: Mapping[str, Any],
        outputs: Mapping[str, Any],
        workflow_output_keys: Sequence[str] | None = None,
        workflow_optional_output_keys: Sequence[str] | None = None,
        workflow_metadata_keys: Sequence[str] | None = None,
    ) -> Any:
        self.validate_transform(transform)
        if not isinstance(selector, str) or not selector:
            raise BindingResolutionError("Invalid input binding selector")
        if selector == self.REQUEST_QUERY:
            value = request.get("query")
        elif selector == self.WORKFLOW:
            request_metadata = request.get("metadata")
            if workflow_metadata_keys is not None:
                metadata = self._select_mapping_keys(
                    request_metadata,
                    workflow_metadata_keys,
                    "workflow metadata",
                )
            else:
                metadata = (
                    {
                        "requested_count": request_metadata["requested_count"]
                    }
                    if isinstance(request_metadata, Mapping)
                    and "requested_count" in request_metadata
                    else {}
                )
            if workflow_output_keys is not None:
                workflow_outputs = self._select_mapping_keys(
                    outputs,
                    workflow_output_keys,
                    "workflow outputs",
                    optional_keys=workflow_optional_output_keys,
                )
                return {
                    "query": request.get("query"),
                    "metadata": deepcopy(metadata),
                    "outputs": deepcopy(workflow_outputs),
                }
            trust_source = dict(request)
            trust_source["outputs"] = outputs
            trust_envelope = preserve_generation_trust_envelope(trust_source)
            metadata_trust = trust_envelope.get("metadata")
            if isinstance(metadata_trust, Mapping):
                metadata.update(metadata_trust)
            workflow_outputs = (
                {"target": outputs["target"]} if "target" in outputs else {}
            )
            outputs_trust = trust_envelope.get("outputs")
            if isinstance(outputs_trust, Mapping):
                workflow_outputs.update(outputs_trust)
            workflow_value = {
                "query": request.get("query"),
                "metadata": metadata,
                "outputs": workflow_outputs,
            }
            if "quality" in trust_envelope:
                workflow_value["quality"] = trust_envelope["quality"]
            if "trust_envelope" in trust_envelope:
                workflow_value["trust_envelope"] = trust_envelope[
                    "trust_envelope"
                ]
            value = deepcopy(workflow_value)
        else:
            key = self.output_key(selector)
            if key not in outputs:
                raise BindingResolutionError(f"Missing bound output: {key}")
            value = outputs[key]

        if transform == "identity":
            return value
        if transform == "smiles_text":
            return self._smiles_text(value)
        raise BindingResolutionError(f"Unsupported input transform: {transform}")

    @staticmethod
    def _select_mapping_keys(
        value: Any,
        keys: Sequence[str],
        label: str,
        optional_keys: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            value = {}
        if isinstance(keys, (str, bytes)) or not isinstance(keys, Sequence):
            raise BindingResolutionError(f"Invalid {label} key allowlist")
        seen: set[str] = set()
        for key in keys:
            if not isinstance(key, str) or not key or key in seen:
                raise BindingResolutionError(f"Invalid {label} key allowlist")
            seen.add(key)
        if isinstance(optional_keys, (str, bytes)) or (
            optional_keys is not None
            and not isinstance(optional_keys, Sequence)
        ):
            raise BindingResolutionError(f"Invalid {label} optional key allowlist")
        optional_values = tuple(optional_keys or ())
        if any(
            not isinstance(key, str) or not key
            for key in optional_values
        ):
            raise BindingResolutionError(f"Invalid {label} optional key allowlist")
        optional = set(optional_values)
        if len(optional) != len(optional_values) or not optional.issubset(seen):
            raise BindingResolutionError(f"Invalid {label} optional key allowlist")
        selected: dict[str, Any] = {}
        for key in keys:
            if key not in value:
                if key in optional:
                    selected[key] = []
                    continue
                raise BindingResolutionError(f"Missing bound output: {key}")
            selected[key] = value[key]
        return selected

    @classmethod
    def _smiles_text(cls, value: Any) -> str:
        smiles: list[str] = []

        def collect(item: Any) -> None:
            if isinstance(item, str):
                if item.strip():
                    smiles.append(item.strip())
            elif isinstance(item, Mapping):
                direct = item.get("smiles")
                if isinstance(direct, str) and direct.strip():
                    smiles.append(direct.strip())
                for key in ("molecules", "candidates", "data"):
                    if key in item:
                        collect(item[key])
            elif isinstance(item, (list, tuple)):
                for child in item:
                    collect(child)

        collect(value)
        return "\n".join(dict.fromkeys(smiles))
