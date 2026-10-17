"""Independently authored SYNTHETIC contracts, never scientific source evidence."""

import copy
import hashlib
import importlib

import pytest

from tests.test_receptor_projection_pdb import fixture_lines, pack


def api():
    return importlib.import_module("src.docking.receptor_projection_source")


def identity(name, seq, chain="A"):
    return dict(model=1, chain=chain, resname=name, resseq=seq, icode="")


def declared_atom(serial, name, element, xyz, altloc="", occupancy=100):
    return dict(serial=serial, name=name, element=element, altloc=altloc,
                occupancy_hundredths=occupancy, xyz_milli=list(xyz),
                bfactor_hundredths=2000, charge="")


@pytest.fixture
def full_fixture():
    # Raw bytes come from P1's fixed fixture. Expected facts below are NOT read
    # from parse_original_pdb, raw atom fields, or a shared atom specification.
    raw = pack(fixture_lines())
    source = dict(
        schema_version="receptor-source-v1", sha256=hashlib.sha256(raw).hexdigest(),
        pdb_id="1ABC",
        provenance=dict(provider="wwpdb", version="2026-10-15", record_id="1ABC", proof_id="proof-1"),
        target=dict(uniprot_id="P06276", taxonomy_id=9606),
        proteins=[dict(model=1, chain="A", entity_id="1", uniprot_id="P06276", taxonomy_id=9606)],
        residues=[
            dict(identity=identity("ALA", 1), record="ATOM", entity_id="1", role="protein",
                 component=dict(record_id="ALA-context", version="v1", sha256="a" * 64),
                 required_heavy_atoms=[dict(name=n, element=e) for n, e in
                                       [("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O"), ("CB", "C")]],
                 atoms=[declared_atom(1, "N", "N", (-1250, 125, 375)),
                        declared_atom(2, "CA", "C", (100, 250, 500)),
                        declared_atom(3, "C", "C", (1400, 750, 650)),
                        declared_atom(4, "O", "O", (2100, 100, 1100)),
                        declared_atom(5, "CB", "C", (250, 1500, -750), "A", 60),
                        declared_atom(6, "CB", "C", (300, 1600, -900), "B", 40)]),
            dict(identity=identity("LIG", 20), record="HETATM", entity_id="2", role="deposited_ligand",
                 component=dict(record_id="LIG", version="v1", sha256="b" * 64),
                 required_heavy_atoms=[dict(name=n, element=e) for n, e in
                                       [("C1", "C"), ("O1", "O"), ("N1", "N")]],
                 atoms=[declared_atom(7, "C1", "C", (3000, 2000, 1000)),
                        declared_atom(8, "O1", "O", (3500, 3100, 1500)),
                        declared_atom(9, "N1", "N", (4200, 1500, 2000))]),
            dict(identity=identity("HOH", 40, "Z"), record="HETATM", entity_id="3", role="water",
                 component=dict(record_id="HOH", version="v1", sha256="c" * 64),
                 required_heavy_atoms=[dict(name="O", element="O")],
                 atoms=[declared_atom(10, "O", "O", (6500, 3000, 4000), occupancy=0)]),
        ], connections=[], conformer_constraints=[], limitations=[])
    return raw, source


def validate(raw, source):
    from src.docking.receptor_projection_pdb import parse_original_pdb
    return api().validate_projection_source(parse_original_pdb(raw), source)


def test_source_cannot_omit_unselected_atom(full_fixture):
    from src.docking.receptor_projection_pdb import ProjectionInvalid
    raw, source = full_fixture
    source["residues"][-1]["atoms"] = []
    with pytest.raises(ProjectionInvalid, match="^source_invalid$"):
        validate(raw, source)


def test_complete_source_detached_facts(full_fixture):
    raw, source = full_fixture
    result = validate(raw, source)
    assert result.source_authentication == "caller_responsibility"
    assert result.source_sha256 == source["sha256"]
    assert result.residues[0].identity == (1, "A", "ALA", 1, "")
    assert result.residues[-1].atoms[0].occupancy_hundredths == 0
    assert result.parsed.original_bytes is raw
    assert len(result.residues[0].atoms) == 6


