"""Independent synthetic format contracts; NOT authentication or scoring evidence."""

import importlib
import importlib.util
import hashlib
import json
import pytest


def api():
    name = "src.docking.rcsb_source"
    assert importlib.util.find_spec(name) is not None, "P6 evidence compiler missing"
    module = importlib.import_module(name)
    assert callable(getattr(module, "compile_rcsb_source", None))
    return module


def test_compiler_callable():
    api()


def test_components_callable():
    assert callable(getattr(api(), "required_rcsb_components", None))


def loop(category, fields, rows):
    return "loop_\n" + "\n".join("_" + category + "." + f for f in fields.split()) + "\n" + "\n".join(rows) + "\n#\n"


def revline(number=1, date="01-JAN-24", continuation="  "):
    return f"REVDAT {number:3d}{continuation} {date} 1ABC    {0 if number == 1 else 1}"


@pytest.fixture
def sample():
    from tests.test_docking_pocket_evidence import atom_line, residue
    # Two independently authored representations. Expected chemistry below is
    # not extracted from this PDB, nor from any production parser.
    pdb = ("\n".join([
        "HEADER    SYNTHETIC ONLY".ljust(62) + "1ABC", revline(),
        atom_line(1, "N", "N", (1, 2, 3), residue("ALA", 10), "ATOM"),
        atom_line(2, "C", "C", (2, 3, 4), residue("ALA", 10), "ATOM"),
        atom_line(3, "N", "N", (3, 4, 5), residue("ALA", 11), "ATOM"),
        atom_line(4, "OXT", "O", (4, 5, 6), residue("ALA", 11), "ATOM"),
        atom_line(7, "C1", "C", (5, 6, 7), residue("LIG", 20)),
        atom_line(8, "O1", "O", (6, 7, 8), residue("LIG", 20)),
        atom_line(9, "O", "O", (7, 8, 9), residue("HOH", 40, "Z"), occupancy=0),
        "END", ""])).encode("ascii")
    atoms = {
        "ALA": ["ALA N N N N N", "ALA C C N N N", "ALA O O N N N", "ALA OXT O N N Y", "ALA H H N N Y"],
        "LIG": ["LIG C1 C N N N", "LIG O1 O N N N", "LIG H1 H N N N"],
        "HOH": ["HOH O O N N N", "HOH H1 H N N N", "HOH H2 H N N N"],
    }
    bonds = {"ALA": ["ALA N C SING N N", "ALA C O DOUB N N", "ALA C OXT SING N N", "ALA N H SING N N"],
             "LIG": ["LIG C1 O1 SING N N", "LIG C1 H1 SING N N"],
             "HOH": ["HOH O H1 SING N N", "HOH O H2 SING N N"]}
    af = "comp_id atom_id type_symbol pdbx_aromatic_flag pdbx_stereo_config pdbx_leaving_atom_flag"
    bf = "comp_id atom_id_1 atom_id_2 value_order pdbx_aromatic_flag pdbx_stereo_config"
    types = {"ALA": "'L-peptide linking'", "LIG": "non-polymer", "HOH": "non-polymer"}
    components = {key: (f"data_{key}\n_chem_comp.id {key}\n_chem_comp.type {types[key]}\n"
                         "_chem_comp.pdbx_release_status REL\n_chem_comp.pdbx_modified_date 2023-01-01\n"
                         + loop("chem_comp_atom", af, atoms[key]) + loop("chem_comp_bond", bf, bonds[key])).encode()
                  for key in atoms}
    cif = "data_1ABC\n_entry.id 1ABC\n"
    cif += loop("pdbx_audit_revision_history", "ordinal data_content_type major_revision minor_revision revision_date",
                ["1 'Structure model' 1 0 2024-01-01"])
    cif += loop("entity", "id type", ["1 polymer", "2 non-polymer", "3 water"])
    cif += loop("entity_poly", "entity_id type pdbx_strand_id", ["1 'polypeptide(L)' A"])
    cif += loop("entity_poly_seq", "entity_id num mon_id hetero", ["1 1 ALA n", "1 2 ALA n"])
    cif += loop("struct_asym", "id entity_id", ["A 1", "B 2", "C 3"])
    cif += loop("pdbx_poly_seq_scheme", "asym_id entity_id seq_id mon_id pdb_seq_num auth_seq_num pdb_mon_id auth_mon_id pdb_strand_id pdb_ins_code hetero",
                ["A 1 1 ALA 10 10 ALA ALA A . n", "A 1 2 ALA 11 11 ALA ALA A . n"])
    cif += loop("struct_ref", "id entity_id db_name db_code pdbx_db_accession", ["1 1 UNP SYNTH_HUMAN P06276"])
    cif += loop("struct_ref_seq", "align_id ref_id pdbx_PDB_id_code pdbx_strand_id seq_align_beg seq_align_end db_align_beg db_align_end pdbx_db_accession",
                ["1 1 1ABC A 1 2 1 2 P06276"])
    cif += loop("entity_src_gen", "entity_id pdbx_gene_src_ncbi_taxonomy_id pdbx_host_org_ncbi_taxonomy_id", ["1 9606 10090"])
    cif += loop("chem_comp", "id type", [key + " " + types[key] for key in atoms])
    cif += loop("chem_comp_atom", af, [row for key in atoms for row in atoms[key]])
    cif += loop("chem_comp_bond", bf, [row for key in bonds for row in bonds[key]])
    cif += loop("pdbx_entity_instance_feature", "ordinal comp_id asym_id auth_asym_id auth_seq_num feature_type",
                ["1 LIG B A 20 'SUBJECT OF INVESTIGATION'"])
    cif += loop("atom_site", "group_PDB id type_symbol label_atom_id label_alt_id label_comp_id label_asym_id label_entity_id label_seq_id pdbx_PDB_ins_code Cartn_x Cartn_y Cartn_z occupancy B_iso_or_equiv pdbx_formal_charge auth_seq_id auth_comp_id auth_asym_id auth_atom_id pdbx_PDB_model_num", [
        "ATOM 1 N N . ALA A 1 1 ? 1.000 2.000 3.000 1.00 20.00 ? 10 ALA A N 1",
        "ATOM 2 C C . ALA A 1 1 ? 2.000 3.000 4.000 1.00 20.00 ? 10 ALA A C 1",
        "ATOM 3 N N . ALA A 1 2 ? 3.000 4.000 5.000 1.00 20.00 ? 11 ALA A N 1",
        "ATOM 4 O OXT . ALA A 1 2 ? 4.000 5.000 6.000 1.00 20.00 ? 11 ALA A OXT 1",
        "HETATM 5 C C1 . LIG B 2 . ? 5.000 6.000 7.000 1.00 20.00 ? 20 LIG A C1 1",
        "HETATM 6 O O1 . LIG B 2 . ? 6.000 7.000 8.000 1.00 20.00 ? 20 LIG A O1 1",
        "HETATM 7 O O . HOH C 3 . ? 7.000 8.000 9.000 0.00 20.00 ? 40 HOH Z O 1",
    ])
    return pdb, cif.encode(), components


def compile_sample(sample, **kwargs):
    return api().compile_rcsb_source(*sample, pdb_id="1ABC", target=kwargs.get("target", {"uniprot_id": "P06276", "taxonomy_id": 9606}))


def rejected(result):
    assert result["ok"] is False
    assert result["code"] == "RCSB_SOURCE_INVALID"
    assert set(result) == {"ok", "code", "reason"}
    assert result["reason"] in api().SAFE_REASONS


def test_complete_candidate_and_component_scan(sample):
    assert api().required_rcsb_components(sample[1], pdb_id="1ABC") == ("ALA", "HOH", "LIG")
    result = compile_sample(sample)
    assert result["ok"], result
    source, evidence = result["source"], result["evidence"]
    assert set(result) == {"ok", "source", "evidence"}
    assert source["sha256"] == hashlib.sha256(sample[0]).hexdigest()
    assert [r["role"] for r in source["residues"]] == ["protein", "protein", "deposited_ligand", "water"]
    assert [a["serial"] for r in source["residues"] for a in r["atoms"]] == [1, 2, 3, 4, 7, 8, 9]
    assert {a["name"] for a in source["residues"][0]["required_heavy_atoms"]} == {"N", "C", "O"}
    assert {a["name"] for a in source["residues"][1]["required_heavy_atoms"]} == {"N", "C", "O", "OXT"}
    assert evidence["schema_version"] == "rcsb-source-evidence-v1"
    assert evidence["latest_revision_date"] == "2024-01-01"
    assert evidence["revision_version"] == "1.0"
    from src.docking.receptor_projection_source import validate_projection_source
    from src.docking.receptor_projection_pdb import parse_original_pdb
    assert validate_projection_source(parse_original_pdb(sample[0]), source).source_authentication == "caller_responsibility"


