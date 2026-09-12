"""Offline Task5 contracts; test doubles are not scientific acceptance.

Restoration consumes caller-prevalidated observations after a storage claim.
This does not test caller authentication/checksums/budgets or crash recovery.
"""
from copy import deepcopy
from dataclasses import replace
import inspect
import sqlite3
from types import SimpleNamespace

import pytest

from src.agent.contracts import (
    AgentContext, AgentErrorCode, AgentExecutionError, ObservationStatus,
    RunOutcome, ToolProvenance, ToolResult, WorkflowArtifact,
)
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.run_session import SessionLifecycleError, WorkflowRunSession
from src.agent.runtime.task_state import TaskEventType
from src.agent.tooling.factory import build_tool_registry
from src.agent.validators import AgentResultValidator
from src.agent.validators.molecule_candidates import sanitize_generated_candidates


class ContractTool:
    name = "property_calculator"
    version = "trusted-test-5"
    description = "Offline session contract double"
    timeout_seconds = 1

    def __init__(self, result=None):
        self.calls = []
        self.result = result or ToolResult.success_result(
            self.name, {"input": "contract-only"}, warnings=["test-warning"])

    def execute(self, query):
        self.calls.append(deepcopy(query))
        return deepcopy(self.result)


def action(name="round-1", *, required=False, **kwargs):
    metadata = {
        "decision_id": "decision-" + name, "tool_call_id": "call-" + name,
        "round": 1, "input_evidence_ids": [], "operation_key": "properties",
        "request_input_digest": "request-digest",
    }
    metadata.update(kwargs.pop("metadata", {}))
    return WorkflowStep(name, kwargs.pop("tool_name", "property_calculator"),
                        input_data=kwargs.pop("input_data", {"query": "CCO"}),
                        output_key=kwargs.pop("output_key", name), required=required,
                        metadata=metadata, **kwargs)


@pytest.fixture
def make_session(tmp_path):
    registries = []

    def build(*, source=None, dynamic=True, steps=None, store=None, trace="trace",
              **kwargs):
        assert "dynamic" in inspect.signature(WorkflowRunSession).parameters, (
            "Task5 opt-in dynamic session API is missing")
        source = source or ContractTool()
        registry = build_tool_registry([source])
        registries.append(registry)
        store = store or SQLiteAgentStateStore(tmp_path / f"run-{len(registries)}.sqlite")
        bus = AgentEventBus(state_store=store)
        orchestrator = WorkflowOrchestrator(event_bus=bus, state_store=store)
        session = WorkflowRunSession(
            orchestrator, AgentContext("CCO", trace, user_id="owner", session_id="session"),
            steps or [], registry.as_mapping(), dynamic=dynamic, **kwargs)
        return SimpleNamespace(session=session, tool=source, store=store, bus=bus,
                               orchestrator=orchestrator)

    yield build
    for registry in registries:
        registry.close()


def terminal_events(bundle):
    return [event for event in bundle.bus.events if event.event in {
        TaskEventType.TASK_COMPLETED, TaskEventType.TASK_PARTIAL,
        TaskEventType.TASK_REJECTED, TaskEventType.TASK_CANCELLED, TaskEventType.TASK_FAILED,
    }]


def settle(bundle, step=None):
    bundle.session.append_step(step or action())
    return bundle.session.execute_step(bundle.session.next_index)


@pytest.mark.parametrize("outcome", [None, RunOutcome.COMPLETED])
def test_dynamic_completion_rejects_preserved_structured_error(make_session, outcome):
    observed = ToolResult.success_result("property_calculator", {"input": "contract-only"})
    observed.error = AgentExecutionError(AgentErrorCode.MODEL_UNAVAILABLE, "offline failure")
    bundle = make_session(source=ContractTool(observed))
    bundle.session.start()
    settle(bundle)
    assert bundle.session.results[0].error == observed.error
    with pytest.raises(SessionLifecycleError, match="known execution failure"):
        bundle.session.finish_dynamic("must remain truthful", outcome=outcome)
    assert not terminal_events(bundle)
    final = bundle.session.finish_dynamic("model unavailable", outcome=RunOutcome.PARTIAL)
    assert not final.success and final.partial
    assert final.error == observed.error
    assert terminal_events(bundle)[0].event == TaskEventType.TASK_PARTIAL


