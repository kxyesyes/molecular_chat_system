from types import SimpleNamespace

from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.tools.reverse_target_tool import ReverseTargetTool
from src.agent.tools.target_database_tool import TargetDatabaseTool
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


def test_adme_py_success_records_backend_method_and_package_version(monkeypatch):
    from src.agent.tools import admet_predictor as admet_module

    class FakeADME:
        def __init__(self, smiles):
            self.smiles = smiles

        def calculate(self):
            return {
                "physiochemical": {"molecular_weight": 46.07},
                "solubility": {},
                "lipophilicity": {},
                "pharmacokinetics": {},
                "druglikeness": {},
                "medicinal": {},
            }

    monkeypatch.setattr(admet_module, "ADME_PY_AVAILABLE", True)
    monkeypatch.setattr(admet_module, "ADME", FakeADME)
    monkeypatch.setattr(admet_module, "ADME_PY_VERSION", "9.8.7", raising=False)

    prediction = ADMETPredictor().predict_admet_with_adme_py("CCO")

    assert prediction["prediction_method"] == "adme_py"
    assert prediction["backend_version"] == "9.8.7"


def test_reverse_target_tool_preserves_stable_identifier_and_assay_evidence():
    tool = ReverseTargetTool()
    tool._predictor = SimpleNamespace(
        predict=lambda *_args, **_kwargs: [
            {
                "target_name": "Epidermal growth factor receptor",
                "organism": "Human",
                "target_chembl_id": "CHEMBL203",
                "standard_type": "IC50",
                "standard_relation": "=",
                "standard_value": 12.5,
                "standard_units": "nM",
                "morgan_similarity": 0.82,
                "maccs_similarity": 0.76,
                "final_similarity": 0.79,
            }
        ]
    )

    result = tool.execute("reverse target for CCO")

    assert result["success"] is True
    record = result["data"][0]
    assert record["target_identifier"] == "CHEMBL203"
    assert record["assay"] == {
        "type": "IC50",
        "relation": "=",
        "value": 12.5,
        "units": "nM",
    }


def test_reverse_target_tool_derives_stable_identifier_when_database_id_missing():
    row = {
        "target_name": "Example kinase",
        "organism": "Human",
        "standard_type": "Ki",
        "standard_value": 20.0,
        "final_similarity": 0.7,
        "morgan_similarity": 0.7,
        "maccs_similarity": 0.7,
    }
    first = ReverseTargetTool._normalize_target_record(row)
    second = ReverseTargetTool._normalize_target_record(dict(row))

    assert first["target_identifier"] == second["target_identifier"]
    assert first["target_identifier"].startswith("name-sha256:")
    assert "relation" not in first["assay"]
    assert "units" not in first["assay"]


def test_target_database_tool_returns_actual_recommended_structure_records():
    class FakeTargetService:
        def search_targets(self, _query):
            return {
                "results": [
                    {
                        "target_id": 7,
                        "gene_symbol": "EGFR",
                        "protein_name": "Epidermal growth factor receptor",
                        "uniprot_id": "P00533",
                        "organism": "Homo sapiens",
                        "structure_count": 2,
                        "has_experimental_structure": True,
                        "has_alphafold_structure": True,
                        "match_reason": "gene_symbol",
                    }
                ]
            }

        def get_target_structures(self, target_id):
            assert target_id == 7
            return {
                "structures": [
                    {
                        "id": 70,
                        "structure_id": "4WKQ",
                        "source": "RCSB PDB",
                        "score": 92.0,
                        "recommendation_level": "recommended",
                        "docking_recommended": True,
                    },
                    {
                        "id": 71,
                        "structure_id": "AF-P00533-F1",
                        "source": "AlphaFold",
                        "score": 50.0,
                        "recommendation_level": "fallback",
                        "docking_recommended": False,
                    },
                ]
            }

    tool = TargetDatabaseTool()
    tool._service = FakeTargetService()

    result = tool.execute("EGFR")

    assert result["success"] is True
    structures = result["data"][0]["recommended_structures"]
    assert [item["structure_id"] for item in structures] == [
        "4WKQ",
        "AF-P00533-F1",
    ]
    assert all(isinstance(item, dict) for item in structures)
