from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src.docking.interaction_analysis import (
    analyze_docking_interactions,
    normalize_prolif_output,
)


def test_pdbqt_input_is_unavailable_instead_of_distance_fallback(tmp_path: Path):
    receptor = tmp_path / "receptor.pdbqt"
    ligand = tmp_path / "pose.sdf"
    receptor.write_text("ATOM      1  C   ALA A   1       0.000   0.000   0.000\n")
    ligand.write_text("pose\n  RDKit\n\n  0  0  0  0  0  0  0  0  0  0  0  0\nM  END\n$$$$\n")

    result = analyze_docking_interactions(receptor, ligand)

    assert result["status"] == "unavailable"
    assert result["reason_code"] == "unsupported_topology"
    assert result["interactions"] == []


def test_missing_analysis_inputs_are_explicitly_unavailable(tmp_path: Path):
    result = analyze_docking_interactions(
        tmp_path / "missing.pdb",
        tmp_path / "missing.sdf",
    )

    assert result["status"] == "unavailable"
    assert result["reason_code"] == "analysis_input_missing"
    assert result["interactions"] == []


def test_prolif_output_is_normalized_without_inventing_geometry():
    result = normalize_prolif_output(
        {
            ("ASP:123", "LIG:1"): {
                "HBDonor": 1,
                "Hydrophobic": 2,
            },
        }
    )

    assert {item["type"] for item in result["interactions"]} == {
        "hydrogen_bond",
        "hydrophobic",
    }
    assert all("distance" not in item for item in result["interactions"])
    assert all(item["method"] == "prolif" for item in result["interactions"])


def test_empty_prolif_output_is_a_tool_result_not_a_successful_hit():
    result = normalize_prolif_output({})

    assert result == {
        "interactions": [],
        "warnings": ["no_interactions_reported_by_prolif"],
    }


def test_pose_export_keeps_hydrogens_for_backend_analysis(tmp_path: Path):
    from src.docking.pose_export import pose_sdf_from_pdbqt

    text = "\n".join(
        [
            "REMARK SMILES C",
            "REMARK SMILES IDX 1 1",
            "MODEL 1",
            "HETATM    1  C   LIG A   1       1.000   2.000   3.000  1.00  0.00     0.000 C",
            "ENDMDL",
        ]
    )
    sdf = pose_sdf_from_pdbqt(text, keep_hydrogens=True)

    assert "V2000" in sdf
    assert int(sdf.splitlines()[3].split()[0]) == 5


def test_prolif_success_is_structured_and_tool_derived(tmp_path: Path, monkeypatch):
    receptor = tmp_path / "receptor.pdb"
    ligand = tmp_path / "pose.sdf"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("sdf\n", encoding="utf-8")

    class FakeUniverse:
        def __init__(self, _path):
            self.atoms = [SimpleNamespace(element="H", name="H1")]

    class FakeFrame:
        index = [0]

        def iloc(self):
            return self

        def to_dict(self):
            return {
                ("ASP:123", "LIG:1"): {"HBDonor": 1},
            }

    class FakeFingerprint:
        def __init__(self, **_kwargs):
            self.ran = False

        def run_from_iterable(self, _poses, _protein):
            self.ran = True

        def to_dataframe(self):
            return SimpleNamespace(index=[0], iloc=[FakeFrame()])

    fake_mda = SimpleNamespace(Universe=FakeUniverse)
    fake_prolif = SimpleNamespace(
        __version__="test",
        Molecule=SimpleNamespace(from_mda=lambda _universe: object()),
        sdf_supplier=lambda _path: [object()],
        Fingerprint=FakeFingerprint,
    )
    monkeypatch.setitem(sys.modules, "MDAnalysis", fake_mda)
    monkeypatch.setitem(sys.modules, "prolif", fake_prolif)
    monkeypatch.setattr("src.docking.interaction_analysis._dependency_available", lambda _: True)

    result = analyze_docking_interactions(receptor, ligand)

    assert result["status"] == "success"
    assert result["provenance"]["tool_derived"] is True
    assert result["provenance"]["analyzer"] == "prolif"
    assert result["interactions"][0]["type"] == "hydrogen_bond"


def test_real_prolif_adapter_smoke_with_explicit_hydrogens(tmp_path: Path):
    pytest.importorskip("MDAnalysis")
    pytest.importorskip("prolif")
    from rdkit import Chem
    from rdkit.Chem import AllChem

    receptor = tmp_path / "receptor.pdb"
    receptor.write_text(
        "\n".join(
            [
                "ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N",
                "ATOM      2  H   ALA A   1       0.900   0.000   0.000  1.00  0.00           H",
                "ATOM      3  CA  ALA A   1       1.450   0.000   0.000  1.00  0.00           C",
                "ATOM      4  C   ALA A   1       2.000   1.300   0.000  1.00  0.00           C",
                "ATOM      5  O   ALA A   1       3.200   1.300   0.000  1.00  0.00           O",
                "CONECT    1    2    3",
                "CONECT    2    1",
                "CONECT    3    1    4",
                "CONECT    4    3    5",
                "CONECT    5    4",
                "TER",
                "END",
            ]
        ),
        encoding="utf-8",
    )
    molecule = Chem.AddHs(Chem.MolFromSmiles("CO"))
    assert AllChem.EmbedMolecule(molecule, randomSeed=7) == 0
    Chem.MolToMolFile(molecule, str(tmp_path / "pose.sdf"))

    result = analyze_docking_interactions(receptor, tmp_path / "pose.sdf")

    assert result["status"] == "success", result
    assert result["provenance"]["tool_derived"] is True
    assert result["provenance"]["analyzer"] == "prolif"
