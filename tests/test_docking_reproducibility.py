from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest


def _pose(path, energy=-7.1):
    # Synthetic parser fixture, never evidence of actual Vina execution.
    path.write_text(
        f"MODEL 1\nREMARK VINA RESULT: {energy:.3f} 0.000 0.000\n"
        "HETATM    1  C   LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C\n"
        "ENDMDL\n", encoding="utf-8",
    )
    return str(path)


def _record(path, seed, energy=-7.1):
    pose_file = Path(_pose(path, energy))
    manifest_path = pose_file.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps({
        "search": {"random_seed": seed},
        "execution": {
            "status": "completed",
            "returncode": 0,
            "output_sha256": hashlib.sha256(pose_file.read_bytes()).hexdigest(),
        },
    }), encoding="utf-8")
    return {
        "seed": seed,
        "success": True,
        "binding_energy": energy,
        "pose_file": str(pose_file),
        "manifest_path": str(manifest_path),
    }


def _molecule(smiles="CCO"):
    Chem = pytest.importorskip("rdkit.Chem")
    from rdkit.Chem import AllChem
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    assert AllChem.EmbedMolecule(mol, randomSeed=11) == 0
    return mol


def test_rmsd_does_not_align_away_a_displaced_docking_pose():
    from src.docking.reproducibility import symmetry_aware_heavy_atom_rmsd
    reference = _molecule()
    candidate = _molecule()
    conformer = candidate.GetConformer()
    for i in range(candidate.GetNumAtoms()):
        point = conformer.GetAtomPosition(i)
        conformer.SetAtomPosition(i, (point.x + 10, point.y, point.z))
    before = conformer.GetPositions().copy()
    assert symmetry_aware_heavy_atom_rmsd(reference, candidate) == pytest.approx(10.0)
    assert (conformer.GetPositions() == before).all()


@pytest.mark.parametrize("mode", ["2d", "nan", "inf", "multiple"])
def test_rmsd_rejects_unusable_conformers(mode):
    from src.docking.reproducibility import symmetry_aware_heavy_atom_rmsd
    mol = _molecule()
    if mode == "2d":
        mol.GetConformer().Set3D(False)
    elif mode == "multiple":
        mol.AddConformer(mol.GetConformer(), assignId=True)
    else:
        mol.GetConformer().SetAtomPosition(0, (float(mode), 0, 0))
    with pytest.raises(ValueError):
        symmetry_aware_heavy_atom_rmsd(mol, mol)


