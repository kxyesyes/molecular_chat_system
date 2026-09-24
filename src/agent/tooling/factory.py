from __future__ import annotations

from typing import Any, Iterable
import logging

from pydantic import BaseModel
from src.agent.capabilities import capability_for_tool
from src.agent.capabilities.catalog import TOOL_ALIASES

from .adapters import LegacyPythonToolAdapter
from .activity_contract import ActivityPredictInput, ActivityPredictOutput, ActivityToolAdapter
from .analysis_contract import ANALYSIS_OUTPUT_SCHEMAS, AnalysisInput, AnalysisToolAdapter
from .docking_contract import DockingInput, DockingOutput, DockingToolAdapter
from .rag_contract import RAGSearchInput, RAGSearchOutput, RAGToolAdapter
from .generation_ranking_contract import (
    GenerationInput, GenerationOutput, GenerationToolAdapter,
    RankingInput, RankingOutput, RankingToolAdapter,
)
from .target_contract import (
    TargetSearchInput, TargetSearchOutput, ReverseTargetInput, ReverseTargetOutput, TargetToolAdapter,
)
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

# Legacy chat-only tool, not a capability or permitted workflow tool. Explicit
# exclusion instead of silently dropping unknown additions to the tool factory.
NON_WORKFLOW_TOOLS = {"rxn_chemistry_agent": "legacy chat-only; no workflow policy"}
LAZY_RUNTIME_TOOLS = frozenset({
    "target_database_search", "reverse_target_predictor", "activity_predictor",
    "llm_molecular_generator", "molecular_docking", "prepare_receptor",
    "prepare_ligand", "run_docking", "get_docking_result",
})


class LegacyQueryInput(BaseModel):
    query: Any


def build_tool_registry(tools: Iterable[Any]) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        name = getattr(tool, "name", tool.__class__.__name__)
        name = TOOL_ALIASES.get(name, name)
        owner = TOOL_AGENT_OWNERS.get(name)
        if not owner:
            if name in NON_WORKFLOW_TOOLS:
                logging.getLogger(__name__).info("Excluded non-workflow tool: %s (%s)", name, NON_WORKFLOW_TOOLS[name])
                continue
            raise ValueError(f"Unknown tool owner: {name}")
        capabilities = {owner}
        try:
            capabilities.add(capability_for_tool(name).name)
        except KeyError:
            pass  # Staged docking helpers have no standalone model capability.
        aliases = set(getattr(tool, "aliases", set()))
        aliases.update(alias for alias, canonical in TOOL_ALIASES.items() if canonical == name)
        if getattr(tool, "name", name) != name:
            aliases.discard(name)  # Legacy canonicalization, not a self-alias.
        spec = ToolSpec(
            name=name,
            version=str(getattr(tool, "version", "1")),
            description=str(getattr(tool, "description", name)),
            input_schema=(RAGSearchInput if name == "rag_search" else
                          GenerationInput if name == "llm_molecular_generator" else
                          RankingInput if name == "candidate_ranker" else
                          TargetSearchInput if name == "target_database_search" else
                          ReverseTargetInput if name == "reverse_target_predictor" else
                          DockingInput if name == "molecular_docking" else
                          ActivityPredictInput if name == "activity_predictor" else
                          AnalysisInput if name in ANALYSIS_OUTPUT_SCHEMAS else LegacyQueryInput),
            output_schema=(RAGSearchOutput if name == "rag_search" else
                           GenerationOutput if name == "llm_molecular_generator" else
                           RankingOutput if name == "candidate_ranker" else
                           TargetSearchOutput if name == "target_database_search" else
                           ReverseTargetOutput if name == "reverse_target_predictor" else
                           DockingOutput if name == "molecular_docking" else
                           ActivityPredictOutput if name == "activity_predictor" else
                           ANALYSIS_OUTPUT_SCHEMAS.get(name)),
            capabilities=capabilities,
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
            aliases=aliases,
        )
        adapter_class = (RAGToolAdapter if name == "rag_search" else
                         GenerationToolAdapter if name == "llm_molecular_generator" else
                         RankingToolAdapter if name == "candidate_ranker" else
                         TargetToolAdapter if name in {"target_database_search", "reverse_target_predictor"} else
                         DockingToolAdapter if name == "molecular_docking" else
                         ActivityToolAdapter if name == "activity_predictor" else
                         AnalysisToolAdapter if name in ANALYSIS_OUTPUT_SCHEMAS else LegacyPythonToolAdapter)
        registry.register(adapter_class(spec, tool, readiness_unknown=name in LAZY_RUNTIME_TOOLS))
    return registry
