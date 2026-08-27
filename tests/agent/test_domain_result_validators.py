import hashlib
import json
from types import SimpleNamespace
import math

import pytest

from src.agent.contracts import (
    AgentErrorCode,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
    WorkflowArtifact,
)
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
    assert validated.status == ObservationStatus.FAILED
    assert "docking evidence" in validated.message.lower()


def test_docking_validator_accepts_redacted_verified_input_contract(tmp_path):
    pose = tmp_path / "pose.pdbqt"
    pose.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
    result = ToolResult.success_result(
        "molecular_docking",
        data={
            "total_poses": 1,
            "pose_file": str(pose),
            "best_pose": {"binding_energy": -7.2, "pose_file": str(pose)},
        },
        quality={
            "docking_inputs": {
                "receptor_provided": True,
                "ligand_provided": True,
                "ligand_mode": "file",
                "center": [1.0, 2.0, 3.0],
                "size": [20.0, 20.0, 20.0],
            }
        },
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is True


def _opensandbox_docking_result(tmp_path, mutation=None):
    pose = tmp_path / "opensandbox-pose.pdbqt"
    pose.write_text(
        "MODEL 1\nREMARK VINA RESULT: -7.200 0.000 0.000\nENDMDL\n",
        encoding="ascii",
    )
    digest = hashlib.sha256(pose.read_bytes()).hexdigest()
    values = {
        "energy": -7.2,
        "pose": pose,
        "quality": {
            "real_execution": True,
            "execution_backend": "opensandbox",
            "secure_runtime": "gvisor",
            "sandbox_image_digest": "a" * 64,
            "cleanup_status": "succeeded",
            "docking_inputs": {
                "receptor_provided": True,
                "ligand_provided": True,
                "ligand_mode": "file",
                "center": [1.0, 2.0, 3.0],
                "size": [20.0, 20.0, 20.0],
            },
        },
        "artifacts": [
            WorkflowArtifact(
                artifact_type="docking_pose",
                path=str(pose),
                label="Verified docking pose",
                mime_type="chemical/x-pdbqt",
                metadata={"sha256": digest},
            )
        ],
        "provenance": ToolProvenance(
            tool_name="molecular_docking",
            tool_version="1.2.5",
            model_name="AutoDock Vina",
            model_version="1.2.5",
            demo_mode=False,
            fallback_used=False,
        ),
    }
    if mutation is not None:
        mutation(values)
    return ToolResult.success_result(
        "molecular_docking",
        data={
            "total_poses": 1,
            "pose_file": str(values["pose"]),
            "best_pose": {
                "binding_energy": values["energy"],
                "pose_file": str(values["pose"]),
            },
        },
        formatted=f"Binding energy: {values['energy']} kcal/mol",
        quality=values["quality"],
        artifacts=values["artifacts"],
        provenance=values["provenance"],
    )


def test_docking_validator_accepts_complete_opensandbox_trust_contract(tmp_path):
    validated = AgentResultValidator().validate_tool_result(
        _opensandbox_docking_result(tmp_path)
    )

    assert validated.success is True
    assert validated.status == ObservationStatus.SUCCEEDED


def test_docking_validator_scrubs_adversarial_already_failed_result(tmp_path):
    secret = "sk-fake-task10-secret-123456789"
    host_path = str((tmp_path / "private" / "pose.pdbqt").resolve())
    result = ToolResult.error_result(
        "molecular_docking",
        AgentErrorCode.TOOL_TIMEOUT,
        f"timeout at https://sandbox.internal.invalid container-raw-123 {secret}",
        details={"pose_file": host_path, "binding_energy": -99.0},
        warnings=[f"affinity -99 kcal/mol at {host_path} {secret}"],
        evidence=[{"raw_stderr": f"pose bytes {secret}"}],
        artifacts=[WorkflowArtifact("docking_pose", host_path, "Untrusted pose")],
        quality={
            "binding_energy": -99.0,
            "affinity": "-99 kcal/mol",
            "pose_file": host_path,
            "container_id": "container-raw-123",
        },
        provenance=ToolProvenance(
            tool_name="molecular_docking",
            tool_version=f"endpoint-{secret}",
        ),
    )
    result.data = {"binding_energy": -99.0, "pose": b"POSE BYTES"}
    result.formatted = f"-99 kcal/mol {host_path}"
    result.status = ObservationStatus.SUCCEEDED

    validated = AgentResultValidator().validate_tool_result(result)
    serialized = json.dumps(validated.to_legacy_dict(), default=str)

    assert validated.success is False
    assert validated.status == ObservationStatus.FAILED
    assert validated.data is None
    assert validated.formatted == ""
    assert validated.artifacts == []
    assert validated.evidence == []
    assert validated.provenance is None
    assert validated.warnings == ["docking_execution_failed"]
    assert validated.quality == {
        "validated": False,
        "failure_category": "docking_failure",
        "failure_code": "tool_timeout",
    }
    for forbidden in (
        "binding_energy",
        "affinity",
        "kcal/mol",
        "pose_file",
        "POSE BYTES",
        host_path,
        secret,
        "sandbox.internal.invalid",
        "container-raw-123",
    ):
        assert forbidden not in serialized


def test_docking_validator_scrubs_adversarial_rejected_success(tmp_path):
    secret = "sk-fake-task10-rejected-123456789"
    host_path = str((tmp_path / "missing" / "pose.pdbqt").resolve())
    result = ToolResult.success_result(
        "molecular_docking",
        data={
            "total_poses": 1,
            "pose_file": host_path,
            "best_pose": {"binding_energy": -88.0, "pose_file": host_path},
        },
        formatted=f"Affinity -88 kcal/mol {secret}",
        warnings=[f"raw endpoint https://sandbox.internal.invalid {secret}"],
        evidence=[{"pose": b"POSE BYTES", "path": host_path}],
        artifacts=[WorkflowArtifact("docking_pose", host_path, "Untrusted pose")],
        quality={
            "real_execution": True,
            "binding_energy": -88.0,
            "affinity": "-88 kcal/mol",
            "container_id": "container-raw-456",
        },
        provenance=ToolProvenance(
            tool_name="molecular_docking",
            tool_version="raw-endpoint",
        ),
    )

    validated = AgentResultValidator().validate_tool_result(result)
    serialized = json.dumps(validated.to_legacy_dict(), default=str)

    assert validated.success is False
    assert validated.data is None
    assert validated.formatted == ""
    assert validated.artifacts == []
    assert validated.evidence == []
    assert validated.provenance is None
    assert validated.warnings == [
        "Referenced artifact file does not exist",
        "docking_result_rejected",
    ]
    assert validated.quality == {
        "validated": False,
        "failure_category": "docking_failure",
        "failure_code": "invalid_output",
    }
    for forbidden in (
        "binding_energy",
        "affinity",
        "kcal/mol",
        "pose_file",
        "POSE BYTES",
        host_path,
        secret,
        "sandbox.internal.invalid",
        "container-raw-456",
    ):
        assert forbidden not in serialized


def _set_quality(name, value):
    def mutate(values):
        if value is None:
            values["quality"].pop(name, None)
        else:
            values["quality"][name] = value

    return mutate


def _set_energy(value):
    return lambda values: values.__setitem__("energy", value)


def _set_provenance(**updates):
    def mutate(values):
        source = values["provenance"].to_dict()
        source.update(updates)
        values["provenance"] = ToolProvenance.from_dict(source)

    return mutate


def _remove_pose_artifact(values):
    values["artifacts"] = []


def _invalidate_pose_hash(values):
    artifact = values["artifacts"][0]
    values["artifacts"] = [
        WorkflowArtifact(
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            label=artifact.label,
            mime_type=artifact.mime_type,
            metadata={"sha256": "f" * 64},
        )
    ]


def _remove_pose_file(values):
    values["pose"].unlink()


def _malform_pose_artifact(values):
    values["artifacts"] = [object()]


@pytest.mark.parametrize(
    "mutation",
    [
        _set_quality("secure_runtime", "runc"),
        _set_quality("sandbox_image_digest", None),
        _set_quality("sandbox_image_digest", "A" * 64),
        _set_quality("sandbox_image_digest", "a" * 63),
        _set_quality("real_execution", False),
        _set_quality("cleanup_status", "failed"),
        _set_energy(math.nan),
        _set_energy(math.inf),
        _set_provenance(demo_mode=True),
        _set_provenance(fallback_used=True),
        lambda values: values.__setitem__("provenance", None),
        _remove_pose_artifact,
        _invalidate_pose_hash,
        _remove_pose_file,
        _malform_pose_artifact,
    ],
    ids=[
        "runc",
        "missing-digest",
        "uppercase-digest",
        "malformed-digest",
        "not-real-execution",
        "cleanup-failed",
        "nan-energy",
        "inf-energy",
        "demo-mode",
        "fallback-used",
        "missing-provenance",
        "missing-pose-artifact",
        "invalid-pose-hash",
        "missing-pose-file",
        "malformed-pose-artifact",
    ],
)
def test_docking_validator_rejects_incomplete_opensandbox_trust_contract(
    tmp_path,
    mutation,
):
    result = _opensandbox_docking_result(tmp_path, mutation)

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.status == ObservationStatus.FAILED
    assert validated.error.code == AgentErrorCode.INVALID_OUTPUT
    assert validated.data is None
    assert validated.artifacts == []
    encoded = json.dumps(validated.to_legacy_dict(), ensure_ascii=False)
    assert "binding_energy" not in encoded
    assert "pose_file" not in encoded
    assert "kcal/mol" not in encoded


def test_docking_validator_rejects_non_finite_or_boolean_science(tmp_path):
    pose = tmp_path / "pose.pdbqt"
    pose.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
    quality = {
        "docking_inputs": {
            "receptor_provided": True,
            "ligand_provided": True,
            "ligand_mode": "file",
            "center": [1.0, 2.0, 3.0],
            "size": [20.0, 20.0, 20.0],
        }
    }
    for energy, pose_count in ((math.nan, 1), (math.inf, 1), (True, 1), (-7.2, True)):
        result = ToolResult.success_result(
            "molecular_docking",
            data={
                "total_poses": pose_count,
                "pose_file": str(pose),
                "best_pose": {
                    "binding_energy": energy,
                    "pose_file": str(pose),
                },
            },
            quality=quality,
        )

        validated = AgentResultValidator().validate_tool_result(result)

        assert validated.success is False


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
