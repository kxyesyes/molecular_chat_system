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


def _pose_result(*, x: float, y: float, z: float, heavy_atoms: int = 1):
    from src.docking.molecular_docking_service import DockingResult

    atoms = "\n".join(
        f"HETATM{index:5d}  C   LIG A   1    {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00     0.000 C"
        for index in range(1, heavy_atoms + 1)
    )
    return DockingResult(
        ligand_id="pose_1",
        binding_energy=-7.1,
        rmsd_lb=0.0,
        rmsd_ub=0.0,
        pose_data=atoms,
        pose_index=1,
    )


def test_pose_geometry_gate_requires_box_evidence_and_keeps_analysis_explicit():
    from src.docking.reproducibility import assess_pose_geometry

    report = assess_pose_geometry(
        [_pose_result(x=0.5, y=-0.5, z=0.0)],
        center=(0.0, 0.0, 0.0),
        size=(4.0, 4.0, 4.0),
        expected_heavy_atom_count=1,
        box_source="user_explicit",
    )

    assert report["status"] == "passed"
    assert report["pose_count"] == 1
    assert report["valid_pose_count"] == 1
    assert report["box_evidence"] == {"source": "user_explicit"}
    assert report["interaction_analysis"] == {
        "status": "not_run",
        "reason_code": "optional_analysis_not_requested",
    }


def test_pose_geometry_redocking_rmsd_passes_with_explicit_reference_and_candidate():
    from src.docking.reproducibility import assess_pose_geometry

    reference = _molecule()
    report = assess_pose_geometry(
        [_pose_result(x=0.5, y=-0.5, z=0.0)],
        center=(0.0, 0.0, 0.0),
        size=(4.0, 4.0, 4.0),
        expected_heavy_atom_count=1,
        box_source="user_explicit",
        reference_pose=reference,
        candidate_pose=reference,
        redocking_rmsd_threshold=0.1,
    )

    assert report["status"] == "passed"
    assert report["redocking_rmsd"] == {
        "status": "passed",
        "reason_code": "rmsd_within_threshold",
        "value_angstrom": pytest.approx(0.0),
        "threshold_angstrom": 0.1,
    }


def test_pose_geometry_redocking_rmsd_fails_without_alignment_for_translation():
    from src.docking.reproducibility import assess_pose_geometry

    reference = _molecule()
    candidate = _molecule()
    conformer = candidate.GetConformer()
    for index in range(candidate.GetNumAtoms()):
        point = conformer.GetAtomPosition(index)
        conformer.SetAtomPosition(index, (point.x + 2.0, point.y, point.z))

    report = assess_pose_geometry(
        [_pose_result(x=0.5, y=-0.5, z=0.0)],
        center=(0.0, 0.0, 0.0),
        size=(4.0, 4.0, 4.0),
        expected_heavy_atom_count=1,
        box_source="user_explicit",
        reference_pose=reference,
        candidate_pose=candidate,
        redocking_rmsd_threshold=0.5,
    )

    assert report["status"] == "failed"
    assert report["redocking_rmsd"]["status"] == "failed"
    assert report["redocking_rmsd"]["reason_code"] == "rmsd_exceeds_threshold"
    assert report["redocking_rmsd"]["value_angstrom"] == pytest.approx(2.0)
    assert "redocking_rmsd_failed" in report["failures"]


def test_pose_geometry_redocking_rmsd_is_not_run_without_reference_or_threshold():
    from src.docking.reproducibility import assess_pose_geometry

    base = {
        "center": (0.0, 0.0, 0.0),
        "size": (4.0, 4.0, 4.0),
        "expected_heavy_atom_count": 1,
        "box_source": "user_explicit",
    }
    without_reference = assess_pose_geometry([_pose_result(x=0.5, y=-0.5, z=0.0)], **base)
    assert without_reference["redocking_rmsd"] == {
        "status": "not_run",
        "reason_code": "reference_pose_not_supplied",
    }

    without_threshold = assess_pose_geometry(
        [_pose_result(x=0.5, y=-0.5, z=0.0)],
        **base,
        reference_pose=_molecule(),
    )
    assert without_threshold["redocking_rmsd"] == {
        "status": "not_run",
        "reason_code": "threshold_not_supplied",
    }


