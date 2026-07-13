from __future__ import annotations

from src.agent.contracts import AgentContext
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence import SQLiteAgentStateStore


class CountingTool:
    def __init__(self, name: str, output):
        self.name = name
        self.output = output
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return {
            "success": True,
            "message": f"{self.name} ok",
            "data": self.output,
            "formatted": f"{self.name}: {query}",
        }


def test_workflow_resumes_completed_compatible_steps_after_restart(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    generator = CountingTool("generator", [{"smiles": "CCO"}])
    properties = CountingTool("properties", {"molecular_weight": 46.07})
    context = AgentContext(
        query="design",
        trace_id="resume-trace",
        active_skill="target_driven_design",
    )
    first = WorkflowOrchestrator(state_store=store, workflow_version="1")
    first.run(
        context,
        [WorkflowStep("generate", "generator", output_key="molecules")],
        {"generator": generator},
    )

    restarted = WorkflowOrchestrator(state_store=store, workflow_version="1")
    result = restarted.run(
        context,
        [
            WorkflowStep("generate", "generator", output_key="molecules"),
            WorkflowStep(
                "properties",
                "properties",
                input_from="molecules",
                output_key="properties",
            ),
        ],
        {"generator": generator, "properties": properties},
    )

    assert result.success is True
    assert generator.calls == ["design"]
    assert properties.calls == ["CCO"]
    assert result.metadata["reused_steps"] == ["generate"]


def test_changed_input_invalidates_checkpoint(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    tool = CountingTool("properties", {"qed": 0.4})
    orchestrator = WorkflowOrchestrator(state_store=store)

    orchestrator.run(
        AgentContext(query="CCO", trace_id="same-trace"),
        [WorkflowStep("properties", "properties")],
        {"properties": tool},
    )
    orchestrator.run(
        AgentContext(query="CCN", trace_id="same-trace"),
        [WorkflowStep("properties", "properties")],
        {"properties": tool},
    )

    assert tool.calls == ["CCO", "CCN"]


def test_idempotency_key_reuses_existing_run_without_duplicate_tool_call(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    tool = CountingTool("properties", {"qed": 0.4})
    steps = [WorkflowStep("properties", "properties")]

    first = WorkflowOrchestrator(state_store=store).run(
        AgentContext(query="CCO", trace_id="trace-original"),
        steps,
        {"properties": tool},
        idempotency_key="request-key",
    )
    duplicate = WorkflowOrchestrator(state_store=store).run(
        AgentContext(query="CCO", trace_id="trace-duplicate"),
        steps,
        {"properties": tool},
        idempotency_key="request-key",
    )

    assert first.success is True
    assert duplicate.success is True
    assert duplicate.trace_id == "trace-original"
    assert tool.calls == ["CCO"]


def test_checkpoint_resume_prefers_latest_write_when_timestamps_tie(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "src.agent.persistence.sqlite_store.time.time",
        lambda: 1_700_000_000.0,
    )
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    tool = CountingTool("properties", {"qed": 0.4})
    steps = [WorkflowStep("properties", "properties")]
    context = AgentContext(query="CCO", trace_id="timestamp-tie")

    WorkflowOrchestrator(state_store=store).run(
        context,
        steps,
        {"properties": tool},
    )
    resumed = WorkflowOrchestrator(state_store=store).run(
        context,
        steps,
        {"properties": tool},
    )

    assert resumed.success is True
    assert resumed.metadata["reused_steps"] == ["properties"]
    assert tool.calls == ["CCO"]


def test_tool_execution_and_terminal_run_status_are_persisted(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    tool = CountingTool("properties", {"qed": 0.4})

    result = WorkflowOrchestrator(state_store=store).run(
        AgentContext(query="CCO", trace_id="trace-status"),
        [WorkflowStep("properties", "properties")],
        {"properties": tool},
    )

    assert result.success is True
    assert store.get_run("trace-status")["status"] == "succeeded"
    execution = store.get_tool_executions("trace-status")[0]
    assert execution["status"] == "succeeded"
    assert execution["input"] == "CCO"