def claimed_session(make_session, source):
    """Exercise PR22 CAS, without pretending the placeholder checksum is verified."""
    store = source.store
    store.update_run_status("trace", "partial")
    waiting = {"schema": 1, "id": "continuation", "configuration": "a" * 64,
               "checksum": "b" * 64, "snapshot": {"results": []}}
    assert store.transition_decision_continuation(
        "trace", user_id="owner", session_id="session", expected=None,
        replacement=waiting, claim=False)
    assert store.transition_decision_continuation(
        "trace", user_id="owner", session_id="session", expected=waiting,
        replacement={**waiting, "claimed_by": "claim-test"}, claim=True)
    resumed = make_session(store=store)
    resumed.session.start(resume_claimed=True)
    return resumed


def test_dynamic_opt_in_and_static_default_contract():
    parameter = inspect.signature(WorkflowRunSession).parameters.get("dynamic")
    assert parameter is not None, "Task5 dynamic opt-in is missing"
    assert parameter.default is False and parameter.kind == parameter.KEYWORD_ONLY


@pytest.mark.parametrize("kwargs", [
    {"steps": [action()]}, {"idempotency_key": "existing"}, {"idempotency_key": ""},
])
def test_dynamic_requires_empty_plan_and_no_idempotency(make_session, kwargs):
    with pytest.raises(SessionLifecycleError):
        make_session(**kwargs)


def test_start_claims_new_run_exclusively_without_fixed_planning(make_session):
    first = make_session()
    first.session.start()
    before = first.store.get_run("trace")
    collision = make_session(store=SQLiteAgentStateStore(first.store.db_path))
    with pytest.raises(sqlite3.IntegrityError):
        collision.session.start()
    assert first.store.get_run("trace") == before
    assert not collision.session.started and not collision.bus.events
    assert [e.event for e in first.bus.events] == [TaskEventType.TASK_STARTED]
    assert not first.session.finished
    with pytest.raises(SessionLifecycleError):
        first.session.finish()


def test_append_one_action_at_a_time_stays_open_and_traces_events(make_session):
    b = make_session()
    with pytest.raises(SessionLifecycleError):
        b.session.append_step(action())
    b.session.start()
    step = action()
    b.session.append_step(step)
    with pytest.raises(SessionLifecycleError):
        b.session.append_step(action("round-2"))
    with pytest.raises(SessionLifecycleError):
        b.session.finish_dynamic("premature")
    advance = b.session.execute_step(0)
    assert not advance.terminal and advance.reason is None
    with pytest.raises(SessionLifecycleError):
        b.session.execute_step(0)
    with pytest.raises(SessionLifecycleError):
        b.session.append_step(step)
    settle(b, action("round-2"))
    assert b.tool.calls == ["CCO", "CCO"]
    assert not terminal_events(b)
    for event in b.bus.events:
        if event.tool:
            assert event.progress is None
            assert event.payload["decision_id"] == "decision-" + event.payload["step_id"]
            assert event.payload["tool_call_id"] == "call-" + event.payload["step_id"]
            assert event.payload["round"] == 1
    result = b.session.finish_dynamic("observed", metadata={"caller_note": "kept"})
    assert result.success and result.final_answer == "observed"
    assert result.metadata["step_count"] == result.metadata["completed_count"] == 2
    assert result.metadata["caller_note"] == "kept"
    assert len(terminal_events(b)) == 1
    for operation in (lambda: b.session.append_step(action("late")),
                      lambda: b.session.finish_dynamic("again")):
        with pytest.raises(SessionLifecycleError):
            operation()


