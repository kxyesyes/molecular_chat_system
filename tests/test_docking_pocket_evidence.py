"""Fixed-width synthetic PDB data; these tests are not experimental evidence."""

import copy
import hashlib
import importlib
import importlib.util

import pytest


def api():
    name = "src.docking.pocket_evidence"
    assert importlib.util.find_spec(name) is not None, "D1 pocket producer is missing"
    return importlib.import_module(name)


def residue(resname="LIG", resseq=20, chain="A", icode="", model=1):
    return dict(model=model, chain=chain, resname=resname, resseq=resseq, icode=icode)


def atom_line(serial, name, element, xyz, identity, record="HETATM", altloc=" ", occupancy=1.0):
    return (f"{record:<6}{serial:5d} {name:>4}{altloc}{identity['resname']:>3} "
            f"{identity['chain']:1}{identity['resseq']:4d}{identity['icode']:1}   "
            f"{xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}{occupancy:6.2f}{20.0:6.2f}"
            f"          {element:>2}  ")


def fixture():
    protein, ligand, glycan, water = residue("ALA", 1), residue(), residue("NAG", 30), residue("HOH", 40)
    specs = [
        (protein, "ATOM", "protein", "1", [(1, "CA", "C", (0, 0, 0)), (2, "N", "N", (1, 0, 0))]),
        (ligand, "HETATM", "deposited_ligand", "2", [(3, "C1", "C", (1, -2, 3)),
          (4, "O1", "O", (7, 4, 5)), (5, "N1", "N", (3, 0, 9)), (6, "H1", "H", (50, 50, 50))]),
        (glycan, "HETATM", "glycan", "3", [(7, "C1", "C", (40, 40, 40))]),
        (water, "HETATM", "water", "4", [(8, "O", "O", (2, 2, 2))]),
    ]
    lines = ["HEADER    SYNTHETIC TEST".ljust(62) + "1ABC"]
    inventory = []
    for identity, record, role, entity, atoms in specs:
        lines.extend(atom_line(n, name, element, xyz, identity, record) for n, name, element, xyz in atoms)
        inventory.append(dict(identity=identity, record=record, role=role, entity_id=entity,
                              atoms=[dict(serial=n, name=name, element=element) for n, name, element, _ in atoms]))
    pdb = ("\n".join(lines) + "\nEND\n").encode("ascii")
    source = dict(schema_version="docking-source-v1", sha256=hashlib.sha256(pdb).hexdigest(), pdb_id="1ABC",
                  provenance=dict(provider="wwpdb", version="2026-10-15", record_id="1ABC", proof_id="proof-1"),
                  target=dict(uniprot_id="P06276", taxonomy_id=9606),
                  proteins=[dict(model=1, chain="A", entity_id="1", uniprot_id="P06276", taxonomy_id=9606)],
                  residues=inventory)
    return pdb, source


def rehash(pdb, source):
    source = copy.deepcopy(source)
    source["sha256"] = hashlib.sha256(pdb).hexdigest()
    return source


def assert_invalid(result):
    assert result == {"ok": False, "code": "POCKET_EVIDENCE_INVALID",
                      "message": "Pocket evidence is unavailable or invalid."}


def test_heavy_atom_aabb_is_exact_source_bound_and_not_centroid():
    pdb, source = fixture()
    result = api().propose_pocket(pdb, source=source, residue=residue())
    assert result["ok"] is True
    assert result["center"] == [4.0, 1.0, 6.0]
    assert result["size"] == [14.0, 14.0, 14.0]
    assert result["margin"] == 4.0 and result["units"] == "angstrom"
    assert result["rule_version"] == "heavy-aabb-margin4-min10-v1"
    assert result["source_sha256"] == hashlib.sha256(pdb).hexdigest()
    assert result["residue"] == residue()
    assert result["atom_count"] == 3
    assert result["atom_ids"] == source["residues"][1]["atoms"][:3]
    assert result["source_authentication"] == "caller_responsibility"


