from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    tool_names: tuple[str, ...]
    risk: str
    approval_required: bool
    scientific_result: bool = True


CAPABILITY_CATALOG = (
    CapabilitySpec("molecule.properties", ("property_calculator",), "low", False),
    CapabilitySpec(
        "molecule.drug_likeness",
        ("drug_likeness_assessment",),
        "low",
        False,
    ),
    CapabilitySpec("molecule.admet", ("admet_predictor",), "medium", False),
    CapabilitySpec("molecule.activity", ("activity_predictor",), "medium", False),
    CapabilitySpec(
        "molecule.generate",
        ("llm_molecular_generator",),
        "medium",
        False,
    ),
    CapabilitySpec("candidate.rank", ("candidate_ranker",), "low", False),
    CapabilitySpec(
        "target.reverse_predict",
        ("reverse_target_predictor",),
        "medium",
        False,
    ),
    CapabilitySpec(
        "target.structure.search",
        ("target_database_search",),
        "low",
        False,
    ),
    CapabilitySpec("docking.execute", ("molecular_docking",), "high", True),
    CapabilitySpec("knowledge.retrieve", ("rag_search",), "low", False, False),
)

_BY_NAME = {item.name: item for item in CAPABILITY_CATALOG}
_BY_TOOL = {
    tool_name: item
    for item in CAPABILITY_CATALOG
    for tool_name in item.tool_names
}


def get_capability(name: str) -> CapabilitySpec:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unknown capability: {name}") from exc


def capability_for_tool(tool_name: str) -> CapabilitySpec:
    try:
        return _BY_TOOL[tool_name]
    except KeyError as exc:
        raise KeyError(f"Unknown tool capability: {tool_name}") from exc