@pytest.mark.parametrize("required,override,terminal", [
    (True, True, True), (False, None, False), (False, False, True),
])
def test_failed_step_retains_structured_persistence_and_stop_policy(
    make_session, required, override, terminal,
):
    failure = ToolResult.error_result(
        "property_calculator", AgentErrorCode.MODEL_UNAVAILABLE, "test dependency absent",
        details={"reason": "offline-test"}, warnings=["dependency warning"])
    b = make_session(source=ContractTool(failure), continue_on_error=True)
    b.session.start()
    advance = settle(b, action(required=required, continue_on_error=override))
    assert advance.terminal is terminal
    observed = b.session.results[0]
    checkpoint = b.store.latest_checkpoint("trace", "round-1")
    execution = b.store.get_tool_executions("trace")[0]
    for row in (checkpoint, execution):
        assert row["output"] == observed.to_legacy_dict()
        assert row["error"] == observed.error.to_dict()
    assert "round-1" not in b.session.outputs
    assert b.session.state.errors[0]["error"] == observed.error.to_dict()
    if terminal:
        with pytest.raises(SessionLifecycleError):
            b.session.append_step(action("later"))
    else:
        assert not b.session.finished
    result = b.session.finish_dynamic("dependency absent")
    assert not result.success and result.outcome == RunOutcome.FAILED
    assert result.error == observed.error


def test_dynamic_identity_rejects_wrong_tool_result(make_session):
    b = make_session(source=ContractTool(ToolResult.success_result(
        "admet_predictor", {"untrusted": "wrong tool"})))
    b.session.start()
    settle(b)
    result = b.session.results[0]
    assert not result.success and result.tool_name == "property_calculator"
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert not b.session.outputs
    assert not b.session.ledger.to_list()[0]["scientific_usable"]


def test_dynamic_authoritative_version_bindings_and_digest(make_session):
    forged = ToolResult.success_result(
        "property_calculator", {"input": "contract"},
        quality={"tool_version": "forged", "operation_key": "forged",
                 "request_input_digest": "forged", "input_evidence_ids": ["forged"]},
        provenance=ToolProvenance("property_calculator", tool_version="forged",
                                  input_digest="forged", output_digest="forged"))
    b = make_session(source=ContractTool(forged))
    b.session.start()
    step = action(metadata={"tool_version": "also-forged"})
    settle(b, step)
    result = b.session.results[0]
    assert result.provenance.tool_version == b.tool.version
    assert result.quality["tool_version"] == b.tool.version
    assert result.provenance.input_digest == b.orchestrator._input_hash(step.input_data)
    assert result.provenance.output_digest == EvidenceLedger.output_digest(result.data)
    for key in ("input_evidence_ids", "operation_key", "request_input_digest"):
        assert result.quality[key] == step.metadata[key]
    checkpoint = b.store.latest_checkpoint("trace", step.name)
    assert checkpoint["tool_version"] == b.tool.version
    record = b.session.ledger.get(result.quality["evidence_id"])
    assert record["input_binding"]["operation_key"] == "properties"
    assert record["provenance"] == result.provenance.to_dict()


@pytest.mark.parametrize("matches", [True, False])
def test_candidate_alignment_digest_matches_accepted_not_raw_data(make_session, matches):
    # Uses real RDKit alignment; no monkeypatch or fake green when unavailable.
    candidates = sanitize_generated_candidates(
        [{"smiles": "CCO"}], requested_count=1).candidate_set.to_dict()
    raw = [{"smiles": "CCC"}]
    if matches:
        raw.insert(0, {"smiles": "OCC"})
    b = make_session(source=ContractTool(ToolResult.success_result("property_calculator", raw)))
    b.session.start()
    b.session.outputs["candidates"] = candidates
    settle(b, action(metadata={"candidate_source": "candidates"}))
    result = b.session.results[0]
    assert result.success is matches
    assert result.data != raw
    expected = EvidenceLedger.output_digest(result.data)
    assert expected != EvidenceLedger.output_digest(raw)
    assert result.provenance.output_digest == expected
    assert b.session.ledger.get(result.quality["evidence_id"])["provenance"]["output_digest"] == expected
    assert b.store.latest_checkpoint("trace")["output"]["provenance"]["output_digest"] == expected