def test_inspection_keeps_identity_and_does_not_promote_glycan_or_water():
    pdb, source = fixture()
    result = api().inspect_pocket_candidates(pdb, source=source)
    assert result["ok"]
    assert [(x["residue"], x["eligible"]) for x in result["candidates"]] == [
        (residue(), True), (residue("NAG", 30), False)]


@pytest.mark.parametrize("mutation", ["hash", "entry", "unknown", "bool", "entity", "atom_missing", "atom_extra", "atom_name", "atom_element", "serial", "unproven", "glycan", "metal", "solvent", "buffer", "target"])
def test_source_mismatch_or_unproven_role_is_safe_failure(mutation):
    pdb, source = fixture()
    if mutation == "hash": source["sha256"] = "0" * 64
    elif mutation == "entry": source["pdb_id"] = "2ABC"
    elif mutation == "unknown": source["credential"] = "not-authority"
    elif mutation == "bool": source["proteins"][0]["model"] = True
    elif mutation == "entity": source["proteins"][0]["entity_id"] = "wrong"
    elif mutation == "atom_missing": source["residues"][1]["atoms"].pop()
    elif mutation == "atom_extra": source["residues"][1]["atoms"].append(dict(serial=99, name="C9", element="C"))
    elif mutation == "atom_name": source["residues"][1]["atoms"][0]["name"] = "XX"
    elif mutation == "atom_element": source["residues"][1]["atoms"][0]["element"] = "N"
    elif mutation == "serial": source["residues"][1]["atoms"][0]["serial"] = 33
    elif mutation == "target": source["target"]["taxonomy_id"] = 10090
    else: source["residues"][1]["role"] = mutation
    assert_invalid(api().propose_pocket(pdb, source=source, residue=residue()))


@pytest.mark.parametrize("mutation", ["short", "nan", "inf", "bad_numeric", "altloc", "occupancy", "partial", "duplicate", "model", "unknown_record", "end_missing"])
def test_strict_parser_never_skips_bad_coordinate_records(mutation):
    pdb, source = fixture()
    lines = pdb.decode().splitlines()
    line = lines[3]
    if mutation == "short": lines[3] = line[:53]
    elif mutation in {"nan", "inf", "bad_numeric"}: lines[3] = line[:30] + {"nan":"     nan", "inf":"     inf", "bad_numeric":"   1x000"}[mutation] + line[38:]
    elif mutation == "altloc": lines[3] = line[:16] + "A" + line[17:]
    elif mutation in {"occupancy", "partial"}: lines[3] = line[:54] + ("  0.00" if mutation == "occupancy" else "  0.50") + line[60:]
    elif mutation == "duplicate": lines.insert(4, line)
    elif mutation == "model": lines.insert(4, "MODEL        2")
    elif mutation == "unknown_record": lines[3] = "HETATX" + line[6:]
    elif mutation == "end_missing": lines.pop()
    pdb = ("\n".join(lines) + "\n").encode()
    assert_invalid(api().propose_pocket(pdb, source=rehash(pdb, source), residue=residue()))


def test_grid_minimum_and_overflow_rejection_not_clamping():
    pdb, source = fixture()
    lines = pdb.decode().splitlines()
    for i, serial, name, element in [(3, 3, "C1", "C"), (4, 4, "O1", "O"), (5, 5, "N1", "N")]:
        lines[i] = atom_line(serial, name, element, (i - 3, 1, 1), residue())
    compact = ("\n".join(lines) + "\n").encode()
    assert api().propose_pocket(compact, source=rehash(compact, source), residue=residue())["size"] == [10, 10, 10]
    lines[4] = atom_line(4, "O1", "O", (100, 1, 1), residue())
    wide = ("\n".join(lines) + "\n").encode()
    assert_invalid(api().propose_pocket(wide, source=rehash(wide, source), residue=residue()))