@pytest.mark.parametrize("old,new", [
    (b"_entry.id 1ABC", b"_entry.id 2ABC"),
    (b"1 'Structure model' 1 0 2024-01-01", b"1 'Structure model' 1 0 2024-01-02"),
    (b"1 9606 10090", b"1 10090 9606"),
    (b"P06276", b"Q12345"),
    (b"1.000 2.000 3.000", b"1.001 2.000 3.000"),
    (b"0.00 20.00 ? 40", b"1.00 20.00 ? 40"),
    (b"5.000 6.000 7.000", b"5.0001 6.000 7.000"),
    (b"5.000 6.000 7.000", b"NaN 6.000 7.000"),
    (b"ALA N N N N N", b"ALA N C N N N"),
    (b"LIG C1 O1 SING N N", b"LIG C1 O1 DOUB N N"),
    (b"A 1 2 ALA 11 11 ALA ALA A . n", b"A 1 2 ALA 12 11 ALA ALA A . n"),
    (b"1 LIG B A 20", b"1 LIG B A 21"),
])
def test_negative_evidence(sample, old, new):
    pdb, cif, components = sample
    assert old in cif
    rejected(compile_sample((pdb, cif.replace(old, new), components)))


@pytest.mark.parametrize("suffix", [b"data_extra\n_x.y 1\n", b"save_bad\n_x.y 1\nsave_\n", b"_entry.id 1ABC\n"])
def test_invalid_document(sample, suffix):
    rejected(compile_sample((sample[0], sample[1] + suffix, sample[2])))


def test_hidden_water_cannot_be_omitted(sample):
    pdb, cif, components = sample
    cif = b"\n".join(line for line in cif.split(b"\n") if not line.startswith(b"HETATM 7 "))
    rejected(compile_sample((pdb, cif, components)))


def test_unknown_ligand_role_not_promoted(sample):
    pdb, cif, components = sample
    result = compile_sample((pdb, cif.replace(b"SUBJECT OF INVESTIGATION", b"OTHER FEATURE"), components))
    assert result["ok"], result
    assert result["source"]["residues"][2]["role"] == "unproven"


@pytest.mark.parametrize("replacement", [b"2025-13-01", b"?", b"2023-02-31"])
def test_component_date(sample, replacement):
    sample[2]["LIG"] = sample[2]["LIG"].replace(b"2023-01-01", replacement)
    rejected(compile_sample(sample))


def test_newer_ccd_metadata_date_is_allowed_for_identical_chemistry(sample):
    sample[2]["LIG"] = sample[2]["LIG"].replace(b"2023-01-01", b"2025-01-01")
    result = compile_sample(sample)
    assert result["ok"], result
    assert next(c for c in result["evidence"]["components"] if c["id"] == "LIG")["date"] == "2025-01-01"


def test_entry_and_ccd_leaving_atom_flags_must_match(sample):
    pdb, cif, components = sample
    cif = cif.replace(b"LIG O1 O N N N", b"LIG O1 O N N Y")
    result = compile_sample((pdb, cif, components))
    rejected(result)
    assert result["reason"] == "component_invalid"


def with_component_atom_charge(raw, charge, atom="LIG O1"):
    """Independently add synthetic charge cells, or omit the entire column."""
    if charge is None:
        return raw
    header = b"_chem_comp_atom.pdbx_leaving_atom_flag\n"
    assert raw.count(header) == 1
    before, table = raw.split(header)
    rows, after = table.split(b"#\n", 1)
    prefix = atom.encode() + b" "
    assert sum(row.startswith(prefix) for row in rows.splitlines()) == 1
    rows = b"\n".join(row + b" " + (charge.encode() if row.startswith(prefix) else b"0")
                       for row in rows.splitlines())
    return before + header + b"_chem_comp_atom.charge\n" + rows + b"\n#\n" + after


@pytest.mark.parametrize("atom", ["LIG O1", "LIG H1"])
@pytest.mark.parametrize("entry_charge,ccd_charge", [
    ("0", "+1"), ("0", "-1"), ("+1", "0"), ("-1", "0"),
    ("+1", "-1"), ("-1", "+1"),
])
def test_component_atom_charge_disagreement_is_rejected(sample, atom, entry_charge, ccd_charge):
    pdb, cif, components = sample
    cif = with_component_atom_charge(cif, entry_charge, atom)
    components["LIG"] = with_component_atom_charge(components["LIG"], ccd_charge, atom)
    result = compile_sample((pdb, cif, components))
    rejected(result)
    assert result["reason"] == "component_invalid"


@pytest.mark.parametrize("entry_charge,ccd_charge", [
    ("0", "0"), ("+0", "-0"), ("1", "+1"), ("+1", "1"),
    ("-1", "-1"), ("+2", "2"), ("-2", "-02"),
])
def test_component_atom_charge_signed_integer_agreement(sample, entry_charge, ccd_charge):
    pdb, cif, components = sample
    cif = with_component_atom_charge(cif, entry_charge)
    components["LIG"] = with_component_atom_charge(components["LIG"], ccd_charge)
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    assert next(c for c in result["evidence"]["components"] if c["id"] == "LIG")["sha256"] == hashlib.sha256(components["LIG"]).hexdigest()


@pytest.mark.parametrize("unknown", [None, "?", "."])
@pytest.mark.parametrize("other", [None, "?", ".", "0", "+1", "-1"])
@pytest.mark.parametrize("unknown_side", ["entry", "ccd"])
def test_component_atom_charge_unknown_is_not_assumed_zero(sample, unknown, other, unknown_side):
    pdb, cif, components = sample
    entry_charge, ccd_charge = (unknown, other) if unknown_side == "entry" else (other, unknown)
    cif = with_component_atom_charge(cif, entry_charge)
    components["LIG"] = with_component_atom_charge(components["LIG"], ccd_charge)
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result


@pytest.mark.parametrize("charge", ["''", "1.0", "1e0", "NaN", "1+", "--1", "+-1", "abc", "10000000000"])
@pytest.mark.parametrize("side", ["entry", "ccd"])
def test_component_atom_charge_malformed_is_rejected_even_when_other_missing(sample, charge, side):
    pdb, cif, components = sample
    if side == "entry":
        cif = with_component_atom_charge(cif, charge)
    else:
        components["LIG"] = with_component_atom_charge(components["LIG"], charge)
    result = compile_sample((pdb, cif, components))
    rejected(result)
    assert result["reason"] == "component_invalid"


@pytest.mark.parametrize("old,new", [
    (b"LIG O1 O N N N", b"LIG O1 O N N Y"),
    (b"LIG O1 O N N N", b"LIG O1 N N N N"),
    (b"LIG C1 O1 SING N N", b"LIG C1 O1 DOUB N N"),
])
def test_newer_ccd_metadata_does_not_allow_chemical_drift(sample, old, new):
    sample[2]["LIG"] = sample[2]["LIG"].replace(b"2023-01-01", b"2025-01-01").replace(old, new)
    result = compile_sample(sample)
    rejected(result)
    assert result["reason"] == "component_invalid"


def test_exact_component_set(sample):
    del sample[2]["HOH"]
    rejected(compile_sample(sample))


def test_version_exact(sample, monkeypatch):
    import gemmi
    monkeypatch.setattr(gemmi, "__version__", "0.7.6")
    rejected(compile_sample(sample))


def test_native_types_no_hooks(sample):
    class Evil(dict):
        def __iter__(self):
            raise AssertionError("hook executed")
    rejected(compile_sample((sample[0], sample[1], Evil(sample[2]))))
    rejected(compile_sample(sample, target=Evil(uniprot_id="P06276", taxonomy_id=9606)))
    rejected(compile_sample(sample, target={"uniprot_id": "P06276", "taxonomy_id": True}))
    rejected(compile_sample((bytearray(sample[0]), sample[1], sample[2])))


def test_resource_cap_before_parse(sample, monkeypatch):
    import gemmi
    def forbidden(*args):
        raise AssertionError("parse before admission")
    monkeypatch.setattr(gemmi.cif, "read_string", forbidden)
    rejected(compile_sample((sample[0], b"x" * (16 * 1024 * 1024 + 1), sample[2])))
    rejected(compile_sample((sample[0], sample[1], {"ALA": b"x" * (1024 * 1024 + 1)})))


def test_capture_mappings_before_native_parse(sample, monkeypatch):
    import gemmi
    target = {"uniprot_id": "P06276", "taxonomy_id": 9606}
    original = gemmi.cif.read_string
    def mutating_parse(value):
        sample[2].clear()
        target["uniprot_id"] = "Q12345"
        return original(value)
    monkeypatch.setattr(gemmi.cif, "read_string", mutating_parse)
    result = compile_sample(sample, target=target)
    assert result["ok"], result
    assert result["source"]["target"]["uniprot_id"] == "P06276"