def test_validation_scrub_rebuilds_provenance_and_keeps_failure(make_session):
    source = ContractTool(ToolResult.error_result(
        "run_docking", AgentErrorCode.TOOL_UNAVAILABLE, "test unavailable",
        provenance=ToolProvenance("run_docking", output_digest="forged")))
    source.name = "run_docking"
    b = make_session(source=source)
    b.session.start()
    settle(b, action(tool_name="run_docking"))
    result = b.session.results[0]
    assert result.data is None and result.evidence == []
    assert result.provenance.tool_version == source.version
    assert result.provenance.output_digest == EvidenceLedger.output_digest(None)
    assert result.quality["step_id"] == "round-1"
    assert result.quality["request_input_digest"] == "request-digest"
    assert not result.success


def test_existing_input_binding_and_precondition_validators_are_used(make_session):
    b = make_session()
    b.session.start()
    settle(b, action(required=True, input_binding="missing.value"))
    assert b.session.results[0].error.code == AgentErrorCode.INVALID_INPUT
    assert b.session.tool_attempt_count == 0 and b.tool.calls == []
    second = make_session(trace="blocked")
    second.session.start()
    advance = settle(second, action(preconditions=("target_evidence",)))
    assert advance.outcome == "skipped_precondition" and advance.terminal
    assert second.tool.calls == []
    final = second.session.finish_dynamic("precondition unavailable")
    assert not final.success and final.error.code == AgentErrorCode.VALIDATION_ERROR


def test_binding_failure_still_records_trusted_adapter_version(make_session):
    b = make_session()
    b.session.start()
    settle(b, action(input_binding="$.outputs.missing", metadata={"tool_version": "forged"}))
    result = b.session.results[0]
    assert result.error.code == AgentErrorCode.INVALID_INPUT
    assert result.provenance.tool_version == b.tool.version
    assert b.store.latest_checkpoint("trace")["tool_version"] == b.tool.version
    assert b.session.tool_attempt_count == 0 and b.tool.calls == []


def test_unbound_action_removes_tool_supplied_binding_claims(make_session):
    forged = ToolResult.success_result("property_calculator", {"input": "contract"}, quality={
        "input_evidence_ids": ["forged"], "request_input_digest": "forged", "operation_key": "forged"})
    b = make_session(source=ContractTool(forged))
    b.session.start()
    settle(b, replace(action(), metadata={}))
    for key in ("input_evidence_ids", "request_input_digest", "operation_key"):
        assert b.session.results[0].quality[key] is None
    assert "input_binding" not in b.session.ledger.to_list()[0]


@pytest.mark.parametrize("operation", ["append", "finish_dynamic", "restore", "claimed_start"])
def test_static_rejects_dynamic_lifecycle(make_session, operation):
    b = make_session(dynamic=False)
    if operation == "claimed_start":
        with pytest.raises(SessionLifecycleError):
            b.session.start(resume_claimed=True)
        b.session.start()
    else:
        b.session.start()
        with pytest.raises(SessionLifecycleError):
            {"append": lambda: b.session.append_step(action()),
             "finish_dynamic": lambda: b.session.finish_dynamic("invalid"),
             "restore": lambda: b.session.restore_observations([], 0)}[operation]()
    assert [e.event for e in b.bus.events][:3] == [
        TaskEventType.TASK_STARTED, TaskEventType.PLANNING_STARTED, TaskEventType.PLANNING_COMPLETED]
    b.session.finish()


def test_finish_runtime_error_cannot_be_forged_by_finish_argument(make_session):
    b = make_session()
    b.session.start()
    b.session.append_step(action())
    error = AgentExecutionError(AgentErrorCode.INTERNAL_ERROR, "caller error")
    with pytest.raises(SessionLifecycleError):
        b.session.finish_dynamic("not settled", error=error, outcome=RunOutcome.FAILED)
    b.session.fail_runtime("verified-runtime-fault")
    final = b.session.finish_dynamic("must not publish an unverified answer")
    assert final.outcome == RunOutcome.FAILED and not final.success and not final.partial
    assert final.final_answer == ""
    assert final.error.details == {"runtime_error_code": "verified-runtime-fault"}
    assert len(terminal_events(b)) == 1 and b.tool.calls == []


