from pathlib import Path

import pytest

from src.system.scientific_status import (
    ObservationStatus,
    RunOutcome,
    aggregate_run_outcome,
    summarize_completion,
)


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


@pytest.mark.parametrize(
    ("completed", "total", "status", "success"),
    [
        (3, 3, "completed", True),
        (1, 3, "partial", False),
        (0, 3, "failed", False),
        (0, 0, "failed", False),
    ],
)
def test_completion_summary_never_promotes_partial_or_empty_work(
    completed, total, status, success
):
    assert summarize_completion(completed, total) == (status, success)


def test_shared_status_contract_has_explicit_timeout_and_not_calculated_states():
    assert ObservationStatus.TIMEOUT.value == "timeout"
    assert ObservationStatus.NOT_CALCULATED.value == "not_calculated"
    assert RunOutcome.TIMEOUT.value == "timeout"
    assert RunOutcome.NOT_CALCULATED.value == "not_calculated"


@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        (ObservationStatus.TIMEOUT, RunOutcome.TIMEOUT),
        (ObservationStatus.UNAVAILABLE, RunOutcome.UNAVAILABLE),
        (ObservationStatus.NOT_CALCULATED, RunOutcome.NOT_CALCULATED),
    ],
)
def test_aggregate_status_preserves_scientific_unavailability_reason(status, outcome):
    assert aggregate_run_outcome([status], all_succeeded=False, any_usable=False) is outcome


def test_legacy_timeout_alias_normalizes_to_canonical_timeout_status():
    from src.agent.tools.base_tool import execute_tool_compat
    from src.agent.contracts import AgentErrorCode

    class TimedTool:
        name = "timed_tool"

        def execute(self, _query):
            return {
                "success": False,
                "status": "timed_out",
                "error": {"code": "tool_timeout", "message": "timed out"},
            }

    result = execute_tool_compat(TimedTool(), "CCO")
    assert result.status is ObservationStatus.TIMEOUT
    assert result.error.code is AgentErrorCode.TOOL_TIMEOUT


def test_docking_runtime_imports_shared_transport_types_without_agent_contract_facade():
    root = Path(__file__).parents[1]
    for relative_path in (
        "src/docking/sandbox_runner.py",
        "src/task_runtime/docking_execution.py",
    ):
        source = (root / relative_path).read_text(encoding="utf-8")
        assert "from src.agent.contracts import" not in source


def test_docking_runtime_uses_docking_owned_result_validator():
    root = Path(__file__).parents[1]
    source = (root / "src" / "task_runtime" / "docking_execution.py").read_text(
        encoding="utf-8"
    )
    assert "src.agent.validators.result_validator" not in source
    assert "src.docking.result_validator" in source


def test_docking_runtime_uses_docking_owned_tool_adapter():
    root = Path(__file__).parents[1]
    source = (root / "src" / "task_runtime" / "docking_execution.py").read_text(
        encoding="utf-8"
    )
    assert "src.agent.tools.molecular_docking" not in source
    assert "src.docking.molecular_docking_adapter" in source


def test_molecular_input_validation_has_one_shared_implementation():
    from src.agent.tools.base_tool import BaseMolecularTool as AgentBaseMolecularTool
    from src.docking.molecular_docking_adapter import MolecularDocking
    from src.system.molecular_input import BaseMolecularTool

    assert AgentBaseMolecularTool is BaseMolecularTool
    assert issubclass(MolecularDocking, BaseMolecularTool)
