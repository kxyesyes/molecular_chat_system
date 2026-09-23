"""Owner-bound entry and atomic-start regressions; all tools are local fixtures."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
import hashlib
import json
import sqlite3

import pytest

from src.agent.contracts import AgentContext
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.run_session import RunClaimConflict, WorkflowRunSession
from src.agent.runtime.delegated_executor import DelegatedWorkflowExecutor
from src.agent.runtime.workflow_executor import WorkflowExecutor
from src.agent.harness import HarnessFactory
from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent
from test_delegated_session_lifecycle import SinglePlanner
from test_supervisor_delegation import build_registry


SKILL = "comprehensive_evaluation"


def snapshot(store):
    with sqlite3.connect(store.db_path) as connection:
        return list(connection.iterdump())


@pytest.fixture(params=[False, True], ids=["plain", "delegated"])
def runtime(request, tmp_path):
    registry, tools = build_registry()
    store = SQLiteAgentStateStore(tmp_path / "ownership.sqlite")
    supervisor = SupervisorAgent(
        tools=tools, planner=SinglePlanner(), state_store=store,
        tool_registry=registry if request.param else None,
        specialists=build_default_specialists() if request.param else None,
    )
    yield supervisor, store, tools
    registry.close()


def invoke(supervisor, **kwargs):
    return supervisor.run(kwargs.pop("query", "CCO"),
                          skill_name=kwargs.pop("skill_name", SKILL), **kwargs)


def assert_denied(result):
    assert result["status"] in {"failed", "rejected"}
    assert result["result"]["metadata"]["run_claim_conflict"] is True
    assert result.get("agent_events", []) == []


@pytest.mark.parametrize("owner,caller", [("a", "b"), ("a", None), (None, "a")])
@pytest.mark.parametrize("by_key", [False, True])
def test_owner_mismatch_is_read_only(runtime, owner, caller, by_key):
    supervisor, store, tools = runtime
    # A raw legacy key must never become a route into an unowned/foreign trace.
    store.start_run({"trace_id": "victim", "status": "succeeded", "query": "CCO",
                     "skill_name": SKILL, "session_id": owner,
                     "idempotency_key": "raw" if by_key else None})
    before = snapshot(store)
    kwargs = {"trace_id": "victim", "session_id": caller}
    if by_key:
        kwargs["metadata"] = {"idempotency_key": "raw"}
    assert_denied(invoke(supervisor, **kwargs))
    assert snapshot(store) == before
    assert tools["property_calculator"].calls == []


@pytest.mark.parametrize("conflict_trace", ["another-new-trace", "occupied"])
def test_explicit_trace_cannot_be_silently_replaced_by_key(runtime, conflict_trace):
    supervisor, store, tools = runtime
    assert invoke(supervisor, trace_id="original", session_id="a",
                  metadata={"idempotency_key": "retry"})["status"] == "succeeded"
    if conflict_trace == "occupied":
        store.start_run({"trace_id": conflict_trace, "session_id": "b"})
    before = snapshot(store)
    assert_denied(invoke(supervisor, trace_id=conflict_trace, session_id="a",
                         metadata={"idempotency_key": "retry"}))
    assert snapshot(store) == before
    assert tools["property_calculator"].calls == ["CCO"]


@pytest.mark.parametrize("change", [{"query": "CCC"}, {"skill_name": "admet_assessment"},
                                    {"metadata": {"idempotency_key": "different"}}])
def test_same_owner_request_identity_is_immutable(runtime, change):
    supervisor, store, tools = runtime
    args = {"trace_id": "same", "session_id": "a", "metadata": {"idempotency_key": "retry"}}
    invoke(supervisor, **args)
    before = snapshot(store)
    assert_denied(invoke(supervisor, **{**args, **change}))
    assert snapshot(store) == before
    assert tools["property_calculator"].calls == ["CCO"]


def test_key_scoping_replay_and_metadata_boundary(runtime):
    supervisor, store, tools = runtime
    metadata = {"idempotency_key": "retry", "session_id": "forged", "user_id": "forged"}
    first = invoke(supervisor, session_id="a", metadata=metadata)
    again = invoke(supervisor, session_id="a", metadata=metadata)
    other = invoke(supervisor, session_id="b", metadata=metadata)
    assert first["status"] == again["status"] == other["status"] == "succeeded"
    assert first["trace_id"] == again["trace_id"] != other["trace_id"]
    run = store.get_run(first["trace_id"])
    expected = hashlib.sha256(json.dumps(["agent-key-v1", "a", "retry"],
                                         separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert run["idempotency_key"] == expected  # exactly one hash through both resolvers
    assert run["session_id"] == "a" and run["user_id"] is None
    assert "session_id" not in run["metadata"] and "user_id" not in run["metadata"]
    assert "idempotency_key" not in run["metadata"]
    assert metadata["session_id"] == "forged"  # caller input remains untouched
    assert tools["property_calculator"].calls == ["CCO", "CCO"]


def test_key_encoding_is_unambiguous():
    key = SupervisorAgent._internal_idempotency_key
    assert key("c", session_id="a\0b") != key("b\0c", session_id="a")


@pytest.mark.parametrize("key", ["", "   ", False, 17, [], {}, "x" * 257, " " * 257 + "x"])
def test_invalid_key_does_not_write_or_execute(runtime, key):
    supervisor, store, tools = runtime
    before = snapshot(store)
    result = invoke(supervisor, session_id="a", metadata={"idempotency_key": key})
    assert result["status"] == "failed"
    assert result["result"]["error"]["details"]["reason"] == "invalid_idempotency_key"
    assert snapshot(store) == before
    assert tools["property_calculator"].calls == []


@pytest.mark.parametrize("change", [{"session_id": "b"}, {"session_id": None},
                                    {"user_id": "other"}, {"query": "CCC"},
                                    {"active_skill": "admet_assessment"}])
@pytest.mark.parametrize("with_key", [False, True])
def test_direct_orchestrator_lookup_checks_owner_and_request(tmp_path, change, with_key):
    store = SQLiteAgentStateStore(tmp_path / "direct.sqlite")
    key = "internal-key" if with_key else None
    context = AgentContext("CCO", "original", session_id="a", active_skill=SKILL)
    store.start_run({"trace_id": context.trace_id, "status": "succeeded", "query": "CCO",
                     "skill_name": SKILL, "session_id": "a", "idempotency_key": key})
    before = snapshot(store)
    orchestrator = WorkflowOrchestrator(state_store=store)
    with pytest.raises(RunClaimConflict):
        orchestrator._resolve_idempotent_context(replace(context, **change), key)
    assert snapshot(store) == before


@pytest.mark.parametrize("entry", ["run", "execute", "dynamic"])
def test_missing_atomic_capability_fails_closed(runtime, entry):
    supervisor, store, tools = runtime
    store.claim_workflow_run = None
    before = snapshot(store)
    events = []
    if entry == "run":
        assert_denied(invoke(supervisor, session_id="a"))
    elif entry == "execute":
        result = supervisor.execute("CCO", active_skill=SKILL, session_id="a",
                                    event_callback=events.append)
        assert result["success"] is False
        assert result["agent_events"] == []
    else:
        session = WorkflowRunSession(supervisor.orchestrator,
                                     AgentContext("CCO", "dynamic", session_id="a"),
                                     [], {}, dynamic=True)
        with pytest.raises(RunClaimConflict):
            session.start()
    assert events == []
    assert snapshot(store) == before
    assert tools["property_calculator"].calls == []


def test_execute_populates_context_owner(runtime):
    supervisor, store, tools = runtime
    result = supervisor.execute("CCO", active_skill=SKILL, session_id="trusted")
    assert result["success"] is True
    run = store.get_run(result["trace_id"])
    assert run["session_id"] == "trusted" and run["user_id"] is None
    assert "session_id" not in run["metadata"]


@pytest.mark.parametrize("field,value", [("session_id", "b"), ("user_id", "b"),
                                        ("query", "CCC"), ("skill_name", "other"),
                                        ("idempotency_key", "other")])
def test_atomic_claim_compares_identity_without_writes(tmp_path, field, value):
    store = SQLiteAgentStateStore(tmp_path / "cas.sqlite")
    run = {"trace_id": "same", "query": "CCO", "skill_name": SKILL,
           "session_id": "a", "user_id": "u", "idempotency_key": "key",
           "status": "succeeded", "metadata": {"unchanged": True}}
    store.start_run(run)
    before = snapshot(store)
    assert not store.claim_workflow_run({**run, field: value, "metadata": {}},
                                        expected_status="succeeded")
    assert snapshot(store) == before


@pytest.mark.parametrize("shared_key", [False, True])
def test_concurrent_atomic_claims_have_one_winner(tmp_path, shared_key):
    path = tmp_path / "race.sqlite"
    stores = [SQLiteAgentStateStore(path) for _ in range(4)]
    barrier = Barrier(4)
    def claim(index):
        barrier.wait(timeout=10)
        return stores[index].claim_workflow_run(
            {"trace_id": str(index) if shared_key else "same", "query": "CCO",
             "session_id": "a", "skill_name": SKILL, "idempotency_key": "key"},
            expected_status=None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sorted(pool.map(claim, range(4))) == [False, False, False, True]


def test_local_session_cannot_overwrite_owner_created_after_lookup(tmp_path):
    class RacingStore(SQLiteAgentStateStore):
        def start_run(self, run, **kwargs):
            super().start_run({**run, "session_id": "browser", "status": "succeeded"})
            self.before = snapshot(self)
            return super().start_run(run, **kwargs)
    store = RacingStore(tmp_path / "race-local.sqlite")
    session = WorkflowOrchestrator(state_store=store).create_session(
        AgentContext("CCO", "racing-local"), [], {})
    with pytest.raises(RunClaimConflict):
        session.start()
    assert snapshot(store) == store.before


def test_legacy_raw_upsert_remains_replacement(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "legacy.sqlite")
    store.start_run({"trace_id": "same", "session_id": "a", "query": "old"})
    store.start_run({"trace_id": "same", "query": "new"}, exclusive=False)
    assert store.get_run("same")["session_id"] is None
    assert store.get_run("same")["query"] == "new"


@pytest.mark.parametrize("entry", ["run", "execute", "session"])
def test_protected_run_without_store_fails_closed(runtime, entry):
    supervisor, _, tools = runtime
    supervisor.state_store = None
    supervisor.orchestrator.state_store = None
    if entry == "run":
        assert_denied(invoke(supervisor, session_id="a"))
    elif entry == "execute":
        result = supervisor.execute("CCO", active_skill=SKILL, session_id="a")
        assert result["success"] is False and result["agent_events"] == []
    else:
        session = supervisor.orchestrator.create_session(
            AgentContext("CCO", "missing-store", session_id="a"), [], {})
        with pytest.raises(RunClaimConflict):
            session.start()
    assert tools["property_calculator"].calls == []


@pytest.mark.parametrize("answer", [None, False, 1, "uncommitted"])
def test_atomic_claim_requires_literal_committed_true(runtime, answer):
    supervisor, store, tools = runtime
    store.claim_workflow_run = lambda *args, **kwargs: answer
    before = snapshot(store)
    assert_denied(invoke(supervisor, session_id="a"))
    assert snapshot(store) == before
    assert tools["property_calculator"].calls == []


@pytest.mark.parametrize("entry", ["executor", "harness"])
@pytest.mark.parametrize("invalid_plan", [False, True])
def test_raw_context_conflicting_trace_key_never_updates_existing_run(runtime, entry, invalid_plan):
    supervisor, store, tools = runtime
    first = invoke(supervisor, trace_id="original", session_id="a",
                   metadata={"idempotency_key": "retry"})
    key = store.get_run(first["trace_id"])["idempotency_key"]
    store.start_run({"trace_id": "foreign", "session_id": "b", "status": "succeeded",
                     "query": "CCO", "skill_name": SKILL})
    before = snapshot(store)
    events = []
    executor = (DelegatedWorkflowExecutor(supervisor) if supervisor.specialists else
                WorkflowExecutor(planner=supervisor.planner, orchestrator=supervisor.orchestrator))
    plan = (WorkflowPlan(SKILL, [WorkflowStep("invalid", "not_allowed")])
            if invalid_plan else None)
    kwargs = dict(context=AgentContext("CCO", "foreign", session_id="a", active_skill=SKILL),
                  policy=supervisor.catalog.get(SKILL), all_tools=tools,
                  idempotency_key=key, event_callback=events.append, plan=plan)
    if supervisor.tool_registry:
        kwargs["all_tools"] = supervisor.tool_registry.as_mapping()
    if entry == "harness":
        supervisor.harness_factory = HarnessFactory(mode="shadow", langgraph_available=lambda: True)
        execution = supervisor._execute_with_harness(executor, **kwargs)
    else:
        execution = executor.execute(**kwargs)
    assert execution.result.success is False
    assert execution.events == events == []
    assert snapshot(store) == before
    assert tools["property_calculator"].calls == ["CCO"]


@pytest.mark.parametrize("owner", ["a", "b", None])
def test_plan_failure_on_existing_trace_has_no_harness_metadata_writes(runtime, owner):
    supervisor, store, _ = runtime
    invoke(supervisor, trace_id="original", session_id="a")
    before = snapshot(store)
    class InvalidPlanner:
        def plan(self, context):
            return WorkflowPlan(SKILL, [WorkflowStep("invalid", "not_allowed")])
    supervisor.planner = InvalidPlanner()
    supervisor.harness_factory = HarnessFactory(mode="shadow", langgraph_available=lambda: True)
    result = invoke(supervisor, trace_id="original", session_id=owner)
    assert result["status"] == "failed"
    assert snapshot(store) == before


@pytest.mark.parametrize("status", ["running", "cancelled", "rejected", "waiting_for_input", "unknown"])
def test_atomic_claim_rejects_non_restartable_status(tmp_path, status):
    store = SQLiteAgentStateStore(tmp_path / "status.sqlite")
    run = {"trace_id": "same", "status": status, "query": "CCO", "session_id": "a"}
    store.start_run(run)
    before = snapshot(store)
    assert store.claim_workflow_run(run, expected_status=status) is False
    assert snapshot(store) == before


@pytest.mark.parametrize("status", ["succeeded", "partial", "failed"])
def test_dynamic_new_session_never_restarts_existing_trace(tmp_path, status):
    store = SQLiteAgentStateStore(tmp_path / "dynamic-existing.sqlite")
    store.start_run({"trace_id": "same", "status": status, "query": "CCO", "session_id": "a"})
    before = snapshot(store)
    session = WorkflowRunSession(WorkflowOrchestrator(state_store=store),
                                 AgentContext("CCO", "same", session_id="a"), [], {}, dynamic=True)
    with pytest.raises((RunClaimConflict, sqlite3.IntegrityError)):
        session.start()
    assert snapshot(store) == before


def claimed_continuation(store):
    """Use the independent continuation CAS, not a fixed-workflow restart."""
    store.start_run({"trace_id": "clarification", "query": "original question",
                     "session_id": "session", "user_id": "user", "status": "partial"})
    waiting = {"schema": 1, "id": "continuation", "configuration": "a" * 64,
               "checksum": "b" * 64, "snapshot": {"results": []}}
    assert store.transition_decision_continuation(
        "clarification", user_id="user", session_id="session", expected=None,
        replacement=waiting, claim=False)
    assert store.transition_decision_continuation(
        "clarification", user_id="user", session_id="session", expected=waiting,
        replacement={**waiting, "claimed_by": "single-use-claim"}, claim=True)
    return AgentContext("clarified question", "clarification", session_id="session", user_id="user")


def test_claimed_dynamic_continuation_accepts_clarified_query_without_workflow_reclaim(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "clarification.sqlite")
    context = claimed_continuation(store)
    original = store.get_run(context.trace_id)
    store.claim_workflow_run = None  # continuation has already consumed its own CAS
    session = WorkflowRunSession(WorkflowOrchestrator(state_store=store), context, [], {}, dynamic=True)
    session.start(resume_claimed=True)
    assert session.started and session.context.query == "clarified question"
    assert store.get_run(context.trace_id) == original


@pytest.mark.parametrize("change", [{"user_id": "other"}, {"session_id": "other"},
                                    {"active_skill": "other"}, {"trace_id": "missing"}])
def test_claimed_dynamic_continuation_still_requires_record_identity(tmp_path, change):
    store = SQLiteAgentStateStore(tmp_path / "clarification-owner.sqlite")
    context = claimed_continuation(store)
    before = snapshot(store)
    session = WorkflowRunSession(WorkflowOrchestrator(state_store=store),
                                 replace(context, **change), [], {}, dynamic=True)
    with pytest.raises(RunClaimConflict):
        session.start(resume_claimed=True)
    assert snapshot(store) == before


def test_local_legacy_idempotent_retry_can_rebind_unused_explicit_trace(runtime):
    supervisor, store, tools = runtime
    first = invoke(supervisor, trace_id="original", metadata={"idempotency_key": "local-retry"})
    again = invoke(supervisor, trace_id="unused-duplicate", metadata={"idempotency_key": "local-retry"})
    assert first["status"] == again["status"] == "succeeded"
    assert again["trace_id"] == first["trace_id"] == "original"
    assert store.get_run("unused-duplicate") is None
    assert tools["property_calculator"].calls == ["CCO"]


def test_local_dynamic_exclusive_start_preserves_integrity_error(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "local-exclusive.sqlite")
    context = AgentContext("CCO", "local")
    orchestrator = WorkflowOrchestrator(state_store=store)
    WorkflowRunSession(orchestrator, context, [], {}, dynamic=True).start()
    before = snapshot(store)
    with pytest.raises(sqlite3.IntegrityError):
        WorkflowRunSession(orchestrator, context, [], {}, dynamic=True).start()
    assert snapshot(store) == before
