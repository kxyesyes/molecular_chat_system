from __future__ import annotations

import requests
from pathlib import Path

from src.activity.predictor import ActivityPredictor
from src.agent.tools import get_optional_tool
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.rxn_chemistry_agent import RXNChemistryAgent
from src.agent.tools.molecular_docking import MolecularDocking


def test_activity_predictor_without_real_weights_returns_no_simulated_score() -> None:
    predictor = ActivityPredictor()
    predictor._loaded = True
    predictor.demo_mode = True
    predictor.current_model_path = None

    result = predictor.predict("CCO")[0]

    assert result["success"] is False
    assert "activity_score" not in result
    assert "model" in result["error"].lower()


def test_rxn_authentication_failure_never_returns_mock_products(monkeypatch) -> None:
    class UnauthorizedResponse:
        status_code = 401
        text = "unauthorized"

        @staticmethod
        def json():
            return {}

    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: UnauthorizedResponse())
    result = RXNChemistryAgent(api_key="invalid").execute(
        "Predict the reaction product for CCO"
    )

    assert result["success"] is False
    assert result["data"] is None
    assert ".oxidized" not in str(result)


def test_rxn_without_api_key_fails_without_network_call(monkeypatch) -> None:
    calls = []

    class UnauthorizedResponse:
        status_code = 401
        text = "unauthorized"

        @staticmethod
        def json():
            return {}

    def record_call(*args, **kwargs):
        calls.append((args, kwargs))
        return UnauthorizedResponse()

    monkeypatch.setattr(requests, "post", record_call)
    result = RXNChemistryAgent(api_key="").execute(
        "Predict the reaction product for CCO"
    )

    assert result["success"] is False
    assert result["data"] is None
    assert calls == []


def test_rxn_retrosynthesis_network_failure_never_returns_mock_routes(
    monkeypatch,
) -> None:
    def fail(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(requests, "post", fail)
    result = RXNChemistryAgent(api_key="configured").execute(
        "Plan retrosynthesis for CCO"
    )

    assert result["success"] is False
    assert result["data"] is None
    assert "amide formation" not in str(result)


def test_rxn_literature_search_does_not_fabricate_citations() -> None:
    result = RXNChemistryAgent(api_key="configured").execute(
        "Find literature for CCO"
    )

    assert result["success"] is False
    assert result["data"] is None
    assert "10.1021/jo.example" not in str(result)


def test_rxn_tool_is_available_to_the_legacy_react_tool_pool() -> None:
    tool = get_optional_tool("RXNChemistryAgent")

    assert tool.name == "rxn_chemistry_agent"


def test_molecular_docking_source_has_no_synthetic_result_code() -> None:
    source_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent"
        / "tools"
        / "molecular_docking.py"
    )
    source = source_path.read_text(encoding="utf-8")
    forbidden = {
        "_generate_binding_mode",
        "_generate_interactions",
        "docking_score",
        "binding_affinity",
        "common_targets",
        "_identify_target",
        "_format_docking_results",
        "_generate_docking_reasoning",
    }

    assert forbidden.isdisjoint(source.split())
    for token in forbidden:
        assert token not in source


def test_molecular_docking_missing_structured_inputs_has_no_energy_claim() -> None:
    result = MolecularDocking().execute({"receptor_path": "missing.pdbqt"})

    assert result["success"] is False
    assert "kcal/mol" not in str(result)
    assert "missing" in result["message"].lower()


def test_molecular_docking_history_warning_reaches_standard_tool_result(
    tmp_path,
    monkeypatch,
) -> None:
    warning = "History persistence failed; docking output remains available."
    docking_payload = {
        "success": True,
        "job_id": "warning-job",
        "results": [],
        "best_pose": None,
        "pose_file": str(tmp_path / "result.pdbqt"),
        "total_poses": 0,
        "warnings": [warning],
    }

    class WarningDockingService:
        def __init__(self, config=None):
            self.config = config

        def verify_environment(self):
            return True

        async def perform_docking(self, **kwargs):
            return dict(docking_payload)

    monkeypatch.setattr(
        "src.docking.molecular_docking_service.MolecularDockingService",
        WarningDockingService,
    )
    receptor = tmp_path / "receptor.pdbqt"
    ligand = tmp_path / "ligand.pdbqt"
    receptor.write_text("", encoding="utf-8")
    ligand.write_text("", encoding="utf-8")
    request = {
        "receptor_path": str(receptor),
        "ligand_path": str(ligand),
        "center": [1.0, 2.0, 3.0],
        "size": [20.0, 20.0, 20.0],
    }
    tool = MolecularDocking()

    legacy_result = tool.execute(request)
    standard_result = execute_tool_compat(tool, request)

    assert legacy_result["success"] is True
    assert legacy_result["warnings"] == [warning]
    assert legacy_result["data"]["warnings"] == [warning]
    assert standard_result.success is True
    assert standard_result.warnings == [warning]
    assert standard_result.data["warnings"] == [warning]
