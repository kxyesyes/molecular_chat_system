from pathlib import Path
from types import SimpleNamespace

import pytest
from rdkit import Chem
from rdkit.Geometry import Point3D

from src.docking.cavity_search import detect_cavities
from src.docking.fitdock_analysis import rank_agreement


def test_real_apo_candidates_are_ordered_and_have_editable_boxes():
    sample = Path(__file__).resolve().parents[1] / "data/samples/PDE4A_apo_6FG5.pdb"
    pockets = detect_cavities(sample.read_bytes())["cavities"]
    assert 1 <= len(pockets) <= 5
    assert pockets[0]["volume"] >= pockets[-1]["volume"]
    assert all(len(p["center"]) == 3 and all(10 <= s <= 50 for s in p["size"]) for p in pockets)


def test_no_protein_is_reported():
    with pytest.raises(ValueError):
        detect_cavities(b"END\n")


def test_agreement_preserves_receptor_frame_and_vina_ties():
    def pose(x):
        mol = Chem.MolFromSmiles("CO")
        conf = Chem.Conformer(2)
        conf.SetAtomPosition(0, Point3D(x, 0, 0))
        conf.SetAtomPosition(1, Point3D(x + 1.4, 0, 0))
        mol.AddConformer(conf)
        return mol

    rows = rank_agreement([(1, -7.0, pose(8)), (2, -7.0, pose(0))], pose(0))
    assert [r["pose"] for r in rows] == [2, 1]
    assert rows[1]["rmsd"] == pytest.approx(8.0)
    assert rows[0]["vina_rank"] == rows[1]["vina_rank"]
    with pytest.raises(ValueError):
        rank_agreement([(1, -7.0, pose(0))], Chem.MolFromSmiles("CC"))


def test_fitdock_command_uses_staged_template_receptor_and_query(tmp_path, monkeypatch):
    import subprocess
    from src.docking import fitdock_analysis
    from src.docking.pose_export import pose_sdf_from_pdbqt

    samples = Path(__file__).resolve().parents[1] / "data/samples"
    ligand = (samples / "roflumilast.pdbqt").read_text()
    # A single parser fixture exercises format conversion and argument wiring.
    vina_text = f"MODEL 1\nREMARK VINA RESULT: -6.0 0.0 0.0\n{ligand}ENDMDL\n"
    obabel = fitdock_analysis.obabel_binary()
    if not obabel:
        pytest.skip("Open Babel CLI unavailable")
    (tmp_path / "ref.sdf").write_text(pose_sdf_from_pdbqt(vina_text, 1))
    subprocess.run([obabel, "ref.sdf", "-O", "ref.mol2", "-h"], cwd=tmp_path, check=True)
    fixture = (tmp_path / "ref.mol2").read_text()
    binary = tmp_path / "FitDock.exe"
    binary.write_text("command contract fixture")
    monkeypatch.setenv("MEDCHAT_FITDOCK_BIN", str(binary))

    run = fitdock_analysis.CommandAdapter.run

    def command(adapter, args, cwd, timeout):
        if args[0] == obabel:
            return run(adapter, args, cwd=cwd, timeout=timeout)
        folder = Path(cwd)
        assert timeout == 180
        assert args[args.index("-Tprot") + 1] == args[args.index("-Qprot") + 1] == "protein.pdb"
        assert "HETATM" in (folder / "protein.pdb").read_text()
        assert (folder / args[args.index("-Tlig") + 1]).read_text() == fixture
        assert "@<TRIPOS>ATOM" in (folder / args[args.index("-Qlig") + 1]).read_text()
        (folder / args[args.index("-o") + 1]).write_text(fixture)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(fitdock_analysis.CommandAdapter, "run", command)
    comparison = fitdock_analysis.compare_with_fitdock(
        vina_text, fixture.encode(), "template", samples / "PDE4A_apo_6FG5.pdb")
    assert comparison["rows"][0]["rmsd"] < 0.001
    assert comparison["provenance"]["mode"] == "template"
