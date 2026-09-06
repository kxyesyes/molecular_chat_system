from __future__ import annotations

from src.agent.tooling import ToolRegistry

from .base import SpecialistAgent
from .contracts import AgentTask, AgentTaskResult


class TargetAgent(SpecialistAgent):
    name = "target"
    allowed_tools = {"target_database_search"}


class ReverseTargetAgent(SpecialistAgent):
    name = "reverse_target"
    allowed_tools = {"reverse_target_predictor"}


class MolecularDesignAgent(SpecialistAgent):
    name = "molecular_design"
    allowed_tools = {"llm_molecular_generator", "candidate_ranker"}


class PropertyAdmetAgent(SpecialistAgent):
    name = "property_admet"
    allowed_tools = {
        "property_calculator",
        "drug_likeness_assessment",
        "admet_predictor",
    }


class ActivityAgent(SpecialistAgent):
    name = "activity"
    allowed_tools = {"activity_predictor"}


class DockingAgent(SpecialistAgent):
    name = "docking"
    allowed_tools = {
        "molecular_docking",
        "prepare_receptor",
        "prepare_ligand",
        "run_docking",
        "get_docking_result",
    }


class ReportAgent(SpecialistAgent):
    name = "report"
    allowed_tools: set[str] = set()

    def execute_task(
        self, task: AgentTask, registry: ToolRegistry
    ) -> AgentTaskResult:
        if task.allowed_tools:
            return self._error(
                task,
                code=self._unauthorized_code(),
                message="Report Agent is read-only and cannot execute tools",
            )
        validated_results = task.inputs.get("validated_results", [])
        return AgentTaskResult(
            task_id=task.task_id,
            status="succeeded",
            outputs={
                "validated_results": validated_results,
                "summary": task.inputs.get("summary", ""),
            },
        )

    @staticmethod
    def _unauthorized_code():
        from src.agent.contracts import AgentErrorCode

        return AgentErrorCode.UNAUTHORIZED_TOOL


def build_default_specialists() -> dict[str, SpecialistAgent]:
    agents = [
        TargetAgent(),
        ReverseTargetAgent(),
        MolecularDesignAgent(),
        PropertyAdmetAgent(),
        ActivityAgent(),
        DockingAgent(),
        ReportAgent(),
    ]
    return {agent.name: agent for agent in agents}
