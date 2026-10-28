from pathlib import Path


def test_adfr_input_removes_only_unparameterizable_unknown_atoms(tmp_path):
    from src.docking.molecular_docking_service import MolecularDockingService

    source = tmp_path / "receptor.pdb"
    output = tmp_path / "receptor.pdbqt"
    source.write_text(
        "HEADER    TEST\n"
        "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C  \n"
        "HETATM    2  UNK UNX A 402       4.000   5.000   6.000  1.00 20.00           X  \n"
        "HETATM    3  ZN  ZN  A 403       7.000   8.000   9.000  1.00 20.00          ZN  \n"
        "END\n",
        encoding="ascii",
    )
    service = object.__new__(MolecularDockingService)

    sanitized, removed = service._sanitize_adfr_receptor_input(str(source), str(output))

    assert Path(sanitized) != source
    assert removed == 1
    text = Path(sanitized).read_text(encoding="ascii")
    assert "UNX" not in text and "           X" not in text
    assert " ZN " in text and "ATOM      1  CA" in text
    assert source.read_text(encoding="ascii").count("UNX") == 1
