from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from copy import deepcopy
from typing import Any, Mapping
from uuid import uuid4

from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    AgentExecutionError,
    AgentResult,
    ObservationStatus,
)
from src.agent.contracts.generation_request import (
    DEFAULT_GENERATION_COUNT,
    GenerationRequestError,
    generation_request_error_details,
    preflight_generation_request,
)
from src.agent.capabilities.catalog import TOOL_ALIASES
from src.agent.harness import HarnessFactory
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.planning.task_planner import TaskPlanner
from src.agent.router import SkillRouter
from src.agent.specialists import SpecialistAgent
from src.agent.tooling import ToolRegistry
from src.agent.persistence.base import AgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.run_session import RunClaimConflict
from src.agent.runtime.delegated_executor import DelegatedWorkflowExecutor as _DelegatedWorkflowExecutor
from src.agent.runtime.workflow_executor import (
    WorkflowExecution,
    WorkflowExecutor,
)
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy


RAG_TOOL_NAMES = frozenset(
    {"rag_search", "rag_database_search", "database_search"}
)
_MOL_COUNT_UNSET = object()
_MAX_IDEMPOTENCY_KEY_LENGTH = 256




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
        *,
        session_id: str | None = None,
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
        decide = getattr(self.skill_router, "decide", None)
        if active_skill is None and callable(decide):
            # Consume the full decision once: route() intentionally returns only
            # a policy and cannot convey the input-confirmation boundary.
            decision = decide(query, llm=self.llm)
            policy = (self.catalog.get(decision.selected_skill)
                      if decision.selected_skill else None)
            if policy is not None:
                from .metrics import metrics_system

                metrics_system.record_route_attempt(policy.name, True)
            if decision.requires_confirmation:
                error = AgentExecutionError(
                    code=AgentErrorCode.INVALID_INPUT,
                    message=("输入无效或信息不足；请检查 SMILES、靶点及 "
                             "receptor/docking box 参数。"),
                    details={"requires_confirmation": True,
                             "reasons": list(decision.reasons)},
                )
                result = AgentResult(
                    trace_id=f"agent-{uuid4().hex[:12]}", success=False,
                    message=error.message, final_answer=error.message,
                    skill_name=decision.selected_skill, error=error,
                )
                return {
                    **result.to_legacy_dict(),
                    "trace_id": result.trace_id,
                    "final_answer": result.final_answer,
                    "tools_used": [], "active_skill": result.skill_name,
                    "agent_result": result, "agent_events": [],
                    "workflow_plan": {"workflow_name": result.skill_name,
                                      "steps": [], "metadata": {}},
                }
        else:
            # Explicit workflows and route-only injected legacy routers keep
            # their existing contract; scientific validators still run.
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
            session_id=session_id,
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
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        metadata = dict(metadata or {})
        metadata.pop("session_id", None)
        metadata.pop("user_id", None)
        try:
            self._preflight_context_count(
                query, metadata, mol_count, skill_name
            )
        except GenerationRequestError as exc:
            return self._invalid_run_response(
                skill_name, trace_id, metadata, mol_count, exc
            )
        raw_idempotency_key = metadata.pop("idempotency_key", None)
        try:
            idempotency_key = self._internal_idempotency_key(
                raw_idempotency_key,
                session_id=session_id,
            )
        except ValueError as exc:
            return self._invalid_idempotency_response(
                skill_name=skill_name,
                trace_id=trace_id,
                message=str(exc),
            )
        context = self._build_context(
            query, skill_name, trace_id, metadata, mol_count, session_id
        )
        try:
            context = self.orchestrator._resolve_idempotent_context(
                context, idempotency_key,
                allow_trace_rebind=trace_id is None or session_id is None,
            )
        except RunClaimConflict as exc:
            return self._format_run_result(
                context.active_skill or "", {}, [], exc.to_result(context),
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
                "target_clarification_required",
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
        context = kwargs["context"]
        try:
            kwargs["context"] = self.orchestrator._resolve_idempotent_context(
                context, kwargs.get("idempotency_key"), allow_trace_rebind=False,
            )
        except RunClaimConflict as exc:
            return WorkflowExecution(
                plan=kwargs.get("plan") or WorkflowPlan(context.active_skill or "", []),
                result=exc.to_result(context), events=[],
            )
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
        # Preflight failures have not claimed/started a run. In particular,
        # shadow diagnostics must not mutate a prior trace on plan rejection.
        if (self.state_store and metadata_updates and execution.events
                and not execution.result.metadata.get("run_claim_conflict")):
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


    @staticmethod
    def _format_delegated_run_result(
        execution: WorkflowExecution,
    ) -> dict[str, Any]:
        result = execution.result
        serialized_results = result.to_legacy_dict()
        delegations = list(result.metadata.get("_harness_delegations", []))
        return {
            "trace_id": result.trace_id,
            "status": "succeeded" if serialized_results["status"] == "completed" else serialized_results["status"],
            "message": result.message,
            "plan": {
                "workflow_name": execution.plan.workflow_name,
                "metadata": execution.plan.metadata,
                "steps": [
                    SupervisorAgent._step_to_dict(step)
                    for step in execution.plan.steps
                ],
            },
            "summary": SupervisorAgent._result_summary(result),
            "delegations": delegations,
            "agent_events": execution.events,
            "result": {
                "status": serialized_results["status"],
                "warnings": serialized_results["warnings"],
                "evidence": serialized_results["evidence"],
                "artifacts": serialized_results["artifacts"],
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


    def _build_context(
        self,
        query: str,
        skill_name: str | None,
        trace_id: str | None,
        metadata: dict[str, Any] | None,
        mol_count: Any = _MOL_COUNT_UNSET,
        session_id: str | None = None,
    ) -> AgentContext:
        policy = self._resolve_policy(query, skill_name)
        active_skill = policy.name if policy is not None else skill_name
        request_metadata = dict(metadata or {})
        request_metadata.pop("session_id", None)
        request_metadata.pop("user_id", None)
        if mol_count is not _MOL_COUNT_UNSET:
            request_metadata["requested_count"] = mol_count
        return AgentContext(
            query=query,
            trace_id=trace_id or str(uuid4()),
            active_skill=active_skill,
            workflow_name=active_skill,
            mol_count=5 if mol_count is _MOL_COUNT_UNSET else mol_count,
            session_id=session_id,
            metadata=request_metadata,
        )

    @staticmethod
    def _internal_idempotency_key(
        value: Any,
        *,
        session_id: str | None,
    ) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("idempotency_key must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError("idempotency_key must not be empty")
        if len(value) > _MAX_IDEMPOTENCY_KEY_LENGTH:
            raise ValueError("idempotency_key is too long")
        if session_id is None:
            return normalized
        # This is the sole raw-client-key boundary. Downstream APIs receive
        # the internal key and must not hash it a second time.
        material = json.dumps(
            ["agent-key-v1", session_id, normalized],
            ensure_ascii=False, separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _invalid_idempotency_response(
        *,
        skill_name: str | None,
        trace_id: str | None,
        message: str,
    ) -> dict[str, Any]:
        error = AgentExecutionError(
            code=AgentErrorCode.INVALID_INPUT,
            message=message,
            details={"reason": "invalid_idempotency_key"},
        )
        response_trace_id = trace_id or str(uuid4())
        result = AgentResult(
            trace_id=response_trace_id,
            success=False,
            message=message,
            skill_name=skill_name,
            error=error,
        ).to_legacy_dict()
        return {
            "trace_id": response_trace_id,
            "status": "failed",
            "message": message,
            "plan": {
                "workflow_name": skill_name,
                "steps": [],
                "metadata": {},
            },
            "result": result,
        }

    def _request_tools(self, context: AgentContext) -> dict[str, Any]:
        """Build a request-local tool view without mutating shared tools."""
        capabilities = context.capabilities
        if not capabilities["scientific_tools"]:
            return {}
        tools = dict(self.tools)
        for alias, canonical in TOOL_ALIASES.items():
            if alias in tools:
                # Preserve an explicitly provided canonical tool. Legacy raw
                # maps may retain both entries; registry conflicts stay strict.
                tools.setdefault(canonical, tools[alias])
        if capabilities["rag"]:
            return tools
        rag_names = self._rag_tool_names()
        return {
            name: tool
            for name, tool in tools.items()
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
    def _result_summary(result: AgentResult) -> dict[str, int]:
        return {
            "completed_steps": len(result.tool_results),
            "successful_steps": sum(
                item.success and item.status == ObservationStatus.SUCCEEDED and item.error is None
                for item in result.tool_results),
            "failed_steps": sum(not item.success for item in result.tool_results),
            "partial_steps": sum(item.status == ObservationStatus.PARTIAL for item in result.tool_results),
        }

    @staticmethod
    def _format_run_result(
        workflow_name: str,
        plan_metadata: dict[str, Any],
        steps: list[WorkflowStep],
        result: AgentResult,
    ) -> dict[str, Any]:
        serialized = result.to_legacy_dict()
        return {
            "trace_id": result.trace_id,
            "status": "succeeded" if serialized["status"] == "completed" else serialized["status"],
            "message": result.message,
            "plan": {
                "workflow_name": workflow_name,
                "metadata": plan_metadata,
                "steps": [SupervisorAgent._step_to_dict(step) for step in steps],
            },
            "summary": SupervisorAgent._result_summary(result),
            "result": serialized,
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