@pytest.mark.parametrize("left,right", [("CCC", "C1CC1"), ("F[C@H](Cl)Br", "F[C@@H](Cl)Br")])
def test_rmsd_rejects_different_chemical_identity(left, right):
    from src.docking.reproducibility import symmetry_aware_heavy_atom_rmsd
    with pytest.raises(ValueError, match="identity"):
        symmetry_aware_heavy_atom_rmsd(_molecule(left), _molecule(right))


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
    _pose(pose_a, -7.1)
    _pose(pose_b, -7.4)

    report = assess_seed_stability(
        [
            _record(pose_a, 11, -7.1),
            _record(pose_b, 17, -7.4),
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
    _pose(pose)

    report = assess_seed_stability(
        [
            _record(pose, 11, -7.1),
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
    manifest = json.loads((tmp_path / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["search"]["random_seed"] == 37


@pytest.mark.parametrize("content", ["", "MODEL 1\nENDMDL\n", "REMARK VINA RESULT: -7.1 0 0\n"])
def test_seed_report_rejects_invalid_pose_content(tmp_path, content):
    from src.docking.reproducibility import assess_seed_stability
    path = tmp_path / "invalid.pdbqt"
    path.write_text(content, encoding="utf-8")
    invalid_record = _record(tmp_path / "invalid-record.pdbqt", 11, -7.1)
    Path(invalid_record["pose_file"]).write_text(content, encoding="utf-8")
    manifest = json.loads(Path(invalid_record["manifest_path"]).read_text(encoding="utf-8"))
    manifest["execution"]["output_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    Path(invalid_record["manifest_path"]).write_text(json.dumps(manifest), encoding="utf-8")
    result = assess_seed_stability([
        invalid_record,
        _record(tmp_path / "valid.pdbqt", 17, -7.1),
    ])
    assert result["status"] == "failed"
    assert result["failures"][0]["reason"] == "pose_artifact_invalid"


def test_seed_report_rejects_score_not_in_artifact(tmp_path):
    from src.docking.reproducibility import assess_seed_stability
    records = [_record(tmp_path / f"{seed}.pdbqt", seed, -7.1) for seed in (11, 17)]
    for record in records:
        record["binding_energy"] = -99
    result = assess_seed_stability(records)
    assert result["status"] == "failed"
    assert result["failures"][0]["reason"] == "binding_energy_mismatch"


def test_seed_report_fail_closes_on_malformed_manifest(tmp_path):
    from src.docking.reproducibility import assess_seed_stability

    first = _record(tmp_path / "first.pdbqt", 11)
    second = _record(tmp_path / "second.pdbqt", 17)
    Path(second["manifest_path"]).write_text(
        json.dumps({"execution": [], "search": {"random_seed": 17}}),
        encoding="utf-8",
    )
    result = assess_seed_stability([first, second])

    assert result["status"] == "failed"
    assert result["failures"][0]["reason"] == "execution_manifest_invalid"


@pytest.mark.parametrize("seed", [None, True, -1, 0, 2**31, 1.5, "37", 11])
def test_seed_report_requires_distinct_explicit_seeds(tmp_path, seed):
    from src.docking.reproducibility import assess_seed_stability
    result = assess_seed_stability([
        dict(seed=11, success=True, binding_energy=-7.1, pose_file=_pose(tmp_path / "a.pdbqt")),
        dict(seed=seed, success=True, binding_energy=-7.1, pose_file=_pose(tmp_path / "b.pdbqt")),
    ])
    assert result["status"] == "failed"


def test_seed_report_cannot_reuse_one_artifact_as_two_runs(tmp_path):
    from src.docking.reproducibility import assess_seed_stability
    path = _pose(tmp_path / "one.pdbqt")
    result = assess_seed_stability([dict(seed=seed, success=True, binding_energy=-7.1,
                                        pose_file=path) for seed in (11, 17)])
    assert result["status"] == "failed"


@pytest.mark.parametrize("minimum", [0, 1, -1, True, 1.5])
def test_seed_report_requires_at_least_two_runs(minimum):
    from src.docking.reproducibility import assess_seed_stability
    with pytest.raises(ValueError, match="minimum_runs"):
        assess_seed_stability([], minimum_runs=minimum)


def test_insufficient_runs_preserve_failure_reasons():
    from src.docking.reproducibility import assess_seed_stability
    result = assess_seed_stability([dict(success=False)])
    assert result["status"] == "failed"
    assert {"index": 1, "reason": "run_not_successful"} in result["failures"]


def test_mutated_seed_is_rejected_before_vina_launch(tmp_path):
    from src.docking import DockingConfig, MolecularDockingService
    config = DockingConfig()
    config.random_seed = "37\ncpu = 100"
    service = MolecularDockingService()
    calls = []
    service.vina_adapter.run_config = lambda *args, **kwargs: calls.append(args)
    assert service.run_vina_docking("r", "l", config, str(tmp_path / "out"), str(tmp_path)) is False
    assert calls == []


@pytest.mark.parametrize("seed", [0, -1, 2**31, 1.5, True, "37"])
def test_docking_config_rejects_invalid_random_seed(seed):
    from src.docking.molecular_docking_service import DockingConfig

    with pytest.raises(ValueError, match="random_seed"):
        DockingConfig(random_seed=seed)