@pytest.mark.parametrize("bad_selection", [{}, {"model":2,"chain":"A","resname":"LIG","resseq":20,"icode":""}, residue(chain="B"), residue(icode="A"), residue("NAG", 30)])
def test_explicit_residue_identity_cannot_select_first_or_largest(bad_selection):
    pdb, source = fixture()
    assert_invalid(api().propose_pocket(pdb, source=source, residue=bad_selection))


def test_receptor_is_new_bytes_with_explicit_hetero_retention_and_complete_lineage():
    pdb, source = fixture()
    original, descriptor = bytes(pdb), copy.deepcopy(source)
    result = api().prepare_receptor_bytes(pdb, source=source,
        protein_selection={"model":1, "chains":[{"chain":"A", "entity_id":"1"}]},
        reference_residue=residue(), retained_hetero_residues=[residue("HOH", 40)])
    assert result["ok"]
    derived = result["pdb_bytes"]
    assert pdb == original and source == descriptor
    assert derived != pdb and b"LIG" not in derived and b"NAG" not in derived
    assert b"HOH" in derived and b"ALA" in derived
    assert result["sha256"] == hashlib.sha256(derived).hexdigest()
    assert result["source_sha256"] == hashlib.sha256(pdb).hexdigest()
    assert [x["residue"] for x in result["retained"]] == [residue("ALA", 1), residue("HOH", 40)]
    assert [x["residue"] for x in result["removed"]] == [residue(), residue("NAG", 30)]
    for item in result["retained"] + result["removed"]:
        selected_lines = [line + b"\n" for line in pdb.splitlines() if line.startswith((b"ATOM  ", b"HETATM"))
                          and int(line[22:26]) == item["residue"]["resseq"]]
        assert item["sha256"] == hashlib.sha256(b"".join(selected_lines)).hexdigest()


@pytest.mark.parametrize("changes", [
    {"protein_selection":{"model":1,"chains":[{"chain":"B","entity_id":"1"}]}},
    {"protein_selection":{"model":1,"chains":[{"chain":"A","entity_id":"2"}]}},
    {"retained_hetero_residues":[residue()]}, {"retained_hetero_residues":[residue("HOH", 90)]},
    {"retained_hetero_residues":[residue("HOH", 40),residue("HOH", 40)]},
])
def test_receptor_selection_is_closed_and_reference_cannot_be_retained(changes):
    pdb, source = fixture()
    kwargs = dict(protein_selection={"model":1,"chains":[{"chain":"A","entity_id":"1"}]},
                  reference_residue=residue(), retained_hetero_residues=[])
    kwargs.update(changes)
    assert_invalid(api().prepare_receptor_bytes(pdb, source=source, **kwargs))


def test_no_proven_ligand_inspection_fails_closed():
    pdb, source = fixture()
    source["residues"][1]["role"] = "unproven"
    assert_invalid(api().inspect_pocket_candidates(pdb, source=source))


def test_different_resnames_at_same_residue_position_are_ambiguous_even_with_inventory():
    pdb, source = fixture()
    # Microheterogeneity cannot be resolved by declaring both names "complete".
    lines = pdb.decode().splitlines()
    lines.insert(-1, atom_line(9, "X1", "C", (2, 3, 4), residue("ALT")))
    pdb = ("\n".join(lines) + "\n").encode()
    source["residues"].append(dict(identity=residue("ALT"), record="HETATM", entity_id="2",
        role="deposited_ligand", atoms=[dict(serial=9, name="X1", element="C")]))
    assert_invalid(api().propose_pocket(pdb, source=rehash(pdb, source), residue=residue()))


def test_one_explicit_model_and_insertion_code_are_preserved():
    pdb, source = fixture()
    lines = pdb.decode().splitlines()
    for i in range(1, len(lines)-1):
        if lines[i][17:20] == "LIG": lines[i] = lines[i][:26] + "B" + lines[i][27:]
    lines.insert(1, "MODEL        7")
    lines.insert(-1, "ENDMDL")
    for item in source["residues"]: item["identity"]["model"] = 7
    source["residues"][1]["identity"]["icode"] = "B"
    source["proteins"][0]["model"] = 7
    pdb = ("\n".join(lines) + "\n").encode()
    result = api().propose_pocket(pdb, source=rehash(pdb, source), residue=residue(icode="B", model=7))
    assert result["ok"] and result["residue"] == residue(icode="B", model=7)
    assert result["center"] == [4, 1, 6]


