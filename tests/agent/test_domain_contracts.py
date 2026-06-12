from src.agent.contracts import AgentContext, ToolResult
from src.agent.contracts.domain import MoleculeCandidate, WorkflowArtifact


def test_molecule_candidate_serializes_for_frontend():
    candidate = MoleculeCandidate(
        smiles="CCO",
        source="generated",
        properties={"qed": 0.42},
        warnings=["demo warning"],
    )

    payload = candidate.to_dict()

    assert payload["smiles"] == "CCO"
    assert payload["source"] == "generated"
    assert payload["properties"]["qed"] == 0.42
    assert payload["warnings"] == ["demo warning"]


def test_tool_result_preserves_warnings_evidence_and_artifacts():
    artifact = WorkflowArtifact(
        artifact_type="csv",
        path="outputs/agent/demo.csv",
        label="candidate table",
    )

    result = ToolResult.success_result(
        tool_name="molecular_design",
        data={"count": 1},
        message="generation completed",
        warnings=["1 candidate was filtered"],
        evidence=[{"source": "rdkit", "message": "SMILES valid"}],
        artifacts=[artifact],
        quality={"confidence": 0.8, "validated": True},
    )

    legacy = result.to_legacy_dict()

    assert legacy["warnings"] == ["1 candidate was filtered"]
    assert legacy["evidence"][0]["source"] == "rdkit"
    assert legacy["artifacts"][0]["path"] == "outputs/agent/demo.csv"
    assert legacy["quality"]["validated"] is True


def test_agent_context_carries_session_and_workflow_metadata():
    context = AgentContext(
        query="design candidates for PDE5",
        trace_id="trace-1",
        session_id="session-1",
        workflow_name="target_driven_design",
        metadata={"target": "PDE5"},
    )

    assert context.session_id == "session-1"
    assert context.workflow_name == "target_driven_design"
    assert context.metadata["target"] == "PDE5"
