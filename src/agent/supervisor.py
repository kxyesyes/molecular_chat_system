from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping
from uuid import uuid4

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentResult,
    ToolResult,
)
from src.agent.contracts.generation_request import (
    DEFAULT_GENERATION_COUNT,
    GenerationRequestError,
    generation_request_error_details,
    preflight_generation_request,
    preserve_target_quality,
)
from src.agent.harness import HarnessFactory
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.planning import PlanCompiler, WorkflowPlan
from src.agent.planning.task_planner import TaskPlanner
from src.agent.router import SkillRouter
from src.agent.specialists import AgentTask, AgentTaskResult, SpecialistAgent
from src.agent.tooling import RetryPolicy, TOOL_AGENT_OWNERS, ToolRegistry
from src.agent.persistence.base import AgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
from src.agent.runtime.workflow_executor import (
    PreparedWorkflow,
    WorkflowExecution,
    WorkflowExecutor,
)
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy


RAG_TOOL_NAMES = frozenset(
    {"rag_search", "rag_database_search", "database_search"}
)
_MOL_COUNT_UNSET = object()


class _DelegatedWorkflowExecutor:
    """Adapt the existing specialist loop to the common harness boundary."""

    def __init__(self, supervisor: "SupervisorAgent"):
        self.supervisor = supervisor
        self.compiler = PlanCompiler()
        self.preflight_executor = WorkflowExecutor(
            planner=supervisor.planner,
            compiler=self.compiler,
        )

    def execute(
        self,
        *,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        event_callback: Any = None,
        idempotency_key: str | None = None,
        plan: WorkflowPlan | None = None,
    ) -> WorkflowExecution:
        del event_callback, idempotency_key
        plan = plan or self.supervisor.planner.plan(context)
        prepared = self.preflight_executor.prepare(
            context=context,
            policy=policy,
            all_tools=all_tools,
            plan=plan,
        )
        if isinstance(prepared, WorkflowExecution):
            return prepared
        assert isinstance(prepared, PreparedWorkflow)
        execution_context, execution_plan = prepared.consume_execution_inputs()
        execution = self.supervisor._run_delegated(
            execution_context,
            execution_plan,
        )
        execution.compiled_dependencies = prepared.compiled.dependencies
        return execution


