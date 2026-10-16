"""C adapter/service integration; synthetic command output, never real chemistry."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.docking.adapters.adfr_adapter import ADFRAdapter
from src.docking.molecular_docking_adapter import MolecularDocking
from src.docking.molecular_docking_service import MolecularDockingService
from tests.task_runtime.test_chat_docking_toolchain import rig, capture, no_external


def atom(serial, name, residue, chain, seq, element, x=1.0, record="HETATM"):
    return (f"{record:<6}{serial:5d} {name:^4} {residue:>3} {chain}{seq:4d}    "
            f"{x:8.3f}{2.0:8.3f}{3.0:8.3f}{1.0:6.2f}{0.0:6.2f}          {element:>2}  ")


def source_atoms():
    return [atom(1, "CA", "ALA", "A", 1, "C", record="ATOM"),
            atom(2, "O", "HOH", "W", 2, "O"),
            atom(3, "C1", "LIG", "X", 3, "C"),
            atom(4, "H1", "ALA", "A", 1, "H", record="ATOM")]


def pdbqt(lines=None):
    lines = source_atoms()[:3] if lines is None else lines
    types = {"C": "C", "O": "OA", "H": "HD"}
    return "\n".join(line[:66] + "    " + " 0.000 " + f"{types[line[76:78].strip()]:<2}"
                     for line in lines) + "\n"


def run_case(rig, monkeypatch, output, *, chat=True, query_extra=None, hook=None,
             ligand_hook=None, source=None, scoped=False, progress=None):
    # perform_docking currently has no await points; avoid Windows asyncio's
    # loopback socketpair while the fixture prohibits ALL network connections.
    def run_coroutine(factory):
        coroutine = factory()
        try:
            coroutine.send(None)
        except StopIteration as done:
            return done.value
        finally:
            coroutine.close()
        raise AssertionError("unexpected asynchronous suspension")
    monkeypatch.setattr("src.docking.molecular_docking_adapter._run_coroutine_sync", run_coroutine)
    receptor = rig.root / "approved.pdb"
    receptor.write_text(source or ("HEADER    SYNTHETIC".ljust(62) + "1ABC\n" +
                        "\n".join(source_atoms()) + "\nEND\n"), encoding="ascii")
    commands, vina = [], []
    binding = capture(rig) if chat else None
    monkeypatch.setattr(MolecularDockingService, "verify_environment", lambda self: True)
    def prepare(adapter, args, **kwargs):
        commands.append(args)
        target = Path(args[args.index("-o") + 1])
        if output is not None:
            target.write_text(output, encoding="ascii")
        if hook:
            hook()
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(ADFRAdapter, "run", prepare)
    def ligand(self, smiles, path, **kwargs):
        Path(path).write_text(pdbqt(), encoding="ascii")
        if ligand_hook:
            ligand_hook(self, Path(path).with_name("receptor.pdbqt"), receptor)
        return True
    monkeypatch.setattr(MolecularDockingService, "prepare_ligand_from_smiles", ligand)
    def run_vina(self, *args, **kwargs):
        vina.append(True)
        return False  # Do not fabricate scientific output for the sentinel.
    monkeypatch.setattr(MolecularDockingService, "run_vina_docking", run_vina)
    query = dict(receptor_path=str(receptor), smiles="CC", center=[1, 2, 3], size=[10]*3,
                 runtime_config={"docking": rig.config})
    query.update(query_extra or {})
    ownership = {}
    if scoped:
        from src.docking.adapters.base import CommandOwnershipScope
        ownership["command_scope"] = CommandOwnershipScope(task_id="scoped-p1", output_root=rig.root / "private")
        ownership["allowed_output_root"] = rig.root / "private"
    result = MolecularDocking(chat_toolchain=binding, **ownership).execute(
        query, job_id="scoped-p1" if scoped else None, progress_callback=progress)
    return result, commands, vina


@pytest.mark.parametrize("drop", [1, 2])
def test_c_rejects_returncode_zero_water_or_whole_nonstandard_chain_loss(rig, monkeypatch, drop):
    lines = source_atoms()[:3]
    del lines[drop]
    result, commands, vina = run_case(rig, monkeypatch, pdbqt(lines))
    assert commands
    assert vina == []
    assert result["success"] is False
    assert result["quality"]["execution_status"] == "receptor_preparation_failed"


def test_c_preservation_uses_explicit_non_destructive_cleanup(rig, monkeypatch):
    result, commands, vina = run_case(rig, monkeypatch, pdbqt())
    assert vina == [True]
    assert commands[0][-4:] == ["-A", "None", "-U", "nphs"]
    assert not result["success"]  # sentinel deliberately stops before real Vina


@pytest.mark.parametrize("output", [None, "", pdbqt().splitlines()[0] + "\n",
    pdbqt() + pdbqt().splitlines()[0] + "\n", pdbqt()[:-4],
    pdbqt().replace("   1.000", "   1.001", 1),
    pdbqt().replace(" C \n", " N \n", 1),
    pdbqt().replace("HOH W", "HOH Z"),
    pdbqt().replace(" 0.000", "   nan", 1),
    pdbqt() + "ROOT\n", pdbqt().replace(" OA", " XX"),
    pdbqt().replace(" C \n", " HD\n", 1),
    pdbqt().replace("    2 ", "    1 ", 1),
    pdbqt() + pdbqt().splitlines()[0].replace("    1 ", "   20 ", 1) + "\n",
    pdbqt().replace(" C1 ", " C2 "), pdbqt()[:-1],
    pdbqt().replace("   1.000", "     nan", 1),
    pdbqt() + "END\n" + pdbqt(),
    pdbqt(source_atoms()[:3] + [atom(5, "H2", "NEW", "Y", 4, "H")]),
    pdbqt(source_atoms()[:3] + [atom(5, "C2", "LIG", "X", 3, "C")])])
def test_c_invalid_actual_output_never_reaches_vina(rig, monkeypatch, output):
    _, commands, vina = run_case(rig, monkeypatch, output)
    assert commands
    assert vina == []


def test_legacy_retains_default_preparation_behavior(rig, monkeypatch):
    _, commands, vina = run_case(rig, monkeypatch, pdbqt(source_atoms()[:1]), chat=False,
        query_extra={"chat_toolchain": True, "preserve_receptor": True})
    assert vina == [True]
    assert "-U" not in commands[0] and "-A" not in commands[0]


def test_query_cannot_disable_constructor_bound_policy(rig, monkeypatch):
    _, _, vina = run_case(rig, monkeypatch, pdbqt(source_atoms()[:1]),
        query_extra={"chat_toolchain": None, "preserve_receptor": False,
                     "runtime_config": {"preserve_receptor": False}})
    assert vina == []


def test_c_allows_hydrogen_changes_and_atom_renumbering(rig, monkeypatch):
    lines = source_atoms()[:3] + [atom(9, "H2", "HOH", "W", 2, "H")]
    output = pdbqt(lines).replace("    1 ", "   11 ", 1)
    _, _, vina = run_case(rig, monkeypatch, output)
    assert vina == [True]


@pytest.mark.parametrize("scoped", [False, True])
def test_c_rechecks_after_ligand_preparation(rig, monkeypatch, scoped):
    def mutate(service, prepared, source):
        prepared.write_text(pdbqt(source_atoms()[:1]), encoding="ascii")
    _, _, vina = run_case(rig, monkeypatch, pdbqt(), ligand_hook=mutate, scoped=scoped)
    assert vina == []


def test_c_compares_to_precommand_snapshot_not_mutated_source(rig, monkeypatch):
    def mutate(service, prepared, source):
        source.write_text(source.read_text().replace("   1.000", "   9.000"), encoding="ascii")
        prepared.write_text(pdbqt().replace("   1.000", "   9.000"), encoding="ascii")
    _, _, vina = run_case(rig, monkeypatch, pdbqt(), ligand_hook=mutate)
    assert vina == []


@pytest.mark.parametrize("field", ["_chat_toolchain", "_chat_toolchain_at_construction", "_command_scope"])
def test_c_scope_or_binding_tamper_never_reaches_vina(rig, monkeypatch, field):
    def mutate(service, prepared, source):
        setattr(service, field, None)
    _, _, vina = run_case(rig, monkeypatch, pdbqt(), ligand_hook=mutate, scoped=True)
    assert vina == []


def test_c_rejects_unsupported_source_element_before_preparation(rig, monkeypatch):
    source = "HEADER    SYNTHETIC".ljust(62) + "1ABC\n" + atom(1, "SE", "MSE", "A", 1, "Se") + "\nEND\n"
    _, commands, vina = run_case(rig, monkeypatch, pdbqt(), source=source)
    assert commands == vina == []


@pytest.mark.parametrize("limit", ["bytes", "atoms", "line"])
def test_c_prepared_output_is_bounded(rig, monkeypatch, limit):
    from src.docking import chat_receptor_validation as validation
    def after_command():
        if limit == "bytes":
            monkeypatch.setattr(validation, "MAX_PREPARATION_BYTES", 64)
        elif limit == "atoms":
            monkeypatch.setattr(validation, "MAX_PREPARATION_ATOMS", 2)
    output = pdbqt() if limit != "line" else pdbqt() + " " * 83 + "\n"
    _, commands, vina = run_case(rig, monkeypatch, output, hook=after_command)
    assert commands and vina == []


def test_c_adfr_ter_records_and_normal_autodock_typing_pass(rig, monkeypatch):
    output = pdbqt().replace(" C \n", " A \n") + "TER       4      LIG X   3 \n"
    _, _, vina = run_case(rig, monkeypatch, output, scoped=True)
    assert vina == [True]


@pytest.mark.parametrize("lines", [source_atoms()[1:2], source_atoms()[2:3], source_atoms()[1:3]])
def test_preservation_does_not_assume_protein_only_or_require_protein(rig, monkeypatch, lines):
    source = "HEADER    SYNTHETIC".ljust(62) + "1ABC\n" + "\n".join(lines) + "\nEND\n"
    _, _, vina = run_case(rig, monkeypatch, pdbqt(lines), source=source)
    assert vina == [True]


def test_c_existing_output_is_not_returncode_zero_evidence(rig, monkeypatch):
    binding = capture(rig)
    service = MolecularDockingService(config=binding.runtime_config(), chat_toolchain=binding)
    source, output = rig.root / "source.pdb", rig.root / "stale.pdbqt"
    source.write_text("HEADER    SYNTHETIC".ljust(62) + "1ABC\n" +
                      "\n".join(source_atoms()) + "\nEND\n", encoding="ascii")
    output.write_text(pdbqt(), encoding="ascii")
    monkeypatch.setattr(ADFRAdapter, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    assert service.prepare_protein(str(source), str(output)) is False