@pytest.mark.parametrize("failure", ["required", "optional", "partial", "runtime", "precondition"])
def test_finish_cannot_upgrade_failed_or_partial_evidence_to_completed(make_session, failure):
    observation = ToolResult.success_result("property_calculator", {"input": "test"})
    if failure in {"required", "optional"}:
        observation = ToolResult.error_result("property_calculator", AgentErrorCode.TOOL_UNAVAILABLE, "absent")
    elif failure == "partial":
        observation.status = ObservationStatus.PARTIAL
    b = make_session(source=ContractTool(observation))
    b.session.start()
    if failure == "runtime":
        b.session.fail_runtime("verified")
    elif failure == "precondition":
        settle(b, action(preconditions=("target_evidence",)))
    else:
        settle(b, action(required=failure == "required"))
    with pytest.raises(SessionLifecycleError):
        b.session.finish_dynamic("false success", outcome=RunOutcome.COMPLETED)
    assert not b.session.finished and not terminal_events(b)
    final = b.session.finish_dynamic("truthful failure")
    assert not final.success


def test_finish_rejects_success_with_error_and_reserved_metadata_override(make_session):
    b = make_session()
    b.session.start()
    settle(b)
    with pytest.raises(SessionLifecycleError):
        b.session.finish_dynamic("invalid", outcome=RunOutcome.COMPLETED,
                                 error=AgentExecutionError(AgentErrorCode.INTERNAL_ERROR, "failed"))
    with pytest.raises(SessionLifecycleError):
        b.session.finish_dynamic("invalid", metadata={"tool_attempt_count": 999})
    assert b.session.finish_dynamic("observed").metadata["tool_attempt_count"] == 1


def test_chat_without_actions_can_explicitly_complete(make_session):
    b = make_session()
    b.session.start()
    result = b.session.finish_dynamic("Hello", outcome=RunOutcome.COMPLETED)
    assert result.success and result.outcome == RunOutcome.COMPLETED
    assert result.error is None and result.final_answer == "Hello"
    assert result.metadata["step_count"] == result.metadata["tool_attempt_count"] == 0
    assert b.tool.calls == [] and len(terminal_events(b)) == 1


@pytest.mark.parametrize("outcome", [RunOutcome.REJECTED, RunOutcome.CANCELLED, RunOutcome.FAILED])
def test_finish_caller_stop_reason_keeps_truthful_error_and_one_event(make_session, outcome):
    b = make_session()
    b.session.start()
    reason = AgentExecutionError(AgentErrorCode.INVALID_INPUT, "caller stopped before dispatch")
    result = b.session.finish_dynamic("no calculation performed", outcome=outcome, error=reason)
    assert result.outcome == outcome and not result.success and not result.partial
    assert result.error == reason and result.tool_results == []
    assert len(terminal_events(b)) == 1
    assert b.store.get_run("trace")["status"] == outcome.value


def test_restore_preserves_identity_state_counts_and_calls_no_tool(make_session):
    first = make_session()
    first.tool.result.quality["fallback_used"] = True
    first.session.start()
    settle(first, action(output_key="target"))
    first.tool.result = ToolResult.error_result(
        "property_calculator", AgentErrorCode.MODEL_UNAVAILABLE, "offline failure",
        warnings=["failure warning"], artifacts=[WorkflowArtifact("test-report", "reports/test.txt", "Test report")])
    settle(first, action("round-2"))
    observations = deepcopy(first.session.results)
    resumed = claimed_session(make_session, first)
    before_executions = first.store.get_tool_executions("trace")
    resumed.session.restore_observations(observations, first.session.tool_attempt_count)
    assert resumed.session.ledger.to_list() == first.session.ledger.to_list()
    assert resumed.session.state.to_dict() == first.session.state.to_dict()
    assert resumed.session.outputs["target"]["quality"]["fallback_used"] is True
    assert resumed.session.results == observations
    assert resumed.session.tool_attempt_count == 2
    assert resumed.tool.calls == []
    assert first.store.get_tool_executions("trace") == before_executions
    with pytest.raises(SessionLifecycleError):
        resumed.session.append_step(action())
    observations[0].warnings.append("external mutation")
    observations[0].data["input"] = "external mutation"
    observations[1].artifacts[0].metadata["changed"] = True
    observations[1].error.message = "external mutation"
    assert "external mutation" not in resumed.session.results[0].warnings
    assert resumed.session.outputs["target"]["data"]["input"] == "contract-only"
    assert resumed.session.state.artifacts[0]["metadata"] == {}
    assert resumed.session.results[1].error.message == "offline failure"
    settle(resumed, action("round-3"))
    final = resumed.session.finish_dynamic("restored observations and one new action")
    assert final.metadata["step_count"] == final.metadata["completed_count"] == 3
    assert final.metadata["tool_attempt_count"] == 3 and resumed.tool.calls == ["CCO"]
    assert final.outcome == RunOutcome.PARTIAL and final.error is not None


