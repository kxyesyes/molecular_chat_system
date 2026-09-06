from __future__ import annotations

from src.agent.contracts import (
    AgentContext,
    CandidateRecord,
    CandidateSet,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
)
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
            },
            provenance=ToolProvenance(
                tool_name="different-tool",
                model_name="gmm-llama:latest",
                input_digest="forged-input",
            ),
        )


class MetadataRejectingCandidateGenerator:
    name = "llm_molecular_generator"

    def __init__(self):
        self.calls = []

    def execute(self, query):
        self.calls.append(query)
        return ToolResult.success_result(
            self.name,
            data=[
                {"smiles": "CCO", "tags": {"not", "json"}},
                {"smiles": "CCN"},
            ],
            quality={"requested_count": 1},
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
    context = AgentContext(
        query="generate",
        trace_id="candidate-resume",
        mol_count=3,
    )
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
    assert generator.calls == [
        {
            "query": "generate",
            "metadata": {"requested_count": 3},
            "outputs": {},
        }
    ]
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
    assert resumed_result.provenance.model_name == "gmm-llama:latest"
    assert candidate_provenance["tool_name"] == "llm_molecular_generator"
    assert (
        candidate_provenance["input_digest"]
        == resumed_result.provenance.input_digest
    )
    assert resumed_result.provenance.input_digest != "forged-input"
    evidence = resumed.metadata["evidence_ledger"][0]
    assert evidence["tool_name"] == evidence["provenance"]["tool_name"]
    assert evidence["input_digest"] == evidence["provenance"]["input_digest"]
    assert "OCC" not in resumed.final_answer


def test_checkpoint_result_restores_status_and_provenance():
    provenance = ToolProvenance(
        tool_name="llm_molecular_generator",
        model_name="gmm-llama:latest",
        input_digest="stored-input",
    )
    checkpoint = {
        "output": {
            "data": CandidateSet(
                requested_count=2,
                candidates=(
                    CandidateRecord.from_smiles(
                        candidate_index=1,
                        source_index=1,
                        original_smiles="CCO",
                        canonical_smiles="CCO",
                    ),
                ),
                status=ObservationStatus.PARTIAL,
            ).to_dict(),
            "message": "stored",
            "status": "partial",
            "quality": {},
            "provenance": provenance.to_dict(),
        }
    }

    restored = WorkflowOrchestrator._result_from_checkpoint(
        "llm_molecular_generator",
        checkpoint,
    )

    assert restored.status == ObservationStatus.PARTIAL
    assert restored.provenance == provenance


def test_validated_checkpoint_trusts_invalid_candidate_metadata_rejection(
    tmp_path,
):
    store = SQLiteAgentStateStore(tmp_path / "trusted-candidate.sqlite3")
    generator = MetadataRejectingCandidateGenerator()
    context = AgentContext(
        query="generate",
        trace_id="trusted-candidate",
        mol_count=1,
    )
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
        {generator.name: generator},
    )
    resumed = WorkflowOrchestrator(state_store=store).run(
        context,
        steps,
        {generator.name: generator},
    )

    assert first.success is True
    assert resumed.success is True
    assert generator.calls == [
        {
            "query": "generate",
            "metadata": {"requested_count": 1},
            "outputs": {},
        }
    ]
    assert resumed.metadata["reused_steps"] == ["generate"]
    assert resumed.tool_results[0].data["rejected"] == [
        {
            "source_index": 1,
            "smiles": "CCO",
            "reason": "invalid_candidate_metadata",
        }
    ]


def test_corrupted_checkpoint_is_cache_miss_and_reexecutes_tool(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "corrupted-checkpoint.sqlite3")
    tool = CountingTool("properties", {"qed": 0.4})
    context = AgentContext(query="CCO", trace_id="corrupted-checkpoint")
    step = WorkflowStep("properties", "properties", output_key="properties")
    orchestrator = WorkflowOrchestrator(state_store=store)
    input_hash = orchestrator._input_hash("CCO")
    store.save_checkpoint(
        {
            "trace_id": context.trace_id,
            "step_id": step.name,
            "workflow_version": orchestrator.workflow_version,
            "status": "succeeded",
            "input_hash": input_hash,
            "tool_name": step.tool_name,
            "tool_version": "1",
            "adapter_version": orchestrator.adapter_version,
            "model_version": "",
            "output": {"status": "corrupted-status"},
            "error": None,
            "metadata": {},
        }
    )

    result = orchestrator.run(
        context,
        [step],
        {tool.name: tool},
    )

    assert result.success is True
    assert tool.calls == ["CCO"]
    assert result.metadata["reused_steps"] == []
    assert result.metadata["checkpoint_warnings"] == [
        {
            "step": "properties",
            "reason": "checkpoint_deserialization_failed",
        }
    ]
    assert "Ignored incompatible checkpoint for properties" in result.warnings
    assert store.latest_checkpoint(context.trace_id, step.name)["status"] == (
        "succeeded"
    )


def test_corrupted_candidate_set_checkpoint_reexecutes_generator(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "corrupted-candidates.sqlite3")
    generator = CountingCandidateGenerator()
    context = AgentContext(
        query="generate",
        trace_id="corrupted-candidates",
        mol_count=3,
    )
    step = WorkflowStep(
        "generate",
        generator.name,
        output_key="molecules",
    )
    orchestrator = WorkflowOrchestrator(state_store=store)
    canonical_request = {
        "query": "generate",
        "metadata": {"requested_count": 3},
        "outputs": {},
    }
    store.save_checkpoint(
        {
            "trace_id": context.trace_id,
            "step_id": step.name,
            "workflow_version": orchestrator.workflow_version,
            "status": "succeeded",
            "input_hash": orchestrator._input_hash(canonical_request),
            "tool_name": step.tool_name,
            "tool_version": "1",
            "adapter_version": orchestrator.adapter_version,
            "model_version": "",
            "output": {
                "status": "succeeded",
                "quality": {"output_contract": "CandidateSet@1"},
                "data": {
                    "version": "1",
                    "requested_count": 1,
                    "candidates": [],
                    "status": "succeeded",
                },
            },
            "error": None,
            "metadata": {},
        }
    )

    result = orchestrator.run(
        context,
        [step],
        {generator.name: generator},
    )

    assert result.partial is True
    assert generator.calls == [canonical_request]
    assert result.metadata["reused_steps"] == []
    assert result.metadata["checkpoint_warnings"] == [
        {
            "step": "generate",
            "reason": "checkpoint_deserialization_failed",
        }
    ]
