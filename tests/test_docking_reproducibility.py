from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_symmetry_aware_heavy_atom_rmsd_accepts_equivalent_atom_order():
    Chem = pytest.importorskip("rdkit.Chem")
    from rdkit.Chem import AllChem

    from src.docking.reproducibility import symmetry_aware_heavy_atom_rmsd

    reference = Chem.AddHs(Chem.MolFromSmiles("Cc1ccccc1"))
    assert reference is not None
    AllChem.EmbedMolecule(reference, randomSeed=11)
    AllChem.UFFOptimizeMolecule(reference)

    candidate = Chem.Mol(reference)
    # Renumbering the aromatic atoms exercises the symmetry-aware mapping
    # instead of relying on identical atom indices.
    order = list(reversed(range(candidate.GetNumAtoms())))
    candidate = Chem.RenumberAtoms(candidate, order)

    rmsd = symmetry_aware_heavy_atom_rmsd(reference, candidate)

    assert rmsd == pytest.approx(0.0, abs=1e-6)


def test_symmetry_aware_heavy_atom_rmsd_rejects_missing_3d_conformer():
    Chem = pytest.importorskip("rdkit.Chem")

    from src.docking.reproducibility import symmetry_aware_heavy_atom_rmsd

    reference = Chem.MolFromSmiles("CCO")
    candidate = Chem.MolFromSmiles("CCO")

    with pytest.raises(ValueError, match="3D conformer"):
        symmetry_aware_heavy_atom_rmsd(reference, candidate)


def test_seed_stability_report_requires_real_pose_artifacts(tmp_path: Path):
    from src.docking.reproducibility import assess_seed_stability

    pose_a = tmp_path / "pose-a.pdbqt"
    pose_b = tmp_path / "pose-b.pdbqt"
    pose_a.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")
    pose_b.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")

    report = assess_seed_stability(
        [
            {"seed": 11, "success": True, "binding_energy": -7.1, "pose_file": str(pose_a)},
            {"seed": 17, "success": True, "binding_energy": -7.4, "pose_file": str(pose_b)},
        ]
    )

    assert report["status"] == "passed"
    assert report["run_count"] == 2
    assert report["successful_runs"] == 2
    assert report["energy_range_kcal_per_mol"] == pytest.approx(0.3)
    assert report["artifact_checks"] == [True, True]


def test_seed_stability_report_does_not_promote_missing_run_to_success(tmp_path: Path):
    from src.docking.reproducibility import assess_seed_stability

    pose = tmp_path / "pose.pdbqt"
    pose.write_text("MODEL 1\nENDMDL\n", encoding="utf-8")

    report = assess_seed_stability(
        [
            {"seed": 11, "success": True, "binding_energy": -7.1, "pose_file": str(pose)},
            {"seed": 17, "success": False, "binding_energy": None, "pose_file": None},
        ]
    )

    assert report["status"] == "failed"
    assert report["successful_runs"] == 1
    assert report["failures"] == [{"index": 2, "reason": "run_not_successful"}]


def test_docking_manifest_records_random_seed(tmp_path: Path):
    from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService

    receptor = tmp_path / "receptor.pdbqt"
    ligand = tmp_path / "ligand.pdbqt"
    receptor.write_bytes(b"receptor")
    ligand.write_bytes(b"ligand")
    service = MolecularDockingService()
    config = DockingConfig(manual_center=True, random_seed=37)
    box = service._resolve_docking_box(str(receptor), config)

    manifest_path, _ = service._write_run_manifest(
        str(tmp_path), str(receptor), str(ligand), "file", config, box
    )
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    assert payload["search"]["random_seed"] == 37


def test_vina_config_records_random_seed_without_inventing_a_default(tmp_path: Path):
    from types import SimpleNamespace

    from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService

    receptor = tmp_path / "receptor.pdbqt"
    ligand = tmp_path / "ligand.pdbqt"
    output = tmp_path / "result.pdbqt"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("ATOM\n", encoding="utf-8")
    service = MolecularDockingService()
    service.vina_exe = "vina"
    captured: dict[str, str] = {}

    def fake_run_config(config_path, cwd, timeout=None, **kwargs):
        captured["config"] = Path(config_path).read_text(encoding="utf-8")
        return SimpleNamespace(returncode=1, args=["vina", "--config", "config.txt"])

    service.vina_adapter.run_config = fake_run_config
    assert service.run_vina_docking(
        str(receptor),
        str(ligand),
        DockingConfig(manual_center=True),
        str(output),
        str(tmp_path),
    ) is False
    assert "seed = " not in captured["config"]

    service.vina_adapter.run_config = fake_run_config
    assert service.run_vina_docking(
        str(receptor),
        str(ligand),
        DockingConfig(manual_center=True, random_seed=37),
        str(output),
        str(tmp_path),
    ) is False
    assert "seed = 37" in captured["config"]


@pytest.mark.parametrize("seed", [-1, 2**31, 1.5, True, "37"])
def test_docking_config_rejects_invalid_random_seed(seed):
    from src.docking.molecular_docking_service import DockingConfig

    with pytest.raises(ValueError, match="random_seed"):
        DockingConfig(random_seed=seed)
