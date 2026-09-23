"""Specialist authorization at the shared workflow's invocation boundary."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from src.agent.contracts import AgentErrorCode, AgentResult, ToolResult
from src.agent.specialists import AgentTask
from src.agent.tooling import TOOL_AGENT_OWNERS, RetryPolicy
from .run_session import RunClaimConflict
from .workflow_executor import PreparedWorkflow, WorkflowExecution, WorkflowExecutor


class SpecialistDispatch:
    """Request-local dispatch only; the Session owns validation and settlement."""

    def __init__(self, context, registry, specialists):
        self.context = deepcopy(context)
        self.registry = registry
        self.specialists = dict(specialists)

    def authorize(self, tool, step):
        owner = TOOL_AGENT_OWNERS.get(step.tool_name)
        specialist = self.specialists.get(owner)
        if specialist is None:
            return ToolResult.error_result(step.tool_name, AgentErrorCode.TOOL_UNAVAILABLE,
                                           f"No specialist owns tool {step.tool_name}")
        if specialist.name != owner or step.tool_name not in specialist.allowed_tools:
            return ToolResult.error_result(step.tool_name, AgentErrorCode.UNAUTHORIZED_TOOL,
                                           "Specialist is not authorized for this tool")
        try:
            authorized = self.registry.resolve(step.tool_name, agent_name=owner)
        except (KeyError, PermissionError, RuntimeError) as exc:
            code = AgentErrorCode.UNAUTHORIZED_TOOL if isinstance(exc, PermissionError) else AgentErrorCode.TOOL_UNAVAILABLE
            return ToolResult.error_result(step.tool_name, code, "Tool authorization unavailable")
        if authorized is not tool:
            return ToolResult.error_result(step.tool_name, AgentErrorCode.UNAUTHORIZED_TOOL,
                                           "Tool registry changed after preflight")
        return None

    def claim_run(self, context, idempotency_key):
        store = self.state_store
        existing = store.get_run(context.trace_id)
        status = existing["status"] if existing else None
        if status in {"cancelled", "rejected", "running"}:
            raise RunClaimConflict(status)
        claim = getattr(store, "claim_workflow_run", None)
        if callable(claim):
            if claim({
                "trace_id": context.trace_id, "status": "running",
                "skill_name": context.active_skill, "query": context.query,
                "workflow_version": self.workflow_version,
                "idempotency_key": idempotency_key,
                "user_id": context.user_id, "session_id": context.session_id,
                "metadata": context.metadata,
            }, expected_status=status) is not True:
                raise RunClaimConflict(status)
            return True
        if context.session_id is not None or context.user_id is not None:
            raise RunClaimConflict(status)
        return False

    def __call__(self, tool, input_data, step):
        denial = self.authorize(tool, step)
        if denial is not None:
            return denial
        owner = TOOL_AGENT_OWNERS.get(step.tool_name)
        specialist = self.specialists.get(owner)
        if specialist is None:
            return ToolResult.error_result(step.tool_name, AgentErrorCode.TOOL_UNAVAILABLE,
                                           f"No specialist owns tool {step.tool_name}")
        task = AgentTask(
            task_id=f"{self.context.trace_id}:{step.name}",
            trace_id=self.context.trace_id, agent_name=specialist.name,
            objective=step.name, inputs={"query": deepcopy(input_data)},
            allowed_tools=[step.tool_name],
            retry_policy=RetryPolicy(max_attempts=int(step.metadata.get("max_attempts", 1))),
            dependencies=list(step.metadata.get("dependencies", [])),
            timeout_seconds=step.timeout_seconds or 120.0,
            idempotency_key=f"{self.context.trace_id}:{step.name}",
            metadata=deepcopy(step.metadata),
        )
        try:
            response = specialist.execute_task(task, self.registry)
            if len(response.tool_results) != 1:
                return ToolResult.error_result(step.tool_name, AgentErrorCode.INVALID_OUTPUT,
                                               "Specialist must return one observation per step")
            result = response.tool_results[0]
            if not isinstance(result, ToolResult) or result.tool_name != step.tool_name:
                return ToolResult.error_result(step.tool_name, AgentErrorCode.INVALID_OUTPUT,
                                               "Specialist observation identity mismatch")
            return result
        except Exception:
            return ToolResult.error_result(step.tool_name, AgentErrorCode.INTERNAL_ERROR,
                                           "Specialist dispatch failed")

    @staticmethod
    def decorate_result(result: AgentResult, context, reused_steps) -> None:
        """Add the legacy public envelope before the terminal event is sealed."""
        result.metadata["request_metadata"] = {
            "requested_count": context.metadata["requested_count"]
        } if "requested_count" in context.metadata else {}
        reused = set(reused_steps)
        result.metadata["_harness_delegations"] = [
            {
                "task_id": f"{result.trace_id}:{item.quality.get('step_id', '')}",
                "agent_name": TOOL_AGENT_OWNERS.get(item.tool_name),
                "tool_name": item.tool_name,
                "status": "succeeded" if item.success and item.status.value == "succeeded"
                else item.status.value,
                "error": item.error.to_dict() if item.error else None,
                "metrics": {"elapsed_ms": item.elapsed_ms},
                "reused": item.quality.get("step_id") in reused,
            }
            for item in result.tool_results
        ]


@dataclass(frozen=True)
class DelegatedPreparedWorkflow(PreparedWorkflow):
    """Adapt the public delegation envelope, not the execution lifecycle."""

    def to_execution(self, result: AgentResult) -> WorkflowExecution:
        if "_harness_delegations" not in result.metadata:
            SpecialistDispatch.decorate_result(
                result, self.context, result.metadata.get("reused_steps", []))
        return super().to_execution(result)

    def _run(self) -> AgentResult:
        try:
            return super()._run()
        except RunClaimConflict as exc:
            return exc.to_result(self.context)


class DelegatedWorkflowExecutor(WorkflowExecutor):
    # Sharing Session does not authorize expanding the production canary cohort.
    canary_execution_enabled = False

    def __init__(self, supervisor):
        super().__init__(planner=supervisor.planner, orchestrator=supervisor.orchestrator)
        self.registry = supervisor.tool_registry
        self.specialists = dict(supervisor.specialists)
        self.state_store = supervisor.state_store

    def prepare(self, *args: Any, **kwargs: Any):
        prepared = super().prepare(*args, **kwargs)
        if isinstance(prepared, WorkflowExecution):
            return prepared
        prepared.orchestrator.state_store = self.state_store
        prepared.event_bus.state_store = self.state_store
        # prepare() already created an isolated orchestrator/event bus. Never
        # attach request state to the shared Supervisor orchestrator.
        dispatch = SpecialistDispatch(
            prepared.context, self.registry, self.specialists)
        dispatch.state_store = self.state_store
        dispatch.workflow_version = prepared.orchestrator.workflow_version
        prepared.orchestrator.step_dispatch = dispatch
        prepared.orchestrator.run_claim = dispatch.claim_run
        return DelegatedPreparedWorkflow(
            context=prepared.context, policy=prepared.policy, plan=prepared.plan,
            compiled=prepared.compiled, tools=prepared.tools,
            orchestrator=prepared.orchestrator, event_bus=prepared.event_bus,
            idempotency_key=prepared.idempotency_key,
        )
