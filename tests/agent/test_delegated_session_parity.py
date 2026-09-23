"""T07 delegated/session regressions; all scientific outputs are synthetic fixtures."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
from threading import Barrier

import pytest

from src.agent.contracts import AgentContext, ToolResult, WorkflowArtifact
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore
from src.agent.planning import WorkflowPlan
from src.agent.runtime import PreparedWorkflow
from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent, _DelegatedWorkflowExecutor
from src.agent.tooling import build_tool_registry


class FixtureTool:
    def __init__(self, name, data=None, barrier=None):
        self.name = name
        self.data = data
        self.barrier = barrier
        self.calls = []

    def execute(self, query):
        self.calls.append(deepcopy(query))
        if self.barrier is not None:
            self.barrier.wait(timeout=10)
        return ToolResult.success_result(
            self.name,
            data=deepcopy(self.data) if self.data is not None else {"input": query},
            evidence=[{"source": "synthetic-only", "tool": self.name}],
            artifacts=[WorkflowArtifact("report", "synthetic.json", "Fixture only")],
            quality={"requested_count": 2} if self.name == "llm_molecular_generator" else {},
        )


class FixedPlanner:
    def __init__(self, steps):
        self.steps = steps

    def plan(self, context):
        return WorkflowPlan(workflow_name=context.active_skill, steps=deepcopy(self.steps))


@pytest.fixture
def make_supervisor():
    registries = []

    def make(store, tools, steps):
        registry = build_tool_registry(tools)
        registries.append(registry)
        return SupervisorAgent(
            tools={tool.name: tool for tool in tools},
            planner=FixedPlanner(steps),
            tool_registry=registry,
            specialists=build_default_specialists(),
            state_store=store,
        )

    yield make
    for registry in registries:
        registry.close()


def run(supervisor, trace="delegated-parity", query="CCO"):
    return supervisor.run(query, skill_name="comprehensive_evaluation", trace_id=trace)


@pytest.mark.parametrize("corruption", ["invalid_status", "invalid_artifact", "invalid_json"])
def test_corrupt_checkpoint_is_recomputed_and_reported(tmp_path, make_supervisor, corruption):
    path = tmp_path / "checkpoint.sqlite"
    store = SQLiteAgentStateStore(path)
    tool = FixtureTool("property_calculator")
    steps = [WorkflowStep("properties", tool.name, input_data="CCO", output_key="properties")]
    supervisor = make_supervisor(store, [tool], steps)
    first = run(supervisor)
    assert first["status"] == "succeeded"
    checkpoint = store.latest_checkpoint(first["trace_id"], "properties")
    output = deepcopy(checkpoint["output"])
    if corruption == "invalid_status":
        output["status"] = "corrupted-status"
    elif corruption == "invalid_artifact":
        output["artifacts"] = [{}]
    encoded = "{not-json" if corruption == "invalid_json" else json.dumps(output)
    with sqlite3.connect(path) as connection:
        changed = connection.execute(
            "UPDATE agent_checkpoints SET output_json = ? WHERE id = ?",
            (encoded, checkpoint["id"]),
        )
        assert changed.rowcount == 1

    # Reopen the actual database rather than substituting a mocked checkpoint reader.
    resumed = run(make_supervisor(SQLiteAgentStateStore(path), [tool], steps))
    assert resumed["status"] == "succeeded"
    assert tool.calls == ["CCO", "CCO"]
    assert resumed["result"]["tool_result_sequence"][0]["data"] == {"input": "CCO"}
    assert resumed["result"]["metadata"]["checkpoint_warnings"] == [
        {"step": "properties", "reason": "checkpoint_deserialization_failed"}
    ]
    assert "Ignored incompatible checkpoint for properties" in resumed["result"]["warnings"]
    assert len(store.get_tool_executions(first["trace_id"])) == 2
    assert store.latest_checkpoint(first["trace_id"], "properties")["output"]["status"] == "succeeded"


def test_delegated_candidate_order_and_evidence_match_common_session(tmp_path, make_supervisor):
    generator = FixtureTool("llm_molecular_generator", [{"smiles": "CCO"}, {"smiles": "CCN"}])
    properties = FixtureTool("property_calculator", [
        {"smiles": "CCN", "fixture_value": 2},
        {"smiles": "CCO", "fixture_value": 1},
    ])
    steps = [
        WorkflowStep("baseline", properties.name, input_data="CCO", output_key="baseline"),
        WorkflowStep("generate", generator.name, input_binding="$.outputs.baseline", output_key="molecules"),
        WorkflowStep("properties", properties.name, input_from="molecules", output_key="properties",
                     metadata={"candidate_source": "molecules"}),
    ]
    supervisor = make_supervisor(SQLiteAgentStateStore(tmp_path / "delegated.sqlite"),
                                 [generator, properties], steps)
    delegated = supervisor.run("generate two fixtures", skill_name="hit_to_lead_optimization",
                               trace_id="candidate-parity", mol_count=2)["result"]
    plain_generator = FixtureTool(generator.name, generator.data)
    plain_properties = FixtureTool(properties.name, properties.data)
    common = WorkflowOrchestrator(state_store=SQLiteAgentStateStore(tmp_path / "common.sqlite")).run(
        AgentContext("generate two fixtures", "common-parity", active_skill="hit_to_lead_optimization",
                     metadata={"requested_count": 2}),
        deepcopy(steps), {tool.name: tool for tool in [plain_generator, plain_properties]},
    ).to_legacy_dict()
    assert delegated["status"] == common["status"] == "completed", delegated
    for actual, expected in zip(delegated["tool_result_sequence"], common["tool_result_sequence"], strict=True):
        assert actual["data"] == expected["data"]
        assert actual["evidence"] == expected["evidence"]
        assert actual["artifacts"] == expected["artifacts"]
    rows = delegated["tool_result_sequence"][2]["data"]
    assert [row["smiles"] for row in rows] == ["CCN", "CCO"]
    assert len({row["candidate_id"] for row in rows}) == 2
    assert delegated["evidence"] == common["evidence"]
    assert delegated["artifacts"] == common["artifacts"]
    assert generator.calls == plain_generator.calls
    assert properties.calls == plain_properties.calls


def test_concurrent_delegated_requests_do_not_share_returned_events(tmp_path, make_supervisor):
    store = SQLiteAgentStateStore(tmp_path / "concurrent.sqlite")
    barrier = Barrier(2)
    tool = FixtureTool("property_calculator", barrier=barrier)
    other = FixtureTool("drug_likeness_assessment", barrier=barrier)
    supervisor = make_supervisor(store, [tool, other], [])

    class RequestPlanner:
        def plan(self, context):
            name = tool.name if context.query == "CCO" else other.name
            return WorkflowPlan(workflow_name=context.active_skill,
                                steps=[WorkflowStep("properties", name)])

    # Separate registered adapters avoid deliberately tripping per-tool concurrency limits.
    supervisor.planner = RequestPlanner()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, supervisor, trace, query)
                   for trace, query in [("request-a", "CCO"), ("request-b", "CCN")]]
        responses = [future.result(timeout=20) for future in futures]
    assert tool.calls == ["CCO"]
    assert other.calls == ["CCN"]
    for response, query in zip(responses, ["CCO", "CCN"], strict=True):
        trace = response["trace_id"]
        assert response["status"] == "succeeded"
        assert response["result"]["tool_result_sequence"][0]["data"] == {"input": query}
        events = response["agent_events"]
        assert events
        assert {event["trace_id"] for event in events} == {trace}
        for event_type in ["task_started", "tool_started", "tool_completed", "task_completed"]:
            assert sum(event["event"] == event_type for event in events) == 1
        persisted = store.get_events(trace)
        assert [event["event"] for event in persisted] == [event["event"] for event in events]
        assert len(store.get_tool_executions(trace)) == 1
        assert store.get_run(trace)["status"] == "succeeded"


@pytest.mark.parametrize("after_commit", [False, True])
def test_prepared_delegated_session_retries_persistence_without_recalling_tool(
    tmp_path, make_supervisor, after_commit,
):
    class FailOnceStore(SQLiteAgentStateStore):
        failed = False

        def record_tool_execution(self, execution):
            if not self.failed:
                self.failed = True
                if after_commit:
                    super().record_tool_execution(execution)
                raise RuntimeError("synthetic persistence failure")
            return super().record_tool_execution(execution)

    store = FailOnceStore(tmp_path / "retry.sqlite")
    tool = FixtureTool("property_calculator")
    supervisor = make_supervisor(store, [tool], [WorkflowStep("properties", tool.name, input_data="CCO")])
    executor = _DelegatedWorkflowExecutor(supervisor)
    # Deliberately fail on the old loop: T07 must expose the common prepared-session boundary.
    assert callable(getattr(executor, "prepare", None)), "delegated executor must expose prepare()"
    prepared = executor.prepare(
        context=AgentContext("CCO", "retry-parity", active_skill="comprehensive_evaluation"),
        policy=supervisor.catalog.get("comprehensive_evaluation"),
        all_tools=supervisor.tool_registry.as_mapping(),
    )
    assert isinstance(prepared, PreparedWorkflow)
    session = prepared.create_session()
    session.start()
    with pytest.raises(RuntimeError, match="synthetic persistence failure"):
        session.execute_step(0)
    assert store.failed
    assert tool.calls == ["CCO"]
    session.execute_step(0)
    result = session.finish()
    assert result.success
    assert tool.calls == ["CCO"]
    assert session.tool_attempt_count == 1
    assert len(result.tool_results) == 1
    assert len(store.get_tool_executions(result.trace_id)) == 1
    assert store.get_run(result.trace_id)["status"] == "succeeded"
    events = store.get_events(result.trace_id)
    assert sum(event["event"] == "tool_completed" for event in events) == 1
    assert sum(event["event"] == "task_completed" for event in events) == 1