def bad_source(raw, source, reason="source_invalid"):
    from src.docking.receptor_projection_pdb import ProjectionInvalid
    with pytest.raises(ProjectionInvalid) as caught:
        validate(raw, source)
    assert caught.value.args == (reason,)
    assert str(caught.value) == reason


@pytest.mark.parametrize("path,value", [
    (("sha256",), "0" * 64), (("schema_version",), "docking-source-v1"),
    (("pdb_id",), "2ABC"), (("provenance", "record_id"), "2ABC"),
    (("provenance", "provider"), "caller"), (("provenance", "version"), ""),
    (("target", "taxonomy_id"), True), (("target", "taxonomy_id"), 0),
    (("target", "uniprot_id"), "file:/secret"),
    (("proteins", 0, "entity_id"), "different"), (("proteins", 0, "chain"), "B"),
    (("proteins", 0, "model"), 2), (("proteins", 0, "taxonomy_id"), -1),
    (("residues", 0, "identity", "resseq"), True),
    (("residues", 0, "identity", "resname"), "GLY"),
    (("residues", 0, "record"), "HETATM"), (("residues", 0, "role"), "other"),
    (("residues", 2, "role"), "deposited_ligand"),
    (("residues", 0, "component", "sha256"), "unverified"),
    (("residues", 0, "required_heavy_atoms"), []),
    (("residues", 0, "atoms", 0, "serial"), True),
    (("residues", 0, "atoms", 0, "serial"), 99),
    (("residues", 0, "atoms", 0, "name"), "NN"),
    (("residues", 0, "atoms", 0, "element"), "C"),
    (("residues", 0, "atoms", 0, "altloc"), "A"),
    (("residues", 0, "atoms", 0, "xyz_milli"), [-1250, 125, 376]),
    (("residues", 0, "atoms", 0, "xyz_milli"), [-1250, 125, True]),
    (("residues", 0, "atoms", 0, "occupancy_hundredths"), 99),
    (("residues", 0, "atoms", 0, "bfactor_hundredths"), 2001),
    (("residues", 0, "atoms", 0, "charge"), "1+"),
])
def test_inventory_mutations_are_fixed_safe_errors(full_fixture, path, value):
    raw, source = full_fixture
    cursor = source
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = value
    bad_source(raw, source)


@pytest.mark.parametrize("path", [(), ("provenance",), ("target",), ("proteins", 0),
    ("residues", 0), ("residues", 0, "identity"), ("residues", 0, "component"),
    ("residues", 0, "atoms", 0), ("residues", 0, "required_heavy_atoms", 0)])
@pytest.mark.parametrize("operation", ["extra", "missing", "subclass"])
def test_every_dictionary_is_closed_and_native(full_fixture, path, operation):
    raw, source = full_fixture
    cursor = source
    for key in path:
        cursor = cursor[key]
    if operation == "extra":
        cursor["secret-file-path"] = "private"
    elif operation == "missing":
        cursor.pop(next(iter(cursor)))
    else:
        class NonNative(dict):
            pass
        if not path:
            source = NonNative(source)
        else:
            parent = source
            for key in path[:-1]:
                parent = parent[key]
            parent[path[-1]] = NonNative(cursor)
    bad_source(raw, source)


@pytest.mark.parametrize("operation", ["missing", "extra", "duplicate", "residue_duplicate", "protein_duplicate"])
def test_inventory_multiset_is_exact(full_fixture, operation):
    raw, source = full_fixture
    atoms = source["residues"][0]["atoms"]
    if operation == "missing": atoms.pop()
    elif operation == "extra": atoms.append(declared_atom(11, "X", "C", (0, 0, 0)))
    elif operation == "duplicate": atoms.append(copy.deepcopy(atoms[0]))
    elif operation == "residue_duplicate": source["residues"].append(copy.deepcopy(source["residues"][0]))
    else: source["proteins"].append(copy.deepcopy(source["proteins"][0]))
    bad_source(raw, source)