def test_known_unselected_protein_keeps_own_identity(sample):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = sample
    pdb = pdb.replace(b"END\n", (atom_line(10, "N", "N", (8, 9, 10), residue("ALA", 1, "B"), "ATOM") + "\nEND\n").encode())
    replacements = [
        (b"3 water\n", b"3 water\n4 polymer\n"),
        (b"1 'polypeptide(L)' A\n", b"1 'polypeptide(L)' A\n4 'polypeptide(L)' B\n"),
        (b"1 2 ALA n\n", b"1 2 ALA n\n4 1 ALA n\n"),
        (b"C 3\n", b"C 3\nD 4\n"),
        (b"A 1 2 ALA 11 11 ALA ALA A . n\n", b"A 1 2 ALA 11 11 ALA ALA A . n\nD 4 1 ALA 1 1 ALA ALA B . n\n"),
        (b"1 1 UNP SYNTH_HUMAN P06276\n", b"1 1 UNP SYNTH_HUMAN P06276\n2 4 UNP SYNTH_MOUSE Q12345\n"),
        (b"1 1 1ABC A 1 2 1 2 P06276\n", b"1 1 1ABC A 1 2 1 2 P06276\n2 2 1ABC B 1 1 1 1 Q12345\n"),
        (b"1 9606 10090\n", b"1 9606 10090\n4 10090 9606\n"),
        (b"HETATM 7 O O . HOH C 3 . ? 7.000 8.000 9.000 0.00 20.00 ? 40 HOH Z O 1\n",
         b"HETATM 7 O O . HOH C 3 . ? 7.000 8.000 9.000 0.00 20.00 ? 40 HOH Z O 1\nATOM 8 N N . ALA D 4 1 ? 8.000 9.000 10.000 1.00 20.00 ? 1 ALA B N 1\n"),
    ]
    for old, new in replacements:
        assert old in cif
        cif = cif.replace(old, new)
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    assert [(p["chain"], p["uniprot_id"], p["taxonomy_id"]) for p in result["source"]["proteins"]] == [("A", "P06276", 9606), ("B", "Q12345", 10090)]


def conn_table(rows):
    fields = ("id conn_type_id ptnr1_label_asym_id ptnr1_label_comp_id ptnr1_label_seq_id ptnr1_label_atom_id "
              "pdbx_ptnr1_label_alt_id ptnr1_auth_asym_id ptnr1_auth_seq_id pdbx_ptnr1_PDB_ins_code ptnr1_symmetry "
              "ptnr2_label_asym_id ptnr2_label_comp_id ptnr2_label_seq_id ptnr2_label_atom_id "
              "pdbx_ptnr2_label_alt_id ptnr2_auth_asym_id ptnr2_auth_seq_id pdbx_ptnr2_PDB_ins_code ptnr2_symmetry "
              "pdbx_value_order pdbx_role pdbx_leaving_atom_flag")
    return loop("struct_conn", fields, rows).encode()


def link(first=("N", " ", "ALA", "A", 10), second=("C1", " ", "LIG", "A", 20)):
    value = list("LINK  ".ljust(80))
    for start, (name, alt, comp, chain, seq) in ((12, first), (42, second)):
        value[start:start+15] = f"{name:>4}{alt}{comp:>3} {chain}{seq:4d} "
    value[59:65] = "  1555"
    value[66:72] = "  1555"
    return "".join(value).encode() + b"\n"


def test_intra_ccd_bond_and_multiplicity(sample):
    pdb, cif, components = sample
    result = compile_sample((pdb.replace(b"END\n", b"CONECT    7    8\nEND\n"), cif, components))
    assert result["ok"], result
    assert result["source"]["connections"][0]["kind"] == "covalent"
    rejected(compile_sample((pdb.replace(b"END\n", b"CONECT    7    8    8\nEND\n"), cif, components)))


def test_exact_interresidue_link(sample):
    pdb, cif, components = sample
    table = conn_table(["c1 covale A ALA 1 N . A 10 ? 1_555 B LIG . C1 . A 20 ? 1_555 sing . none"])
    linked = pdb.replace(b"ATOM      1", link() + b"ATOM      1")
    result = compile_sample((linked, cif+table, components))
    assert result["ok"], result
    assert result["source"]["connections"][0]["kind"] == "covalent"
    rejected(compile_sample((pdb, cif+table, components)))
    rejected(compile_sample((linked, cif, components)))
    rejected(compile_sample((linked, cif+table.replace(b"1_555", b"2_555"), components)))
    rejected(compile_sample((linked, cif+table.replace(b"covale", b"unsupported"), components)))


@pytest.mark.parametrize("variant_seq,variant_auth", [(1, 10), (2, 11)])
def test_missing_residues_and_variant_are_carried(sample, variant_seq, variant_auth):
    pdb, cif, components = sample
    pdb = b"\n".join(row for row in pdb.split(b"\n") if not row.startswith((b"ATOM      3", b"ATOM      4")))
    cif = b"\n".join(row for row in cif.split(b"\n") if not row.startswith((b"ATOM 3 ", b"ATOM 4 ")))
    cif += loop("pdbx_unobs_or_zero_occ_residues", "id polymer_flag occupancy_flag PDB_model_num auth_asym_id auth_comp_id auth_seq_id PDB_ins_code label_asym_id label_comp_id label_seq_id",
                ["1 Y 1 1 A ALA 11 ? A ALA 2"]).encode()
    cif += loop("struct_ref_seq_dif", "pdbx_ordinal align_id seq_num mon_id db_mon_id pdbx_pdb_strand_id pdbx_pdb_id_code pdbx_seq_db_accession_code pdbx_seq_db_name pdbx_auth_seq_num pdbx_pdb_ins_code pdbx_seq_db_seq_num details",
                [f"1 1 {variant_seq} ALA GLY A 1ABC P06276 UNP {variant_auth} ? {variant_seq} 'engineered mutation'"]).encode()
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    assert {row["kind"] for row in result["source"]["limitations"]} == {"missing_residues", "sequence_variant"}
    assert not any(row["identity"]["resseq"] == 11 for row in result["source"]["residues"])
    assert result["evidence"]["sequence_context"][-1]["residue"]["resseq"] == variant_auth
    rejected(compile_sample((pdb, cif.replace(b"1 Y 1 1 A ALA 11", b"1 Y 1 1 A ALA 12"), components)))


@pytest.fixture
def annotated_zero_occupancy_water(sample):
    pdb, cif, components = sample
    cif += loop("pdbx_unobs_or_zero_occ_residues",
        "id polymer_flag occupancy_flag PDB_model_num auth_asym_id auth_comp_id auth_seq_id PDB_ins_code label_asym_id label_comp_id label_seq_id",
        ["1 N 0 1 Z HOH 40 ? C HOH ."]).encode()
    return pdb, cif, components


