from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from src.docking.interaction_analysis import (
    analyze_docking_interactions,
    normalize_prolif_output,
    resolve_analysis_inputs,
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


def test_analysis_input_resolution_skips_missing_candidate_names(tmp_path: Path):
    receptor = tmp_path / "receptor_analysis.pdb"
    ligand = tmp_path / "pose_1.sdf"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("sdf\n", encoding="utf-8")

    resolved_receptor, resolved_ligand = resolve_analysis_inputs(tmp_path, 1)

    assert resolved_receptor == receptor
    assert resolved_ligand == ligand


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
        "geometry_status": "unavailable",
    }


def test_prolif_metadata_preserves_tool_derived_geometry_and_atom_indices():
    result = normalize_prolif_output(
        {
            ("ASP:123", "LIG:1"): {
                "HBDonor": [
                    {
                        "distance": 2.91,
                        "angle": 157.4,
                        "protein_indices": [4],
                        "ligand_indices": [0, 1],
                    }
                ]
            }
        }
    )

    assert result["geometry_status"] == "available"
    assert result["interactions"] == [
        {
            "type": "hydrogen_bond",
            "protein_residue": "ASP:123",
            "ligand_residue": "LIG:1",
            "count": 1,
            "distance_angstrom": 2.91,
            "angle_degrees": 157.4,
            "protein_atom_indices": [4],
            "ligand_atom_indices": [0, 1],
            "method": "prolif",
            "quality": "tool_derived",
        }
    ]


def test_invalid_geometry_is_omitted_and_reported_as_unavailable():
    result = normalize_prolif_output(
        {
            ("ASP:123", "LIG:1"): {
                "HBDonor": [
                    {
                        "distance": float("nan"),
                        "angle": -5,
                        "protein_indices": ["bad"],
                    }
                ]
            }
        }
    )

    assert result["geometry_status"] == "unavailable"
    assert "invalid_geometry_metadata" in result["warnings"]
    assert "distance_angstrom" not in result["interactions"][0]
    assert "angle_degrees" not in result["interactions"][0]
    assert "protein_atom_indices" not in result["interactions"][0]


def test_count_only_output_remains_compatible_but_geometry_is_unavailable():
    result = normalize_prolif_output(
        {("ASP:123", "LIG:1"): {"HBDonor": 2}}
    )

    assert result["geometry_status"] == "unavailable"
    assert result["interactions"][0]["count"] == 2
    assert "distance_angstrom" not in result["interactions"][0]


def test_prolif_frame_metadata_normalizes_ligand_first_pairs_and_multiple_occurrences():
    result = normalize_prolif_output(
        {
            0: {
                ("QNB1.G", "ASP129.A"): {
                    "HBDonor": (
                        {
                            "indices": {"ligand": (0,), "protein": (4,)},
                            "distance": 2.8,
                            "DHA_angle": 151.0,
                        },
                        {
                            "indices": {"ligand": (1,), "protein": (5,)},
                            "distance": 3.0,
                            "DHA_angle": 149.0,
                        },
                    )
                }
            }
        }, pair_order="ligand_protein"
    )

    assert result["geometry_status"] == "available"
    assert len(result["interactions"]) == 1
    item = result["interactions"][0]
    assert item["count"] == 2
    assert item["protein_residue"] == "ASP129.A"
    assert item["ligand_residue"] == "QNB1.G"
    assert [entry["distance_angstrom"] for entry in item["geometry_occurrences"]] == [2.8, 3.0]


def test_known_pication_is_mapped_without_an_unknown_interaction_warning():
    result = normalize_prolif_output({("ASP1", "LIG1"): {"PiCation": 1}})
    assert result["interactions"][0]["type"] == "cation_pi"
    assert "unsupported_interaction_types" not in result


def test_unknown_prolif_interaction_is_explicitly_unavailable():
    result = normalize_prolif_output({("ASP1", "LIG1"): {"FutureInteraction": 1}})
    assert result["interactions"] == []
    assert result["unsupported_interaction_types"] == ["FutureInteraction"]
    assert "unsupported_interaction_type" in result["warnings"]


