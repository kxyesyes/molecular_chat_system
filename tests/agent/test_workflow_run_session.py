from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from src.agent import runtime
from src.agent.contracts import (
    AgentContext,
    AgentErrorCode,
    RunOutcome,
    ToolResult,
)
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
from src.agent.validators import AgentResultValidator
from src.agent.validators.semantic_inputs import SemanticDecision


class CountingTool:
    def __init__(self, name="property_calculator"):
        self.name = name
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return {
            "success": True,
            "message": f"{self.name} completed",
            "data": {"input": query},
            "formatted": f"{self.name}: {query}",
        }


class RaisingTool:
    name = "raising_tool"

    def __init__(self):
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        raise RuntimeError("tool exploded")


class OutcomeTool:
    def __init__(self, name, *, success, order=None):
        self.name = name
        self.success = success
        self.calls = []
        self.order = order

    def execute(self, query):
        self.calls.append(query)
        if self.order is not None:
            self.order.append(self.name)
        if self.success:
            return ToolResult.success_result(
                self.name,
                data={"input": query},
                message=f"{self.name} completed",
                formatted=f"{self.name}: {query}",
            )
        return ToolResult.error_result(
            self.name,
            AgentErrorCode.INTERNAL_ERROR,
            f"{self.name} failed",
        )


