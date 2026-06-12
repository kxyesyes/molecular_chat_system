from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping
from uuid import uuid4

from src.agent.contracts import AgentContext, AgentResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.planning.task_planner import TaskPlanner
from src.agent.skills.skill_registry import SkillRegistry


class SupervisorAgent:
    """Plan and execute multi-step CADD workflows across existing tools."""

    def __init__(
        self,
        tools: Mapping[str, Any] | None = None,
        planner: TaskPlanner | None = None,
        registry: SkillRegistry | None = None,
        orchestrator: WorkflowOrchestrator | None = None,
    ):
        self.tools = dict(tools) if tools is not None else build_default_tools()
        self.planner = planner or TaskPlanner()
        self.registry = registry or SkillRegistry()
        self.orchestrator = orchestrator or WorkflowOrchestrator()

    def plan(
        self,
        query: str,
        skill_name: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self._build_context(query, skill_name, trace_id, metadata)
        workflow_plan = self.planner.plan(context)

        steps = workflow_plan.steps
        if not steps and context.active_skill in self.tools:
            steps = [
                WorkflowStep(
                    name=context.active_skill or "single_step",
                    tool_name=context.active_skill or "",
                    input_data=query,
                    output_key="result",
                )
            ]

        return {
            "trace_id": context.trace_id,
            "skill_name": context.active_skill,
            "workflow_name": workflow_plan.workflow_name,
            "metadata": workflow_plan.metadata,
            "steps": [self._step_to_dict(step) for step in steps],
        }

    def run(
        self,
        query: str,
        skill_name: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = self._build_context(query, skill_name, trace_id, metadata)
        workflow_plan = self.planner.plan(context)
        steps = workflow_plan.steps

        if not steps:
            return {
                "trace_id": context.trace_id,
                "status": "failed",
                "message": "未生成可执行工作流计划",
                "plan": {
                    "workflow_name": workflow_plan.workflow_name,
                    "steps": [],
                    "metadata": workflow_plan.metadata,
                },
                "result": None,
            }

        result = self.orchestrator.run(
            context=context,
            steps=steps,
            tools=self.tools,
            continue_on_error=True,
        )
        return self._format_run_result(workflow_plan.workflow_name, workflow_plan.metadata, steps, result)

    def _build_context(
        self,
        query: str,
        skill_name: str | None,
        trace_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> AgentContext:
        active_skill = skill_name or self._route_skill(query)
        return AgentContext(
            query=query,
            trace_id=trace_id or str(uuid4()),
            active_skill=active_skill,
            workflow_name=active_skill,
            metadata=metadata or {},
        )

    def _route_skill(self, query: str) -> str | None:
        routed = self.registry.route_by_rules(query)
        if routed:
            return routed.name
        query_lower = query.lower()
        if any(token in query_lower for token in ["设计", "生成", "design", "generate"]) and any(
            token in query_lower for token in ["pde", "egfr", "kras", "braf", "target", "靶点"]
        ):
            return "target_driven_design"
        if any(token in query_lower for token in ["全面", "综合", "comprehensive"]):
            return "comprehensive_evaluation"
        return None

    @staticmethod
    def _step_to_dict(step: WorkflowStep) -> dict[str, Any]:
        data = asdict(step)
        data["required"] = bool(data.get("required", True))
        return data

    @staticmethod
    def _format_run_result(
        workflow_name: str,
        plan_metadata: dict[str, Any],
        steps: list[WorkflowStep],
        result: AgentResult,
    ) -> dict[str, Any]:
        return {
            "trace_id": result.trace_id,
            "status": "succeeded" if result.success else "partial" if result.partial else "failed",
            "message": result.message,
            "plan": {
                "workflow_name": workflow_name,
                "metadata": plan_metadata,
                "steps": [SupervisorAgent._step_to_dict(step) for step in steps],
            },
            "summary": {
                "completed_steps": len(result.tool_results),
                "successful_steps": sum(1 for item in result.tool_results if item.success),
                "failed_steps": sum(1 for item in result.tool_results if not item.success),
            },
            "result": result.to_legacy_dict(),
        }


def build_default_tools() -> dict[str, Any]:
    tools: dict[str, Any] = {}
    imports = [
        ("property_calculator", "src.agent.tools.property_calculator", "PropertyCalculator"),
        ("admet_predictor", "src.agent.tools.admet_predictor", "ADMETPredictor"),
        ("activity_predictor", "src.agent.tools.activity_predictor_tool", "ActivityPredictorTool"),
        ("reverse_target_predictor", "src.agent.tools.reverse_target_tool", "ReverseTargetTool"),
        ("target_database_search", "src.agent.tools.target_database_tool", "TargetDatabaseTool"),
        ("llm_molecular_generator", "src.agent.tools.llm_molecular_generator", "LLMMolecularGenerator"),
        ("molecular_docking", "src.agent.tools.molecular_docking", "MolecularDocking"),
    ]
    for tool_name, module_name, class_name in imports:
        try:
            module = __import__(module_name, fromlist=[class_name])
            tools[tool_name] = getattr(module, class_name)()
        except Exception:
            continue
    return tools