@pytest.mark.parametrize("bad", ["identity", "missing_provenance", "duplicate", "bad_status"])
def test_bad_restore_is_all_or_nothing_and_retryable(make_session, bad):
    first = make_session()
    first.session.start()
    settle(first)
    settle(first, action("round-2"))
    observations = deepcopy(first.session.results)
    resumed = claimed_session(make_session, first)
    original = (resumed.session.ledger.to_list(), deepcopy(resumed.session.state.to_dict()))
    if bad == "identity":
        observations[1].quality["evidence_id"] = "wrong"
    elif bad == "missing_provenance":
        observations[1].provenance = None
    elif bad == "duplicate":
        observations[1] = deepcopy(observations[0])
    else:
        observations[1].status = "running"
    with pytest.raises(SessionLifecycleError):
        resumed.session.restore_observations(observations, 2)
    assert (resumed.session.ledger.to_list(), resumed.session.state.to_dict()) == original
    assert resumed.session.results == [] and resumed.session.tool_attempt_count == 0
    resumed.session.restore_observations(first.session.results, 2)
    assert resumed.session.ledger.to_list() == first.session.ledger.to_list()


@pytest.mark.parametrize("count", [-1, True, 1.5, "2"])
def test_restore_requires_nonnegative_integer_attempt_count(make_session, count):
    source = make_session()
    source.session.start()
    resumed = claimed_session(make_session, source)
    with pytest.raises(SessionLifecycleError):
        resumed.session.restore_observations([], count)
    assert resumed.session.tool_attempt_count == 0


@pytest.mark.parametrize("state", ["not_started", "new_run", "appended", "runtime", "finished", "restored"])
def test_restore_rejects_bad_lifecycle_even_with_empty_observations(make_session, state):
    source = make_session()
    source.session.start()
    if state == "not_started":
        b = make_session()
    elif state == "new_run":
        b = source
    else:
        b = claimed_session(make_session, source)
        if state == "appended":
            b.session.append_step(action())
        elif state == "runtime":
            b.session.fail_runtime("verified")
        elif state == "finished":
            b.session.finish_dynamic("no observations")
        else:
            b.session.restore_observations([], 0)
    with pytest.raises(SessionLifecycleError):
        b.session.restore_observations([], 0)


def test_postprocess_retry_never_dispatches_another_tool(make_session):
    class FailOnceValidator(AgentResultValidator):
        failed = False

        def validate_tool_result(self, result, **kwargs):
            if not self.failed:
                self.failed = True
                raise RuntimeError("injected postprocess fault")
            return super().validate_tool_result(result, **kwargs)

    b = make_session()
    b.orchestrator.validator = FailOnceValidator()
    b.session.start()
    b.session.append_step(action())
    with pytest.raises(RuntimeError, match="injected postprocess"):
        b.session.execute_step(0)
    with pytest.raises(SessionLifecycleError):
        b.session.append_step(action("unsafe-next"))
    with pytest.raises(SessionLifecycleError):
        b.session.finish_dynamic("unsettled")
    assert not b.session.execute_step(0).terminal
    assert b.tool.calls == ["CCO"] and b.session.tool_attempt_count == 1


