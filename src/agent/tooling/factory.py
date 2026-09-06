from __future__ import annotations

from typing import Any, Iterable

from pydantic import BaseModel

from .adapters import LegacyPythonToolAdapter
from .registry import ToolRegistry
from .spec import RetryPolicy, ToolSpec


TOOL_AGENT_OWNERS = {
    "target_database_search": "target",
    "reverse_target_predictor": "reverse_target",
    "llm_molecular_generator": "molecular_design",
    "candidate_ranker": "molecular_design",
    "property_calculator": "property_admet",
    "drug_likeness_assessment": "property_admet",
    "admet_predictor": "property_admet",
    "activity_predictor": "activity",
    "molecular_docking": "docking",
    "prepare_receptor": "docking",
    "prepare_ligand": "docking",
    "run_docking": "docking",
    "get_docking_result": "docking",
    "rag_search": "rag",
    "rag_database_search": "rag",
}


class LegacyQueryInput(BaseModel):
    query: Any


def build_tool_registry(tools: Iterable[Any]) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        name = getattr(tool, "name", tool.__class__.__name__)
        owner = TOOL_AGENT_OWNERS.get(name)
        if not owner:
            continue
        spec = ToolSpec(
            name=name,
            version=str(getattr(tool, "version", "1")),
            description=str(getattr(tool, "description", name)),
            input_schema=LegacyQueryInput,
            output_schema=None,
            capabilities={owner},
            timeout_seconds=float(getattr(tool, "timeout_seconds", 180.0)),
            retry_policy=RetryPolicy(
                max_attempts=2 if name in {"target_database_search"} else 1,
                retryable_error_codes={"provider_error"},
            ),
            side_effects=(
                "filesystem"
                if name
                in {
                    "molecular_docking",
                    "prepare_receptor",
                    "prepare_ligand",
                    "run_docking",
                }
                else "none"
            ),
            idempotent=name not in {"molecular_docking", "run_docking"},
            sensitive_fields={"api_key", "authorization", "token"},
            owner_agents={owner},
            aliases=set(getattr(tool, "aliases", set())),
        )
        registry.register(LegacyPythonToolAdapter(spec, tool))
    return registry
