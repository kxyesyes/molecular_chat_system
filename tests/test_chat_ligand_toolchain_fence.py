"""Pinned command fences, using synthetic adapter output; never run Vina."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.docking.adapters.adfr_adapter import ADFRAdapter
from src.docking.adapters.base import CommandOwnershipScope
from src.docking.adapters.meeko_adapter import MeekoAdapter
from src.docking.chat_preparation import build_ligand_sdf
from src.docking.molecular_docking_service import DockingConfig, MolecularDockingService
from src.task_runtime.docking_consent import DockingConsentError
from tests.task_runtime.test_chat_docking_toolchain import capture, no_external, rig
from tests.test_chat_receptor_preparation_integrity import pdbqt, source_atoms


def service_for(rig, *, chat, scoped):
    binding = capture(rig) if chat else None
    output = rig.root / "private"
    ownership = ({"command_scope": CommandOwnershipScope(task_id="fence", output_root=output),
                  "allowed_output_root": output} if scoped else {})
    return MolecularDockingService(
        config=binding.runtime_config() if binding else {"docking": rig.config},
        chat_toolchain=binding, **ownership)


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("input_type", ["file", "smiles"])
@pytest.mark.parametrize("chat,drift", [(True, True), (True, False), (False, True)])
def test_phase_drift_is_blocked_before_actual_ligand_adapter(
        rig, monkeypatch, scoped, input_type, chat, drift):
    service = service_for(rig, chat=chat, scoped=scoped)
    receptor, ligand = rig.root / "approved.pdb", rig.root / "approved.sdf"
    receptor.write_text("HEADER    SYNTHETIC".ljust(62) + "1ABC\n" +
                        "\n".join(source_atoms()) + "\nEND\n", encoding="ascii")
    ligand.write_bytes(build_ligand_sdf("CCO")["sdf_bytes"])
    calls, vina = [], []
    monkeypatch.setattr(MolecularDockingService, "verify_environment", lambda self: True)

    def prepare_receptor(adapter, args, **kwargs):
        Path(args[args.index("-o") + 1]).write_text(pdbqt(), encoding="ascii")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def prepare_ligand(adapter, args, **kwargs):
        calls.append(rig.paths["prepare_ligand_cmd"].read_bytes())
        Path(args[args.index("-o") + 1]).write_text(pdbqt(), encoding="ascii")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def run_vina(*args, **kwargs):
        vina.append(True)
        return False  # Sentinel, not synthetic scientific success.

    monkeypatch.setattr(ADFRAdapter, "run", prepare_receptor)
    monkeypatch.setattr(MeekoAdapter, "run", prepare_ligand)
    monkeypatch.setattr(MolecularDockingService, "run_vina_docking", run_vina)

    def progress(phase, percent):
        if drift and phase == "ligand_preparation":
            rig.paths["prepare_ligand_cmd"].write_bytes(b"changed after confirmation")

    coroutine = service.perform_docking(
        str(receptor), "CCO" if input_type == "smiles" else str(ligand),
        DockingConfig(center_x=1, center_y=2, center_z=3, size_x=10, size_y=10, size_z=10),
        input_type=input_type, progress_callback=progress)
    try:
        coroutine.send(None)  # No await points; avoid Windows loopback sockets.
    except StopIteration as done:
        result = done.value
    else:
        pytest.fail("unexpected asynchronous suspension")
    finally:
        coroutine.close()

    assert result["success"] is False
    if chat and drift:
        assert calls == [], "unconfirmed tool bytes reached ligand command entry"
        assert vina == []
    else:
        assert len(calls) == 1
        assert vina == [True]


@pytest.mark.parametrize("scoped", [False, True])
def test_direct_ligand_command_rejects_drift_before_adapter(rig, monkeypatch, scoped):
    service = service_for(rig, chat=True, scoped=scoped)
    calls = []
    def prepare(*args, **kwargs):
        calls.append(True)
        return SimpleNamespace(returncode=1, stdout="", stderr="synthetic stop")
    monkeypatch.setattr(MeekoAdapter, "prepare_ligand", prepare)
    rig.paths["prepare_ligand_cmd"].write_bytes(b"unconfirmed")
    with pytest.raises(DockingConsentError, match="consent_policy_unavailable"):
        service._run_prepare_ligand(str(rig.root / "ligand.sdf"),
                                    str(rig.root / "ligand.pdbqt"), str(rig.root), "test")
    assert calls == []


@pytest.mark.parametrize("scoped", [False, True])
def test_implicit_h_retry_rechecks_pinned_command(rig, monkeypatch, scoped):
    service = service_for(rig, chat=True, scoped=scoped)
    ligand, output = rig.root / "ligand.sdf", rig.root / "ligand.pdbqt"
    ligand.write_bytes(build_ligand_sdf("CCO")["sdf_bytes"])
    calls = []

    def fail_once(adapter, args, **kwargs):
        calls.append(True)
        rig.paths["prepare_ligand_cmd"].write_bytes(b"changed before retry")
        return SimpleNamespace(returncode=1, stdout="", stderr="implicit Hs")

    monkeypatch.setattr(MeekoAdapter, "run", fail_once)
    assert service.prepare_ligand_from_file(str(ligand), str(output)) is False
    assert calls == [True], "implicit-H retry used an unconfirmed command"
