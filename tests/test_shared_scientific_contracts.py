from pathlib import Path


def test_scientific_transport_types_are_owned_by_system_and_reexported_by_agent():
    from src.agent.contracts import (
        AgentErrorCode,
        AgentExecutionError,
        ToolProvenance,
        ToolResult,
        WorkflowArtifact,
    )
    from src.system.scientific_contracts import (
        AgentErrorCode as SystemAgentErrorCode,
        AgentExecutionError as SystemAgentExecutionError,
        ToolProvenance as SystemToolProvenance,
        ToolResult as SystemToolResult,
        WorkflowArtifact as SystemWorkflowArtifact,
    )

    assert AgentErrorCode is SystemAgentErrorCode
    assert AgentExecutionError is SystemAgentExecutionError
    assert ToolProvenance is SystemToolProvenance
    assert ToolResult is SystemToolResult
    assert WorkflowArtifact is SystemWorkflowArtifact


def test_docking_runtime_imports_shared_transport_types_without_agent_contract_facade():
    root = Path(__file__).parents[1]
    for relative_path in (
        "src/docking/sandbox_runner.py",
        "src/task_runtime/docking_execution.py",
    ):
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "from src.agent.contracts import" not in source
