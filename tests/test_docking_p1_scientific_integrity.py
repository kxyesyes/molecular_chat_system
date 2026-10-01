from pathlib import Path
import json

import pytest


def test_docking_box_requires_confirmation_without_evidence(tmp_path: Path, monkeypatch):
    from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService

    receptor = tmp_path / "receptor.pdb"
    receptor.write_text("ATOM\n", encoding="utf-8")
    service = MolecularDockingService()
    monkeypatch.setattr(service, "_auto_box_from_co_crystal", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="docking_box_confirmation_required"):
        service._resolve_docking_box(str(receptor), DockingConfig())


def test_blind_docking_is_explicit_and_records_search_cost(tmp_path: Path, monkeypatch):
    from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService

    receptor = tmp_path / "receptor.pdb"
    receptor.write_text("ATOM\n", encoding="utf-8")
    service = MolecularDockingService()
    monkeypatch.setattr(service, "_auto_box_from_co_crystal", lambda *_args, **_kwargs: None)
    config = DockingConfig(
        blind_docking=True,
        exhaustiveness=16,
        num_modes=20,
        size_x=30.0,
        size_y=30.0,
        size_z=30.0,
    )

    provenance = service._resolve_docking_box(str(receptor), config)

    assert provenance["source"] == "blind_explicit"
    assert provenance["mode"] == "blind"
    assert provenance["size"] == [30.0, 30.0, 30.0]
    assert provenance["exhaustiveness"] == 16
    assert provenance["num_modes"] == 20
    assert provenance["warning"]


def test_co_crystal_box_is_recorded_as_targeted(tmp_path: Path, monkeypatch):
    from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService

    receptor = tmp_path / "complex.pdb"
    receptor.write_text("HETATM\n", encoding="utf-8")
    service = MolecularDockingService()
    monkeypatch.setattr(
        service,
        "_auto_box_from_co_crystal",
        lambda *_args, **_kwargs: (1.0, 2.0, 3.0, 12.0, 13.0, 14.0),
    )
    config = DockingConfig()

    provenance = service._resolve_docking_box(str(receptor), config)

    assert provenance["source"] == "co_crystal_ligand"
    assert provenance["mode"] == "targeted"
    assert provenance["center"] == [1.0, 2.0, 3.0]
    assert provenance["size"] == [12.0, 13.0, 14.0]
    assert config.center_x == 1.0
    assert config.size_z == 14.0


def test_explicit_box_provenance_is_not_described_as_blind(tmp_path: Path):
    from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService

    receptor = tmp_path / "receptor.pdbqt"
    receptor.write_text("ATOM\n", encoding="utf-8")
    service = MolecularDockingService()
    config = DockingConfig(
        center_x=5.0,
        center_y=6.0,
        center_z=7.0,
        size_x=18.0,
        size_y=19.0,
        size_z=20.0,
        manual_center=True,
    )

    provenance = service._resolve_docking_box(str(receptor), config)

    assert provenance["source"] == "user_explicit"
    assert provenance["mode"] == "targeted"
    assert "blind" not in provenance["warning"].lower()


def test_run_manifest_records_inputs_box_and_preprocessing_without_absolute_inputs(tmp_path: Path):
    from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService

    receptor = tmp_path / "receptor.pdbqt"
    ligand = tmp_path / "ligand.sdf"
    receptor.write_bytes(b"receptor")
    ligand.write_bytes(b"ligand")
    service = MolecularDockingService()
    config = DockingConfig(manual_center=True)
    box = service._resolve_docking_box(str(receptor), config)

    manifest_path, provenance = service._write_run_manifest(
        str(tmp_path), str(receptor), str(ligand), "file", config, box
    )
    payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))

    assert payload["inputs"]["receptor"]["available"] is True
    assert payload["inputs"]["ligand"]["available"] is True
    assert payload["docking_box"]["source"] == "user_explicit"
    assert payload["preprocessing"]["ligand"].startswith("Meeko")
    assert provenance["manifest"] == "run_manifest.json"
    assert str(tmp_path) not in json.dumps(payload)