def test_observed_zero_occupancy_water_annotation_is_preserved(annotated_zero_occupancy_water):
    result = compile_sample(annotated_zero_occupancy_water)
    assert result["ok"], result
    water = next(row for row in result["source"]["residues"] if row["role"] == "water")
    assert water["atoms"][0]["occupancy_hundredths"] == 0
    assert water["atoms"][0]["xyz_milli"] == [7000, 8000, 9000]
    annotation = next(row for row in result["evidence"]["sequence_context"]
                      if row.get("annotation") == "zero_occupancy_water")
    assert annotation == {"residue": water["identity"], "annotation": "zero_occupancy_water",
                          "source_record_id": "unobserved-1"}
    proof = hashlib.sha256(json.dumps(result["evidence"], sort_keys=True,
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    assert result["source"]["provenance"]["proof_id"] == proof


@pytest.mark.parametrize("old,new", [
    (b"1 N 0 1 Z HOH 40", b"1 N 1 1 Z HOH 40"),
    (b"1 N 0 1 Z HOH 40", b"1 N 0 1 Z HOH 41"),
    (b"1 N 0 1 Z HOH 40", b"1 N 0 1 A LIG 20"),
    (b"40 ? C HOH .", b"40 ? A HOH ."),
    (b"40 ? C HOH .", b"40 ? C LIG ."),
    (b"40 ? C HOH .", b"40 ? C HOH 1"),
])
def test_zero_occupancy_water_annotation_must_match_source(annotated_zero_occupancy_water, old, new):
    pdb, cif, components = annotated_zero_occupancy_water
    assert old in cif
    rejected(compile_sample((pdb, cif.replace(old, new), components)))


def test_missing_terminal_oxt_is_required_not_repaired(sample):
    pdb, cif, components = sample
    pdb = b"\n".join(row for row in pdb.split(b"\n") if not row.startswith(b"ATOM      4"))
    cif = b"\n".join(row for row in cif.split(b"\n") if not row.startswith(b"ATOM 4 "))
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    terminal = result["source"]["residues"][1]
    assert "OXT" in {a["name"] for a in terminal["required_heavy_atoms"]}
    assert "OXT" not in {a["name"] for a in terminal["atoms"]}


def test_unlinked_alts_preserved(sample):
    pdb, cif, components = sample
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb = b"\n".join(row for row in pdb.split(b"\n") if not row.startswith(b"ATOM      1"))
    pdb = pdb.replace(b"ATOM      2", (atom_line(1, "N", "N", (1, 2, 3), residue("ALA", 10), "ATOM", "A", .6) + "\n" +
                        atom_line(10, "N", "N", (1.1, 2, 3), residue("ALA", 10), "ATOM", "B", .4) + "\n").encode() + b"ATOM      2")
    cif = cif.replace(b"ATOM 1 N N . ALA A 1 1 ? 1.000 2.000 3.000 1.00", b"ATOM 1 N N A ALA A 1 1 ? 1.000 2.000 3.000 0.60")
    cif = cif.replace(b"ATOM 2 C C .", b"ATOM 8 N N B ALA A 1 1 ? 1.100 2.000 3.000 0.40 20.00 ? 10 ALA A N 1\nATOM 2 C C .")
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    assert result["source"]["conformer_constraints"] == []
    table = conn_table(["c1 covale A ALA 1 N A A 10 ? 1_555 B LIG . C1 . A 20 ? 1_555 sing . none"])
    linked = pdb.replace(b"ATOM      1", link(first=("N", "A", "ALA", "A", 10)) + b"ATOM      1")
    rejected(compile_sample((linked, cif+table, components)))


def test_component_only_subject_requires_unique_compatible_instance(sample):
    pdb, cif, components = sample
    result = compile_sample((pdb, cif.replace(b"1 LIG B A 20", b"1 LIG ? ? ?"), components))
    assert result["ok"], result
    assert result["source"]["residues"][2]["role"] == "deposited_ligand"


@pytest.fixture
def glycan_sample(sample):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = sample
    af = "comp_id atom_id type_symbol pdbx_aromatic_flag pdbx_stereo_config pdbx_leaving_atom_flag"
    bf = "comp_id atom_id_1 atom_id_2 value_order pdbx_aromatic_flag pdbx_stereo_config"
    atoms = ["SUG C1 C N R N", "SUG O1 O N N Y", "SUG O4 O N N N", "SUG HO4 H N N Y"]
    bonds = ["SUG C1 O1 SING N N", "SUG C1 O4 SING N N", "SUG O4 HO4 SING N N"]
    components["SUG"] = ("data_SUG\n_chem_comp.id SUG\n_chem_comp.type 'D-saccharide'\n"
        "_chem_comp.pdbx_release_status REL\n_chem_comp.pdbx_modified_date 2023-01-01\n" + loop("chem_comp_atom", af, atoms) + loop("chem_comp_bond", bf, bonds)).encode()
    cif = cif.replace(b"3 water\n", b"3 water\n4 branched\n").replace(b"C 3\n", b"C 3\nD 4\n")
    cif = cif.replace(b"HOH non-polymer\n", b"HOH non-polymer\nSUG 'D-saccharide'\n")
    cif = cif.replace(b"ALA N N N N N\n", ("\n".join(atoms)+"\nALA N N N N N\n").encode())
    cif = cif.replace(b"ALA N C SING N N\n", ("\n".join(bonds)+"\nALA N C SING N N\n").encode())
    coords = []
    for serial, name, element, xyz, seq in [(10, "C1", "C", (8, 9, 10), 30), (11, "O4", "O", (9, 10, 11), 30),
                                           (12, "C1", "C", (10, 11, 12), 31), (13, "O4", "O", (11, 12, 13), 31)]:
        coords.append(atom_line(serial, name, element, xyz, residue("SUG", seq, "G")))
    pdb = pdb.replace(b"END\n", ("\n".join(coords)+"\nCONECT   10   13\nEND\n").encode())
    cif = cif.replace(b"HETATM 7 O O . HOH", (
        "HETATM 8 C C1 . SUG D 4 . ? 8.000 9.000 10.000 1.00 20.00 ? 30 SUG G C1 1\n"
        "HETATM 9 O O4 . SUG D 4 . ? 9.000 10.000 11.000 1.00 20.00 ? 30 SUG G O4 1\n"
        "HETATM 10 C C1 . SUG D 4 . ? 10.000 11.000 12.000 1.00 20.00 ? 31 SUG G C1 1\n"
        "HETATM 11 O O4 . SUG D 4 . ? 11.000 12.000 13.000 1.00 20.00 ? 31 SUG G O4 1\nHETATM 7 O O . HOH").encode())
    cif += loop("pdbx_entity_branch", "entity_id type", ["4 oligosaccharide"]).encode()
    cif += loop("pdbx_entity_branch_list", "entity_id comp_id num hetero", ["4 SUG 1 n", "4 SUG 2 n"]).encode()
    cif += loop("pdbx_branch_scheme", "asym_id entity_id mon_id num pdb_asym_id pdb_mon_id pdb_seq_num auth_asym_id auth_mon_id auth_seq_num hetero",
                ["D 4 SUG 1 G SUG 30 D SUG 901 n", "D 4 SUG 2 G SUG 31 D SUG 902 n"]).encode()
    cif += loop("pdbx_entity_branch_link", "link_id entity_id entity_branch_list_num_1 comp_id_1 atom_id_1 leaving_atom_id_1 entity_branch_list_num_2 comp_id_2 atom_id_2 leaving_atom_id_2 value_order",
                ["1 4 1 SUG C1 O1 2 SUG O4 HO4 sing"]).encode()
    cif += conn_table(["c1 covale D SUG . C1 . G 30 ? 1_555 D SUG . O4 . G 31 ? 1_555 sing . both"])
    return pdb, cif, components


def test_glycan_leaving_atoms_require_explicit_branch_edge(glycan_sample):
    result = compile_sample(glycan_sample)
    assert result["ok"], result
    sugars = [r for r in result["source"]["residues"] if r["role"] == "glycan"]
    assert len(sugars) == 2
    assert "O1" not in {a["name"] for a in sugars[0]["required_heavy_atoms"]}
    assert "O1" in {a["name"] for a in sugars[1]["required_heavy_atoms"]}
    pdb, cif, components = glycan_sample
    for old, new in [(b"SUG C1 O1 2", b"SUG C1 O4 2"), (b"SUG O4 HO4 sing", b"SUG O4 ? sing"),
                     (b"G 31 ? 1_555 sing", b"G 32 ? 1_555 sing"), (b"SUG C1 O1 SING", b"SUG O4 O1 SING")]:
        rejected(compile_sample((pdb, cif.replace(old, new), components)))


def test_branch_without_link_cannot_silently_drop_missing_atoms(glycan_sample):
    pdb, cif, components = glycan_sample
    cif = cif.replace(b"1 4 1 SUG C1 O1 2 SUG O4 HO4 sing", b"1 4 1 SUG C1 O1 2 SUG C1 O1 sing")
    rejected(compile_sample((pdb, cif, components)))


def test_all_revisions_and_continuations(sample):
    pdb, cif, components = sample
    cif = cif.replace(b"1 'Structure model' 1 0 2024-01-01", b"1 'Structure model' 1 0 2024-01-01\n2 'Structure model' 2 0 2024-02-01")
    pdb = pdb.replace(revline().encode(), (revline(2, "01-FEB-24") + "\n" + revline(2, "01-FEB-24", " 2") + "\n" + revline()).encode())
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    assert result["evidence"]["revision_version"] == "2.0"
    for bad in [pdb.replace(b"01-FEB-24", b"02-FEB-24", 1), pdb.replace(b"REVDAT   2 2", b"REVDAT   2 3"),
                pdb.replace(revline().encode()+b"\n", b"")]:
        rejected(compile_sample((bad, cif, components)))


@pytest.fixture
def independent_revision_histories(sample):
    """Synthetic histories with different numbering, as observed for 1P0I."""
    pdb, cif, components = sample
    pdb = pdb.replace(revline().encode(), "\n".join([
        revline(3, "01-FEB-24"), revline(2, "15-JAN-24"), revline(),
    ]).encode())
    cif = cif.replace(b"1 'Structure model' 1 0 2024-01-01",
        b"1 'Structure model' 1 0 2024-01-01\n2 'Structure model' 1 1 2024-02-01")
    return pdb, cif, components


def test_independent_revision_numbering_preserves_verified_structure(independent_revision_histories):
    result = compile_sample(independent_revision_histories)
    assert result["ok"], result
    assert result["evidence"]["revision_version"] == "1.1"
    assert result["evidence"]["latest_revision_date"] == "2024-02-01"
    assert len(result["evidence"]["revision_history"]) == 2


def test_structure_history_ordinals_can_include_other_file_types(independent_revision_histories):
    pdb, cif, components = independent_revision_histories
    cif = cif.replace(b"2 'Structure model' 1 1 2024-02-01",
        b"2 'Structure factors' 1 0 2024-01-15\n3 'Structure model' 1 1 2024-02-01")
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    assert [row["ordinal"] for row in result["evidence"]["revision_history"]] == [1, 3]


@pytest.mark.parametrize("ordinals", [(1, 3), (2, 3)])
def test_missing_global_revision_ordinals_are_rejected(independent_revision_histories, ordinals):
    pdb, cif, components = independent_revision_histories
    cif = cif.replace(b"1 'Structure model' 1 0 2024-01-01\n2 'Structure model' 1 1 2024-02-01",
        f"{ordinals[0]} 'Structure model' 1 0 2024-01-01\n{ordinals[1]} 'Structure model' 1 1 2024-02-01".encode())
    result = compile_sample((pdb, cif, components))
    rejected(result)
    assert result["reason"] == "revision_mismatch"


@pytest.mark.parametrize("old,new", [
    (revline(3, "01-FEB-24"), revline(3, "02-FEB-24")),
    (revline(), revline(1, "02-JAN-24")),
    (revline(2, "15-JAN-24") + "\n", ""),
    (revline(2, "15-JAN-24"), revline(3, "15-JAN-24")),
    (revline(2, "15-JAN-24"), revline(2, "31-JAN-24") + "\n" + revline(2, "15-JAN-24")),
    (revline(2, "15-JAN-24"), revline(2, "31-FEB-24")),
    (revline(2, "15-JAN-24"), revline(2, "01-DEC-23")),
    (revline(), revline()[:-1] + "1"),
    (revline(3, "01-FEB-24"), revline(3, "01-FEB-24")[:-1] + "2"),
])
def test_independent_histories_still_reject_invalid_pdb_history(independent_revision_histories, old, new):
    pdb, cif, components = independent_revision_histories
    assert old.encode() in pdb
    result = compile_sample((pdb.replace(old.encode(), new.encode()), cif, components))
    rejected(result)
    assert result["reason"] == "revision_mismatch"


def test_matching_revision_dates_do_not_hide_coordinate_changes(independent_revision_histories):
    pdb, cif, components = independent_revision_histories
    cif = cif.replace(b"1.000 2.000 3.000", b"1.001 2.000 3.000")
    result = compile_sample((pdb, cif, components))
    rejected(result)
    assert result["reason"] == "inventory_mismatch"


def test_pdb_revision_dates_can_cross_century_boundary(independent_revision_histories):
    pdb, cif, components = independent_revision_histories
    for old, new in [(b"01-JAN-24", b"01-DEC-99"), (b"15-JAN-24", b"15-JAN-00"),
                     (b"01-FEB-24", b"01-FEB-00")]:
        pdb = pdb.replace(old, new)
    cif = cif.replace(b"2024-01-01", b"1999-12-01").replace(b"2024-02-01", b"2000-02-01")
    components = {key: raw.replace(b"2023-01-01", b"1998-01-01") for key, raw in components.items()}
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    assert result["evidence"]["latest_revision_date"] == "2000-02-01"


def test_single_pdb_revision_cannot_mask_a_century_of_changes(sample):
    pdb, cif, components = sample
    pdb = pdb.replace(b"01-JAN-24", b"01-JAN-04")
    cif = cif.replace(b"1 'Structure model' 1 0 2024-01-01",
        b"1 'Structure model' 1 0 1904-01-01\n2 'Structure model' 1 1 2004-01-01")
    components = {key: raw.replace(b"2023-01-01", b"1998-01-01") for key, raw in components.items()}
    result = compile_sample((pdb, cif, components))
    rejected(result)
    assert result["reason"] == "revision_mismatch"


def test_ambiguous_intermediate_pdb_century_is_rejected(independent_revision_histories):
    pdb, cif, components = independent_revision_histories
    pdb = pdb.replace(b"01-JAN-24", b"01-JAN-04").replace(b"15-JAN-24", b"15-JAN-04").replace(b"01-FEB-24", b"01-FEB-04")
    cif = cif.replace(b"2024-01-01", b"1904-01-01").replace(b"2024-02-01", b"2004-02-01")
    result = compile_sample((pdb, cif, components))
    rejected(result)
    assert result["reason"] == "revision_mismatch"


def test_scan_safe_errors(sample):
    rejected(api().required_rcsb_components(sample[1], pdb_id="2ABC"))
    rejected(api().required_rcsb_components(bytearray(sample[1]), pdb_id="1ABC"))


@pytest.fixture
def modification_sample(glycan_sample):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = glycan_sample
    atoms = ["ASN N N N N N", "ASN C C N N N", "ASN O O N N N", "ASN OXT O N N Y", "ASN CG C N N N", "ASN ND2 N N N N"]
    bonds = ["ASN N C SING N N", "ASN C O DOUB N N", "ASN C OXT SING N N", "ASN C CG SING N N", "ASN CG ND2 SING N N"]
    components["ASN"] = ("data_ASN\n_chem_comp.id ASN\n_chem_comp.type 'L-peptide linking'\n"
        "_chem_comp.pdbx_release_status REL\n_chem_comp.pdbx_modified_date 2023-01-01\n"
        + loop("chem_comp_atom", "comp_id atom_id type_symbol pdbx_aromatic_flag pdbx_stereo_config pdbx_leaving_atom_flag", atoms)
        + loop("chem_comp_bond", "comp_id atom_id_1 atom_id_2 value_order pdbx_aromatic_flag pdbx_stereo_config", bonds)).encode()
    for old, new in [
        (b"4 branched\n", b"4 branched\n5 polymer\n"),
        (b"1 'polypeptide(L)' A\n", b"1 'polypeptide(L)' A\n5 'polypeptide(L)' B\n"),
        (b"1 2 ALA n\n", b"1 2 ALA n\n5 1 ASN n\n"),
        (b"D 4\n", b"D 4\nE 5\n"),
        (b"A 1 2 ALA 11 11 ALA ALA A . n\n", b"A 1 2 ALA 11 11 ALA ALA A . n\nE 5 1 ASN 1 1 ASN ASN B . n\n"),
        (b"1 1 UNP SYNTH_HUMAN P06276\n", b"1 1 UNP SYNTH_HUMAN P06276\n2 5 UNP SYNTH_HUMAN Q12345\n"),
        (b"1 1 1ABC A 1 2 1 2 P06276\n", b"1 1 1ABC A 1 2 1 2 P06276\n2 2 1ABC B 1 1 1 1 Q12345\n"),
        (b"1 9606 10090\n", b"1 9606 10090\n5 9606 10090\n"),
        (b"HOH non-polymer\n", b"HOH non-polymer\nASN 'L-peptide linking'\n"),
        (b"ALA N N N N N\n", ("\n".join(atoms)+"\nALA N N N N N\n").encode()),
        (b"ALA N C SING N N\n", ("\n".join(bonds)+"\nALA N C SING N N\n").encode()),
        (b"HETATM 7 O O . HOH", b"ATOM 12 N ND2 . ASN E 5 1 ? 12.000 13.000 14.000 1.00 20.00 ? 1 ASN B ND2 1\nHETATM 7 O O . HOH"),
        (b"sing . both\n", b"sing . both\nc2 covale E ASN 1 ND2 . B 1 ? 1_555 D SUG . C1 . G 31 ? 1_555 sing N-Glycosylation one\n")]:
        assert old in cif
        cif = cif.replace(old, new)
    pdb = pdb.replace(b"CONECT   10   13", (atom_line(14, "ND2", "N", (12, 13, 14), residue("ASN", 1, "B"), "ATOM")+"\nCONECT   14   12\nCONECT   10   13").encode())
    cif += loop("pdbx_modification_feature", "ordinal label_comp_id label_asym_id label_seq_id label_alt_id modified_residue_label_comp_id modified_residue_label_asym_id modified_residue_label_seq_id modified_residue_label_alt_id auth_comp_id auth_asym_id auth_seq_id PDB_ins_code symmetry modified_residue_auth_comp_id modified_residue_auth_asym_id modified_residue_auth_seq_id modified_residue_PDB_ins_code modified_residue_symmetry comp_id_linking_atom modified_residue_id_linking_atom modified_residue_id ref_comp_id type category",
                ["1 SUG D . ? ASN E 1 ? SUG G 31 ? 1_555 ASN B 1 ? 1_555 C1 ND2 ASN SUG N-Glycosylation Carbohydrate"]).encode()
    return pdb, cif, components


def test_modification_and_connection_prove_unique_ccd_leaving_atom(modification_sample):
    result = compile_sample(modification_sample)
    assert result["ok"], result
    sugars = [r for r in result["source"]["residues"] if r["role"] == "glycan"]
    assert len(sugars) == 2
    assert all("O1" not in {a["name"] for a in r["required_heavy_atoms"]} for r in sugars)
    pdb, cif, components = modification_sample
    for old, new in [(b"SUG G 31 ? 1_555 ASN", b"SUG D 902 ? 1_555 ASN"),
                     (b"sing N-Glycosylation one", b"sing O-Glycosylation one"),
                     (b"1_555 C1 ND2 ASN", b"1_555 O4 ND2 ASN")]:
        rejected(compile_sample((pdb, cif.replace(old, new), components)))


def test_modification_ambiguous_leaving_group_rejected(modification_sample):
    pdb, cif, components = modification_sample
    for key in ("SUG",):
        components[key] = components[key].replace(b"SUG O1 O N N Y", b"SUG O1 O N N Y\nSUG O2 O N N Y").replace(b"SUG C1 O1 SING N N", b"SUG C1 O1 SING N N\nSUG C1 O2 SING N N")
    cif = cif.replace(b"SUG O1 O N N Y", b"SUG O1 O N N Y\nSUG O2 O N N Y").replace(b"SUG C1 O1 SING N N", b"SUG C1 O1 SING N N\nSUG C1 O2 SING N N")
    rejected(compile_sample((pdb, cif, components)))


@pytest.fixture
def connection_only_glycosylation(modification_sample):
    pdb, cif, components = modification_sample
    marker = b"loop_\n_pdbx_modification_feature.ordinal\n"
    assert marker in cif
    cif, _ = cif.split(marker)
    return pdb, cif, components


def test_explicit_glycosylation_connection_needs_no_duplicate_feature(connection_only_glycosylation):
    result = compile_sample(connection_only_glycosylation)
    assert result["ok"], result
    sugar = next(row for row in result["source"]["residues"] if row["identity"]["resseq"] == 31)
    assert sugar["role"] == "glycan"
    assert "O1" not in {a["name"] for a in sugar["required_heavy_atoms"]}
    assert any(row.get("basis") == "struct-conn-c2" and row["removed_atom"] == "O1"
               for row in result["evidence"]["chemical_context"])


@pytest.mark.parametrize("old,new", [
    (b"sing N-Glycosylation one", b"sing . one"),
    (b"sing N-Glycosylation one", b"sing N-Glycosylation none"),
    (b"sing N-Glycosylation one", b"sing N-Glycosylation both"),
    (b"sing N-Glycosylation one", b"sing O-Glycosylation one"),
    (b"sing N-Glycosylation one", b"doub N-Glycosylation one"),
])
def test_connection_only_glycosylation_keeps_exact_evidence_gate(connection_only_glycosylation, old, new):
    pdb, cif, components = connection_only_glycosylation
    assert old in cif
    rejected(compile_sample((pdb, cif.replace(old, new), components)))


def test_deposited_joint_alt_assignments(sample):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = sample
    pdb = b"\n".join(row for row in pdb.split(b"\n") if not row.startswith((b"ATOM      1", b"HETATM    7")))
    first = [atom_line(1, "N", "N", (1, 2, 3), residue("ALA", 10), "ATOM", "A", .6),
             atom_line(10, "N", "N", (1.1, 2, 3), residue("ALA", 10), "ATOM", "B", .4)]
    second = [atom_line(7, "C1", "C", (5, 6, 7), residue("LIG", 20), "HETATM", "A", .6),
              atom_line(11, "C1", "C", (5.1, 6, 7), residue("LIG", 20), "HETATM", "B", .4)]
    pdb = pdb.replace(b"ATOM      2", ("\n".join(first)+"\n").encode()+b"ATOM      2")
    pdb = pdb.replace(b"HETATM    8", ("\n".join(second)+"\n").encode()+b"HETATM    8")
    pdb = pdb.replace(b"ATOM      1", link(first=("N", "A", "ALA", "A", 10), second=("C1", "A", "LIG", "A", 20)) +
                      link(first=("N", "B", "ALA", "A", 10), second=("C1", "B", "LIG", "A", 20)) + b"ATOM      1")
    cif = cif.replace(b"ATOM 1 N N . ALA A 1 1 ? 1.000 2.000 3.000 1.00", b"ATOM 1 N N A ALA A 1 1 ? 1.000 2.000 3.000 0.60")
    cif = cif.replace(b"ATOM 2 C C .", b"ATOM 8 N N B ALA A 1 1 ? 1.100 2.000 3.000 0.40 20.00 ? 10 ALA A N 1\nATOM 2 C C .")
    cif = cif.replace(b"HETATM 5 C C1 . LIG B 2 . ? 5.000 6.000 7.000 1.00", b"HETATM 5 C C1 A LIG B 2 . ? 5.000 6.000 7.000 0.60")
    cif = cif.replace(b"HETATM 6 O O1 .", b"HETATM 9 C C1 B LIG B 2 . ? 5.100 6.000 7.000 0.40 20.00 ? 20 LIG A C1 1\nHETATM 6 O O1 .")
    cif += conn_table(["c1 covale A ALA 1 N A A 10 ? 1_555 B LIG . C1 A A 20 ? 1_555 sing . none",
                       "c2 covale A ALA 1 N B A 10 ? 1_555 B LIG . C1 B A 20 ? 1_555 sing . none"])
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    assert result["source"]["conformer_constraints"][0]["allowed_assignments"] == [["A", "A"], ["B", "B"]]


@pytest.mark.parametrize("annotation", [b"_atom_sites.fract_transf_matrix[1][1] 1.0\n", b"_symmetry.space_group_name_H-M 'P 1'\n"])
def test_standard_indexed_matrix_tags_are_valid_cif(sample, annotation):
    pdb, cif, components = sample
    cif += annotation
    assert api().required_rcsb_components(cif, pdb_id="1ABC") == ("ALA", "HOH", "LIG")
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result


def test_exact_scaled_zero_padding_is_not_rounding(sample):
    pdb, cif, components = sample
    result = compile_sample((pdb, cif.replace(b"1.00 20.00", b"1.000 20.000"), components))
    assert result["ok"], result
    rejected(compile_sample((pdb, cif.replace(b"1.00 20.00", b"1.001 20.000"), components)))


def test_conect_topology_does_not_reinterpret_ccd_bond_order(sample):
    pdb, cif, components = sample
    components["LIG"] = components["LIG"].replace(b"LIG C1 O1 SING", b"LIG C1 O1 DOUB")
    cif = cif.replace(b"LIG C1 O1 SING", b"LIG C1 O1 DOUB")
    result = compile_sample((pdb.replace(b"END\n", b"CONECT    7    8\nCONECT    8    7\nEND\n"), cif, components))
    assert result["ok"], result
    assert all(r["kind"] == "covalent" for r in result["source"]["connections"])
    rejected(compile_sample((pdb.replace(b"END\n", b"CONECT    7    8    8\nEND\n"), cif, components)))


def test_private_shared_ledger_never_resets(sample, monkeypatch):
    from src.docking.receptor_projection_pdb import AllocationBudget, ProjectionInvalid
    module = api()
    discover = getattr(module, "_discover_components", None)
    assert callable(discover), "shared discovery seam missing"
    budget = AllocationBudget()
    budget.reserve(100000)
    assert discover(sample[1], "1ABC", budget) == ("ALA", "HOH", "LIG")
    used = budget.used
    result = module._compile(*sample, "1ABC", {"uniprot_id": "P06276", "taxonomy_id": 9606}, budget=budget)
    assert result["ok"] and budget.used > used > 100000
    import gemmi
    def forbidden(value):
        raise AssertionError("native parse after exhausted root ledger")
    monkeypatch.setattr(gemmi.cif, "read_string", forbidden)
    budget.reserve(budget.limit-budget.used)
    with pytest.raises(ProjectionInvalid, match="resource_limit"):
        discover(sample[1], "1ABC", budget)
    with pytest.raises(ProjectionInvalid, match="resource_limit"):
        module._compile(*sample, "1ABC", {"uniprot_id": "P06276", "taxonomy_id": 9606}, budget=budget)


def test_branch_scheme_pdb_fields_point_to_atom_site_auth_not_original_author(glycan_sample):
    pdb, cif, components = glycan_sample
    result = compile_sample(glycan_sample)
    assert result["ok"], result
    rejected(compile_sample((pdb, cif.replace(b"D 4 SUG 1 G SUG 30 D SUG 901 n", b"D 4 SUG 1 G SUG 32 G SUG 30 n"), components)))


def test_terminal_linking_definition_cannot_erase_oxt(sample):
    pdb, cif, components = sample
    pdb = b"\n".join(row for row in pdb.split(b"\n") if not row.startswith(b"ATOM      4"))
    # Retain one terminal ALA only, so an internal-OXT check cannot mask this.
    pdb = b"\n".join(row for row in pdb.split(b"\n") if not row.startswith((b"ATOM      1", b"ATOM      2")))
    cif = b"\n".join(row for row in cif.split(b"\n") if not row.startswith((b"ATOM 1 ", b"ATOM 2 ", b"ATOM 4 ", b"ALA OXT ", b"ALA C OXT ", b"1 1 ALA n", b"A 1 1 ALA 10 ")))
    cif = cif.replace(b"1 2 ALA n", b"1 1 ALA n").replace(b"A 1 2 ALA 11", b"A 1 1 ALA 11")
    cif = cif.replace(b"ALA A 1 2 ?", b"ALA A 1 1 ?").replace(b"1 1 1ABC A 1 2 1 2 P06276", b"1 1 1ABC A 1 1 1 1 P06276")
    components["ALA"] = b"\n".join(row for row in components["ALA"].split(b"\n") if not row.startswith((b"ALA OXT ", b"ALA C OXT ")))
    rejected(compile_sample((pdb, cif, components)))


def test_orphan_reference_entity_is_invalid(sample):
    pdb, cif, components = sample
    cif = cif.replace(b"1 1 UNP SYNTH_HUMAN P06276\n", b"1 1 UNP SYNTH_HUMAN P06276\n2 99 UNP ORPHAN P06276\n")
    rejected(compile_sample((pdb, cif, components)))


def test_sequence_variant_database_position_must_match_alignment(sample):
    pdb, cif, components = sample
    cif += loop("struct_ref_seq_dif", "pdbx_ordinal align_id seq_num mon_id db_mon_id pdbx_pdb_strand_id pdbx_pdb_id_code pdbx_seq_db_accession_code pdbx_seq_db_name pdbx_auth_seq_num pdbx_pdb_ins_code pdbx_seq_db_seq_num details",
                ["1 1 1 ALA GLY A 1ABC P06276 UNP 10 ? 2 'engineered mutation'"]).encode()
    rejected(compile_sample((pdb, cif, components)))


@pytest.mark.parametrize("old,new", [(b"REVDAT   1", b"REVDATX  1"), (b"1ABC    0", b"1ABC    X")])
def test_revision_header_is_coherent_before_continuations(sample, old, new):
    pdb, cif, components = sample
    assert old in pdb
    rejected(compile_sample((pdb.replace(old, new), cif, components)))


@pytest.mark.parametrize("part,old,new", [
    ("pdb", b"1ABC", b"2ABC"),
    ("cif", b"ATOM 2 C C .", b"ATOM 1 C C ."),
    ("cif", b"2 non-polymer", b"2 water"),
    ("cif", b"1 'polypeptide(L)' A", b"1 'polypeptide(L)' B"),
    ("cif", b"1 1 UNP", b"1 1 PDB"),
    ("cif", b"1.000 2.000 3.000", b"inf 2.000 3.000"),
    ("cif", b"1.00 20.00 ? 10", b"1.00 20.00 1 10"),
    ("ccd", b"pdbx_release_status REL", b"pdbx_release_status HOLD"),
    ("ccd", b"LIG C1 C N N N", b"LIG C1 C Y N N"),
    ("ccd", b"LIG O1 O N N N", b"LIG O1 O N R N"),
    ("ccd", b"LIG C1 O1 SING N N", b"LIG C1 O1 SING Y N"),
    ("ccd", b"LIG C1 O1 SING N N", b"LIG C1 O1 SING N E"),
    ("ccd", b"LIG H1 H N N N", b"LIG H2 H N N N"),
])
def test_additional_closed_evidence_negatives(sample, part, old, new):
    pdb, cif, components = sample
    if part == "pdb":
        assert old in pdb
        pdb = pdb.replace(old, new)
    elif part == "cif":
        assert old in cif
        cif = cif.replace(old, new)
    else:
        assert old in components["LIG"]
        components["LIG"] = components["LIG"].replace(old, new)
    rejected(compile_sample((pdb, cif, components)))


def test_single_ledger_capacity_precedes_native_dom(sample, monkeypatch):
    module = api()
    import gemmi
    monkeypatch.setattr(module, "MAX_LOGICAL_BYTES", 1000)
    def forbidden(value):
        raise AssertionError("native allocation before budget")
    monkeypatch.setattr(gemmi.cif, "read_string", forbidden)
    rejected(compile_sample(sample))


def test_metaclass_equality_and_mapping_hooks_never_run(sample):
    class Meta(type):
        def __eq__(self, other):
            raise AssertionError("metaclass comparison")
        def __hash__(self):
            raise AssertionError("metaclass hash")
    class Hostile(metaclass=Meta):
        def __len__(self):
            raise AssertionError("length hook")
        def __iter__(self):
            raise AssertionError("iterator hook")
    for index in range(3):
        values = list(sample)
        values[index] = Hostile()
        rejected(compile_sample(tuple(values)))
    rejected(compile_sample(sample, target=Hostile()))


def test_duplicate_pdb_link_multiplicity_is_not_collapsed(sample):
    pdb, cif, components = sample
    table = conn_table(["c1 covale A ALA 1 N . A 10 ? 1_555 B LIG . C1 . A 20 ? 1_555 sing . none"])
    rejected(compile_sample((pdb.replace(b"ATOM      1", link()+link()+b"ATOM      1"), cif+table, components)))


def test_component_count_and_aggregate_bound_before_parse(sample, monkeypatch):
    import gemmi
    def forbidden(value):
        raise AssertionError("native parse before aggregate admission")
    monkeypatch.setattr(gemmi.cif, "read_string", forbidden)
    rejected(compile_sample((sample[0], sample[1], {f"A{i:02d}": b"x" for i in range(65)})))
    chunk = b"x" * (1024*1024)
    rejected(compile_sample((sample[0], sample[1], {f"A{i:02d}": chunk for i in range(17)})))


def test_native_source_taxonomy_not_expression_host_and_natural_source(sample):
    pdb, cif, components = sample
    cif = cif.replace(b"_entity_src_gen.entity_id", b"_entity_src_nat.entity_id")
    cif = cif.replace(b"_entity_src_gen.pdbx_gene_src_ncbi_taxonomy_id", b"_entity_src_nat.pdbx_ncbi_taxonomy_id")
    cif = cif.replace(b"_entity_src_gen.pdbx_host_org_ncbi_taxonomy_id", b"_entity_src_nat.details")
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    rejected(compile_sample((pdb, cif.replace(b"1 9606 10090", b"1 ? 9606"), components)))


def test_parser_diagnostic_never_escapes(sample):
    pdb, cif, components = sample
    result = compile_sample((pdb, cif+b"private-secret-marker\n", components))
    rejected(result)
    assert "private-secret-marker" not in str(result)


def test_spec_required_heavy_cartesian_expansion_rejected_before_allocation(sample, monkeypatch):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = sample
    # Synthetic, independently enumerated component facts and sparse instances.
    # The raw inputs are small; their *product* would allocate >4M dictionaries.
    extra_atoms = "\n".join(f"LIG X{i:03X} C N N N" for i in range(4000)).encode() + b"\n"
    components["LIG"] = components["LIG"].replace(b"LIG H1 H N N N\n", b"LIG H1 H N N N\n" + extra_atoms)
    cif = cif.replace(b"LIG H1 H N N N\n", b"LIG H1 H N N N\n" + extra_atoms)
    pdb_atoms = []
    cif_atoms = []
    for index in range(999):
        pdb_atoms.append(atom_line(10+index, "C1", "C", (1, 2, 3), residue("LIG", 100+index)))
        cif_atoms.append(f"HETATM {8+index} C C1 . LIG B 2 . ? 1.000 2.000 3.000 1.00 20.00 ? {100+index} LIG A C1 1")
    pdb = pdb.replace(b"END\n", ("\n".join(pdb_atoms) + "\nEND\n").encode())
    cif = cif.replace(b"HETATM 7 O O . HOH", ("\n".join(cif_atoms) + "\nHETATM 7 O O . HOH").encode())
    assert len(pdb) < 16*1024*1024 and len(cif) < 16*1024*1024
    assert all(len(value) < 1024*1024 for value in components.values())
    def forbidden_expansion(*args, **kwargs):
        pytest.fail("required-heavy expansion entered before cardinality/byte reservation")
    monkeypatch.setattr(api(), "_required_atoms", forbidden_expansion)
    result = compile_sample((pdb, cif, components))
    rejected(result)
    assert result["reason"] == "resource_limit"


@pytest.mark.parametrize("name,element", [("XX", "H"), ("H1", "D"), ("H1", "H")])
def test_spec_every_observed_hydrogen_must_match_ccd(sample, name, element):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = sample
    pdb = pdb.replace(b"END\n", (atom_line(10, name, element, (8, 9, 10), residue("LIG", 20))+"\nEND\n").encode())
    cif = cif.replace(b"HETATM 7 O O . HOH", (f"HETATM 8 {element} {name} . LIG B 2 . ? 8.000 9.000 10.000 1.00 20.00 ? 20 LIG A {name} 1\nHETATM 7 O O . HOH").encode())
    result = compile_sample((pdb, cif, components))
    if (name, element) == ("H1", "H"):
        assert result["ok"], result
    else:
        rejected(result)


@pytest.mark.parametrize("name,order", [("O1", "trip"), ("H1", "trip"), ("O1", "sing"), ("H1", "sing")])
def test_spec_intra_struct_conn_cannot_override_ccd(sample, name, order):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = sample
    if name == "H1":
        pdb = pdb.replace(b"END\n", (atom_line(10, name, "H", (8, 9, 10), residue("LIG", 20))+"\nEND\n").encode())
        cif = cif.replace(b"HETATM 7 O O . HOH", b"HETATM 8 H H1 . LIG B 2 . ? 8.000 9.000 10.000 1.00 20.00 ? 20 LIG A H1 1\nHETATM 7 O O . HOH")
    serial = 8 if name == "O1" else 10
    pdb = pdb.replace(b"END\n", f"CONECT    7{serial:5d}\nEND\n".encode())
    cif += conn_table([f"c1 covale B LIG . C1 . A 20 ? 1_555 B LIG . {name} . A 20 ? 1_555 {order} . none"])
    result = compile_sample((pdb, cif, components))
    if order == "sing":
        assert result["ok"], result
    else:
        rejected(result)


@pytest.mark.parametrize("flag", [b"none", b"one"])
def test_spec_branch_leaving_flag_cannot_contradict_displacement(glycan_sample, flag):
    pdb, cif, components = glycan_sample
    assert b"sing . both" in cif
    rejected(compile_sample((pdb, cif.replace(b"sing . both", b"sing . " + flag), components)))


def test_spec_shared_remaining_bytes_reject_before_required_expansion(sample, monkeypatch):
    from src.docking.receptor_projection_pdb import AllocationBudget, ProjectionInvalid
    module = api()
    budget = AllocationBudget()
    budget.reserve(64 * 1024 * 1024)
    module._discover_components(sample[1], "1ABC", budget)
    original = module._reserve_required_atoms
    visited = []

    def constrained(residues, components, ledger):
        assert ledger is budget
        # The tiny fixture is below the atom-count bound; only the shared
        # remaining-byte limit must stop its required-atom expansion.
        total = sum(sum(attrs[0] not in {"H", "D"} for attrs in components[ident[2]]["atoms"].values())
                    for ident in residues)
        assert total < 100
        charge = 1024 + 256 * len(components) + total * 512 + len(residues) * 1024
        ledger.limit = ledger.used + charge - 1
        visited.append(ledger.used)
        original(residues, components, ledger)

    def forbidden(*args, **kwargs):
        pytest.fail("required-heavy copies allocated after shared byte budget exhausted")

    monkeypatch.setattr(module, "_reserve_required_atoms", constrained)
    monkeypatch.setattr(module, "_required_atoms", forbidden)
    with pytest.raises(ProjectionInvalid, match="resource_limit"):
        module._compile(*sample, "1ABC", {"uniprot_id": "P06276", "taxonomy_id": 9606}, budget=budget)
    assert len(visited) == 1 and budget.used >= visited[0] > 64 * 1024 * 1024


@pytest.mark.parametrize("element", ["H", "D"])
@pytest.mark.parametrize("observed_seq", [None, 31, 30])
def test_declared_displaced_hydrogen_must_be_absent_from_exact_instance(glycan_sample, element, observed_seq):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = glycan_sample
    if element == "D":
        # Match both independent chemistry representations, not just coordinates.
        old, new = b"SUG HO4 H N N Y", b"SUG HO4 D N N Y"
        assert old in cif and old in components["SUG"]
        cif = cif.replace(old, new)
        components["SUG"] = components["SUG"].replace(old, new)
    if observed_seq is not None:
        pdb = pdb.replace(b"CONECT   10   13", (atom_line(14, "HO4", element, (12, 13, 14), residue("SUG", observed_seq, "G")) + "\nCONECT   10   13").encode())
        cif = cif.replace(b"HETATM 7 O O . HOH", (f"HETATM 12 {element} HO4 . SUG D 4 . ? 12.000 13.000 14.000 1.00 20.00 ? {observed_seq} SUG G HO4 1\nHETATM 7 O O . HOH").encode())
    originals = (pdb, cif, dict(components))
    result = compile_sample((pdb, cif, components))
    assert (pdb, cif, components) == originals  # Validation never repairs input.
    if observed_seq == 31:
        rejected(result)
        assert result["reason"] == "context_unsupported"
    else:
        assert result["ok"], result
        sugars = [r for r in result["source"]["residues"] if r["role"] == "glycan"]
        assert all("HO4" not in {a["name"] for a in row["required_heavy_atoms"]} for row in sugars)
        assert sum(a["name"] == "HO4" and a["element"] == element for row in sugars for a in row["atoms"]) == (observed_seq == 30)


def test_declared_displaced_heavy_atom_still_rejected_when_observed(glycan_sample):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = glycan_sample
    pdb = pdb.replace(b"CONECT   10   13", (atom_line(14, "O1", "O", (12, 13, 14), residue("SUG", 30, "G")) + "\nCONECT   10   13").encode())
    cif = cif.replace(b"HETATM 7 O O . HOH", b"HETATM 12 O O1 . SUG D 4 . ? 12.000 13.000 14.000 1.00 20.00 ? 30 SUG G O1 1\nHETATM 7 O O . HOH")
    result = compile_sample((pdb, cif, components))
    rejected(result)
    assert result["reason"] == "context_unsupported"


@pytest.mark.parametrize("table", ["sequence", "scheme"])
@pytest.mark.parametrize("alias_first", [True, False])
@pytest.mark.parametrize("conflicting", [True, False])
def test_integer_sequence_keys_reject_alias_duplicates(sample, table, alias_first, conflicting):
    pdb, cif, components = sample
    if table == "sequence":
        original = b"1 1 ALA n\n"
        alias = b"1 01 LIG n\n" if conflicting else b"1 01 ALA n\n"
    else:
        original = b"A 1 1 ALA 10 10 ALA ALA A . n\n"
        alias = b"A 1 01 LIG 20 20 LIG LIG A . n\n" if conflicting else b"A 1 01 ALA 10 10 ALA ALA A . n\n"
    assert cif.count(original) == 1
    cif = cif.replace(original, alias + original if alias_first else original + alias)
    rejected(compile_sample((pdb, cif, components)))


def test_atom_site_integer_ids_reject_alias_duplicates(sample):
    pdb, cif, components = sample
    rejected(compile_sample((pdb, cif.replace(b"ATOM 2 C C .", b"ATOM 01 C C ."), components)))


@pytest.mark.parametrize("old,new", [
    (b"1 1 ALA n\n", b"1 01 ALA n\n"),
    (b"A 1 1 ALA 10 10 ALA ALA A . n\n", b"A 1 01 ALA 10 10 ALA ALA A . n\n"),
    (b"ATOM 1 N N .", b"ATOM 01 N N ."),
])
def test_unique_zero_padded_integer_key_is_not_a_duplicate(sample, old, new):
    pdb, cif, components = sample
    assert cif.count(old) == 1
    result = compile_sample((pdb, cif.replace(old, new), components))
    assert result["ok"], result


@pytest.mark.parametrize("extra", [
    b"2 1 2ABC Z 1 99999 1 99999 Q12345\n",
    b"2 1 1ABC Z 1 2 1 2 P06276\n",
    b"2 1 1ABC A,Z 1 2 1 2 P06276\n",
    b"2 1 1ABC Z 1 2 1 2 Q12345\n",
    b"2 1 1ABC Z 0 2 1 2 P06276\n",
    b"2 1 1ABC Z 1 99999 1 99999 P06276\n",
    b"2 1 1ABC Z 1 2 2 1 P06276\n",
])
@pytest.mark.parametrize("extra_first", [True, False])
def test_every_alignment_row_must_resolve_to_its_polymer(sample, extra, extra_first):
    pdb, cif, components = sample
    original = b"1 1 1ABC A 1 2 1 2 P06276\n"
    cif = cif.replace(original, extra + original if extra_first else original + extra)
    rejected(compile_sample((pdb, cif, components)))


@pytest.mark.parametrize("joint_row", [True, False])
@pytest.mark.parametrize("reverse_rows", [True, False])
def test_valid_multiple_polymer_chains_keep_all_alignments(sample, joint_row, reverse_rows):
    from tests.test_docking_pocket_evidence import atom_line, residue
    pdb, cif, components = sample
    coords, atoms = [], []
    for index, (name, element, seq, label_seq) in enumerate([
        ("N", "N", 10, 1), ("C", "C", 10, 1), ("N", "N", 11, 2), ("OXT", "O", 11, 2),
    ]):
        coords.append(atom_line(10 + index, name, element, (8 + index, 9, 10), residue("ALA", seq, "B"), "ATOM"))
        atoms.append(f"ATOM {8 + index} {element} {name} . ALA D 1 {label_seq} ? {8 + index}.000 9.000 10.000 1.00 20.00 ? {seq} ALA B {name} 1")
    pdb = pdb.replace(b"END\n", ("\n".join(coords) + "\nEND\n").encode())
    cif = cif.replace(b"C 3\n", b"C 3\nD 1\n")
    cif = cif.replace(b"1 'polypeptide(L)' A\n", b"1 'polypeptide(L)' A,B\n")
    cif = cif.replace(b"A 1 2 ALA 11 11 ALA ALA A . n\n", b"A 1 2 ALA 11 11 ALA ALA A . n\nD 1 1 ALA 10 10 ALA ALA B . n\nD 1 2 ALA 11 11 ALA ALA B . n\n")
    cif = cif.replace(b"HETATM 7 O O . HOH", ("\n".join(atoms) + "\nHETATM 7 O O . HOH").encode())
    alignments = [b"1 1 1ABC A,B 1 2 1 2 P06276\n"] if joint_row else [b"1 1 1ABC A 1 2 1 2 P06276\n", b"2 1 1ABC B 1 2 1 2 P06276\n"]
    if reverse_rows:
        alignments.reverse()
    cif = cif.replace(b"1 1 1ABC A 1 2 1 2 P06276\n", b"".join(alignments))
    result = compile_sample((pdb, cif, components))
    assert result["ok"], result
    assert {row["chain"] for row in result["source"]["proteins"]} == {"A", "B"}