def test_finish_retry_detaches_caller_error_from_durable_event(make_session, monkeypatch):
    b = make_session()
    b.session.start()
    error = AgentExecutionError(
        AgentErrorCode.INVALID_INPUT, "original reason", {"reason": {"value": "original"}})
    original_error = deepcopy(error)
    original_update = b.store.update_run_status
    failed = False

    def fail_once(*args):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected status fault")
        return original_update(*args)

    monkeypatch.setattr(b.store, "update_run_status", fail_once)
    with pytest.raises(RuntimeError, match="injected status"):
        b.session.finish_dynamic("stopped", outcome=RunOutcome.REJECTED, error=error)
    events = [e for e in b.store.get_events("trace") if e["event"] == "task_rejected"]
    assert len(events) == 1 and events[0]["payload"]["error"] == original_error.to_dict()
    error.message = "mutated reason"
    error.details["reason"]["value"] = "mutated"
    final = b.session.finish_dynamic("stopped", outcome=RunOutcome.REJECTED, error=original_error)
    assert final.error == original_error
    assert len(terminal_events(b)) == 1
    assert terminal_events(b)[0].payload["error"] == original_error.to_dict()
    assert b.store.get_run("trace")["status"] == "rejected"


def test_finish_retry_keeps_original_result_and_one_terminal_event(make_session, monkeypatch):
    b = make_session()
    b.session.start()
    settle(b)
    original_update = b.store.update_run_status
    failed = False

    def fail_once(*args):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected status fault")
        return original_update(*args)

    monkeypatch.setattr(b.store, "update_run_status", fail_once)
    with pytest.raises(RuntimeError, match="injected status"):
        b.session.finish_dynamic("original")
    with pytest.raises(SessionLifecycleError):
        b.session.append_step(action("late"))
    with pytest.raises(SessionLifecycleError):
        b.session.finish_dynamic("changed", outcome=RunOutcome.FAILED)
    final = b.session.finish_dynamic("original")
    assert final.success and final.final_answer == "original"
    assert len(terminal_events(b)) == 1
    assert terminal_events(b)[0].payload["final_answer"] == final.final_answer


@pytest.mark.parametrize("phase", ["execution", "checkpoint"])
def test_dynamic_persistence_retry_after_commit_is_idempotent(make_session, monkeypatch, phase):
    b = make_session()
    method = "record_tool_execution" if phase == "execution" else "save_checkpoint"
    original = getattr(b.store, method)
    failed = False

    def fail_after_commit(row):
        nonlocal failed
        value = original(row)
        if row["status"] != "running" and not failed:
            failed = True
            raise RuntimeError("injected commit fault")
        return value

    monkeypatch.setattr(b.store, method, fail_after_commit)
    b.session.start()
    b.session.append_step(action())
    with pytest.raises(RuntimeError, match="injected commit"):
        b.session.execute_step(0)
    with pytest.raises(SessionLifecycleError):
        b.session.finish_dynamic("unsettled")
    assert not b.session.execute_step(0).terminal
    assert b.tool.calls == ["CCO"] and len(b.session.results) == 1
    assert len(b.session.ledger.to_list()) == 1
    assert len(b.store.get_tool_executions("trace")) == 1
    assert b.store.latest_checkpoint("trace")["status"] == "succeeded"
    assert b.session.finish_dynamic("observed").success


@pytest.mark.parametrize("event", [TaskEventType.TASK_STARTED, TaskEventType.TOOL_STARTED,
                                   TaskEventType.TOOL_COMPLETED, TaskEventType.TASK_COMPLETED])
def test_dynamic_callback_fault_retry_does_not_duplicate_events(make_session, event):
    b = make_session()
    failed = False

    def fail_once(item):
        nonlocal failed
        if item.event == event and not failed:
            failed = True
            raise RuntimeError("injected callback fault")

    b.bus.on_event = fail_once
    if event == TaskEventType.TASK_STARTED:
        with pytest.raises(RuntimeError, match="injected callback"):
            b.session.start()
    b.session.start()
    b.session.append_step(action())
    if event in {TaskEventType.TOOL_STARTED, TaskEventType.TOOL_COMPLETED}:
        with pytest.raises(RuntimeError, match="injected callback"):
            b.session.execute_step(0)
    b.session.execute_step(0)
    if event == TaskEventType.TASK_COMPLETED:
        with pytest.raises(RuntimeError, match="injected callback"):
            b.session.finish_dynamic("observed")
    assert b.session.finish_dynamic("observed").success
    assert b.tool.calls == ["CCO"] and b.session.tool_attempt_count == 1
    assert [e.event for e in b.bus.events].count(event) == 1
    assert len(terminal_events(b)) == 1
