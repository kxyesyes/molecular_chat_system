from __future__ import annotations

from src.agent.contracts import AgentContext, ToolResult
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


class CountingCandidateGenerator:
    name = "llm_molecular_generator"

    def __init__(self):
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return ToolResult.success_result(
            self.name,
            data=[
                {"smiles": "CCO"},
                {"smiles": "OCC"},
                {"smiles": "CCN"},
            ],
            formatted="unsafe raw output OCC",
            warnings=[
                "Only generated 2 valid unique SMILES out of requested 3."
            ],
            quality={
                "requested_count": 3,
                "model": "gmm-llama:latest",
            },
        )


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


def test_generator_candidate_set_survives_checkpoint_reuse(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "candidate-state.sqlite3")
    generator = CountingCandidateGenerator()
    context = AgentContext(query="generate", trace_id="candidate-resume")
    steps = [
        WorkflowStep(
            "generate",
            "llm_molecular_generator",
            output_key="molecules",
        )
    ]

    first = WorkflowOrchestrator(state_store=store).run(
        context,
        steps,
        {"llm_molecular_generator": generator},
    )
    resumed = WorkflowOrchestrator(state_store=store).run(
        context,
        steps,
        {"llm_molecular_generator": generator},
    )

    first_result = first.tool_results[0]
    resumed_result = resumed.tool_results[0]
    assert generator.calls == ["generate"]
    assert resumed.metadata["reused_steps"] == ["generate"]
    assert resumed_result.data == first_result.data
    assert resumed_result.quality == first_result.quality
    assert resumed_result.warnings == first_result.warnings == [
        "Generated 2 valid unique SMILES out of 3 requested"
    ]
    assert [item["source_index"] for item in resumed_result.data["candidates"]] == [
        1,
        3,
    ]
    assert resumed_result.data["rejected"] == first_result.data["rejected"]
    candidate_provenance = resumed_result.data["candidates"][0][
        "generation_provenance"
    ]
    assert candidate_provenance["model_name"] == "gmm-llama:latest"
    assert (
        candidate_provenance["input_digest"]
        == resumed_result.provenance.input_digest
    )
    assert "OCC" not in resumed.final_answer
