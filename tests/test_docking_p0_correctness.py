import math
from pathlib import Path

import pytest


def _atom(serial: int, x: float, y: float, z: float) -> str:
    return (
        f"HETATM{serial:5d}  C   LIG A   1    "
        f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00     0.000 C"
    )


def _vina_output(*energies: float, include_atoms: bool = True) -> str:
    lines = []
    for index, energy in enumerate(energies, start=1):
        lines.extend(
            [
                f"MODEL {index}",
                f"REMARK VINA RESULT: {energy:8.3f} 0.000 0.000",
            ]
        )
        if include_atoms:
            lines.append(_atom(1, float(index), 2.0, 3.0))
        lines.append("ENDMDL")
    return "\n".join(lines) + "\n"


def test_vina_parser_requires_real_pose_atoms_and_selects_lowest_energy(tmp_path: Path):
    from src.docking.molecular_docking_service import MolecularDockingService

    result_file = tmp_path / "result.pdbqt"
    result_file.write_text(_vina_output(-5.0, -9.0), encoding="utf-8")

    results = MolecularDockingService().parse_vina_results(str(result_file))

    assert [item.binding_energy for item in results] == [-9.0, -5.0]
    assert results[0].pose_index == 2


@pytest.mark.parametrize(
    "content",
    [
        "REMARK VINA RESULT: -9.0 0.0 0.0\n",
        _vina_output(-9.0, include_atoms=False),
        _vina_output(math.nan),
    ],
)
def test_vina_parser_rejects_score_only_invalid_or_nonfinite_output(
    tmp_path: Path, content: str
):
    from src.docking.molecular_docking_service import MolecularDockingService

    result_file = tmp_path / "result.pdbqt"
    result_file.write_text(content, encoding="utf-8")

    assert MolecularDockingService().parse_vina_results(str(result_file)) == []


def test_ligand_efficiency_uses_negative_delta_g_and_missing_is_unavailable():
    from src.docking.molecular_docking_service import _ligand_efficiency

    assert _ligand_efficiency(-8.0, 16) == pytest.approx(0.5)
    assert _ligand_efficiency(None, 16) is None
    assert _ligand_efficiency(-8.0, 0) is None


def test_history_summary_accepts_only_valid_pose_models(tmp_path: Path):
    from src.docking.history_index import _parse_vina_summary

    invalid = tmp_path / "invalid.pdbqt"
    invalid.write_text("REMARK VINA RESULT: -9.0 0.0 0.0\n", encoding="utf-8")
    assert _parse_vina_summary(invalid) == (None, 0)

    valid = tmp_path / "valid.pdbqt"
    valid.write_text(_vina_output(-5.0, -9.0), encoding="utf-8")
    assert _parse_vina_summary(valid) == (-9.0, 2)


def test_durable_pose_validator_rejects_nonfinite_atom_coordinates():
    from src.task_runtime.docking_execution import (
        _VinaPoseStreamValidator,
    )
    from src.task_runtime.completion import CompletionError

    validator = _VinaPoseStreamValidator(
        expected_pose_count=1,
        expected_best_energy=-9.0,
    )
    bad_atom = _atom(1, float("nan"), 2.0, 3.0).encode("ascii")
    with pytest.raises(CompletionError):
        validator.feed(
            b"MODEL 1\n"
            b"REMARK VINA RESULT: -9.000 0.000 0.000\n"
            + bad_atom
            + b"\n"
        )
        validator.finish()


def test_smiles_preparation_rejects_nonconverged_force_field(monkeypatch, tmp_path: Path):
    pytest.importorskip("rdkit")
    from rdkit.Chem import AllChem
    from src.docking.molecular_docking_service import MolecularDockingService

    service = MolecularDockingService()
    monkeypatch.setattr(AllChem, "MMFFOptimizeMolecule", lambda mol: 1)
    monkeypatch.setattr(AllChem, "UFFOptimizeMolecule", lambda mol: 1)
    called = False

    def should_not_prepare(*args, **kwargs):
        nonlocal called
        called = True
        return True, ""

    monkeypatch.setattr(service, "_run_prepare_ligand", should_not_prepare)

    assert not service.prepare_ligand_from_smiles(
        "CCO", str(tmp_path / "ligand.pdbqt")
    )
    assert called is False


def test_smiles_preparation_rejects_when_primary_and_fallback_embedding_fail(
    monkeypatch, tmp_path: Path
):
    pytest.importorskip("rdkit")
    from rdkit.Chem import AllChem
    from src.docking.molecular_docking_service import MolecularDockingService

    service = MolecularDockingService()
    monkeypatch.setattr(AllChem, "EmbedMolecule", lambda *args, **kwargs: -1)

    assert not service.prepare_ligand_from_smiles(
        "CCO", str(tmp_path / "ligand.pdbqt")
    )


@pytest.mark.parametrize("mutation", [
    lambda text: text.replace("ENDMDL", "REMARK VINA RESULT: nan 0 0\nENDMDL"),
    lambda text: text.replace("0.000 0.000", "-1.000 0.000"),
    lambda text: text + "ATOM      1  C\n",
    lambda text: text.replace("MODEL 1", "MODEL 0"),
    lambda text: text.replace("ENDMDL", _atom(1, 0, 0, 0) + "\nENDMDL"),
])
def test_all_pose_consumers_reject_corrupt_records(tmp_path, mutation):
    from src.docking.molecular_docking_service import MolecularDockingService
    from src.docking.history_index import _parse_vina_summary
    from src.task_runtime.docking_execution import _VinaPoseStreamValidator
    from src.task_runtime.completion import CompletionError

    content = mutation(_vina_output(-9.0))
    path = tmp_path / "result.pdbqt"
    path.write_text(content, encoding="ascii")
    assert MolecularDockingService().parse_vina_results(str(path)) == []
    assert _parse_vina_summary(path) == (None, 0)
    parser = _VinaPoseStreamValidator(expected_pose_count=1, expected_best_energy=-9.0)
    with pytest.raises(CompletionError):
        parser.feed(content.encode("ascii"))
        parser.finish()


def test_result_tool_never_reports_empty_pose_set_as_success(tmp_path):
    from src.agent.tools.docking_tools import GetDockingResultTool
    from src.docking.molecular_docking_service import MolecularDockingService
    path = tmp_path / "result.pdbqt"
    path.write_text("REMARK VINA RESULT: -9.0 0 0\n", encoding="ascii")
    result = GetDockingResultTool(service=MolecularDockingService()).run(str(path))
    assert result["success"] is False