class SupervisorAgent:
    """Plan and execute multi-step CADD workflows across existing tools."""

    def __init__(
        self,
        tools: Mapping[str, Any] | None = None,
        planner: TaskPlanner | None = None,
        catalog: WorkflowCatalog | None = None,
        orchestrator: WorkflowOrchestrator | None = None,
        tool_registry: ToolRegistry | None = None,
        specialists: Mapping[str, SpecialistAgent] | None = None,
        state_store: AgentStateStore | None = None,
        event_bus: AgentEventBus | None = None,
        llm: Any = None,
        molecular_generator_llm: Any = None,
        skill_router: SkillRouter | None = None,
        harness_factory: HarnessFactory | None = None,
    ):
        self.tools = dict(tools) if tools is not None else build_default_tools()
        self.llm = llm
        self.molecular_generator_llm = molecular_generator_llm
        router_catalog = getattr(skill_router, "catalog", None)
        if catalog is not None:
            self.catalog = catalog
        elif isinstance(router_catalog, WorkflowCatalog):
            self.catalog = router_catalog
        else:
            self.catalog = WorkflowCatalog()
        self.skill_router = skill_router or SkillRouter(catalog=self.catalog)
        self.planner = planner or TaskPlanner()
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
        self.harness_factory = harness_factory or HarnessFactory()

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
        preflight_generation_request(query)
        return self._resolve_policy(query) is not None

    @staticmethod
    def _is_molecular_generation_skill(skill: Any) -> bool:
        if isinstance(skill, WorkflowPolicy):
            return "llm_molecular_generator" in skill.allowed_tools
        name = getattr(skill, "name", skill)
        return name in {
            "molecular_design",
            "target_driven_design",
            "hit_to_lead_optimization",
        }

    def _resolve_policy(
        self,
        query: str,
        active_workflow: WorkflowPolicy | str | None = None,
    ) -> WorkflowPolicy | None:
        if isinstance(active_workflow, WorkflowPolicy):
            return self.catalog.get(active_workflow.name)
        if isinstance(active_workflow, str):
            return self.catalog.get(active_workflow)
        routed = self.skill_router.route(query, llm=self.llm)
        if routed is None:
            return None
        return self.catalog.get(routed.name)

    def execute(
        self,
        query: str,
        temperature: float = 0.7,
        mol_count: Any = _MOL_COUNT_UNSET,
        active_skill=None,
        event_callback=None,
        capabilities: Mapping[str, bool] | None = None,
    ) -> dict[str, Any]:
        """Chat-compatible entry point backed by the workflow runtime."""
        raw_requested_count: Any = (
            mol_count if mol_count is not _MOL_COUNT_UNSET else None
        )
        try:
            preflight_count = preflight_generation_request(
                query,
                mol_count if mol_count is not _MOL_COUNT_UNSET else None,
                count_supplied=mol_count is not _MOL_COUNT_UNSET,
                field="mol_count",
                active_molecular_skill=self._is_molecular_generation_skill(
                    active_skill
                ),
            )
            if preflight_count is not None:
                requested_count = preflight_count
                raw_requested_count = requested_count
            else:
                requested_count = DEFAULT_GENERATION_COUNT
        except GenerationRequestError as exc:
            if raw_requested_count is None and exc.value is not None:
                raw_requested_count = exc.value
            active_name = getattr(active_skill, "name", active_skill)
            classification = generation_request_error_details(exc)
            error = AgentExecutionError(
                code=AgentErrorCode.INVALID_INPUT,
                message="Invalid molecular generation request",
                details=classification,
            )
            trace_id = f"agent-{uuid4().hex[:12]}"
            agent_result = AgentResult(
                trace_id=trace_id,
                success=False,
                message=error.message,
                skill_name=active_name if isinstance(active_name, str) else None,
                error=error,
            )
            legacy = agent_result.to_legacy_dict()
            return {
                "success": False,
                "final_answer": error.message,
                "tools_used": [],
                "active_skill": agent_result.skill_name,
                "tool_results": legacy["tool_results"],
                "tool_result_sequence": legacy["tool_result_sequence"],
                "tool_results_by_step": legacy["tool_results_by_step"],
                "partial": False,
                "trace_id": trace_id,
                "agent_result": agent_result,
                "agent_events": [],
                "error": legacy["error"],
                "workflow_plan": {
                    "workflow_name": agent_result.skill_name
                    or "molecular_design",
                    "steps": [],
                    "metadata": {
                        "requested_count": (
                            raw_requested_count
                            if raw_requested_count is not None
                            else None
                        ),
                        **classification,
                    },
                },
            }
        policy = self._resolve_policy(query, active_skill)
        if policy is None:
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

        request_metadata: dict[str, Any] = (
            {"capabilities": dict(capabilities)}
            if capabilities is not None
            else {}
        )
        if mol_count is not _MOL_COUNT_UNSET:
            request_metadata["requested_count"] = mol_count
        elif "llm_molecular_generator" in policy.allowed_tools:
            request_metadata["requested_count"] = requested_count
        context = AgentContext(
            query=query,
            trace_id=f"agent-{uuid4().hex[:12]}",
            active_skill=policy.name,
            temperature=temperature,
            mol_count=requested_count,
            metadata=request_metadata,
        )
        request_tools = self._request_tools(context)
        execution = self._execute_with_harness(
            WorkflowExecutor(
                planner=self.planner,
                orchestrator=self.orchestrator,
            ),
            context=context,
            policy=policy,
            all_tools=request_tools,
            event_callback=event_callback,
        )
        capability_failure = self._capability_failure_result(
            context,
            [step.tool_name for step in execution.plan.steps],
        )
        if capability_failure is not None:
            legacy_failure = capability_failure.to_legacy_dict()
            return {
                "success": False,
                "final_answer": capability_failure.final_answer,
                "tools_used": [],
                "active_skill": policy.name,
                "tool_results": legacy_failure["tool_results"],
                "tool_result_sequence": legacy_failure["tool_result_sequence"],
                "tool_results_by_step": legacy_failure["tool_results_by_step"],
                "partial": False,
                "trace_id": capability_failure.trace_id,
                "agent_result": capability_failure,
                "agent_events": [],
                "error": legacy_failure["error"],
                "metadata": legacy_failure["metadata"],
                "workflow_plan": {
                    "workflow_name": execution.plan.workflow_name,
                    "steps": [
                        step.tool_name for step in execution.plan.steps
                    ],
                    "metadata": execution.plan.metadata,
                },
            }
        result = execution.result
        legacy_result = result.to_legacy_dict()
        return {
            "success": result.success or result.partial,
            "status": legacy_result["status"],
            "error": legacy_result["error"],
            "warnings": legacy_result["warnings"],
            "final_answer": result.final_answer or result.message,
            "tools_used": [item.tool_name for item in result.tool_results],
            "active_skill": policy.name,
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
        mol_count: Any = _MOL_COUNT_UNSET,
    ) -> dict[str, Any]:
        metadata = dict(metadata or {})
        try:
            self._preflight_context_count(
                query, metadata, mol_count, skill_name
            )
        except GenerationRequestError as exc:
            return self._invalid_plan_response(
                query, skill_name, trace_id, metadata, mol_count, exc
            )
        context = self._build_context(
            query, skill_name, trace_id, metadata, mol_count
        )
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
        mol_count: Any = _MOL_COUNT_UNSET,
    ) -> dict[str, Any]:
        metadata = dict(metadata or {})
        try:
            self._preflight_context_count(
                query, metadata, mol_count, skill_name
            )
        except GenerationRequestError as exc:
            return self._invalid_run_response(
                skill_name, trace_id, metadata, mol_count, exc
            )
        idempotency_key = metadata.get("idempotency_key")
        if self.state_store and idempotency_key:
            existing = self.state_store.get_run_by_idempotency_key(idempotency_key)
            if existing:
                trace_id = existing["trace_id"]
        context = self._build_context(
            query, skill_name, trace_id, metadata, mol_count
        )
        workflow_plan = self.planner.plan(context)
        steps = workflow_plan.steps
        request_tools = self._request_tools(context)

        policy = self.catalog.get(context.active_skill or "")
        if context.active_skill and policy is None:
            return {
                "trace_id": context.trace_id,
                "status": "failed",
                "message": f"Unknown workflow: {context.active_skill}",
                "plan": {
                    "workflow_name": workflow_plan.workflow_name,
                    "steps": [self._step_to_dict(step) for step in steps],
                    "metadata": workflow_plan.metadata,
                },
                "result": None,
            }

        capability_failure = self._capability_failure_result(
            context,
            [step.tool_name for step in steps],
        )
        if capability_failure is not None:
            return self._format_run_result(
                workflow_plan.workflow_name,
                workflow_plan.metadata,
                steps,
                capability_failure,
            )

        if not steps:
            if policy is not None and workflow_plan.metadata.get("reason") in {
                "invalid_requested_count",
                "invalid_requested_count_type",
                "malformed_requested_count",
                "requested_count_out_of_range",
            }:
                execution = WorkflowExecutor(
                    planner=self.planner,
                    orchestrator=self.orchestrator,
                ).execute(
                    context=context,
                    policy=policy,
                    all_tools=(
                        self.tool_registry.as_mapping()
                        if self.tool_registry is not None
                        else request_tools
                    ),
                    idempotency_key=idempotency_key,
                    plan=workflow_plan,
                )
                return self._format_run_result(
                    workflow_plan.workflow_name,
                    workflow_plan.metadata,
                    steps,
                    execution.result,
                )
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

        if policy is None:
            return {
                "trace_id": context.trace_id,
                "status": "failed",
                "message": "No supported scientific workflow matched the request",
                "plan": {
                    "workflow_name": workflow_plan.workflow_name,
                    "steps": [self._step_to_dict(step) for step in steps],
                    "metadata": workflow_plan.metadata,
                },
                "result": None,
            }

        executor = WorkflowExecutor(
            planner=self.planner,
            orchestrator=self.orchestrator,
        )
        delegated = self.tool_registry is not None and bool(self.specialists)
        if delegated:
            execution = self._execute_with_harness(
                _DelegatedWorkflowExecutor(self),
                context=context,
                policy=policy,
                all_tools=self.tool_registry.as_mapping(),
                idempotency_key=idempotency_key,
                plan=workflow_plan,
            )
            if "_harness_delegations" in execution.result.metadata:
                return self._format_delegated_run_result(execution)
            return self._format_run_result(
                execution.plan.workflow_name,
                execution.plan.metadata,
                execution.plan.steps,
                execution.result,
            )

        execution = self._execute_with_harness(
            executor,
            context=context,
            policy=policy,
            all_tools=request_tools,
            idempotency_key=idempotency_key,
            plan=workflow_plan,
        )
        return self._format_run_result(
            execution.plan.workflow_name,
            execution.plan.metadata,
            execution.plan.steps,
            execution.result,
        )

    @staticmethod
    def _preflight_context_count(
        query: str,
        metadata: dict[str, Any],
        mol_count: Any,
        skill_name: str | None = None,
    ) -> int | None:
        if mol_count is not _MOL_COUNT_UNSET:
            count = preflight_generation_request(
                query,
                mol_count,
                count_supplied=True,
                field="mol_count",
            )
            metadata["requested_count"] = count
            return count
        if "requested_count" in metadata:
            count = preflight_generation_request(
                query,
                metadata["requested_count"],
                count_supplied=True,
                field="metadata.requested_count",
            )
            metadata["requested_count"] = count
            return count
        count = preflight_generation_request(
            query,
            active_molecular_skill=(
                SupervisorAgent._is_molecular_generation_skill(skill_name)
            ),
        )
        if count is not None:
            metadata["requested_count"] = count
        return count

    @staticmethod
    def _invalid_plan_response(
        query: str,
        skill_name: str | None,
        trace_id: str | None,
        metadata: Mapping[str, Any],
        mol_count: Any,
        exc: GenerationRequestError,
    ) -> dict[str, Any]:
        raw_count = (
            mol_count
            if mol_count is not _MOL_COUNT_UNSET
            else (
                metadata["requested_count"]
                if "requested_count" in metadata
                else exc.value
            )
        )
        classification = generation_request_error_details(exc)
        return {
            "trace_id": trace_id or str(uuid4()),
            "skill_name": skill_name,
            "workflow_name": skill_name or "molecular_design",
            "metadata": {
                "requested_count": raw_count,
                **classification,
            },
            "steps": [],
            "error": {
                "code": AgentErrorCode.INVALID_INPUT.value,
                "message": str(exc),
                "details": classification,
            },
        }

    @staticmethod
    def _invalid_run_response(
        skill_name: str | None,
        trace_id: str | None,
        metadata: Mapping[str, Any],
        mol_count: Any,
        exc: GenerationRequestError,
    ) -> dict[str, Any]:
        plan = SupervisorAgent._invalid_plan_response(
            "", skill_name, trace_id, metadata, mol_count, exc
        )
        error = AgentExecutionError(
            code=AgentErrorCode.INVALID_INPUT,
            message="Invalid molecular generation request",
            details=generation_request_error_details(exc),
        )
        result = AgentResult(
            trace_id=plan["trace_id"],
            success=False,
            message=error.message,
            skill_name=skill_name,
            error=error,
        ).to_legacy_dict()
        return {
            "trace_id": plan["trace_id"],
            "status": "failed",
            "message": error.message,
            "plan": {
                "workflow_name": plan["workflow_name"],
                "steps": [],
                "metadata": plan["metadata"],
            },
            "result": result,
        }

    def _execute_with_harness(
        self,
        executor: WorkflowExecutor,
        **kwargs: Any,
    ) -> WorkflowExecution:
        harness = self.harness_factory.create(executor)
        harness_run = harness.execute(**kwargs)
        execution = harness_run.authoritative
        metadata_updates: dict[str, Any] = {}
        if harness_run.execution is not None:
            execution_metadata = harness_run.execution.to_dict()
            execution.result.metadata["harness_execution"] = execution_metadata
            metadata_updates["harness_execution"] = execution_metadata
        shadow_metadata: dict[str, Any] | None = None
        if harness_run.shadow is not None:
            shadow_metadata = harness_run.shadow.to_dict()
        else:
            warning = getattr(self.harness_factory, "last_warning", None)
            if warning:
                shadow_metadata = {
                    "status": "unavailable",
                    "error_code": warning,
                }
        if shadow_metadata is not None:
            execution.result.metadata["harness_shadow"] = shadow_metadata
            metadata_updates["harness_shadow"] = shadow_metadata
        if self.state_store and metadata_updates:
            try:
                if self.state_store.get_run(execution.result.trace_id) is not None:
                    self.state_store.update_run_metadata(
                        execution.result.trace_id,
                        metadata_updates,
                    )
            except Exception:
                warning = "harness_metadata_persistence_failed"
                if warning not in execution.result.warnings:
                    execution.result.warnings.append(warning)
        return execution

    def _run_delegated(
        self,
        context: AgentContext,
        plan: WorkflowPlan,
    ) -> WorkflowExecution:
        steps = plan.steps
        outputs: dict[str, Any] = {}
        delegations: list[dict[str, Any]] = []
        tool_results = []
        tool_attempt_count = 0
        skipped_steps: list[dict[str, str]] = []
        semantic_evidence: list[dict[str, str]] = []
        checkpoint_warnings: list[dict[str, str]] = []
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
                if not step.continue_after_failure():
                    break
                continue

            input_data = self.orchestrator._resolve_input(context, step, outputs)
            semantic_input = self.orchestrator._resolve_semantic_input(
                context,
                step,
                outputs,
            )
            decision = self.orchestrator.semantic_validator.validate(
                step,
                semantic_input,
            )
            if not decision.allowed:
                skipped_steps.append(
                    {
                        "step_id": step.name,
                        "status": "skipped_precondition",
                        "requirement": str(decision.requirement or ""),
                        "reason": str(decision.reason or ""),
                    }
                )
                for remaining in steps[index + 1 :]:
                    skipped_steps.append(
                        {
                            "step_id": remaining.name,
                            "status": "skipped_precondition",
                            "requirement": str(decision.requirement or ""),
                            "reason": f"blocked_by:{step.name}",
                        }
                    )
                break
            if decision.evidence_digest:
                semantic_evidence.append(
                    {
                        "step_id": step.name,
                        "requirement": str(decision.requirement or ""),
                        "evidence_digest": decision.evidence_digest,
                    }
                )
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
            model_version = str(step.metadata.get("model_version", ""))
            checkpoint = self.orchestrator._compatible_checkpoint(
                context.trace_id, step, input_hash, str(adapter.spec.version),
                model_version, adapter_version=str(adapter.adapter_version),
                state_store=self.state_store,
            ) if self.state_store is not None else None
            reused_result = None
            if checkpoint is not None:
                try:
                    reused_result = self.orchestrator._result_from_checkpoint(step.tool_name, checkpoint)
                except (AttributeError, KeyError, TypeError, ValueError):
                    checkpoint_warnings.append({
                        "step": step.name, "reason": "checkpoint_deserialization_failed"})
            reusable = reused_result is not None
            if reused_result is not None:
                task_result = AgentTaskResult(
                    task_id=task.task_id,
                    status="succeeded",
                    outputs={step.tool_name: reused_result.data},
                    tool_results=[reused_result],
                    metrics={"reused_checkpoint": True},
                )
            else:
                tool_attempt_count += 1
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
                        model_version=model_version,
                    )
            if task_result.status == "succeeded" and step.output_key:
                output = task_result.outputs.get(step.tool_name)
                if step.output_key == "target":
                    source_result = next(
                        (
                            item
                            for item in task_result.tool_results
                            if item.tool_name == step.tool_name
                        ),
                        None,
                    )
                    if source_result is not None:
                        output = preserve_target_quality(
                            output,
                            source_result.quality,
                        )
                outputs[step.output_key] = output
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
                if not step.continue_after_failure():
                    break

        successful = sum(1 for item in tool_results if item.success)
        failed = sum(1 for item in tool_results if not item.success)
        status = "succeeded" if successful and not failed else "partial" if successful else "failed"
        if skipped_steps:
            status = "partial" if successful else "failed"
        final_answer = "\n\n".join(
            item.formatted for item in tool_results if item.success and item.formatted
        )
        self._emit(
            context,
            (
                TaskEventType.TASK_COMPLETED
                if status == "succeeded"
                else TaskEventType.TASK_PARTIAL
                if status == "partial"
                else TaskEventType.TASK_FAILED
            ),
            f"Supervisor workflow {status}",
            progress=1.0,
        )
        if self.state_store:
            self.state_store.update_run_status(context.trace_id, status)
        agent_result = AgentResult(
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
            metadata={
                "skipped_steps": skipped_steps,
                "semantic_evidence": semantic_evidence,
                "checkpoint_warnings": checkpoint_warnings,
                "request_metadata": {
                    "requested_count": context.metadata["requested_count"]
                }
                if "requested_count" in context.metadata
                else {},
                "_harness_delegations": delegations,
            },
            error=(
                AgentExecutionError(
                    code=AgentErrorCode.VALIDATION_ERROR,
                    message="Scientific workflow precondition failed",
                    details={"skipped_steps": skipped_steps},
                )
                if skipped_steps
                else None
            ),
        )
        events = (
            [event.to_dict() for event in self.event_bus.events]
            if self.event_bus
            else []
        )
        return WorkflowExecution(
            plan=plan,
            result=agent_result,
            events=events,
            tool_attempt_count=tool_attempt_count,
        )

    @staticmethod
    def _format_delegated_run_result(
        execution: WorkflowExecution,
    ) -> dict[str, Any]:
        result = execution.result
        status = (
            "succeeded" if result.success else "partial" if result.partial else "failed"
        )
        serialized_results = result.to_legacy_dict()
        delegations = list(result.metadata.pop("_harness_delegations", []))
        return {
            "trace_id": result.trace_id,
            "status": status,
            "message": result.message,
            "plan": {
                "workflow_name": execution.plan.workflow_name,
                "metadata": execution.plan.metadata,
                "steps": [
                    SupervisorAgent._step_to_dict(step)
                    for step in execution.plan.steps
                ],
            },
            "summary": {
                "completed_steps": len(result.tool_results),
                "successful_steps": sum(
                    1 for item in result.tool_results if item.success
                ),
                "failed_steps": sum(
                    1 for item in result.tool_results if not item.success
                ),
            },
            "delegations": delegations,
            "agent_events": execution.events,
            "result": {
                "success": result.success,
                "partial": result.partial,
                "final_answer": result.final_answer,
                "tool_results": serialized_results["tool_results"],
                "tool_result_sequence": serialized_results["tool_result_sequence"],
                "tool_results_by_step": serialized_results["tool_results_by_step"],
                "metadata": serialized_results["metadata"],
                "error": serialized_results["error"],
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
        model_version: str = "",
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
                "workflow_version": self.orchestrator.workflow_version,
                "status": "succeeded" if result.success else "failed",
                "input_hash": input_hash,
                "tool_name": step.tool_name,
                "tool_version": tool_version,
                "adapter_version": adapter_version,
                "model_version": model_version,
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
        mol_count: Any = _MOL_COUNT_UNSET,
    ) -> AgentContext:
        policy = self._resolve_policy(query, skill_name)
        active_skill = policy.name if policy is not None else skill_name
        request_metadata = dict(metadata or {})
        if mol_count is not _MOL_COUNT_UNSET:
            request_metadata["requested_count"] = mol_count
        return AgentContext(
            query=query,
            trace_id=trace_id or str(uuid4()),
            active_skill=active_skill,
            workflow_name=active_skill,
            mol_count=5 if mol_count is _MOL_COUNT_UNSET else mol_count,
            metadata=request_metadata,
        )

    def _request_tools(self, context: AgentContext) -> dict[str, Any]:
        """Build a request-local tool view without mutating shared tools."""
        capabilities = context.capabilities
        if not capabilities["scientific_tools"]:
            return {}
        if capabilities["rag"]:
            return dict(self.tools)
        rag_names = self._rag_tool_names()
        return {
            name: tool
            for name, tool in self.tools.items()
            if name not in rag_names
        }

    def _rag_tool_names(self) -> set[str]:
        names = set(RAG_TOOL_NAMES)
        candidates = list(self.tools.items())
        if self.tool_registry is not None:
            candidates.extend(self.tool_registry.as_mapping().items())
        for registered_name, tool in candidates:
            tool_name = str(getattr(tool, "name", registered_name))
            raw_aliases = getattr(tool, "aliases", None)
            if raw_aliases is None and hasattr(tool, "spec"):
                raw_aliases = getattr(tool.spec, "aliases", set())
            if isinstance(raw_aliases, str):
                aliases = {raw_aliases}
            else:
                aliases = set(raw_aliases or set())
            related_names = {registered_name, tool_name, *aliases}
            if related_names & names:
                names.update(related_names)
        return names

    def _capability_failure_result(
        self,
        context: AgentContext,
        planned_tools: list[str],
    ) -> AgentResult | None:
        capabilities = context.capabilities
        if not capabilities["scientific_tools"]:
            capability = "scientific_tools"
            blocked_tools = list(planned_tools)
        elif not capabilities["rag"]:
            capability = "RAG"
            rag_names = self._rag_tool_names()
            blocked_tools = [
                name for name in planned_tools if name in rag_names
            ]
        else:
            return None

        if not blocked_tools:
            return None

        message = (
            f"{capability} capability disabled; unavailable tools: "
            f"{', '.join(blocked_tools)}"
        )
        details = {
            "reason": "capability_disabled",
            "capability": capability.lower(),
            "disabled_tools": blocked_tools,
            "capabilities": capabilities,
        }
        error = AgentExecutionError(
            code=AgentErrorCode.TOOL_UNAVAILABLE,
            message=message,
            details=details,
        )
        return AgentResult(
            trace_id=context.trace_id,
            success=False,
            message=message,
            final_answer=message,
            skill_name=context.active_skill,
            error=error,
            metadata={"capability_failure": details},
        )

    @staticmethod
    def _step_to_dict(step: WorkflowStep) -> dict[str, Any]:
        data = asdict(step)
        data["required"] = bool(data.get("required", True))
        data["preconditions"] = list(step.preconditions)
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
        ("candidate_ranker", "src.agent.tools.candidate_ranker", "CandidateRanker"),
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
