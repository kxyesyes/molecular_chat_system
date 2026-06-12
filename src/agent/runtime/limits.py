from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass(frozen=True)
class RuntimeLimits:
    llm_generate_seconds: int = 60
    tool_default_seconds: int = 30
    reverse_target_seconds: int = 120
    pharmacophore_single_candidate_seconds: int = 8
    docking_seconds: int = 180
    reverse_target_concurrency: int = 2
    docking_concurrency: int = 1
    llm_concurrency: int = 2

    @classmethod
    def from_dict(cls, values: dict | None) -> "RuntimeLimits":
        if not values:
            return cls()
        allowed = {field.name for field in fields(cls)}
        filtered = {key: value for key, value in values.items() if key in allowed}
        return cls(**filtered)


def get_default_limits() -> RuntimeLimits:
    return RuntimeLimits()
