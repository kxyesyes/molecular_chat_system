from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


class BindingResolutionError(ValueError):
    pass


class BindingResolver:
    OUTPUT = re.compile(r"^\$\.outputs\.([A-Za-z0-9_-]+)$")

    def resolve(
        self,
        selector: str,
        transform: str,
        request: Mapping[str, Any],
        outputs: Mapping[str, Any],
    ) -> Any:
        if selector == "$.request.query":
            value = request.get("query")
        elif selector == "$.workflow":
            value = {
                "query": request.get("query"),
                "metadata": dict(request.get("metadata") or {}),
                "outputs": dict(outputs),
            }
        else:
            match = self.OUTPUT.fullmatch(selector)
            if not match:
                raise BindingResolutionError(
                    f"Unsupported binding selector: {selector}"
                )
            key = match.group(1)
            if key not in outputs:
                raise BindingResolutionError(f"Missing bound output: {key}")
            value = outputs[key]

        if transform == "identity":
            return value
        if transform == "smiles_text":
            return self._smiles_text(value)
        raise BindingResolutionError(
            f"Unsupported binding transform: {transform}"
        )

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
