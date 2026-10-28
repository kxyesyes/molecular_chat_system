"""Avoid a known Meeko rejection without changing ligand preparation policy."""
from pathlib import Path
import subprocess
import threading

import pytest

from src.docking.adapters.base import CommandCancelledError
from src.docking.molecular_docking_service import MolecularDockingService


@pytest.fixture
def ligand_case(tmp_path):
    Chem = pytest.importorskip("rdkit.Chem")
    from rdkit.Chem import AllChem

    molecule = Chem.AddHs(Chem.MolFromSmiles("CCO"))
    assert AllChem.EmbedMolecule(molecule, randomSeed=123) == 0
    source = tmp_path / "input.sdf"
    service = MolecularDockingService({"prepare_ligand_cmd": "mk_prepare_ligand.exe"})
    return service, Chem, molecule, source, tmp_path / "ligand.pdbqt"


def write_sdf(Chem, source, *molecules):
    with Chem.SDWriter(str(source)) as writer:
        for molecule in molecules:
            writer.write(molecule)


@pytest.mark.parametrize("extension", [".sdf", ".mol"])
def test_implicit_h_ligand_is_normalized_before_first_meeko_launch(ligand_case, monkeypatch, extension):
    service, Chem, molecule, source, output = ligand_case
    source = source.with_suffix(extension)
    write_sdf(Chem, source, Chem.RemoveHs(molecule))
    launches = []
    control = threading.Event()

    def prepare(input_path, output_path, cwd, timeout, *, cancel_event):
        loaded = Chem.SDMolSupplier(input_path, removeHs=False)[0]
        has_implicit_h = any(atom.GetNumImplicitHs() for atom in loaded.GetAtoms())
        launches.append((Path(input_path).name, cancel_event, has_implicit_h))
        if has_implicit_h:
            return subprocess.CompletedProcess([], 1, stderr="implicit Hs", stdout="")
        Path(output_path).write_text("synthetic prepared artifact", encoding="utf-8")
        return subprocess.CompletedProcess([], 0, stderr="", stdout="")

    monkeypatch.setattr(service.ligand_adapter, "prepare_ligand", prepare)
    trace = {}
    assert service.prepare_ligand_from_file(str(source), str(output), cancel_event=control, trace=trace)
    assert launches == [("ligand_explicit_h.sdf", control, False)]
    assert trace["ligand_method"] == "rdkit_explicit_h_then_meeko"
    assert trace["ligand_geometry"]["converged"] is True


@pytest.mark.parametrize("mode", ["explicit_h", "legacy", "multiple", "unsupported"])
def test_other_inputs_keep_the_direct_preparation_path(ligand_case, monkeypatch, mode):
    service, Chem, molecule, source, output = ligand_case
    if mode == "legacy":
        service.ligand_adapter.executable = "prepare_ligand.bat"
    if mode == "unsupported":
        source = source.with_suffix(".pdb")
    write_sdf(Chem, source, *([molecule] if mode == "explicit_h" else [Chem.RemoveHs(molecule)] * (2 if mode == "multiple" else 1)))
    calls = []

    def prepare(input_path, *args, **kwargs):
        calls.append(input_path)
        return True, ""

    monkeypatch.setattr(service, "_run_prepare_ligand", prepare)
    trace = {}
    assert service.prepare_ligand_from_file(str(source), str(output), trace=trace)
    assert calls == [str(source)]
    assert trace["ligand_method"] == "meeko_direct"


def test_normalization_failure_does_not_launch_meeko(ligand_case, monkeypatch):
    service, Chem, molecule, source, output = ligand_case
    from rdkit.Chem import AllChem
    write_sdf(Chem, source, Chem.RemoveHs(molecule))
    monkeypatch.setattr(AllChem, "MMFFOptimizeMolecule", lambda *a, **k: 1)
    launches = []
    monkeypatch.setattr(service, "_run_prepare_ligand", lambda *a, **k: launches.append(True) or (False, "implicit Hs"))
    trace = {}
    assert not service.prepare_ligand_from_file(str(source), str(output), trace=trace)
    assert launches == []
    assert trace["ligand_geometry"]["converged"] is False
    assert "ligand_method" not in trace


def test_cancelled_input_does_not_normalize_or_launch(ligand_case, monkeypatch):
    service, _, _, source, output = ligand_case
    control = threading.Event()
    control.set()
    monkeypatch.setattr(service, "_prepare_ligand_file_with_explicit_hs", lambda *a, **k: pytest.fail("normalized cancelled input"))
    monkeypatch.setattr(service, "_run_prepare_ligand", lambda *a, **k: pytest.fail("launched cancelled input"))
    with pytest.raises(CommandCancelledError):
        service.prepare_ligand_from_file(str(source), str(output), cancel_event=control)