def test_pose_geometry_redocking_rmsd_fails_closed_for_invalid_reference_or_missing_candidate():
    Chem = pytest.importorskip("rdkit.Chem")
    from src.docking.reproducibility import assess_pose_geometry

    base = {
        "center": (0.0, 0.0, 0.0),
        "size": (4.0, 4.0, 4.0),
        "expected_heavy_atom_count": 1,
        "box_source": "user_explicit",
        "redocking_rmsd_threshold": 0.5,
    }
    invalid_reference = assess_pose_geometry(
        [_pose_result(x=0.5, y=-0.5, z=0.0)],
        **base,
        reference_pose=Chem.MolFromSmiles("CCO"),
        candidate_pose=_molecule(),
    )
    assert invalid_reference["status"] == "failed"
    assert invalid_reference["redocking_rmsd"]["reason_code"] == "reference_pose_invalid"

    missing_candidate = assess_pose_geometry(
        [_pose_result(x=0.5, y=-0.5, z=0.0)],
        **base,
        reference_pose=_molecule(),
    )
    assert missing_candidate["status"] == "failed"
    assert missing_candidate["redocking_rmsd"] == {
        "status": "failed",
        "reason_code": "candidate_pose_unavailable",
    }


def _meeko_vina_output(path: Path, *, include_mapping: bool = True):
    Chem = pytest.importorskip("rdkit.Chem")
    from rdkit.Chem import AllChem
    from meeko import MoleculePreparation, PDBQTWriterLegacy

    molecule = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    assert AllChem.EmbedMolecule(molecule, randomSeed=11) == 0
    setup = MoleculePreparation().prepare(molecule)[0]
    pdbqt, success, error = PDBQTWriterLegacy.write_string(setup)
    assert success, error
    body_lines = [line for line in pdbqt.splitlines() if not line.startswith("REMARK")]
    atom_lines = [line for line in body_lines if line.startswith("ATOM")]
    translated_atoms = {
        line: f"{line[:30]}{float(line[30:38]) + 5.0:8.3f}{line[38:]}"
        for line in atom_lines
    }
    translated = [translated_atoms.get(line, line) for line in body_lines]
    mapping = ""
    if include_mapping:
        mapping = "\n".join(
            line for line in pdbqt.splitlines()
            if line.startswith("REMARK SMILES ") and not line.startswith("REMARK SMILES IDX")
        ) + "\n"
        mapping += "REMARK SMILES IDX 1 1 2 2 3 3\nREMARK H PARENT 3 4\n"
    output = mapping
    output += "MODEL 1\nREMARK VINA RESULT: -5.000 0.000 0.000\n"
    output += "\n".join(body_lines) + "\nENDMDL\n"
    output += "MODEL 2\nREMARK VINA RESULT: -7.000 0.000 0.000\n"
    output += "\n".join(translated) + "\nENDMDL\n"
    path.write_text(output, encoding="utf-8")
    return path


def test_vina_candidate_recovery_selects_requested_model_conformer(tmp_path: Path):
    from src.docking.molecular_docking_service import MolecularDockingService

    output = _meeko_vina_output(tmp_path / "result.pdbqt")
    candidate, provenance = MolecularDockingService.recover_vina_candidate_pose(
        str(output), pose_index=2,
    )

    assert candidate.GetNumConformers() == 1
    assert provenance["selected_model_index"] == 2
    assert provenance["ligand_count"] == 1
    assert provenance["conformer_count"] == 2
    assert candidate.GetConformer().GetAtomPosition(0).x == pytest.approx(4.074, abs=1e-3)


def test_vina_candidate_recovery_rejects_missing_smiles_index_mapping(tmp_path: Path):
    from src.docking.molecular_docking_service import MolecularDockingService

    output = _meeko_vina_output(tmp_path / "result-no-mapping.pdbqt", include_mapping=False)
    with pytest.raises(ValueError, match="mapping"):
        MolecularDockingService.recover_vina_candidate_pose(str(output), pose_index=1)