def test_order_and_immutable_facts_and_descriptor_digest(full_fixture):
    import json
    from tests.test_receptor_projection_pdb import assert_immutable
    raw, source = full_fixture
    source["residues"].reverse()
    for row in source["residues"]: row["atoms"].reverse()
    digest = hashlib.sha256(json.dumps(source, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()
    result = validate(raw, source)
    assert_immutable(result)
    assert result.descriptor_sha256 == digest
    assert [a.serial for r in result.residues for a in r.atoms] == list(range(1, 11))
    source["target"]["taxonomy_id"] = 1
    source["residues"].clear()
    assert result.target.taxonomy_id == 9606
    assert result.residues[0].required_heavy_atoms[-1].name == "CB"


def test_required_definition_is_not_observed_completeness(full_fixture):
    raw, source = full_fixture
    # Independently declared terminal oxygen is absent from BOTH conformers.
    source["residues"][0]["required_heavy_atoms"].append(dict(name="OXT", element="O"))
    result = validate(raw, source)
    assert result.residues[0].required_heavy_atoms[-1].name == "OXT"


@pytest.mark.parametrize("bad", [[dict(name="CB", element="N")],
    [dict(name="H", element="H")], [dict(name="D", element="D")],
    [dict(name="O", element="O"), dict(name="O", element="O")]])
def test_chemical_definition_is_validated_separately(full_fixture, bad):
    raw, source = full_fixture
    source["residues"][0]["required_heavy_atoms"].extend(bad)
    bad_source(raw, source)


@pytest.mark.parametrize("native", [list, str, int])
def test_scalar_and_list_subclasses_never_coerce(full_fixture, native):
    class NonNative(native): pass
    raw, source = full_fixture
    if native is list: source["residues"] = NonNative(source["residues"])
    elif native is str: source["pdb_id"] = NonNative("1ABC")
    else: source["target"]["taxonomy_id"] = NonNative(9606)
    bad_source(raw, source)


@pytest.mark.parametrize("mutation,reason", [("cycle", "source_invalid"),
    ("depth", "resource_limit"), ("long", "resource_limit"), ("large_list", "resource_limit")])
def test_bounded_native_walk_precedes_hashing(full_fixture, monkeypatch, mutation, reason):
    raw, source = full_fixture
    if mutation == "cycle": source["limitations"].append(source)
    elif mutation == "depth":
        value = 1
        for _ in range(14): value = [value]
        source["limitations"] = value
    elif mutation == "long": source["provenance"]["proof_id"] = "s" * 129
    else: source["conformer_constraints"] = [None] * 257
    def forbidden(*args, **kwargs): pytest.fail("hashed or serialized before bounded traversal")
    monkeypatch.setattr(api().hashlib, "sha256", forbidden)
    monkeypatch.setattr(api().json, "dumps", forbidden)
    bad_source(raw, source, reason)


@pytest.fixture(params=["LINK", "SSBOND", "CONECT"])
def connected_fixture(request, full_fixture):
    from tests.test_receptor_projection_pdb import connection_fixture
    _, source = full_fixture
    record = request.param
    raw = pack(connection_fixture(record))
    if record == "SSBOND":
        source["proteins"].append(dict(model=1, chain="B", entity_id="4", uniprot_id="Q12345", taxonomy_id=10090))
        for serial, seq, xyz in [(11, 2, (1000, 2000, 3000)), (12, 3, (2000, 3000, 4000))]:
            source["residues"].append(dict(identity=identity("CYS", seq, "B"), record="ATOM",
                entity_id="4", role="protein", component=dict(record_id="CYS-context", version="v1", sha256="d" * 64),
                atoms=[declared_atom(serial, "SG", "S", xyz)],
                required_heavy_atoms=[dict(name=n, element=e) for n, e in
                                      [("N", "N"), ("CA", "C"), ("C", "C"), ("O", "O"), ("CB", "C"), ("SG", "S")]]))
    source["sha256"] = hashlib.sha256(raw).hexdigest()
    source["connections"] = [dict(record_index=12 if record == "CONECT" else 2,
        record_type=record, atom_serials={"LINK": [5, 7], "SSBOND": [11, 12], "CONECT": [7, 5, 5, 6, 8]}[record],
        kind="disulfide" if record == "SSBOND" else "unknown",
        symmetry=[] if record == "CONECT" else ["identity", "identity"])]
    return raw, source


def test_connections_preserve_all_p1_facts_and_independent_kind(connected_fixture):
    from tests.test_receptor_projection_pdb import assert_immutable
    raw, source = connected_fixture
    result = validate(raw, source)
    connection, = result.connections
    assert connection.parsed is result.parsed.connections[0]
    assert connection.kind == source["connections"][0]["kind"]
    assert connection.parsed.atom_serials == tuple(source["connections"][0]["atom_serials"])
    assert connection.parsed.record_index == source["connections"][0]["record_index"]
    assert connection.parsed.symmetry == tuple(source["connections"][0]["symmetry"])
    assert_immutable(result)


@pytest.mark.parametrize("mutation", ["omit", "duplicate", "index", "type", "serial", "order", "symmetry", "kind", "extra", "missing"])
def test_connection_inventory_is_closed_and_exact(connected_fixture, mutation):
    raw, source = connected_fixture
    row = source["connections"][0]
    if mutation == "omit": source["connections"].clear()
    elif mutation == "duplicate": source["connections"].append(copy.deepcopy(row))
    elif mutation == "index": row["record_index"] += 1
    elif mutation == "type": row["record_type"] = "TER"
    elif mutation == "serial": row["atom_serials"][0] = 99
    elif mutation == "order": row["atom_serials"].reverse()
    elif mutation == "symmetry": row["symmetry"] = ["nonidentity", "identity"]
    elif mutation == "kind": row["kind"] = "distance-inferred"
    elif mutation == "extra": row["secret"] = "private"
    else: row.pop("kind")
    bad_source(raw, source)


def test_hydrogens_must_be_present_in_inventory(full_fixture):
    from tests.test_docking_pocket_evidence import atom_line
    raw, source = full_fixture
    lines = fixture_lines()
    lines.insert(-1, atom_line(11, "H1", "H", (3, 2, 1), identity("LIG", 20)))
    raw = pack(lines)
    source["sha256"] = hashlib.sha256(raw).hexdigest()
    bad_source(raw, source)
    source["residues"][1]["atoms"].append(declared_atom(11, "H1", "H", (3000, 2000, 1000)))
    result = validate(raw, source)
    assert result.residues[1].atoms[-1].element == "H"
    assert len(result.residues[1].required_heavy_atoms) == 3


def test_modified_hetatm_protein_is_not_removable_hetero(full_fixture):
    from tests.test_receptor_projection_pdb import change
    _, source = full_fixture
    lines = fixture_lines()
    for i in range(1, 7):
        lines[i] = change(change(lines[i], 0, 6, "HETATM"), 17, 20, "MSE")
    raw = pack(lines)
    source["sha256"] = hashlib.sha256(raw).hexdigest()
    row = source["residues"][0]
    row["identity"]["resname"] = "MSE"
    row["record"] = "HETATM"
    row["component"]["record_id"] = "MSE-context"
    result = validate(raw, source)
    assert result.residues[0].role == "protein"
    for role in ["other", "deposited_ligand", "cofactor"]:
        row["role"] = role
        bad_source(raw, source)


def test_non_target_protein_incomplete_inventory_is_allowed(connected_fixture):
    raw, source = connected_fixture
    result = validate(raw, source)
    if len(result.proteins) == 2:
        assert result.proteins[1].taxonomy_id != result.target.taxonomy_id
        assert len(result.residues[-1].required_heavy_atoms) > len(result.residues[-1].atoms)


@pytest.fixture
def constrained_fixture(full_fixture):
    from tests.test_docking_pocket_evidence import atom_line
    _, source = full_fixture
    lines = fixture_lines()
    lines[-1:-1] = [atom_line(11, "C1", "C", (1, 2, 3), identity("NAG", 30), altloc="X", occupancy=.5),
                    atom_line(12, "C1", "C", (2, 3, 4), identity("NAG", 30), altloc="Y", occupancy=.5)]
    raw = pack(lines)
    source["sha256"] = hashlib.sha256(raw).hexdigest()
    source["residues"].append(dict(identity=identity("NAG", 30), record="HETATM", role="glycan", entity_id="4",
        component=dict(record_id="NAG", version="v1", sha256="d" * 64),
        required_heavy_atoms=[dict(name="C1", element="C"), dict(name="O1", element="O")],
        atoms=[declared_atom(11, "C1", "C", (1000, 2000, 3000), "X", 50),
               declared_atom(12, "C1", "C", (2000, 3000, 4000), "Y", 50)]))
    source["conformer_constraints"] = [dict(id="pair-1", residues=[identity("ALA", 1), identity("NAG", 30)],
        allowed_assignments=[["A", "Y"], ["B", "X"]])]
    source["limitations"] = [dict(id="gap-1", kind="missing_residues", residues=[identity("GLY", 2)], source_record_id="missing-1"),
        dict(id="variant-1", kind="sequence_variant", residues=[identity("ALA", 1)], source_record_id="variant-1")]
    return raw, source


def test_constraints_and_limitations_are_frozen_explicit_facts(constrained_fixture):
    from tests.test_receptor_projection_pdb import assert_immutable
    raw, source = constrained_fixture
    result = validate(raw, source)
    group, = result.conformer_constraints
    assert group.residues == ((1, "A", "ALA", 1, ""), (1, "A", "NAG", 30, ""))
    assert group.allowed_assignments == (("A", "Y"), ("B", "X"))
    assert result.limitations[0].residues == ((1, "A", "GLY", 2, ""),)
    source["conformer_constraints"][0]["allowed_assignments"][0][0] = "B"
    assert group.allowed_assignments[0] == ("A", "Y")
    assert_immutable(result)


@pytest.mark.parametrize("reverse", [False, True])
def test_absent_variant_requires_exact_explicit_missing_residue(constrained_fixture, reverse):
    raw, source = constrained_fixture
    source["limitations"][1]["residues"] = [identity("GLY", 2)]
    if reverse:
        source["limitations"].reverse()
    result = validate(raw, source)
    variant = next(row for row in result.limitations if row.kind == "sequence_variant")
    assert variant.residues == ((1, "A", "GLY", 2, ""),)
    assert all(row.identity != variant.residues[0] for row in result.residues)


@pytest.mark.parametrize("mutation", ["no_gap", "truncation", "name", "position", "insertion"])
def test_absent_variant_cannot_use_unrelated_absence(constrained_fixture, mutation):
    raw, source = constrained_fixture
    source["limitations"][1]["residues"] = [identity("GLY", 2)]
    gap = source["limitations"][0]
    if mutation == "no_gap": source["limitations"].pop(0)
    elif mutation == "truncation": gap["kind"] = "construct_truncation"
    elif mutation == "name": gap["residues"][0]["resname"] = "ALA"
    elif mutation == "position": gap["residues"][0]["resseq"] = 3
    else: gap["residues"][0]["icode"] = "A"
    bad_source(raw, source)


@pytest.mark.parametrize("mutation", ["empty", "absent", "label", "width", "duplicate", "duplicate_residue",
    "duplicate_id", "missing_key", "extra_key", "no_assignments", "blank", "boolean"])
def test_constraints_never_invent_or_drop_recorded_dependencies(constrained_fixture, mutation):
    raw, source = constrained_fixture
    row = source["conformer_constraints"][0]
    if mutation == "empty": row["residues"] = []
    elif mutation == "absent": row["residues"][0]["resseq"] = 99
    elif mutation == "label": row["allowed_assignments"][0][0] = "X"
    elif mutation == "width": row["allowed_assignments"][0].pop()
    elif mutation == "duplicate": row["allowed_assignments"].append(copy.deepcopy(row["allowed_assignments"][0]))
    elif mutation == "duplicate_residue": row["residues"][1] = copy.deepcopy(row["residues"][0])
    elif mutation == "duplicate_id": source["conformer_constraints"].append(copy.deepcopy(row))
    elif mutation == "missing_key": row.pop("allowed_assignments")
    elif mutation == "extra_key": row["extra"] = "private"
    elif mutation == "no_assignments": row["allowed_assignments"] = []
    elif mutation == "blank": row["allowed_assignments"][0][0] = ""
    else: row["allowed_assignments"][0][0] = True
    bad_source(raw, source)


@pytest.mark.parametrize("mutation", ["empty", "chain", "model", "collision", "id", "kind", "record", "extra", "duplicate", "missing"])
def test_only_reviewable_identity_bound_limitations(constrained_fixture, mutation):
    raw, source = constrained_fixture
    row = source["limitations"][0]
    if mutation == "empty": row["residues"] = []
    elif mutation == "chain": row["residues"][0]["chain"] = "Z"
    elif mutation == "model": row["residues"][0]["model"] = 2
    elif mutation == "collision": row["residues"][0]["resseq"] = 1
    elif mutation == "id": row["id"] = ""
    elif mutation == "kind": row["kind"] = "missing_required_atoms"
    elif mutation == "record": row["source_record_id"] = ""
    elif mutation == "extra": row["override"] = 1
    elif mutation == "duplicate": source["limitations"].append(copy.deepcopy(row))
    else: row.pop("source_record_id")
    bad_source(raw, source)


@pytest.mark.parametrize("field,value", [("role", []), ("record", {}), ("element", []), ("element", {})])
def test_wrong_native_container_cannot_escape_safe_error(full_fixture, field, value):
    raw, source = full_fixture
    if field == "element": source["residues"][0]["required_heavy_atoms"][0][field] = value
    else: source["residues"][0][field] = value
    bad_source(raw, source)


def test_walk_exact_escaped_utf8_size_and_depth_boundary():
    import json
    module = api()
    value = {"x": ["\\\"\n\u0000é中😀", -123, 0]}
    stats = module.check_projection_resources(value)
    assert stats.json_bytes == len(json.dumps(value, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8"))
    at_limit = "x"
    for _ in range(12): at_limit = [at_limit]
    module.check_projection_resources(at_limit)
    from src.docking.receptor_projection_pdb import ProjectionInvalid
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        module.check_projection_resources([at_limit])


@pytest.mark.parametrize("value", [None, True, 1.5, float("nan"), (), b"source", {1: "x"}, {"x": "\ud800"}])
def test_resource_walk_only_native_json_contract_types(value):
    from src.docking.receptor_projection_pdb import ProjectionInvalid
    with pytest.raises(ProjectionInvalid, match="^source_invalid$"):
        api().check_projection_resources(value)


@pytest.mark.parametrize("field,value", [("MAX_NODES", 20), ("MAX_SOURCE_JSON_BYTES", 100), ("MAX_DEPTH", 3)])
def test_lowered_traversal_gates_precede_hash_or_index(full_fixture, monkeypatch, field, value):
    raw, source = full_fixture
    module = api()
    monkeypatch.setattr(module, field, value)
    def forbidden(*args, **kwargs): pytest.fail("allocation/hash reached after failed traversal gate")
    monkeypatch.setattr(module, "_index_parsed", forbidden)
    monkeypatch.setattr(module, "_descriptor_digest", forbidden)
    monkeypatch.setattr(module.hashlib, "sha256", forbidden)
    bad_source(raw, source, "resource_limit")


def test_node_count_boundary_counts_keys_and_scalar_values(monkeypatch):
    from src.docking.receptor_projection_pdb import ProjectionInvalid
    module = api()
    monkeypatch.setattr(module, "MAX_NODES", 4)
    assert module.check_projection_resources({"x": [1]}).nodes == 4
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        module.check_projection_resources({"x": [1, 2]})


def test_source_and_selection_serialization_caps_are_distinct(monkeypatch):
    from src.docking.receptor_projection_pdb import ProjectionInvalid
    module = api()
    assert module.MAX_SOURCE_JSON_BYTES == 64 * 1024 * 1024
    assert module.MAX_SELECTION_JSON_BYTES == 8 * 1024 * 1024
    monkeypatch.setattr(module, "MAX_SELECTION_JSON_BYTES", 10)
    module.check_projection_resources(["abcdefghijkl"], kind="source")
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        module.check_projection_resources(["abcdefghijkl"], kind="selection")


@pytest.mark.parametrize("field,count", [("conformer_constraints", 257), ("proteins", 64),
    ("residues", 100000), ("connections", 100000), ("limitations", 100000)])
def test_tighter_semantic_list_caps_precede_child_visits(full_fixture, monkeypatch, field, count):
    raw, source = full_fixture
    # object() would give source_invalid if traversed: bounds must fire first.
    source[field] = [object()] * count
    def forbidden(*args, **kwargs): pytest.fail("serializer called on oversized list")
    monkeypatch.setattr(api(), "_descriptor_digest", forbidden)
    bad_source(raw, source, "resource_limit")


@pytest.mark.parametrize("field,count", [("residues", 65), ("allowed_assignments", 257)])
def test_constraint_semantic_caps_precede_child_visits(constrained_fixture, field, count):
    raw, source = constrained_fixture
    source["conformer_constraints"][0][field] = [object()] * count
    bad_source(raw, source, "resource_limit")


def test_atom_aggregate_cap_before_visiting_second_list():
    from src.docking.receptor_projection_pdb import ProjectionInvalid
    # Cheap scalar entries exercise aggregate sizing without a giant atom tree.
    value = [{"atoms": [0] * 60000}, {"atoms": [object()] * 40000}]
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        api().check_projection_resources(value)


@pytest.mark.parametrize("gate", ["parse", "index", "serialization"])
def test_one_budget_includes_retained_p1_before_every_allocation(full_fixture, monkeypatch, gate):
    from src.docking.receptor_projection_pdb import parse_original_pdb, AllocationBudget, ProjectionInvalid
    module = api()
    raw, source = full_fixture
    parsed = parse_original_pdb(raw)
    events = []
    original_reserve = AllocationBudget.reserve
    original_index = module._index_parsed
    original_digest = module._descriptor_digest
    def record_reserve(self, amount):
        original_reserve(self, amount)
        events.append((id(self), self.used, amount))
    def record_index(value):
        events.append(("index", events[-1][1]))
        return original_index(value)
    def record_digest(value):
        events.append(("serialization", events[-1][1]))
        return original_digest(value)
    monkeypatch.setattr(AllocationBudget, "reserve", record_reserve)
    monkeypatch.setattr(module, "_index_parsed", record_index)
    monkeypatch.setattr(module, "_descriptor_digest", record_digest)
    result = module.validate_projection_source(parsed, source)
    charges = [e for e in events if len(e) == 3]
    assert len({e[0] for e in charges}) == 1
    assert charges[0][2] == module._parsed_charge(parsed)
    assert result.logical_charge_bytes == charges[-1][1] <= 512 * 1024 * 1024
    boundary = charges[0][1] if gate == "parse" else next(e[1] for e in events if e[0] == gate)
    monkeypatch.setattr(module, "MAX_LOGICAL_BYTES", boundary - 1)
    def forbidden(*args, **kwargs): pytest.fail("guarded allocation ran before reservation")
    monkeypatch.setattr(module, "_descriptor_digest", forbidden)
    if gate in {"parse", "index"}:
        monkeypatch.setattr(module, "_index_parsed", forbidden)
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        module.validate_projection_source(parsed, source)


def test_p1_ledger_recomputed_with_ter_and_connection(connected_fixture):
    from src.docking.receptor_projection_pdb import parse_original_pdb, _preflight
    raw, source = connected_fixture
    assert api()._parsed_charge(parse_original_pdb(raw)) == _preflight(raw).used
    lines = raw.decode("ascii").splitlines()
    # A TER closes ALA and shifts every later physical record index by one.
    at = next(i for i, line in enumerate(lines) if line.startswith("HETATM"))
    lines.insert(at, "TER      13      ALA A   1 ")
    raw = pack(lines)
    assert api()._parsed_charge(parse_original_pdb(raw)) == _preflight(raw).used


def test_harmless_alias_is_charged_each_occurrence_and_detached(full_fixture):
    raw, source = full_fixture
    shared = source["residues"][0]["component"]
    source["residues"][1]["component"] = shared
    result = validate(raw, source)
    shared["version"] = "changed"
    assert result.residues[0].component.version == result.residues[1].component.version == "v1"


def test_bounded_source_string_is_never_treated_as_path_or_code(full_fixture):
    raw, source = full_fixture
    source["provenance"]["proof_id"] = "https:example.invalid"
    assert validate(raw, source).source_authentication == "caller_responsibility"


@pytest.mark.parametrize("mutation", ["drop_multiplicity", "wrong_symmetry_type", "boolean_endpoint", "boolean_index"])
def test_connection_endpoint_semantics_cannot_coerce(connected_fixture, mutation):
    raw, source = connected_fixture
    row = source["connections"][0]
    if mutation == "drop_multiplicity": row["atom_serials"].pop()
    elif mutation == "wrong_symmetry_type": row["symmetry"] = "identity"
    elif mutation == "boolean_endpoint": row["atom_serials"][0] = True
    else: row["record_index"] = True
    bad_source(raw, source)


def _paths(value, path=()):
    yield path, value
    if type(value) is dict:
        for key, child in value.items():
            yield from _paths(child, path + (key,))
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from _paths(child, path + (index,))


def test_all_schema_positions_wrong_native_types_have_fixed_errors(constrained_fixture):
    """Probe every schema/container/scalar position, not just a handful of atoms."""
    from src.docking.receptor_projection_pdb import parse_original_pdb, ProjectionInvalid
    raw, source = constrained_fixture
    parsed = parse_original_pdb(raw)
    probes = 0
    for path, original in _paths(source):
        for replacement in [None, True, [], {}, "", 1, 1.0, (), b"x"]:
            if type(replacement) is type(original):
                continue
            candidate = copy.deepcopy(source)
            if path:
                parent = candidate
                for key in path[:-1]: parent = parent[key]
                parent[path[-1]] = replacement
            else:
                candidate = replacement
            with pytest.raises(ProjectionInvalid) as caught:
                api().validate_projection_source(parsed, candidate)
            assert caught.value.args == ("source_invalid",)
            probes += 1
    assert probes > 1500


def test_no_coercion_hooks_on_untrusted_objects(full_fixture):
    raw, source = full_fixture
    class HostileList(list):
        def __iter__(self): pytest.fail("caller iterator executed")
        def __len__(self): pytest.fail("caller length executed")
    class HostileDict(dict):
        def items(self): pytest.fail("caller items executed")
        def __len__(self): pytest.fail("caller length executed")
    class HostileInt(int):
        def __int__(self): pytest.fail("caller coercion executed")
    class HostileStr(str):
        def __str__(self): pytest.fail("caller string executed")
    for value in [HostileList(), HostileDict(), HostileInt(1), HostileStr("wwpdb")]:
        source["provenance"]["provider"] = value
        bad_source(raw, source)


def test_raw_coordinate_change_cannot_be_hidden_by_rehash(full_fixture):
    from tests.test_receptor_projection_pdb import change
    _, source = full_fixture
    lines = fixture_lines()
    lines[10] = change(lines[10], 30, 38, "   6.501")
    raw = pack(lines)
    source["sha256"] = hashlib.sha256(raw).hexdigest()
    bad_source(raw, source)


def test_safe_reason_extension_only_and_no_new_execution_entrypoints():
    from src.docking.receptor_projection_pdb import ProjectionInvalid, _REASONS
    assert _REASONS == {"raw_invalid", "connection_invalid", "resource_limit", "source_invalid",
                        "selection_invalid", "selection_incomplete", "incomplete_heavy_atoms",
                        "occupancy_invalid", "reference_invalid", "constraint_invalid"}
    assert str(ProjectionInvalid("source_invalid")) == "source_invalid"
    for name in ["inspect_receptor_projection", "project_receptor_bytes", "verify_receptor_projection"]:
        assert not hasattr(api(), name)


def test_component_and_assignment_reservation_precedes_construction(constrained_fixture, monkeypatch):
    from src.docking.receptor_projection_pdb import AllocationBudget, ProjectionInvalid
    module = api()
    reserve = AllocationBudget.reserve
    def forbid(*args, **kwargs): pytest.fail("fact constructed after failed reservation")
    raw, source = constrained_fixture
    for symbol, amount in [("HeavyAtom", 5 * 512), ("ConformerConstraint", 2 * 64 + 128)]:
        with monkeypatch.context() as patch:
            entered = [symbol == "HeavyAtom"]
            original_constraints = module._constraints
            def mark_constraints(*args):
                entered[0] = True
                return original_constraints(*args)
            def deny(self, size):
                if entered[0] and size == amount:
                    raise ProjectionInvalid("resource_limit")
                return reserve(self, size)
            patch.setattr(AllocationBudget, "reserve", deny)
            patch.setattr(module, "_constraints", mark_constraints)
            patch.setattr(module, symbol, forbid)
            bad_source(raw, source, "resource_limit")


def test_empty_inventory_never_succeeds(full_fixture):
    raw, source = full_fixture
    source["residues"] = []
    bad_source(raw, source)
