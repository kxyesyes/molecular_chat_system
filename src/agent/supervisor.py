from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping
from uuid import uuid4

from src.agent.contracts import AgentContext, AgentResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.planning.task_planner import TaskPlanner
from src.agent.router import SkillRouter
from src.agent.skills.skill_registry import SkillRegistry
from src.agent.specialists import AgentTask, AgentTaskResult, SpecialistAgent
from src.agent.tooling import RetryPolicy, TOOL_AGENT_OWNERS, ToolRegistry
from src.agent.persistence.base import AgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
from src.agent.runtime.workflow_executor import WorkflowExecutor


class SupervisorAgent:
    """Plan and execute multi-step CADD workflows across existing tools."""

    def __init__(
        self,
        tools: Mapping[str, Any] | None = None,
        planner: TaskPlanner | None = None,
        registry: SkillRegistry | None = None,
        orchestrator: WorkflowOrchestrator | None = None,
        tool_registry: ToolRegistry | None = None,
        specialists: Mapping[str, SpecialistAgent] | None = None,
        state_store: AgentStateStore | None = None,
        event_bus: AgentEventBus | None = None,
        llm: Any = None,
        molecular_generator_llm: Any = None,
        skill_router: SkillRouter | None = None,
    ):
        self.tools = dict(tools) if tools is not None else build_default_tools()
        self.llm = llm
        self.molecular_generator_llm = molecular_generator_llm
        self.skill_router = skill_router or SkillRouter()
        self.planner = planner or TaskPlanner()
        self.registry = registry or SkillRegistry()
        self.tool_registry = tool_registry
        self.specialists = dict(specialists or {})
        self.state_store = state_store
        self.event_bus = event_bus or (
            AgentEventBus(state_store=state_store) if state_store else None
        )
        self.orchestrator = orchestrator or WorkflowOrchestrator(
            event_bus=self.event_bus,
            state_store=state_store,
        )

    def set_llm(self, llm: Any) -> None:
        """Update the main model without rebinding the local generator."""
        self.llm = llm
        for tool_name, tool in self.tools.items():
            if tool_name == "llm_molecular_generator":
                continue
            if hasattr(tool, "llm"):
                tool.llm = llm

    def should_use_tools(self, query: str) -> bool:
        """Return whether the query maps to a supported scientific skill."""
        return self.skill_router.route(query, llm=self.llm) is not None

    def execute(
        self,
        query: str,
        temperature: float = 0.7,
        mol_count: int = 5,
        active_skill=None,
        event_callback=None,
    ) -> dict[str, Any]:
        """Chat-compatible entry point backed by the workflow runtime."""
        skill = active_skill or self.skill_router.route(query, llm=self.llm)
        if skill is None:
            return {
                "success": False,
                "final_answer": "No supported scientific workflow matched the request",
                "tools_used": [],
                "active_skill": None,
                "tool_results": {},
                "tool_result_sequence": [],
                "tool_results_by_step": {},
                "agent_events": [],
            }

        context = AgentContext(
            query=query,
            trace_id=f"agent-{uuid4().hex[:12]}",
            active_skill=skill.name,
            temperature=temperature,
            mol_count=mol_count,
        )
        execution = WorkflowExecutor(
            planner=self.planner,
            orchestrator=self.orchestrator,
        ).execute(
            context=context,
            skill=skill,
            all_tools=self.tools,
            event_callback=event_callback,
        )
        result = execution.result
        legacy_result = result.to_legacy_dict()
        return {
            "success": result.success or result.partial,
            "final_answer": result.final_answer or result.message,
            "tools_used": [item.tool_name for item in result.tool_results],
            "active_skill": skill.name,
            "tool_results": legacy_result["tool_results"],
            "tool_result_sequence": legacy_result["tool_result_sequence"],
            "tool_results_by_step": legacy_result["tool_results_by_step"],
            "partial": result.partial,
            "trace_id": result.trace_id,
            "agent_result": result,
            "agent_events": execution.events,
            "workflow_plan": {
                "workflow_name": execution.plan.workflow_name,
                "steps": [
                    step.tool_name for step in execution.plan.steps
                ],
                "metadata": execution.plan.metadata,
            },
        }

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
        metadata = metadata or {}
        idempotency_key = metadata.get("idempotency_key")
        if self.state_store and idempotency_key:
            existing = self.state_store.get_run_by_idempotency_key(idempotency_key)
            if existing:
                trace_id = existing["trace_id"]
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

        if self.tool_registry and self.specialists:
            return self._run_delegated(context, workflow_plan.workflow_name, workflow_plan.metadata, steps)

        skill = self.registry.get_skill_by_name(context.active_skill or "")
        if skill is None:
            return {
                "trace_id": context.trace_id,
                "status": "failed",
                "message": f"Unknown workflow skill: {context.active_skill}",
                "plan": {
                    "workflow_name": workflow_plan.workflow_name,
                    "steps": [self._step_to_dict(step) for step in steps],
                    "metadata": workflow_plan.metadata,
                },
                "result": None,
            }
        execution = WorkflowExecutor(
            planner=self.planner,
            orchestrator=self.orchestrator,
        ).execute(
            context=context,
            skill=skill,
            all_tools=self.tools,
            idempotency_key=idempotency_key,
        )
        return self._format_run_result(
            execution.plan.workflow_name,
            execution.plan.metadata,
            execution.plan.steps,
            execution.result,
        )

    def _run_delegated(
        self,
        context: AgentContext,
        workflow_name: str,
        plan_metadata: dict[str, Any],
        steps: list[WorkflowStep],
    ) -> dict[str, Any]:
        outputs: dict[str, Any] = {}
        delegations: list[dict[str, Any]] = []
        tool_results = []
        idempotency_key = context.metadata.get("idempotency_key")

        if self.state_store:
            self.state_store.start_run(
                {
                    "trace_id": context.trace_id,
                    "status": "running",
                    "skill_name": context.active_skill,
                    "query": context.query,
                    "workflow_version": "1",
                    "idempotency_key": idempotency_key,
                    "user_id": context.user_id,
                    "session_id": context.session_id,
                    "metadata": context.metadata,
                }
            )
        self._emit(
            context,
            TaskEventType.TASK_STARTED,
            "Supervisor workflow started",
            progress=0.0,
        )
        self._emit(
            context,
            TaskEventType.PLANNING_STARTED,
            "Supervisor planning started",
            progress=0.0,
        )
        self._emit(
            context,
            TaskEventType.PLANNING_COMPLETED,
            "Supervisor planning completed",
            progress=0.0,
            payload={"steps": [self._step_to_dict(step) for step in steps]},
        )

        for index, step in enumerate(steps):
            agent_name = TOOL_AGENT_OWNERS.get(step.tool_name)
            specialist = self.specialists.get(agent_name or "")
            if specialist is None:
                from src.agent.contracts import AgentErrorCode, ToolResult

                missing = ToolResult.error_result(
                    step.tool_name,
                    AgentErrorCode.TOOL_UNAVAILABLE,
                    f"No specialist owns tool {step.tool_name}",
                )
                missing.quality = {
                    **missing.quality,
                    "step_id": step.name,
                    "output_key": step.output_key,
                }
                tool_results.append(missing)
                delegations.append(
                    {
                        "task_id": f"{context.trace_id}:{step.name}",
                        "agent_name": agent_name,
                        "tool_name": step.tool_name,
                        "status": "failed",
                        "error": missing.error.to_dict(),
                    }
                )
                if step.required and not step.continue_on_error:
                    break
                continue

            input_data = self.orchestrator._resolve_input(context, step, outputs)
            self._emit(
                context,
                TaskEventType.TOOL_STARTED,
                f"Delegating {step.name}",
                tool=step.tool_name,
                progress=index / max(len(steps), 1),
            )
            task = AgentTask(
                task_id=f"{context.trace_id}:{step.name}",
                trace_id=context.trace_id,
                agent_name=specialist.name,
                objective=step.name,
                inputs={"query": input_data},
                allowed_tools=[step.tool_name],
                dependencies=list(step.metadata.get("dependencies", [])),
                retry_policy=RetryPolicy(
                    max_attempts=int(step.metadata.get("max_attempts", 1))
                ),
                timeout_seconds=step.timeout_seconds or 120.0,
                idempotency_key=f"{context.trace_id}:{step.name}",
                metadata=step.metadata,
            )
            adapter = self.tool_registry.resolve(
                step.tool_name,
                require_available=False,
            )
            input_hash = self.orchestrator._input_hash(input_data)
            checkpoint = (
                self.state_store.latest_checkpoint(context.trace_id, step.name)
                if self.state_store
                else None
            )
            reusable = bool(
                checkpoint
                and checkpoint.get("status") == "succeeded"
                and checkpoint.get("input_hash") == input_hash
                and str(checkpoint.get("tool_version") or "")
                == str(adapter.spec.version)
                and str(checkpoint.get("adapter_version") or "")
                == str(adapter.adapter_version)
            )
            if reusable:
                reused_result = self.orchestrator._result_from_checkpoint(
                    step.tool_name, checkpoint
                )
                task_result = AgentTaskResult(
                    task_id=task.task_id,
                    status="succeeded",
                    outputs={step.tool_name: reused_result.data},
                    tool_results=[reused_result],
                    metrics={"reused_checkpoint": True},
                )
            else:
                task_result = specialist.execute_task(task, self.tool_registry)
            for item in task_result.tool_results:
                item.quality = {
                    **item.quality,
                    "step_id": step.name,
                    "output_key": step.output_key,
                }
            tool_results.extend(task_result.tool_results)
            if not reusable:
                for item in task_result.tool_results:
                    self._persist_delegated_result(
                        context,
                        step,
                        input_data,
                        item,
                        tool_version=adapter.spec.version,
                        adapter_version=adapter.adapter_version,
                    )
            if task_result.status == "succeeded" and step.output_key:
                outputs[step.output_key] = task_result.outputs.get(step.tool_name)
            delegations.append(
                {
                    "task_id": task.task_id,
                    "agent_name": specialist.name,
                    "tool_name": step.tool_name,
                    "status": task_result.status,
                    "error": task_result.error.to_dict() if task_result.error else None,
                    "metrics": task_result.metrics,
                    "reused": reusable,
                }
            )
            self._emit(
                context,
                (
                    TaskEventType.TOOL_COMPLETED
                    if task_result.status == "succeeded"
                    else TaskEventType.TOOL_FAILED
                ),
                f"{step.name} {task_result.status}",
                tool=step.tool_name,
                progress=(index + 1) / max(len(steps), 1),
                payload=task_result.to_dict(),
            )
            if task_result.status != "succeeded":
                should_continue = (
                    step.continue_on_error
                    if step.continue_on_error is not None
                    else True
                )
                if not should_continue:
                    break

        successful = sum(1 for item in tool_results if item.success)
        failed = sum(1 for item in tool_results if not item.success)
        status = "succeeded" if successful and not failed else "partial" if successful else "failed"
        final_answer = "\n\n".join(
            item.formatted for item in tool_results if item.success and item.formatted
        )
        self._emit(
            context,
            (
                TaskEventType.TASK_COMPLETED
                if status in {"succeeded", "partial"}
                else TaskEventType.TASK_FAILED
            ),
            f"Supervisor workflow {status}",
            progress=1.0,
        )
        if self.state_store:
            self.state_store.update_run_status(context.trace_id, status)
        serialized_results = AgentResult(
            trace_id=context.trace_id,
            success=status == "succeeded",
            partial=status == "partial",
            message=(
                "Workflow completed"
                if status == "succeeded"
                else "Workflow returned partial results"
                if status == "partial"
                else "Workflow failed"
            ),
            skill_name=context.active_skill,
            final_answer=final_answer,
            tool_results=tool_results,
        ).to_legacy_dict()
        return {
            "trace_id": context.trace_id,
            "status": status,
            "message": (
                "Workflow completed"
                if status == "succeeded"
                else "Workflow returned partial results"
                if status == "partial"
                else "Workflow failed"
            ),
            "plan": {
                "workflow_name": workflow_name,
                "metadata": plan_metadata,
                "steps": [self._step_to_dict(step) for step in steps],
            },
            "summary": {
                "completed_steps": len(tool_results),
                "successful_steps": successful,
                "failed_steps": failed,
            },
            "delegations": delegations,
            "agent_events": (
                [event.to_dict() for event in self.event_bus.events]
                if self.event_bus
                else []
            ),
            "result": {
                "success": status == "succeeded",
                "partial": status == "partial",
                "final_answer": final_answer,
                "tool_results": serialized_results["tool_results"],
                "tool_result_sequence": serialized_results["tool_result_sequence"],
                "tool_results_by_step": serialized_results["tool_results_by_step"],
            },
        }

    def _persist_delegated_result(
        self,
        context: AgentContext,
        step: WorkflowStep,
        input_data: Any,
        result,
        tool_version: str = "1",
        adapter_version: str = "1",
    ) -> None:
        if not self.state_store:
            return
        legacy = result.to_legacy_dict()
        input_hash = self.orchestrator._input_hash(input_data)
        self.state_store.record_tool_execution(
            {
                "trace_id": context.trace_id,
                "step_id": step.name,
                "tool_name": step.tool_name,
                "status": "succeeded" if result.success else "failed",
                "input": input_data,
                "output": legacy if result.success else None,
                "error": legacy.get("error"),
                "elapsed_ms": result.elapsed_ms,
            }
        )
        self.state_store.save_checkpoint(
            {
                "trace_id": context.trace_id,
                "step_id": step.name,
                "workflow_version": "1",
                "status": "succeeded" if result.success else "failed",
                "input_hash": input_hash,
                "tool_name": step.tool_name,
                "tool_version": tool_version,
                "adapter_version": adapter_version,
                "model_version": "",
                "output": legacy if result.success else None,
                "error": legacy.get("error"),
            }
        )

    def _emit(
        self,
        context: AgentContext,
        event: TaskEventType,
        message: str,
        tool: str | None = None,
        progress: float | None = None,
        payload: Any = None,
    ) -> None:
        if not self.event_bus:
            return
        self.event_bus.emit(
            trace_id=context.trace_id,
            event=event,
            message=message,
            skill=context.active_skill,
            tool=tool,
            progress=progress,
            payload=payload,
        )

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
        (
            "drug_likeness_assessment",
            "src.agent.tools.drug_likeness_assessment",
            "DrugLikenessAssessment",
        ),
        ("admet_predictor", "src.agent.tools.admet_predictor", "ADMETPredictor"),
        ("activity_predictor", "src.agent.tools.activity_predictor_tool", "ActivityPredictorTool"),
        ("reverse_target_predictor", "src.agent.tools.reverse_target_tool", "ReverseTargetTool"),
        ("target_database_search", "src.agent.tools.target_database_tool", "TargetDatabaseTool"),
        ("llm_molecular_generator", "src.agent.tools.llm_molecular_generator", "LLMMolecularGenerator"),
        ("molecular_docking", "src.agent.tools.molecular_docking", "MolecularDocking"),
        ("rag_search", "src.agent.tools.rag_search_tool", "RAGSearchTool"),
    ]
    for tool_name, module_name, class_name in imports:
        try:
            module = __import__(module_name, fromlist=[class_name])
            tools[tool_name] = getattr(module, class_name)()
        except Exception:
            continue
    return tools
