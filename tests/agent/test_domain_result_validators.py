from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.validators import AgentResultValidator


def test_docking_validator_rejects_energy_without_execution_evidence():
    result = ToolResult.success_result(
        "molecular_docking",
        data={
            "total_poses": 1,
            "best_pose": {"binding_energy": -7.2},
        },
        formatted="Binding energy: -7.2 kcal/mol",
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.error.code == AgentErrorCode.INVALID_OUTPUT
    assert "docking evidence" in validated.message.lower()


def test_activity_validator_rejects_pic50_without_real_model_provenance():
    result = ToolResult.success_result(
        "activity_predictor",
        data=[
            {
                "smiles": "CCO",
                "success": True,
                "activity_score": 6.1,
            }
        ],
        formatted="pIC50: 6.1",
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.error.code == AgentErrorCode.INVALID_OUTPUT
    assert "model provenance" in validated.message.lower()


def test_activity_validator_accepts_real_model_provenance():
    result = ToolResult.success_result(
        "activity_predictor",
        data=[
            {
                "smiles": "CCO",
                "success": True,
                "activity_score": 6.1,
            }
        ],
        quality={
            "model_provenance": {
                "model_path": "data/activity/models/model.pt",
                "demo_mode": False,
            }
        },
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is True


def test_admet_validator_requires_prediction_method():
    result = ToolResult.success_result(
        "admet_predictor",
        data=[{"smiles": "CCO", "admet": {"toxicity": 0.2}}],
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.error.code == AgentErrorCode.INVALID_OUTPUT


def test_target_validator_rejects_names_without_evidence():
    result = ToolResult.success_result(
        "reverse_target_predictor",
        data=[{"target_name": "EGFR"}],
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.error.code == AgentErrorCode.INVALID_OUTPUT