def test_failed_scientific_quality_survives_job_cleanup(tmp_path: Path):
    from src.docking.molecular_docking_service import MolecularDockingService

    job_dir = tmp_path / "docking-job"
    job_dir.mkdir()
    (job_dir / "run_manifest.json").write_text("{}", encoding="utf-8")
    quality = {
        "status": "failed",
        "redocking_rmsd": {
            "status": "failed",
            "reason_code": "rmsd_exceeds_threshold",
        },
    }

    response = MolecularDockingService._failed_job_response(
        str(job_dir),
        "scientific_pose_geometry_failed",
        "scientific gate failed",
        scientific_quality=quality,
    )

    assert response["scientific_quality"] == quality
    assert not job_dir.exists()


@pytest.mark.parametrize("failure", [None, "copy", "manifest", "missing", "changed"])
def test_docking_service_persists_explicit_redocking_provenance(tmp_path: Path, monkeypatch, failure):
    Chem = pytest.importorskip("rdkit.Chem")
    import asyncio
    import shutil

    from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService

    output = _meeko_vina_output(tmp_path / "fixture-result.pdbqt")
    candidate, _ = MolecularDockingService.recover_vina_candidate_pose(
        str(output), pose_index=2,
    )
    reference = tmp_path / "reference.sdf"
    Chem.MolToMolFile(candidate, str(reference))
    receptor = tmp_path / "receptor.pdbqt"
    receptor.write_text("RECEPTOR\n", encoding="utf-8")

    service = MolecularDockingService()
    service.work_dir = str(tmp_path / "work")
    Path(service.work_dir).mkdir()
    service._resolve_docking_box = lambda receptor_file, config, cancel_event=None: {
        "source": "user_explicit",
    }
    service.prepare_protein = lambda source, destination, **kwargs: (
        shutil.copyfile(source, destination) or True
    )
    service.prepare_ligand_from_smiles = lambda smiles, destination, **kwargs: (
        Path(destination).write_text("LIGAND\n", encoding="utf-8") or True
    )
    service.run_vina_docking = lambda receptor_pdbqt, ligand_pdbqt, config, destination, **kwargs: (
        shutil.copyfile(output, destination) or True
    )
    if failure == "copy":
        def fail_copy(*args, **kwargs):
            raise OSError("sensitive-path-marker")
        monkeypatch.setattr(service, "_copy_file_cooperatively", fail_copy)
    elif failure == "manifest":
        original_update = service._update_run_manifest
        def fail_quality_write(job_dir, **updates):
            return False if "scientific_quality" in updates else original_update(job_dir, **updates)
        monkeypatch.setattr(service, "_update_run_manifest", fail_quality_write)
    elif failure == "missing":
        reference.unlink()
    elif failure == "changed":
        def change_reference(*args, **kwargs):
            reference.write_text("corrupted after snapshot", encoding="utf-8")
            shutil.copyfile(output, args[3])
            return True
        service.run_vina_docking = change_reference

    result = asyncio.run(service.perform_docking(
            str(receptor),
            "CCO",
            DockingConfig(manual_center=True, size_x=20.0, size_y=20.0, size_z=20.0),
            job_id="redocking-provenance",
            reference_pose=str(reference),
            redocking_rmsd_threshold=0.1,
        )
    )

    if failure in {"copy", "missing", "manifest"}:
        assert result["success"] is False
        assert result["scientific_quality"]["status"] == "failed"
        assert str(tmp_path) not in json.dumps(result)
        assert "sensitive-path-marker" not in json.dumps(result)
        return
    assert result["success"] is True
    assert result["scientific_quality"]["redocking_rmsd"]["status"] == "passed"
    provenance = result["scientific_quality"]["redocking_rmsd_provenance"]
    assert provenance["candidate"]["selected_model_index"] == 2
    assert provenance["reference"]["snapshot"] == "reference_pose.sdf"
    assert "reference.sdf" not in json.dumps(result["scientific_quality"])
    manifest = json.loads(
        (Path(service.work_dir) / "docking_redocking-provenance" / "run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["scientific_quality"]["redocking_rmsd"]["status"] == "passed"
    assert manifest["scientific_quality"]["redocking_rmsd_provenance"]["candidate"]["selected_model_index"] == 2


@pytest.mark.parametrize("mapping", ["1 1 2 1 3 3", "1 0 2 2 3 3", "1 1 2 2", "1 3 2 2 3 1"])
def test_vina_candidate_rejects_corrupt_mapping(tmp_path, mapping):
    from src.docking.molecular_docking_service import MolecularDockingService
    output = _meeko_vina_output(tmp_path / "corrupt.pdbqt")
    output.write_text(output.read_text().replace("IDX 1 1 2 2 3 3", "IDX " + mapping))
    with pytest.raises(ValueError):
        MolecularDockingService.recover_vina_candidate_pose(str(output), pose_index=2)


def test_redocking_error_does_not_disclose_reference_path(tmp_path):
    from src.docking.reproducibility import assess_pose_geometry
    report = assess_pose_geometry(
        [_pose_result(x=0, y=0, z=0)], center=(0, 0, 0), size=(20, 20, 20),
        box_source="user_explicit", reference_pose=tmp_path / "private-missing.sdf",
        candidate_pose=_molecule(), redocking_rmsd_threshold=1,
    )
    assert report["redocking_rmsd"]["status"] == "failed"
    assert "private-missing" not in json.dumps(report)


@pytest.mark.parametrize("threshold", [0.5, True])
def test_structured_adapter_preserves_safe_failed_rmsd_and_validates_threshold(tmp_path, monkeypatch, threshold):
    from src.docking.molecular_docking_adapter import MolecularDocking
    captured = {}
    class FakeService:
        def __init__(self, **kwargs):
            pass
        def verify_environment(self):
            return True
        async def perform_docking(self, **kwargs):
            captured.update(kwargs)
            return {"success": False, "scientific_quality": {
                "status": "failed", "private": "sensitive-marker",
                "redocking_rmsd": {"status": "failed", "reason_code": "rmsd_exceeds_threshold",
                                   "value_angstrom": 2.0, "threshold_angstrom": 0.5,
                                   "detail": "sensitive-marker"},
            }}
    monkeypatch.setattr("src.docking.molecular_docking_service.MolecularDockingService", FakeService)
    reference = tmp_path / "reference.sdf"
    reference.write_text("fixture")
    receptor = tmp_path / "receptor.pdbqt"
    receptor.write_text("fixture")
    result = MolecularDocking().execute({
        "receptor_path": str(receptor), "smiles": "CCO", "center": [0, 0, 0], "size": [20, 20, 20],
        "reference_pose": str(reference), "redocking_rmsd_threshold": threshold,
    })
    assert not result["success"]
    if threshold is True:
        assert result["status"] == "invalid_input"
        assert not captured
        return
    assert captured["reference_pose"] == str(reference.resolve())
    assert captured["redocking_rmsd_threshold"] == threshold
    assert result["quality"]["scientific_quality"] == {
        "status": "failed", "redocking_rmsd": {
            "status": "failed", "reason_code": "rmsd_exceeds_threshold",
            "value_angstrom": 2.0, "threshold_angstrom": 0.5,
        },
    }
    assert "sensitive-marker" not in json.dumps(result)


def test_pose_geometry_gate_rejects_out_of_box_or_atom_count_mismatch():
    from src.docking.reproducibility import assess_pose_geometry

    report = assess_pose_geometry(
        [_pose_result(x=10.0, y=0.0, z=0.0, heavy_atoms=2)],
        center=(0.0, 0.0, 0.0),
        size=(4.0, 4.0, 4.0),
        expected_heavy_atom_count=1,
        box_source="user_explicit",
    )

    assert report["status"] == "failed"
    assert "pose_centroid_outside_box" in report["failures"]
    assert "heavy_atom_count_mismatch" in report["failures"]


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