@pytest.mark.parametrize("value", [object(), "bad", [None], [1], float("inf"), -1, 1.5])
def test_malformed_occurrences_never_create_interactions(value):
    result = normalize_prolif_output({("ASP1", "LIG1"): {"HBDonor": value}})
    assert result["interactions"] == []
    assert "invalid_geometry_metadata" in result["warnings"]


def test_indices_alone_are_preserved_as_available_geometry_metadata():
    result = normalize_prolif_output({("ASP1", "LIG1"): {
        "HBDonor": [{"indices": {"ligand": (1,), "protein": (2,)}}]
    }})
    assert result["geometry_status"] == "available"


def test_distinct_angles_are_not_collapsed_and_parent_indices_keep_scope():
    result = normalize_prolif_output({("ASP1", "LIG1"): {"PiStacking": [{
        "plane_angle": 25.0, "normal_to_centroid_angle": 30.0,
        "parent_indices": {"ligand": (12,), "protein": (120,)},
    }]}})
    item = result["interactions"][0]
    assert item["angles_degrees"] == {"plane_angle": 25.0, "normal_to_centroid_angle": 30.0}
    assert "angle_degrees" not in item
    assert item["parent_atom_indices"] == {"ligand": [12], "protein": [120]}


@pytest.mark.parametrize("field,value", [
    ("distance", 0), ("distance", -1), ("distance", 21),
    ("distance", True), ("distance", float("inf")), ("distance", 10**400),
    ("angle", -1), ("angle", 361), ("angle", float("nan")),
    ("indices", {"ligand": (), "protein": (False,)}),
])
def test_invalid_fields_warn_without_geometry_claim(field, value):
    result = normalize_prolif_output({("ASP1", "LIG1"): {"HBDonor": [{field: value}]}})
    assert result["geometry_status"] == "unavailable"
    assert "invalid_geometry_metadata" in result["warnings"]


def test_conflicting_geometry_aliases_are_not_silently_chosen():
    result = normalize_prolif_output({("ASP1", "LIG1"): {"HBDonor": [{
        "distance": 2.0, "distance_angstrom": 3.0,
    }]}})
    assert result["geometry_status"] == "unavailable"
    assert "distance_angstrom" not in result["interactions"][0]
    assert "invalid_geometry_metadata" in result["warnings"]


def test_valid_geometry_alias_with_invalid_alias_emits_warning():
    result = normalize_prolif_output({("ASP1", "LIG1"): {"HBDonor": [{
        "distance": 2.0, "distance_angstrom": "bad",
    }]}})
    assert result["geometry_status"] == "available"
    assert result["interactions"][0]["distance_angstrom"] == 2.0
    assert "invalid_geometry_metadata" in result["warnings"]


@pytest.fixture
def analyzer_inputs(tmp_path, monkeypatch):
    receptor = tmp_path / "receptor.pdb"
    ligand = tmp_path / "pose.sdf"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("sdf\n", encoding="utf-8")
    fake_prolif = SimpleNamespace(
        __version__="test",
        Molecule=SimpleNamespace(from_mda=lambda _: object()),
        sdf_supplier=lambda _: [object()],
    )
    monkeypatch.setitem(sys.modules, "MDAnalysis", SimpleNamespace(
        Universe=lambda _: SimpleNamespace(atoms=[SimpleNamespace(element="H", name="H1")])
    ))
    monkeypatch.setitem(sys.modules, "prolif", fake_prolif)
    monkeypatch.setattr("src.docking.interaction_analysis._dependency_available", lambda _: True)
    return receptor, ligand, fake_prolif


@pytest.mark.parametrize("metadata", [None, [], {"bad": {}}, {("L", "P"): {"HBDonor": 1}},
    {("L", "P"): {"HBDonor": (None,)}}])
def test_runtime_rejects_malformed_metadata(analyzer_inputs, metadata):
    receptor, ligand, plf = analyzer_inputs
    plf.Fingerprint = lambda **_: SimpleNamespace(generate=lambda *args, **kwargs: metadata)
    result = analyze_docking_interactions(receptor, ligand)
    assert result["status"] == "unavailable"
    assert result["reason_code"] == "geometry_metadata_unavailable"
    assert result["interactions"] == []


