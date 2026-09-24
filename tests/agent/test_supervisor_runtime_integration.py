from __future__ import annotations

from dataclasses import replace

from src.agent.persistence import SQLiteAgentStateStore
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent
from src.agent.tooling import build_tool_registry, LegacyPythonToolAdapter, ToolRegistry
from src.agent.tooling.factory import LegacyQueryInput
from tests.agent.test_family_activity_tool import family_row


class FakeTool:
    def __init__(self, name, output=None):
        self.name = name
        self.output = output
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return {
            "success": True,
            "message": "ok",
            "data": self.output if self.output is not None else {"query": query},
        }


def build_tools():
    return [
        FakeTool(
            "target_database_search",
            [
                {
                    "gene_symbol": "PDE5A",
                    "source_record_id": "LOCAL_PDE5A",
                    "source": "local_target_db",
                }
            ],
        ),
        FakeTool("llm_molecular_generator", [{"smiles": s} for s in ("CCO", "CCN", "CCC")]),
        FakeTool("property_calculator", [{"smiles": s} for s in ("CCO", "CCN", "CCC")]),
        FakeTool("admet_predictor", [{"smiles": s} for s in ("CCO", "CCN", "CCC")]),
        # Explicit synthetic family observations; no model assets or inference.
        FakeTool("activity_predictor", [family_row(smiles=s, requested_target="PDE5")
                                        for s in ("CCO", "CCN", "CCC")]),
        FakeTool("candidate_ranker"),
        FakeTool("molecular_docking"),
    ]


def test_tool_registry_factory_assigns_declared_agent_owners():
    registry = build_tool_registry(build_tools())

    assert registry.resolve(
        "llm_molecular_generator", agent_name="molecular_design"
    )
    assert registry.resolve(
        "property_calculator", agent_name="property_admet"
    )


def runtime_fixture_registry(tools):
    # These persistence/idempotency tests deliberately use a query sentinel,
    # not a scientific ranking. Keep their legacy observation and downstream
    # assertions without exempting the production typed ranker or renaming it.
    typed = build_tool_registry(tools)
    registry = ToolRegistry()
    for adapter in typed.as_mapping().values():
        if adapter.spec.name == "candidate_ranker":
            adapter = LegacyPythonToolAdapter(
                replace(adapter.spec, input_schema=LegacyQueryInput, output_schema=None),
                adapter.tool,
            )
        registry.register(adapter)
    return registry


def test_delegated_supervisor_persists_run_events_and_tool_results(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    event_bus = AgentEventBus(state_store=store)
    registry = runtime_fixture_registry(build_tools())
    supervisor = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
        state_store=store,
        event_bus=event_bus,
    )

    result = supervisor.run(
        "针对 PDE5 设计 3 个候选分子并评估 docking",
        skill_name="target_driven_design",
        trace_id="runtime-trace",
        metadata={"idempotency_key": "runtime-request"},
    )

    assert result["status"] == "succeeded"
    assert store.get_run("runtime-trace")["status"] == "succeeded"
    assert len(store.get_tool_executions("runtime-trace")) == 6
    event_names = [item["event"] for item in store.get_events("runtime-trace")]
    assert event_names[:3] == [
        "task_started",
        "planning_started",
        "planning_completed",
    ]
    assert event_names[-1] == "task_completed"


def test_delegated_supervisor_reuses_idempotent_completed_steps(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    tools = build_tools()
    registry = runtime_fixture_registry(tools)

    first = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
        state_store=store,
    ).run(
        "针对 PDE5 设计 3 个候选分子并评估 docking",
        skill_name="target_driven_design",
        trace_id="original-trace",
        metadata={"idempotency_key": "same-request"},
    )
    duplicate = SupervisorAgent(
        tool_registry=registry,
        specialists=build_default_specialists(),
        state_store=store,
    ).run(
        "针对 PDE5 设计 3 个候选分子并评估 docking",
        skill_name="target_driven_design",
        trace_id="duplicate-trace",
        metadata={"idempotency_key": "same-request"},
    )

    assert first["status"] == "succeeded"
    assert duplicate["trace_id"] == "original-trace"
    assert all(
        len(tool.calls) == (0 if tool.name == "molecular_docking" else 1)
        for tool in tools
    )
    assert all(item["reused"] is True for item in duplicate["delegations"])