def test_derived_pdb_uses_valid_fixed_width_ter_and_preserves_segment_boundary():
    pdb, source = fixture()
    lines = pdb.decode().splitlines()
    lines.insert(3, "TER       9      ALA A   1 ")
    lines.insert(4, atom_line(10, "CA", "C", (2, 2, 2), residue("GLY", 2), "ATOM"))
    source["residues"].append(dict(identity=residue("GLY", 2), record="ATOM", entity_id="1", role="protein",
        atoms=[dict(serial=10, name="CA", element="C")]))
    pdb = ("\n".join(lines) + "\n").encode()
    result = api().prepare_receptor_bytes(pdb, source=rehash(pdb, source),
        protein_selection={"model":1,"chains":[{"chain":"A","entity_id":"1"}]},
        reference_residue=residue(), retained_hetero_residues=[])
    assert result["ok"]
    records = result["pdb_bytes"].decode().splitlines()
    atom_end = next(i for i, line in enumerate(records) if line.startswith("ATOM") and line[12:16].strip() == "N")
    assert records[atom_end+1].startswith("TER")
    assert records[atom_end+2].startswith("ATOM")
    assert all(len(line) >= 27 and line[6:11].strip().isdigit() for line in records if line.startswith("TER"))


def test_multiple_eligible_residues_require_explicit_choice_not_largest():
    pdb, source = fixture()
    source["residues"][2]["role"] = "deposited_ligand"
    found = api().inspect_pocket_candidates(pdb, source=source)
    assert sum(c["eligible"] for c in found["candidates"]) == 2
    selected = api().propose_pocket(pdb, source=source, residue=residue("NAG", 30))
    assert selected["center"] == [40,40,40] and selected["atom_count"] == 1


@pytest.mark.parametrize("invalid", [None, [], True, {"schema_version":"docking-source-v1"}])
def test_non_native_or_incomplete_source_has_fixed_error(invalid):
    pdb, _ = fixture()
    assert_invalid(api().inspect_pocket_candidates(pdb, source=invalid))


@pytest.mark.parametrize("field", ["schema_version", "provider", "record", "record_id", "key"])
def test_source_string_subclasses_are_not_native_schema_values(field):
    class NonNative(str): pass
    pdb, source = fixture()
    if field == "schema_version": source[field] = NonNative(source[field])
    elif field == "provider": source["provenance"][field] = NonNative("wwpdb")
    elif field == "record_id": source["provenance"][field] = NonNative("1ABC")
    elif field == "key": source[NonNative("schema_version")] = source.pop("schema_version")
    else: source["residues"][0][field] = NonNative("ATOM")
    assert_invalid(api().propose_pocket(pdb, source=source, residue=residue()))


@pytest.mark.parametrize("record", ["MODEL        1 trailing", "NUMMDL       2"])
def test_malformed_or_incomplete_model_envelope_is_not_ignored(record):
    pdb, source = fixture()
    lines = pdb.decode().splitlines()
    lines.insert(1, record)
    if record.startswith("MODEL"): lines.insert(-1, "ENDMDL")
    pdb = ("\n".join(lines)+"\n").encode()
    assert_invalid(api().propose_pocket(pdb, source=rehash(pdb, source), residue=residue()))


def test_all_hydrogen_residue_is_not_an_eligible_pocket():
    pdb, source = fixture()
    lines = pdb.decode().splitlines()
    for index in (3,4,5): lines[index] = lines[index][:76] + " H" + lines[index][78:]
    for atom in source["residues"][1]["atoms"]: atom["element"] = "H"
    pdb = ("\n".join(lines)+"\n").encode()
    assert_invalid(api().inspect_pocket_candidates(pdb, source=rehash(pdb, source)))