class FailOnceStartStore(SQLiteAgentStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.start_attempts = 0

    def start_run(self, run):
        self.start_attempts += 1
        if self.start_attempts == 1:
            raise RuntimeError("start persistence failed")
        return super().start_run(run)


class FailOnceStatusStore(SQLiteAgentStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.status_attempts = 0

    def update_run_status(self, trace_id, status):
        self.status_attempts += 1
        if self.status_attempts == 1:
            raise RuntimeError("status persistence failed")
        return super().update_run_status(trace_id, status)


class CountingStartStore(SQLiteAgentStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.start_attempts = 0

    def start_run(self, run):
        self.start_attempts += 1
        return super().start_run(run)


class CountingCheckpointStore(SQLiteAgentStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.latest_checkpoint_calls = 0

    def latest_checkpoint(self, trace_id, step_id=None):
        self.latest_checkpoint_calls += 1
        return super().latest_checkpoint(trace_id, step_id)


class FailSessionEventPersistenceOnceStore(SQLiteAgentStateStore):
    def __init__(self, path, *, after_commit):
        super().__init__(path)
        self.after_commit = after_commit
        self.failed = False

    def append_event(self, event):
        if not self.failed:
            self.failed = True
            if self.after_commit:
                super().append_event(event)
            raise RuntimeError("session event persistence failed")
        return super().append_event(event)


class FailFinalCheckpointOnceStore(SQLiteAgentStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.failed = False

    def save_checkpoint(self, checkpoint):
        if checkpoint.get("status") != "running" and not self.failed:
            self.failed = True
            raise RuntimeError("final checkpoint persistence failed")
        return super().save_checkpoint(checkpoint)


class CommitThenFailExecutionOnceStore(SQLiteAgentStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.failed = False

    def record_tool_execution(self, execution):
        execution_id = super().record_tool_execution(execution)
        if not self.failed:
            self.failed = True
            raise RuntimeError("execution wrapper failed after commit")
        return execution_id


class FailOnceSemanticValidator:
    def __init__(self):
        self.calls = 0

    def validate(self, step, input_data):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("semantic validator unavailable")
        return SemanticDecision(True)


class FailOncePostprocessValidator(AgentResultValidator):
    def __init__(self):
        self.calls = 0

    def validate_tool_result(self, result, *, trusted_checkpoint=False):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("tool result postprocess failed")
        return super().validate_tool_result(
            result,
            trusted_checkpoint=trusted_checkpoint,
        )


class FailOnceEventCallback:
    def __init__(self, target):
        self.target = target
        self.failed = False

    def __call__(self, event):
        if event.event == self.target and not self.failed:
            self.failed = True
            raise RuntimeError(f"{self.target.value} callback failed")


class FailOnceExtend(list):
    def __init__(self):
        super().__init__()
        self.failed = False

    def extend(self, values):
        if not self.failed:
            self.failed = True
            raise RuntimeError("workflow state update failed")
        return super().extend(values)


class FailOncePersistStore(SQLiteAgentStateStore):
    def __init__(self, path):
        super().__init__(path)
        self.persist_attempts = 0

    def record_tool_execution(self, execution):
        self.persist_attempts += 1
        if self.persist_attempts == 1:
            raise RuntimeError("step persistence failed")
        return super().record_tool_execution(execution)


class FailAfterTerminalEventBus(AgentEventBus):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.failed = False

    def emit(self, **kwargs):
        event = super().emit(**kwargs)
        if kwargs["event"].value.startswith("task_") and kwargs[
            "event"
        ].value != "task_started" and not self.failed:
            self.failed = True
            raise RuntimeError("terminal callback failed")
        return event


def _make_session(
    *,
    steps=None,
    tools=None,
    trace_id="run-session",
    state_store=None,
    continue_on_error=False,
    semantic_validator=None,
    validator=None,
    event_bus=None,
):
    event_bus = event_bus or AgentEventBus(state_store=state_store)
    orchestrator = WorkflowOrchestrator(
        event_bus=event_bus,
        state_store=state_store,
        semantic_validator=semantic_validator,
        validator=validator,
    )
    session = orchestrator.create_session(
        context=AgentContext(query="CCO", trace_id=trace_id),
        steps=steps
        if steps is not None
        else [WorkflowStep("properties", "property_calculator", "CCO")],
        tools=tools or {},
        continue_on_error=continue_on_error,
    )
    return session, event_bus


def test_session_executes_single_step_once_and_completes_once():
    tool = CountingTool()
    session, event_bus = _make_session(tools={tool.name: tool})

    session.start()
    advance = session.execute_step(0)
    result = session.finish()

    assert session.started is True
    assert session.finished is True
    assert session.tool_attempt_count == 1
    assert tool.calls == ["CCO"]
    assert advance.step_id == "properties"
    assert advance.outcome == "succeeded"
    assert advance.terminal is True
    assert result.success is True
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1

    with pytest.raises(FrozenInstanceError):
        advance.terminal = False


def test_session_rejects_duplicate_step_and_duplicate_finish():
    tool = CountingTool()
    session, _ = _make_session(tools={tool.name: tool})
    session.start()
    session.execute_step(0)

    with pytest.raises(runtime.SessionLifecycleError):
        session.execute_step(0)

    session.finish()
    with pytest.raises(runtime.SessionLifecycleError):
        session.finish()
    assert tool.calls == ["CCO"]


def test_tool_exception_counts_one_real_attempt():
    tool = RaisingTool()
    session, _ = _make_session(
        steps=[WorkflowStep("explode", tool.name, "CCO")],
        tools={tool.name: tool},
    )
    session.start()

    advance = session.execute_step(0)
    result = session.finish()

    assert session.tool_attempt_count == 1
    assert tool.calls == ["CCO"]
    assert advance.outcome == "failed"
    assert result.success is False


def test_checkpoint_reuse_does_not_increment_tool_attempt_count(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "session.sqlite3")
    tool = CountingTool()
    context = AgentContext(query="CCO", trace_id="session-checkpoint")
    steps = [WorkflowStep("properties", tool.name, "CCO")]
    WorkflowOrchestrator(state_store=store).run(
        context,
        steps,
        {tool.name: tool},
    )
    event_bus = AgentEventBus(state_store=store)
    session = WorkflowOrchestrator(
        event_bus=event_bus,
        state_store=store,
    ).create_session(context=context, steps=steps, tools={tool.name: tool})

    session.start()
    advance = session.execute_step(0)
    result = session.finish()

    assert advance.outcome == "succeeded"
    assert session.tool_attempt_count == 0
    assert tool.calls == ["CCO"]
    assert result.metadata["reused_steps"] == ["properties"]
    assert result.metadata["tool_attempt_count"] == 0


def test_semantic_precondition_skip_does_not_call_tool():
    tool = CountingTool("llm_molecular_generator")
    step = WorkflowStep(
        "generate",
        tool.name,
        input_data=[],
        preconditions=("target_evidence",),
    )
    session, _ = _make_session(steps=[step], tools={tool.name: tool})

    session.start()
    advance = session.execute_step(0)
    result = session.finish()

    assert advance.outcome == "skipped_precondition"
    assert advance.terminal is True
    assert session.tool_attempt_count == 0
    assert tool.calls == []
    assert result.error.code == AgentErrorCode.VALIDATION_ERROR


def test_blocked_generation_emits_partial_terminal_message():
    target_search = CountingTool("target_database_search")
    generator = CountingTool("llm_molecular_generator")
    steps = [
        WorkflowStep(
            "search_targets",
            target_search.name,
            input_data={"records": []},
            output_key="targets",
        ),
        WorkflowStep(
            "generate",
            generator.name,
            input_binding="$.outputs.targets",
            preconditions=("target_evidence",),
        ),
    ]
    session, event_bus = _make_session(
        steps=steps,
        tools={target_search.name: target_search, generator.name: generator},
    )

    session.start()
    first = session.execute_step(0)
    second = session.execute_step(1)
    result = session.finish()
    terminal = event_bus.events[-1]

    assert first.outcome == "succeeded"
    assert second.outcome == "skipped_precondition"
    assert target_search.calls == [{"records": []}]
    assert generator.calls == []
    assert result.outcome == RunOutcome.PARTIAL
    assert terminal.event == TaskEventType.TASK_PARTIAL
    assert "partial" in terminal.message.lower()
    assert terminal.message != "Workflow completed"
    assert terminal.progress == 1.0


def test_target_quality_fallback_envelope_blocks_generation_and_is_immutable():
    target_data = [{"gene_symbol": "PDE5A", "source": "UniProt"}]

    class FallbackTargetTool:
        name = "target_database_search"

        def __init__(self):
            self.calls = []

        def execute(self, query):
            self.calls.append(query)
            return {
                "success": True,
                "message": "fallback target data",
                "data": target_data,
                "quality": {"fallback_used": True, "secret": "drop-me"},
            }

    target = FallbackTargetTool()
    generator = CountingTool("llm_molecular_generator")
    steps = [
        WorkflowStep(
            "target_search",
            target.name,
            input_data="PDE5A",
            output_key="target",
            capability="target.structure.search",
        ),
        WorkflowStep(
            "generate",
            generator.name,
            input_binding="$.workflow",
            output_key="molecules",
            capability="molecule.generate",
            preconditions=("target_evidence",),
        ),
    ]
    session, _ = _make_session(steps=steps, tools={target.name: target, generator.name: generator})
    session.context.metadata["requested_count"] = 1

    session.start()
    first = session.execute_step(0)

    assert first.outcome == "succeeded"
    assert session.outputs["target"]["data"] == target_data
    assert dict(session.outputs["target"]["quality"]) == {
        "fallback_used": True
    }
    with pytest.raises(TypeError):
        session.outputs["target"]["quality"]["fallback_used"] = False

    second = session.execute_step(1)
    result = session.finish()

    assert second.outcome == "skipped_precondition"
    assert generator.calls == []
    assert result.error.code == AgentErrorCode.VALIDATION_ERROR


def test_fail_runtime_finishes_with_internal_error_and_one_failed_event():
    session, event_bus = _make_session(steps=[], tools={})
    session.start()
    session.fail_runtime("graph_node_crashed")

    with pytest.raises(runtime.SessionLifecycleError):
        session.fail_runtime("duplicate_failure")

    result = session.finish()

    assert result.success is False
    assert result.partial is False
    assert result.outcome == RunOutcome.FAILED
    assert result.error.code == AgentErrorCode.INTERNAL_ERROR
    assert result.final_answer == ""
    assert "completed" not in result.message.lower()
    assert [event.event.value for event in event_bus.events].count("task_failed") == 1
    assert all(event.event.value != "task_completed" for event in event_bus.events)

    with pytest.raises(runtime.SessionLifecycleError):
        session.fail_runtime("late_failure")


def test_step_and_finish_require_start():
    session, _ = _make_session(tools={"property_calculator": CountingTool()})

    with pytest.raises(runtime.SessionLifecycleError):
        session.execute_step(0)
    with pytest.raises(runtime.SessionLifecycleError):
        session.finish()


def test_start_can_only_be_called_once():
    session, _ = _make_session(steps=[], tools={})
    session.start()

    with pytest.raises(runtime.SessionLifecycleError):
        session.start()


def test_two_step_session_rejects_early_finish():
    first = CountingTool("first")
    second = CountingTool("second")
    session, _ = _make_session(
        steps=[
            WorkflowStep("first", first.name, "CCO"),
            WorkflowStep("second", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    advance = session.execute_step(0)

    assert advance.terminal is False
    assert session.next_index == 1
    with pytest.raises(runtime.SessionLifecycleError):
        session.finish()
    assert session.finished is False
    assert second.calls == []


def test_session_rejects_executing_last_step_first():
    first = CountingTool("first")
    second = CountingTool("second")
    session, _ = _make_session(
        steps=[
            WorkflowStep("first", first.name, "CCO"),
            WorkflowStep("second", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    with pytest.raises(runtime.SessionLifecycleError):
        session.execute_step(1)

    assert session.next_index == 0
    assert first.calls == []
    assert second.calls == []


def test_two_step_session_executes_in_order_and_then_finishes():
    order = []
    first = OutcomeTool("first", success=True, order=order)
    second = OutcomeTool("second", success=True, order=order)
    session, event_bus = _make_session(
        steps=[
            WorkflowStep("first", first.name, "CCO"),
            WorkflowStep("second", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    first_advance = session.execute_step(0)
    second_advance = session.execute_step(1)
    result = session.finish()

    assert first_advance.terminal is False
    assert second_advance.terminal is True
    assert session.next_index == 2
    assert order == ["first", "second"]
    assert result.success is True
    terminal_events = [
        event.event.value
        for event in event_bus.events
        if event.event.value.startswith("task_")
        and event.event.value not in {"task_started"}
    ]
    assert terminal_events == ["task_completed"]


def test_required_failure_stops_session_before_next_step():
    first = OutcomeTool("required", success=False)
    second = OutcomeTool("after", success=True)
    session, _ = _make_session(
        steps=[
            WorkflowStep("required", first.name, "CCO", required=True),
            WorkflowStep("after", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    advance = session.execute_step(0)
    result = session.finish()

    assert advance.terminal is True
    assert advance.reason == "required_step_failed"
    assert second.calls == []
    assert result.success is False


def test_optional_failure_continues_by_default():
    first = OutcomeTool("optional", success=False)
    second = OutcomeTool("after", success=True)
    session, _ = _make_session(
        steps=[
            WorkflowStep("optional", first.name, "CCO", required=False),
            WorkflowStep("after", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    first_advance = session.execute_step(0)
    second_advance = session.execute_step(1)
    result = session.finish()

    assert first_advance.terminal is False
    assert second_advance.terminal is True
    assert second.calls == ["CCN"]
    assert result.partial is True


def test_optional_failure_can_explicitly_stop():
    first = OutcomeTool("optional", success=False)
    second = OutcomeTool("after", success=True)
    session, _ = _make_session(
        steps=[
            WorkflowStep(
                "optional",
                first.name,
                "CCO",
                required=False,
                continue_on_error=False,
            ),
            WorkflowStep("after", second.name, "CCN"),
        ],
        tools={first.name: first, second.name: second},
    )
    session.start()

    advance = session.execute_step(0)
    result = session.finish()

    assert advance.terminal is True
    assert second.calls == []
    assert result.success is False


def test_global_continue_on_error_matches_legacy_required_failure_stop():
    legacy_first = OutcomeTool("required", success=False)
    legacy_second = OutcomeTool("after", success=True)
    steps = [
        WorkflowStep("required", legacy_first.name, "CCO", required=True),
        WorkflowStep("after", legacy_second.name, "CCN"),
    ]
    legacy_result = WorkflowOrchestrator().run(
        AgentContext(query="CCO", trace_id="legacy-global-continue"),
        steps,
        {legacy_first.name: legacy_first, legacy_second.name: legacy_second},
        continue_on_error=True,
    )

    first = OutcomeTool("required", success=False)
    second = OutcomeTool("after", success=True)
    session, _ = _make_session(
        steps=steps,
        tools={first.name: first, second.name: second},
        continue_on_error=True,
    )
    session.start()

    advance = session.execute_step(0)
    result = session.finish()

    assert advance.terminal is True
    assert legacy_second.calls == []
    assert second.calls == legacy_second.calls
    assert result.outcome == legacy_result.outcome


@pytest.mark.parametrize(
    "event_type",
    [TaskEventType.TASK_STARTED, TaskEventType.PLANNING_STARTED],
)
def test_start_retry_does_not_duplicate_events_appended_before_callback_failure(
    tmp_path,
    event_type,
):
    store = CountingStartStore(tmp_path / f"{event_type.value}.sqlite3")
    event_bus = AgentEventBus(
        on_event=FailOnceEventCallback(event_type),
        state_store=store,
    )
    session, _ = _make_session(
        steps=[],
        tools={},
        trace_id=f"retry-{event_type.value}",
        state_store=store,
        event_bus=event_bus,
    )

    with pytest.raises(RuntimeError, match="callback failed"):
        session.start()

    assert session.started is False
    session.start()
    session.finish()

    assert store.start_attempts == 1
    event_names = [event.event for event in event_bus.events]
    assert event_names.count(TaskEventType.TASK_STARTED) == 1
    assert event_names.count(TaskEventType.PLANNING_STARTED) == 1
    assert event_names.count(TaskEventType.PLANNING_COMPLETED) == 1


@pytest.mark.parametrize("after_commit", [False, True])
def test_session_event_persistence_retry_is_exactly_once(
    tmp_path,
    after_commit,
):
    trace_id = f"session-event-{after_commit}"
    store = FailSessionEventPersistenceOnceStore(
        tmp_path / f"session-event-{after_commit}.sqlite3",
        after_commit=after_commit,
    )
    session, event_bus = _make_session(
        steps=[],
        tools={},
        trace_id=trace_id,
        state_store=store,
    )

    with pytest.raises(RuntimeError, match="session event persistence failed"):
        session.start()

    session.start()
    session.finish()

    task_started_memory = [
        event
        for event in event_bus.events
        if event.event == TaskEventType.TASK_STARTED
    ]
    task_started_database = [
        event
        for event in store.get_events(trace_id)
        if event["event"] == TaskEventType.TASK_STARTED.value
    ]
    assert len(task_started_memory) == 1
    assert len(task_started_database) == 1


def test_tool_started_callback_failure_retries_without_duplicate_or_tool_call():
    tool = CountingTool()
    event_bus = AgentEventBus(
        on_event=FailOnceEventCallback(TaskEventType.TOOL_STARTED)
    )
    session, _ = _make_session(tools={tool.name: tool}, event_bus=event_bus)
    session.start()

    with pytest.raises(RuntimeError, match="tool_started callback failed"):
        session.execute_step(0)

    assert session.tool_attempt_count == 0
    assert session.next_index == 0
    assert tool.calls == []
    advance = session.execute_step(0)
    session.finish()

    assert advance.terminal is True
    assert tool.calls == ["CCO"]
    assert [event.event for event in event_bus.events].count(
        TaskEventType.TOOL_STARTED
    ) == 1


def test_checkpoint_terminal_event_failure_resumes_without_tool_or_duplicate(
    tmp_path,
):
    store = SQLiteAgentStateStore(tmp_path / "checkpoint-event.sqlite3")
    tool = CountingTool()
    context = AgentContext(query="CCO", trace_id="checkpoint-event")
    steps = [WorkflowStep("properties", tool.name, "CCO")]
    WorkflowOrchestrator(state_store=store).run(context, steps, {tool.name: tool})
    event_bus = AgentEventBus(
        on_event=FailOnceEventCallback(TaskEventType.TOOL_COMPLETED),
        state_store=store,
    )
    session = WorkflowOrchestrator(
        event_bus=event_bus,
        state_store=store,
    ).create_session(context=context, steps=steps, tools={tool.name: tool})
    session.start()

    with pytest.raises(RuntimeError, match="tool_completed callback failed"):
        session.execute_step(0)

    assert session.tool_attempt_count == 0
    assert session.next_index == 0
    advance = session.execute_step(0)
    result = session.finish()

    assert advance.terminal is True
    assert tool.calls == ["CCO"]
    assert result.metadata["reused_steps"] == ["properties"]
    assert [event.event for event in event_bus.events].count(
        TaskEventType.TOOL_COMPLETED
    ) == 1


def test_checkpoint_state_update_failure_resumes_without_rereading_checkpoint(
    tmp_path,
):
    store = CountingCheckpointStore(tmp_path / "checkpoint-state.sqlite3")
    tool = CountingTool()
    context = AgentContext(query="CCO", trace_id="checkpoint-state")
    steps = [WorkflowStep("properties", tool.name, "CCO")]
    WorkflowOrchestrator(state_store=store).run(context, steps, {tool.name: tool})
    baseline_reads = store.latest_checkpoint_calls
    session = WorkflowOrchestrator(state_store=store).create_session(
        context=context,
        steps=steps,
        tools={tool.name: tool},
    )
    session.start()
    session.state.warnings = FailOnceExtend()

    with pytest.raises(RuntimeError, match="workflow state update failed"):
        session.execute_step(0)

    assert session.next_index == 0
    advance = session.execute_step(0)
    session.finish()

    assert advance.terminal is True
    assert session.tool_attempt_count == 0
    assert store.latest_checkpoint_calls - baseline_reads == 1
    assert tool.calls == ["CCO"]


def test_missing_tool_persistence_failure_resumes_cached_result(tmp_path):
    store = FailOncePersistStore(tmp_path / "missing-tool.sqlite3")
    session, _ = _make_session(
        steps=[WorkflowStep("missing", "not_registered", "CCO")],
        tools={},
        trace_id="missing-tool",
        state_store=store,
    )
    session.start()

    with pytest.raises(RuntimeError, match="step persistence failed"):
        session.execute_step(0)

    assert session.next_index == 0
    assert session.tool_attempt_count == 0
    advance = session.execute_step(0)
    result = session.finish()

    assert advance.outcome == "failed"
    assert result.success is False
    assert store.persist_attempts == 2


def test_real_tool_postprocess_failure_resumes_without_second_tool_call():
    tool = CountingTool()
    validator = FailOncePostprocessValidator()
    session, _ = _make_session(
        tools={tool.name: tool},
        validator=validator,
    )
    session.start()

    with pytest.raises(RuntimeError, match="tool result postprocess failed"):
        session.execute_step(0)

    assert session.tool_attempt_count == 1
    assert session.next_index == 0
    assert tool.calls == ["CCO"]
    advance = session.execute_step(0)
    result = session.finish()

    assert advance.terminal is True
    assert result.success is True
    assert tool.calls == ["CCO"]


def test_final_checkpoint_failure_retries_without_duplicate_execution(tmp_path):
    store = FailFinalCheckpointOnceStore(tmp_path / "checkpoint-failure.sqlite3")
    tool = CountingTool()
    trace_id = "checkpoint-failure"
    session, _ = _make_session(
        tools={tool.name: tool},
        trace_id=trace_id,
        state_store=store,
    )
    session.start()

    with pytest.raises(RuntimeError, match="final checkpoint persistence failed"):
        session.execute_step(0)

    assert len(store.get_tool_executions(trace_id)) == 1
    advance = session.execute_step(0)
    session.finish()

    assert advance.terminal is True
    assert len(store.get_tool_executions(trace_id)) == 1
    assert store.latest_checkpoint(trace_id, "properties")["status"] == "succeeded"
    assert tool.calls == ["CCO"]
    assert session.tool_attempt_count == 1


def test_execution_commit_then_raise_retries_with_same_stable_id(tmp_path):
    store = CommitThenFailExecutionOnceStore(
        tmp_path / "execution-commit-failure.sqlite3"
    )
    tool = CountingTool()
    trace_id = "execution-commit-failure"
    session, _ = _make_session(
        tools={tool.name: tool},
        trace_id=trace_id,
        state_store=store,
    )
    session.start()

    with pytest.raises(RuntimeError, match="execution wrapper failed after commit"):
        session.execute_step(0)

    assert len(store.get_tool_executions(trace_id)) == 1
    advance = session.execute_step(0)
    session.finish()

    assert advance.terminal is True
    assert len(store.get_tool_executions(trace_id)) == 1
    assert tool.calls == ["CCO"]
    assert session.tool_attempt_count == 1


def test_new_session_attempt_can_replace_failed_checkpoint_and_then_be_reused(
    tmp_path,
):
    store = SQLiteAgentStateStore(tmp_path / "cross-session-attempt.sqlite3")
    trace_id = "cross-session-attempt"
    tool = OutcomeTool("property_calculator", success=False)
    steps = [WorkflowStep("properties", tool.name, "CCO")]
    callback_events = []
    event_bus = AgentEventBus(
        state_store=store,
        on_event=callback_events.append,
    )

    first, _ = _make_session(
        steps=steps,
        tools={tool.name: tool},
        trace_id=trace_id,
        state_store=store,
        event_bus=event_bus,
    )
    first.start()
    first.execute_step(0)
    first_result = first.finish()

    assert first_result.success is False
    assert len(store.get_tool_executions(trace_id)) == 1
    assert store.latest_checkpoint(trace_id, "properties")["status"] == "failed"
    first_event_names = [event["event"] for event in store.get_events(trace_id)]
    assert first_event_names.count("tool_failed") == 1
    assert first_event_names.count("task_failed") == 1

    tool.success = True
    second, _ = _make_session(
        steps=steps,
        tools={tool.name: tool},
        trace_id=trace_id,
        state_store=store,
        event_bus=event_bus,
    )
    second.start()
    second.execute_step(0)
    second_result = second.finish()

    assert second_result.success is True
    assert second.tool_attempt_count == 1
    assert tool.calls == ["CCO", "CCO"]
    assert len(store.get_tool_executions(trace_id)) == 2
    assert store.latest_checkpoint(trace_id, "properties")["status"] == "succeeded"
    second_event_names = [event["event"] for event in store.get_events(trace_id)]
    assert second_event_names.count("tool_failed") == 1
    assert second_event_names.count("task_failed") == 1
    assert second_event_names.count("tool_completed") == 1
    assert second_event_names.count("task_completed") == 1

    third, _ = _make_session(
        steps=steps,
        tools={tool.name: tool},
        trace_id=trace_id,
        state_store=store,
        event_bus=event_bus,
    )
    third.start()
    advance = third.execute_step(0)
    third_result = third.finish()

    assert advance.outcome == "succeeded"
    assert third_result.success is True
    assert third.tool_attempt_count == 0
    assert tool.calls == ["CCO", "CCO"]
    assert third_result.metadata["reused_steps"] == ["properties"]
    final_event_names = [event["event"] for event in store.get_events(trace_id)]
    assert final_event_names.count("task_started") == 3
    assert final_event_names.count("tool_started") == 3
    assert final_event_names.count("tool_failed") == 1
    assert final_event_names.count("task_failed") == 1
    assert final_event_names.count("tool_completed") == 2
    assert final_event_names.count("task_completed") == 2
    memory_event_names = [event.event.value for event in event_bus.events]
    callback_event_names = [event.event.value for event in callback_events]
    assert memory_event_names == final_event_names
    assert callback_event_names == final_event_names


@pytest.mark.parametrize(
    ("attribute", "value"),
    [
        ("started", True),
        ("finished", True),
        ("tool_attempt_count", 9),
        ("next_index", 9),
    ],
)
def test_session_lifecycle_state_is_read_only(attribute, value):
    session, _ = _make_session(steps=[], tools={})

    with pytest.raises(AttributeError):
        setattr(session, attribute, value)


def test_session_step_count_uses_internal_snapshot_and_is_read_only():
    source_steps = [
        WorkflowStep("first", "first_tool", "CCO"),
        WorkflowStep("second", "second_tool", "CCN"),
    ]
    session, _ = _make_session(steps=source_steps, tools={})
    source_steps.clear()

    assert session.step_count == 2
    with pytest.raises(AttributeError):
        session.step_count = 0


def test_pre_tool_semantic_failure_can_retry_same_step_safely():
    validator = FailOnceSemanticValidator()
    tool = CountingTool()
    session, _ = _make_session(
        tools={tool.name: tool},
        semantic_validator=validator,
    )
    session.start()

    with pytest.raises(RuntimeError, match="semantic validator unavailable"):
        session.execute_step(0)

    assert session.tool_attempt_count == 0
    assert session.next_index == 0
    advance = session.execute_step(0)
    result = session.finish()
    assert advance.terminal is True
    assert tool.calls == ["CCO"]
    assert result.success is True


def test_start_failure_leaves_session_retryable(tmp_path):
    store = FailOnceStartStore(tmp_path / "start-retry.sqlite3")
    session, event_bus = _make_session(
        steps=[],
        tools={},
        trace_id="start-retry",
        state_store=store,
    )

    with pytest.raises(RuntimeError, match="start persistence failed"):
        session.start()

    assert session.started is False
    assert session.state is None
    assert event_bus.events == []
    session.start()
    result = session.finish()
    assert session.started is True
    assert session.finished is True
    assert result.outcome == RunOutcome.FAILED


def test_finish_retries_failed_status_update_without_duplicate_terminal_event(
    tmp_path,
):
    store = FailOnceStatusStore(tmp_path / "finish-retry.sqlite3")
    tool = CountingTool()
    session, event_bus = _make_session(
        tools={tool.name: tool},
        trace_id="finish-retry",
        state_store=store,
    )
    session.start()
    session.execute_step(0)

    with pytest.raises(RuntimeError, match="status persistence failed"):
        session.finish()

    assert session.finished is False
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1
    result = session.finish()
    assert result.success is True
    assert session.finished is True
    assert store.get_run("finish-retry")["status"] == "succeeded"
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1


def test_finish_retry_does_not_duplicate_terminal_event_after_callback_failure():
    event_bus = FailAfterTerminalEventBus()
    tool = CountingTool()
    session, _ = _make_session(
        tools={tool.name: tool},
        event_bus=event_bus,
    )
    session.start()
    session.execute_step(0)

    with pytest.raises(RuntimeError, match="terminal callback failed"):
        session.finish()

    assert session.finished is False
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1
    result = session.finish()
    assert result.success is True
    assert session.finished is True
    assert [event.event.value for event in event_bus.events].count(
        "task_completed"
    ) == 1


def test_legacy_run_delegates_entire_lifecycle_to_created_session(monkeypatch):
    orchestrator = WorkflowOrchestrator()
    context = AgentContext(query="CCO", trace_id="delegated-run")
    steps = [
        WorkflowStep(f"step-{index}", f"tool-{index}")
        for index in range(4)
    ]
    tools = {"sentinel": object()}
    marker = object()
    create_calls = []

    class SessionDouble:
        def __init__(self):
            self.step_count = len(steps)
            self.start_calls = 0
            self.execute_calls = []
            self.finish_calls = 0

        def start(self):
            self.start_calls += 1

        def execute_step(self, index):
            self.execute_calls.append(index)
            return SimpleNamespace(terminal=index == 1)

        def finish(self):
            self.finish_calls += 1
            return marker

    session = SessionDouble()

    def fake_create_session(**kwargs):
        create_calls.append(kwargs)
        return session

    monkeypatch.setattr(orchestrator, "create_session", fake_create_session)

    result = orchestrator.run(
        context,
        steps,
        tools,
        continue_on_error=True,
        idempotency_key="delegation-key",
    )

    assert create_calls == [
        {
            "context": context,
            "steps": steps,
            "tools": tools,
            "continue_on_error": True,
            "idempotency_key": "delegation-key",
        }
    ]
    assert create_calls[0]["context"] is context
    assert create_calls[0]["steps"] is steps
    assert create_calls[0]["tools"] is tools
    assert session.start_calls == 1
    assert session.execute_calls == [0, 1]
    assert session.finish_calls == 1
    assert result is marker


@pytest.mark.parametrize(
    ("caller_step_count", "session_step_count"),
    [(1, 3), (2, 0)],
)
def test_legacy_run_uses_authoritative_session_step_count(
    monkeypatch,
    caller_step_count,
    session_step_count,
):
    orchestrator = WorkflowOrchestrator()
    caller_steps = [
        WorkflowStep(f"caller-{index}", f"tool-{index}")
        for index in range(caller_step_count)
    ]
    marker = object()

    class OverrideSession:
        step_count = session_step_count

        def __init__(self):
            self.started = False
            self.execute_calls = []
            self.finished = False

        def start(self):
            self.started = True

        def execute_step(self, index):
            assert 0 <= index < self.step_count
            self.execute_calls.append(index)
            return SimpleNamespace(terminal=index == self.step_count - 1)

        def finish(self):
            assert self.execute_calls == list(range(self.step_count))
            self.finished = True
            return marker

    session = OverrideSession()
    monkeypatch.setattr(
        orchestrator,
        "create_session",
        lambda **_kwargs: session,
    )

    result = orchestrator.run(
        AgentContext(query="seed", trace_id="session-plan-override"),
        caller_steps,
        {},
    )

    assert session.started is True
    assert session.execute_calls == list(range(session_step_count))
    assert session.finished is True
    assert result is marker


def test_legacy_run_uses_session_plan_when_start_callback_clears_source_steps():
    tool = CountingTool()
    steps = [
        WorkflowStep("first", tool.name, "CCO"),
        WorkflowStep("second", tool.name, "CCN"),
    ]

    def clear_source_steps(event):
        if event.event == TaskEventType.TASK_STARTED:
            steps.clear()

    result = WorkflowOrchestrator(
        event_bus=AgentEventBus(on_event=clear_source_steps)
    ).run(
        AgentContext(query="seed", trace_id="cleared-source-steps"),
        steps,
        {tool.name: tool},
    )

    assert steps == []
    assert tool.calls == ["CCO", "CCN"]
    assert result.success is True
    assert result.metadata["completed_count"] == 2


def test_legacy_run_ignores_steps_appended_by_start_callback():
    tool = CountingTool()
    steps = []

    def append_source_step(event):
        if event.event == TaskEventType.TASK_STARTED:
            steps.append(WorkflowStep("late", tool.name, "late-input"))

    result = WorkflowOrchestrator(
        event_bus=AgentEventBus(on_event=append_source_step)
    ).run(
        AgentContext(query="seed", trace_id="appended-source-steps"),
        steps,
        {tool.name: tool},
    )

    assert len(steps) == 1
    assert tool.calls == []
    assert result.outcome == RunOutcome.FAILED
    assert result.metadata["completed_count"] == 0


def _manual_session_driver(orchestrator, context, steps, tools, **kwargs):
    session = orchestrator.create_session(
        context=context,
        steps=steps,
        tools=tools,
        **kwargs,
    )
    step_count = session.step_count
    session.start()
    for index in range(step_count):
        advance = session.execute_step(index)
        if advance.terminal:
            break
    return session.finish()


def _without_runtime_identity(value):
    ignored = {
        "attempt_id",
        "created_at",
        "event_id",
        "session_attempt_id",
        "timestamp",
        "trace_id",
        "updated_at",
    }
    if not isinstance(value, dict):
        return deepcopy(value)
    return {
        key: deepcopy(item)
        for key, item in value.items()
        if key not in ignored
    }


def _public_events(event_bus):
    return [
        {
            "event": event.event.value,
            "message": event.message,
            "skill": event.skill,
            "tool": event.tool,
            "progress": event.progress,
            "payload": deepcopy(event.payload),
        }
        for event in event_bus.events
    ]


def test_public_event_projection_preserves_skill_and_business_payload():
    event_bus = AgentEventBus()
    event_bus.emit(
        trace_id="runtime-trace",
        event=TaskEventType.TOOL_COMPLETED,
        message="candidate generation completed",
        skill="molecule_generation",
        tool="llm_molecular_generator",
        progress=0.75,
        payload={
            "status": "succeeded",
            "warnings": ["business warning"],
            "quality": {"validated": True},
            "evidence": [{"claim": "CCO is valid"}],
            "business_audit": {
                "trace_id": "source-trace",
                "timestamp": "2026-08-11T00:00:00Z",
                "created_at": "2026-08-10T00:00:00Z",
            },
        },
    )

    assert _public_events(event_bus) == [
        {
            "event": "tool_completed",
            "message": "candidate generation completed",
            "skill": "molecule_generation",
            "tool": "llm_molecular_generator",
            "progress": 0.75,
            "payload": {
                "status": "succeeded",
                "warnings": ["business warning"],
                "quality": {"validated": True},
                "evidence": [{"claim": "CCO is valid"}],
                "business_audit": {
                    "trace_id": "source-trace",
                    "timestamp": "2026-08-11T00:00:00Z",
                    "created_at": "2026-08-10T00:00:00Z",
                },
            },
        }
    ]

    different_payload = AgentEventBus()
    different_payload.emit(
        trace_id="other-runtime-trace",
        event=TaskEventType.TOOL_COMPLETED,
        message="candidate generation completed",
        skill="different_skill",
        tool="llm_molecular_generator",
        progress=0.75,
        payload={"status": "failed", "quality": {"validated": False}},
    )

    assert _public_events(event_bus) != _public_events(different_payload)


def test_legacy_run_matches_manual_session_for_single_success():
    context = AgentContext(query="CCO", trace_id="single-parity")
    steps = [
        WorkflowStep(
            "properties",
            "property_calculator",
            input_data="CCO",
            output_key="properties",
        )
    ]
    legacy_tool = CountingTool()
    manual_tool = CountingTool()
    legacy_events = AgentEventBus()
    manual_events = AgentEventBus()

    legacy_result = WorkflowOrchestrator(event_bus=legacy_events).run(
        context,
        steps,
        {legacy_tool.name: legacy_tool},
    )
    manual_result = _manual_session_driver(
        WorkflowOrchestrator(event_bus=manual_events),
        context,
        steps,
        {manual_tool.name: manual_tool},
    )

    assert _without_runtime_identity(
        deepcopy(legacy_result.to_legacy_dict())
    ) == _without_runtime_identity(deepcopy(manual_result.to_legacy_dict()))
    assert _public_events(legacy_events) == _public_events(manual_events)
    assert legacy_tool.calls == manual_tool.calls == ["CCO"]


def test_legacy_run_matches_manual_session_for_optional_failure_then_success():
    context = AgentContext(query="seed", trace_id="multi-step-parity")
    steps = [
        WorkflowStep(
            "optional",
            "optional_tool",
            input_data="optional-input",
            required=False,
        ),
        WorkflowStep(
            "required",
            "required_tool",
            input_data="required-input",
            output_key="result",
        ),
    ]
    legacy_optional = OutcomeTool("optional_tool", success=False)
    legacy_required = OutcomeTool("required_tool", success=True)
    manual_optional = OutcomeTool("optional_tool", success=False)
    manual_required = OutcomeTool("required_tool", success=True)
    legacy_events = AgentEventBus()
    manual_events = AgentEventBus()

    legacy_result = WorkflowOrchestrator(event_bus=legacy_events).run(
        context,
        steps,
        {
            legacy_optional.name: legacy_optional,
            legacy_required.name: legacy_required,
        },
    )
    manual_result = _manual_session_driver(
        WorkflowOrchestrator(event_bus=manual_events),
        context,
        steps,
        {
            manual_optional.name: manual_optional,
            manual_required.name: manual_required,
        },
    )

    assert _without_runtime_identity(
        deepcopy(legacy_result.to_legacy_dict())
    ) == _without_runtime_identity(deepcopy(manual_result.to_legacy_dict()))
    assert _public_events(legacy_events) == _public_events(manual_events)
    assert legacy_optional.calls == manual_optional.calls == ["optional-input"]
    assert legacy_required.calls == manual_required.calls == ["required-input"]