def test_runtime_empty_metadata_is_completed_without_hits(analyzer_inputs):
    receptor, ligand, plf = analyzer_inputs
    plf.Fingerprint = lambda **_: SimpleNamespace(generate=lambda *args, **kwargs: {})
    result = analyze_docking_interactions(receptor, ligand)
    assert result["status"] == "success"
    assert result["geometry_status"] == "unavailable"
    assert result["interactions"] == []


def test_runtime_unknown_interaction_is_unavailable(analyzer_inputs):
    receptor, ligand, plf = analyzer_inputs
    plf.Fingerprint = lambda **_: SimpleNamespace(
        generate=lambda *args, **kwargs: {("L", "P"): {"FutureInteraction": ({},)}}
    )
    result = analyze_docking_interactions(receptor, ligand)
    assert result["status"] == "unavailable"
    assert result["reason_code"] == "unsupported_interaction_type"


def test_runtime_old_generate_api_is_unavailable(analyzer_inputs):
    receptor, ligand, plf = analyzer_inputs
    plf.Fingerprint = lambda **_: SimpleNamespace(generate=lambda lig, prot: {})
    result = analyze_docking_interactions(receptor, ligand)
    assert result["reason_code"] == "geometry_metadata_unavailable"


def test_runtime_analyzer_exception_is_sanitized(analyzer_inputs):
    receptor, ligand, plf = analyzer_inputs
    def fail(*args, **kwargs):
        raise RuntimeError("private-path-secret")
    plf.Fingerprint = lambda **_: SimpleNamespace(generate=fail)
    result = analyze_docking_interactions(receptor, ligand)
    assert result["reason_code"] == "analysis_execution_failed"
    assert "private-path-secret" not in str(result)


def test_dependency_missing_is_unavailable(analyzer_inputs, monkeypatch):
    receptor, ligand, _ = analyzer_inputs
    monkeypatch.setattr("src.docking.interaction_analysis._dependency_available", lambda _: False)
    result = analyze_docking_interactions(receptor, ligand)
    assert result["reason_code"] == "dependency_missing"
    assert result["geometry_status"] == "unavailable"


def test_mol2_uses_matching_supplier(analyzer_inputs):
    receptor, ligand, plf = analyzer_inputs
    mol2 = ligand.with_suffix(".mol2")
    mol2.write_text("mol2\n", encoding="utf-8")
    selected = object()
    plf.mol2_supplier = lambda _: [selected]
    def generate(lig, prot, *, metadata):
        assert lig is selected
        return {}
    plf.Fingerprint = lambda **_: SimpleNamespace(generate=generate)
    result = analyze_docking_interactions(receptor, mol2)
    assert result["status"] == "success"


def test_metadata_api_absence_returns_explicit_unavailable_result(tmp_path, monkeypatch):
    receptor = tmp_path / "receptor.pdb"
    ligand = tmp_path / "pose.sdf"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("sdf\n", encoding="utf-8")

    class FakeFingerprint:
        def __init__(self, **_kwargs):
            pass

        def run_from_iterable(self, _poses, _protein):
            pass

        def to_dataframe(self):
            return SimpleNamespace(index=[0], iloc=[SimpleNamespace(to_dict=lambda: {})])

    fake_mda = SimpleNamespace(
        Universe=lambda _path: SimpleNamespace(atoms=[SimpleNamespace(element="H", name="H1")])
    )
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

    assert result["status"] == "unavailable"
    assert result["reason_code"] == "geometry_metadata_unavailable"
    assert result["geometry_status"] == "unavailable"


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
            self.ifp = {
                0: {
                    ("QNB:1", "ASP:123"): {
                        "HBDonor": [
                            {
                                "distance": 2.9,
                                "angle": 156.0,
                                "protein_indices": [4],
                                "ligand_indices": [0],
                            }
                        ]
                    }
                }
            }

        def run_from_iterable(self, _poses, _protein):
            self.ran = True

        def generate(self, _pose, _protein, *, metadata):
            assert metadata is True
            return self.ifp[0]

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
    assert result["provenance"]["metadata_mode"] == "metadata"
    assert result["geometry_status"] == "available"
    assert result["interactions"][0]["type"] == "hydrogen_bond"
    assert result["interactions"][0]["protein_residue"] == "ASP:123"


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
