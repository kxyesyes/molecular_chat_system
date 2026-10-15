"""Independent SYNTHETIC P3 selections; not source authentication or docking."""

import copy
import hashlib
import importlib
import importlib.util
import json

import pytest

from tests.test_receptor_projection_source import full_fixture, identity, declared_atom
from tests.test_receptor_projection_pdb import fixture_lines, pack, change, assert_immutable


def selection():
    # Authored independently of the parser and source validator.
    return dict(protein_selection=dict(model=1, chains=[dict(chain="A", entity_id="1")]),
                reference_residue=identity("LIG", 20),
                hetero_choices=[dict(residue=identity("HOH", 40, "Z"), action="remove")],
                conformer_choices=[dict(residue=identity("ALA", 1), altloc="A")],
                reviewed_limitations=[])


def api():
    name = "src.docking.receptor_projection"
    assert importlib.util.find_spec(name) is not None, "P3 inspection callable missing"
    module = importlib.import_module(name)
    assert callable(getattr(module, "inspect_receptor_projection", None)), "P3 inspection callable missing"
    return module


def prepare(raw, source):
    from src.docking.receptor_projection_pdb import parse_original_pdb
    from src.docking.receptor_projection_source import validate_projection_source
    parsed = parse_original_pdb(raw)
    return parsed, validate_projection_source(parsed, source)


def select(raw, source, choice=None):
    module = importlib.import_module("src.docking.receptor_projection_source")
    call = getattr(module, "validate_projection_selection", None)
    assert callable(call), "P3 selection callable missing"
    return call(*prepare(raw, source), selection() if choice is None else choice)


def rejected(raw, source, choice, reason):
    from src.docking.receptor_projection_pdb import ProjectionInvalid
    with pytest.raises(ProjectionInvalid) as caught:
        select(raw, source, choice)
    assert caught.value.args == (reason,)


def test_inspection_is_complete_closed_and_never_ready(full_fixture):
    raw, source = full_fixture
    result = api().inspect_receptor_projection(raw, source=source)
    assert set(result) == {"ok", "source_authentication", "source_sha256", "choices", "limitations", "input_ready"}
    assert result["ok"] is True and result["input_ready"] is False
    assert result["source_authentication"] == "caller_responsibility"
    assert result["source_sha256"] == hashlib.sha256(raw).hexdigest()
    choices = result["choices"]
    assert choices["reference_residue"] == [identity("LIG", 20)]
    assert choices["hetero_choices"][-1] == dict(residue=identity("HOH", 40, "Z"), role="water", actions=["retain", "remove"])
    assert choices["conformer_choices"] == [dict(residue=identity("ALA", 1), altlocs=["A", "B"])]
    assert sum(len(r["atoms"]) for r in choices["residues"]) == 10
    assert choices["residues"][-1]["atoms"][0]["occupancy_hundredths"] == 0
    source["residues"].clear()
    assert len(choices["residues"]) == 3
    assert "pdb_bytes" not in result  # Inspection remains separate from P4.


@pytest.mark.parametrize("label,retained,excluded", [("A", (1, 2, 3, 4, 5), 6), ("B", (1, 2, 3, 4, 6), 5)])
def test_explicit_choice_frozen_detached_canonical_facts(full_fixture, label, retained, excluded):
    raw, source = full_fixture
    choice = selection()
    choice["conformer_choices"][0]["altloc"] = label
    result = select(raw, source, choice)
    assert_immutable(result)
    assert result.retained_serials == retained
    assert result.reference_serials == (7, 8, 9)
    assert dict(result.atom_reasons)[excluded] == "unselected_conformer"
    assert dict(result.atom_reasons)[7] == "reference_removed"
    assert dict(result.atom_reasons)[10] == "explicit_hetero_removal"
    expected = json.dumps(choice, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    assert result.canonical_json == expected
    assert result.selection_sha256 == hashlib.sha256(expected).hexdigest()
    choice["conformer_choices"].clear()
    source["residues"].clear()
    assert result.canonical_json == expected
    assert raw == pack(fixture_lines())


@pytest.mark.parametrize("field", ["conformer_choices", "hetero_choices"])
def test_no_implicit_choice(full_fixture, field):
    raw, source = full_fixture
    choice = selection()
    choice[field] = []
    rejected(raw, source, choice, "selection_incomplete")


@pytest.mark.parametrize("field", ["conformer_choices", "hetero_choices"])
@pytest.mark.parametrize("mode", ["duplicate", "extra"])
def test_no_extra_or_duplicate_choices(full_fixture, field, mode):
    raw, source = full_fixture
    choice = selection()
    row = copy.deepcopy(choice[field][0])
    if mode == "extra": row["residue"] = identity("UNK", 99)
    choice[field].append(row)
    rejected(raw, source, choice, "selection_invalid")


@pytest.mark.parametrize("label", ["", "C", "AB", True, 1])
def test_no_unobserved_blank_or_per_atom_conformer(full_fixture, label):
    raw, source = full_fixture
    choice = selection()
    choice["conformer_choices"][0]["altloc"] = label
    rejected(raw, source, choice, "selection_invalid")


@pytest.mark.parametrize("path,value", [
    (("protein_selection", "model"), 2), (("protein_selection", "model"), True),
    (("protein_selection", "chains", 0, "chain"), "B"),
    (("protein_selection", "chains", 0, "entity_id"), "2"),
    (("protein_selection", "chains"), []),
    (("hetero_choices", 0, "action"), "default"),
    (("reference_residue", "resname"), "ALA"),
])
def test_invalid_selection_fields(full_fixture, path, value):
    raw, source = full_fixture
    choice = selection()
    cursor = choice
    for key in path[:-1]: cursor = cursor[key]
    cursor[path[-1]] = value
    rejected(raw, source, choice, "selection_invalid")


@pytest.mark.parametrize("field,value", [("taxonomy_id", 10090), ("uniprot_id", "Q12345")])
def test_every_selected_chain_matches_target(full_fixture, field, value):
    raw, source = full_fixture
    source["proteins"][0][field] = value
    rejected(raw, source, selection(), "selection_invalid")


@pytest.mark.parametrize("mutation,reason", [("source", "source_invalid"), ("raw", "raw_invalid")])
def test_inspection_safe_closed_failure(full_fixture, mutation, reason):
    raw, source = full_fixture
    if mutation == "source": source["private-path-secret"] = "C:/private/token"
    else: raw += b"PRIVATE TOKEN\n"
    result = api().inspect_receptor_projection(raw, source=source)
    assert result == dict(ok=False, code="RECEPTOR_PROJECTION_INVALID", reason=reason,
                          message="Receptor projection input is invalid.")


def repack(lines, source):
    raw = pack(lines)
    source["sha256"] = hashlib.sha256(raw).hexdigest()
    return raw


@pytest.mark.parametrize("serial", [1, 5, 7, 10])
def test_no_active_atom_at_zero_occupancy(full_fixture, serial):
    _, source = full_fixture
    lines = fixture_lines()
    lines[serial] = change(lines[serial], 54, 60, "  0.00")
    for row in source["residues"]:
        for atom in row["atoms"]:
            if atom["serial"] == serial: atom["occupancy_hundredths"] = 0
    choice = selection()
    if serial == 10: choice["hetero_choices"][0]["action"] = "retain"
    rejected(repack(lines, source), source, choice, "occupancy_invalid")


@pytest.mark.parametrize("serial,element", [(11, "H"), (12, "D")])
@pytest.mark.parametrize("reference", [True, False])
def test_zero_hydrogen_not_hidden_by_heavy_completeness(full_fixture, serial, element, reference):
    from tests.test_docking_pocket_evidence import atom_line
    _, source = full_fixture
    row = source["residues"][1 if reference else 0]
    lines = fixture_lines()
    lines.insert(-1, atom_line(serial, "H1", element, (1, 2, 3), row["identity"], row["record"], occupancy=0))
    row["atoms"].append(declared_atom(serial, "H1", element, (1000, 2000, 3000), occupancy=0))
    rejected(repack(lines, source), source, selection(), "occupancy_invalid")


@pytest.mark.parametrize("occupancy", [1, 99])
def test_reference_heavy_atoms_require_unit_occupancy(full_fixture, occupancy):
    _, source = full_fixture
    lines = fixture_lines()
    lines[8] = change(lines[8], 54, 60, f"{occupancy / 100:6.2f}")
    source["residues"][1]["atoms"][1]["occupancy_hundredths"] = occupancy
    rejected(repack(lines, source), source, selection(), "reference_invalid")


@pytest.mark.parametrize("index", [0, 1, 2])
def test_required_heavy_definition_not_observed_atoms(full_fixture, index):
    _, source = full_fixture
    lines = fixture_lines()
    choice = selection()
    if index == 2:
        lines[10] = change(lines[10], 54, 60, "  1.00")
        source["residues"][2]["atoms"][0]["occupancy_hundredths"] = 100
        choice["hetero_choices"][0]["action"] = "retain"
    source["residues"][index]["required_heavy_atoms"].append(dict(name="OX", element="O"))
    rejected(repack(lines, source), source, choice, "incomplete_heavy_atoms")


@pytest.mark.parametrize("label", ["A", "B"])
def test_no_mixing_atom_names_across_conformers(full_fixture, label):
    _, source = full_fixture
    lines = fixture_lines()
    lines[6] = change(lines[6], 12, 16, " CG ")
    source["residues"][0]["atoms"][5]["name"] = "CG"
    source["residues"][0]["required_heavy_atoms"].append(dict(name="CG", element="C"))
    choice = selection()
    choice["conformer_choices"][0]["altloc"] = label
    rejected(repack(lines, source), source, choice, "incomplete_heavy_atoms")


@pytest.mark.parametrize("role", ["glycan", "metal", "buffer", "solvent", "cofactor", "other", "unproven", "water"])
def test_no_reference_role_fallback(full_fixture, role):
    raw, source = full_fixture
    source["residues"][1]["role"] = role
    rejected(raw, source, selection(), "reference_invalid")


def test_modified_hetatm_remains_protein(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    for index in range(1, 7):
        lines[index] = change(change(lines[index], 0, 6, "HETATM"), 17, 20, "MSE")
    row = source["residues"][0]
    row["record"] = "HETATM"
    row["identity"]["resname"] = "MSE"
    row["component"]["record_id"] = "MSE-context"
    choice = selection()
    choice["conformer_choices"][0]["residue"] = identity("MSE", 1)
    raw = repack(lines, source)
    assert select(raw, source, choice).retained_serials == (1, 2, 3, 4, 5)
    choice["hetero_choices"].append(dict(residue=identity("MSE", 1), action="remove"))
    rejected(raw, source, choice, "selection_invalid")


def add_glycan(source, lines):
    from tests.test_docking_pocket_evidence import atom_line
    key = identity("NAG", 30)
    lines[-1:-1] = [atom_line(11, "C1", "C", (1, 2, 3), key, altloc="X", occupancy=.5),
                    atom_line(12, "C1", "C", (2, 3, 4), key, altloc="Y", occupancy=.5)]
    source["residues"].append(dict(identity=key, record="HETATM", entity_id="4", role="glycan",
        component=dict(record_id="NAG", version="v1", sha256="d" * 64),
        required_heavy_atoms=[dict(name="C1", element="C")],
        atoms=[declared_atom(11, "C1", "C", (1000, 2000, 3000), "X", 50),
               declared_atom(12, "C1", "C", (2000, 3000, 4000), "Y", 50)]))


@pytest.mark.parametrize("labels,valid", [(('A', 'Y'), True), (('B', 'X'), True),
                                        (('A', 'X'), False), (('B', 'Y'), False)])
def test_explicit_cross_residue_assignments_not_letter_heuristic(full_fixture, labels, valid):
    _, source = full_fixture
    lines = fixture_lines()
    add_glycan(source, lines)
    source["conformer_constraints"] = [dict(id="coupled", residues=[identity("ALA", 1), identity("NAG", 30)],
                                            allowed_assignments=[["A", "Y"], ["B", "X"]])]
    choice = selection()
    choice["hetero_choices"].append(dict(residue=identity("NAG", 30), action="retain"))
    choice["conformer_choices"][0]["altloc"] = labels[0]
    choice["conformer_choices"].append(dict(residue=identity("NAG", 30), altloc=labels[1]))
    raw = repack(lines, source)
    if valid:
        assert select(raw, source, choice).retained_serials[-1] == (12 if labels[1] == "Y" else 11)
    else:
        rejected(raw, source, choice, "constraint_invalid")


def test_partially_selected_recorded_constraint_is_unresolved(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    add_glycan(source, lines)
    source["conformer_constraints"] = [dict(id="coupled", residues=[identity("ALA", 1), identity("NAG", 30)],
                                            allowed_assignments=[["A", "Y"]])]
    choice = selection()
    choice["hetero_choices"].append(dict(residue=identity("NAG", 30), action="remove"))
    rejected(repack(lines, source), source, choice, "selection_incomplete")


def add_limitations(source):
    source["limitations"] = [dict(id="variant-z", kind="sequence_variant", residues=[identity("ALA", 1)], source_record_id="v1"),
        dict(id="gap-a", kind="missing_residues", residues=[identity("GLY", 2)], source_record_id="g1")]


@pytest.mark.parametrize("ids", [[], ["gap-a"], ["gap-a", "variant-z", "unknown"],
                                 ["gap-a", "gap-a", "variant-z"]])
def test_exact_applicable_limitation_ids(full_fixture, ids):
    raw, source = full_fixture
    add_limitations(source)
    choice = selection()
    choice["reviewed_limitations"] = ids
    rejected(raw, source, choice, "selection_incomplete" if len(ids) < 2 else "selection_invalid")


def test_limitation_order_lexical_and_hard_failure_not_overridable(full_fixture):
    raw, source = full_fixture
    add_limitations(source)
    choice = selection()
    choice["reviewed_limitations"] = ["variant-z", "gap-a"]
    first = select(raw, source, choice)
    choice["reviewed_limitations"].reverse()
    second = select(raw, source, choice)
    assert first.canonical_json == second.canonical_json
    assert first.selection_sha256 == second.selection_sha256
    assert first.reviewed_limitations == ("gap-a", "variant-z")
    source["residues"][0]["required_heavy_atoms"].append(dict(name="OXT", element="O"))
    rejected(raw, source, choice, "incomplete_heavy_atoms")


def add_conect(source, lines, serials, kind="covalent"):
    index = len(lines)
    lines.insert(-1, "CONECT" + "".join(f"{serial:5d}" for serial in serials))
    source["connections"].append(dict(record_index=index, record_type="CONECT",
                                      atom_serials=list(serials), kind=kind, symmetry=[]))


@pytest.mark.parametrize("serials,action", [((1, 2), "emit"), ((7, 8), "exclude"),
                                         ((6, 7), "exclude"), ((2, 5, 5), "emit")])
def test_connections_keep_exact_edges_and_multiplicity(full_fixture, serials, action):
    _, source = full_fixture
    lines = fixture_lines()
    add_conect(source, lines, serials)
    result = select(repack(lines, source), source)
    assert tuple(e.atom_serials for e in result.connection_edges) == tuple((serials[0], s) for s in serials[1:])
    assert all(e.action == action for e in result.connection_edges)
    assert all(e.record_index == 12 and e.kind == "covalent" and e.symmetry == () for e in result.connection_edges)
    assert_immutable(result)


@pytest.mark.parametrize("serials", [(1, 7), (1, 10), (1, 6), (6, 2, 2)])
def test_cut_connection_without_existing_chosen_relation_rejected(full_fixture, serials):
    _, source = full_fixture
    lines = fixture_lines()
    add_conect(source, lines, serials)
    rejected(repack(lines, source), source, selection(), "connection_invalid")


@pytest.mark.parametrize("chosen", [(2, 5), (5, 2)])
def test_cut_alt_edge_only_excluded_with_actual_chosen_counterpart(full_fixture, chosen):
    _, source = full_fixture
    lines = fixture_lines()
    add_conect(source, lines, (2, 6))
    add_conect(source, lines, chosen)
    result = select(repack(lines, source), source)
    assert [e.action for e in result.connection_edges] == ["exclude", "emit"]
    assert result.connection_edges[0].reason == "unselected_conformer"
    assert result.connection_edges[1].atom_serials == chosen


@pytest.mark.parametrize("mutation", ["different_kind", "different_atom", "different_multiplicity", "different_symmetry"])
def test_counterpart_must_be_independently_identical_relation(full_fixture, mutation):
    _, source = full_fixture
    lines = fixture_lines()
    add_conect(source, lines, (2, 6, 6) if mutation == "different_multiplicity" else (2, 6))
    if mutation == "different_symmetry":
        from tests.test_receptor_projection_pdb import link_line
        link = link_line(name1="CA", alt1=" ", name2="CB", alt2="A")
        link = change(change(link, 47, 50, "ALA"), 52, 56, "   1")
        lines.insert(1, link)
        source["connections"][0]["record_index"] += 1
        source["connections"].append(dict(record_index=2, record_type="LINK", atom_serials=[2, 5],
                                           kind="covalent", symmetry=["identity", "identity"]))
    else:
        add_conect(source, lines, (3 if mutation == "different_atom" else 2, 5),
                   "coordination" if mutation == "different_kind" else "covalent")
    rejected(repack(lines, source), source, selection(), "connection_invalid")


@pytest.mark.parametrize("serials", [(1, 2), (7, 8)])
@pytest.mark.parametrize("kind", ["unknown", "disulfide"])
def test_unsupported_connection_chemistry_even_if_excluded(full_fixture, serials, kind):
    _, source = full_fixture
    lines = fixture_lines()
    add_conect(source, lines, serials, kind)
    rejected(repack(lines, source), source, selection(), "connection_invalid")


@pytest.mark.parametrize("kind", ["covalent", "coordination", "unknown"])
def test_reference_link_to_retained_protein_not_detached(full_fixture, kind):
    from tests.test_receptor_projection_pdb import link_line
    _, source = full_fixture
    lines = fixture_lines()
    lines.insert(1, link_line())
    source["connections"].append(dict(record_index=2, record_type="LINK", atom_serials=[5, 7],
                                       kind=kind, symmetry=["identity", "identity"]))
    rejected(repack(lines, source), source, selection(), "connection_invalid")


@pytest.mark.parametrize("path", [(), ("protein_selection",), ("protein_selection", "chains", 0),
    ("reference_residue",), ("hetero_choices", 0), ("hetero_choices", 0, "residue"),
    ("conformer_choices", 0), ("conformer_choices", 0, "residue")])
@pytest.mark.parametrize("mutation", ["extra", "missing", "subclass"])
def test_selection_native_closed_dictionaries(full_fixture, path, mutation):
    raw, source = full_fixture
    choice = selection()
    value = choice
    for key in path: value = value[key]
    if mutation == "extra": value["private-path"] = "secret"
    elif mutation == "missing": value.pop(next(iter(value)))
    else:
        class NonNative(dict):
            def items(self): pytest.fail("subclass hook invoked")
        replacement = NonNative(value)
        if not path: choice = replacement
        else:
            parent = choice
            for key in path[:-1]: parent = parent[key]
            parent[path[-1]] = replacement
    rejected(raw, source, choice, "selection_invalid")


@pytest.mark.parametrize("mutation,reason", [
    ("cycle", "selection_invalid"), ("bool", "selection_invalid"),
    ("float", "selection_invalid"), ("tuple", "selection_invalid"),
    ("list_subclass", "selection_invalid"), ("str_subclass", "selection_invalid"),
    ("int_subclass", "selection_invalid"), ("depth", "resource_limit"),
    ("nodes", "resource_limit"), ("scalar", "resource_limit"),
    ("selection_json", "resource_limit"), ("list_cap", "resource_limit"),
    ("chains_cap", "resource_limit"), ("aggregate", "resource_limit")])
def test_preflight_before_copy_hash_or_serialization(full_fixture, monkeypatch, mutation, reason):
    from src.docking import receptor_projection_source as module
    from src.docking.receptor_projection_pdb import ProjectionInvalid
    args = prepare(*full_fixture)
    choice = selection()
    if mutation == "cycle": choice["hetero_choices"].append(choice)
    elif mutation == "bool": choice["protein_selection"]["model"] = True
    elif mutation == "float": choice["protein_selection"]["model"] = 1.0
    elif mutation == "tuple": choice["hetero_choices"] = tuple(choice["hetero_choices"])
    elif mutation in {"list_subclass", "str_subclass", "int_subclass"}:
        if mutation == "list_subclass":
            class Bad(list):
                def __iter__(self): pytest.fail("subclass iteration")
            choice["hetero_choices"] = Bad(choice["hetero_choices"])
        elif mutation == "str_subclass":
            class Bad(str):
                def __hash__(self): pytest.fail("subclass hashing")
            choice["conformer_choices"][0]["altloc"] = Bad("A")
        else:
            class Bad(int):
                def __str__(self): pytest.fail("subclass coercion")
            choice["protein_selection"]["model"] = Bad(1)
    elif mutation == "depth":
        nested = 1
        for _ in range(14): nested = [nested]
        choice["hetero_choices"] = nested
    elif mutation == "nodes": monkeypatch.setattr(module, "MAX_NODES", 10)
    elif mutation == "scalar": choice["reviewed_limitations"] = ["x" * 129]
    elif mutation == "selection_json": monkeypatch.setattr(module, "MAX_SELECTION_JSON_BYTES", 10)
    elif mutation == "list_cap": choice["reviewed_limitations"] = ["x"] * 100000
    elif mutation == "chains_cap": choice["protein_selection"]["chains"] *= 63
    else: monkeypatch.setattr(module, "MAX_LOGICAL_BYTES", args[1].logical_charge_bytes + 10)
    def forbidden(*args, **kwargs): pytest.fail("copy or serialization before rejection")
    monkeypatch.setattr(module.json, "dumps", forbidden)
    monkeypatch.setattr(module.hashlib, "sha256", forbidden)
    monkeypatch.setattr(module, "identity_dict", forbidden)
    with pytest.raises(ProjectionInvalid) as caught:
        module.validate_projection_selection(*args, choice)
    assert caught.value.args == (reason,)


def test_serialization_capacity_charged_before_canonical_copy(full_fixture, monkeypatch):
    from src.docking import receptor_projection_source as module
    from src.docking.receptor_projection_pdb import AllocationBudget, ProjectionInvalid
    parsed, source = prepare(*full_fixture)
    choice = selection()
    resources = module.check_projection_resources(choice, kind="selection")
    amount = 12 * resources.json_bytes + 64 * resources.nodes
    reserve = AllocationBudget.reserve
    def deny(self, size):
        if size == amount: raise ProjectionInvalid("resource_limit")
        return reserve(self, size)
    def forbidden(*args, **kwargs): pytest.fail("copy before serialization reservation")
    monkeypatch.setattr(AllocationBudget, "reserve", deny)
    monkeypatch.setattr(module, "identity_dict", forbidden)
    monkeypatch.setattr(module.json, "dumps", forbidden)
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        module.validate_projection_selection(parsed, source, choice)


def test_connection_scratch_charged_before_edge_facts(full_fixture, monkeypatch):
    from src.docking import receptor_projection_source as module
    from src.docking.receptor_projection_pdb import AllocationBudget, ProjectionInvalid
    _, descriptor = full_fixture
    lines = fixture_lines()
    add_conect(descriptor, lines, (1, 2))
    args = prepare(repack(lines, descriptor), descriptor)
    reserve = AllocationBudget.reserve
    def deny(self, size):
        if size == 8192 + 1024 * 10: raise ProjectionInvalid("resource_limit")
        return reserve(self, size)
    def forbidden(*args, **kwargs): pytest.fail("edge allocated before reserve")
    monkeypatch.setattr(AllocationBudget, "reserve", deny)
    monkeypatch.setattr(module, "SelectedConnectionEdge", forbidden)
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        module.validate_projection_selection(*args, selection())


def test_selection_carries_entire_source_peak(full_fixture):
    from src.docking import receptor_projection_source as module
    parsed, source = prepare(*full_fixture)
    result = module.validate_projection_selection(parsed, source, selection())
    assert result.logical_charge_bytes > source.logical_charge_bytes
    assert result.logical_charge_bytes <= 512 * 1024 * 1024
    from src.docking.receptor_projection_pdb import parse_original_pdb, ProjectionInvalid
    with pytest.raises(ProjectionInvalid, match="^source_invalid$"):
        module.validate_projection_selection(parse_original_pdb(full_fixture[0]), source, selection())


def test_inspection_output_reserved_before_copy(full_fixture, monkeypatch):
    from src.docking import receptor_projection_source as module
    parsed, source = prepare(*full_fixture)
    monkeypatch.setattr(module, "validate_projection_source", lambda *args: source)
    monkeypatch.setattr(module, "MAX_LOGICAL_BYTES", source.logical_charge_bytes + 1)
    def forbidden(*args, **kwargs): pytest.fail("inspection output allocated before reservation")
    monkeypatch.setattr(module, "identity_dict", forbidden)
    result = api().inspect_receptor_projection(full_fixture[0], source=full_fixture[1])
    assert result == dict(ok=False, code="RECEPTOR_PROJECTION_INVALID", reason="resource_limit",
                          message="Receptor projection input is invalid.")


def test_canonical_order_independent_of_caller_lists(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    add_glycan(source, lines)
    add_limitations(source)
    choice = selection()
    choice["hetero_choices"].append(dict(residue=identity("NAG", 30), action="retain"))
    choice["conformer_choices"].append(dict(residue=identity("NAG", 30), altloc="Y"))
    choice["reviewed_limitations"] = ["variant-z", "gap-a"]
    raw = repack(lines, source)
    first = select(raw, source, choice)
    for field in ["hetero_choices", "conformer_choices", "reviewed_limitations"]: choice[field].reverse()
    source["residues"].reverse()
    for row in source["residues"]: row["atoms"].reverse()
    second = select(raw, source, choice)
    assert first.canonical_json == second.canonical_json
    assert first.selection_sha256 == second.selection_sha256
    assert first.hetero_choices == second.hetero_choices
    assert first.conformer_choices == second.conformer_choices


def test_atom_partition_is_in_original_physical_order_even_interleaved(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    # Format-valid interleaving: source residue order does not sort all atoms.
    lines.insert(3, lines.pop(7))
    raw = repack(lines, source)
    result = select(raw, source)
    assert tuple(serial for serial, reason in result.atom_reasons) == (1, 2, 7, 3, 4, 5, 6, 8, 9, 10)


def test_selected_residue_index_budget_precedes_sets_and_copies(full_fixture, monkeypatch):
    from src.docking import receptor_projection_source as module
    from src.docking.receptor_projection_pdb import AllocationBudget, ProjectionInvalid
    args = prepare(*full_fixture)
    reserve = AllocationBudget.reserve
    def deny(self, size):
        if size == 2048 * 3 + 1024 * 10 + 1024: raise ProjectionInvalid("resource_limit")
        return reserve(self, size)
    def forbidden(*args, **kwargs): pytest.fail("selected index used before reserve")
    monkeypatch.setattr(AllocationBudget, "reserve", deny)
    monkeypatch.setattr(module, "protein_matches_target", forbidden)
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        module.validate_projection_selection(*args, selection())


def test_independent_required_set_and_constraint_scratch_precharged(full_fixture, monkeypatch):
    from src.docking import receptor_projection_source as module
    from src.docking.receptor_projection_pdb import AllocationBudget, ProjectionInvalid
    args = prepare(*full_fixture)
    reserve = AllocationBudget.reserve
    denied = []
    def deny(self, size):
        if size == 256 * 9:
            denied.append(True)
            raise ProjectionInvalid("resource_limit")
        return reserve(self, size)
    def forbidden(*args, **kwargs): pytest.fail("heavy set construction before reservation")
    monkeypatch.setattr(AllocationBudget, "reserve", deny)
    monkeypatch.setattr(module, "_selected_chemistry", forbidden)
    with pytest.raises(ProjectionInvalid, match="^resource_limit$"):
        module.validate_projection_selection(*args, selection())
    assert denied


def test_highest_occupancy_atoms_are_not_mixed(full_fixture):
    from tests.test_docking_pocket_evidence import atom_line
    _, source = full_fixture
    lines = fixture_lines()
    lines[-1:-1] = [atom_line(11, "CG", "C", (1, 2, 3), identity("ALA", 1), "ATOM", "A", .4),
                    atom_line(12, "CG", "C", (2, 3, 4), identity("ALA", 1), "ATOM", "B", .6)]
    row = source["residues"][0]
    row["required_heavy_atoms"].append(dict(name="CG", element="C"))
    row["atoms"].extend([declared_atom(11, "CG", "C", (1000, 2000, 3000), "A", 40),
                         declared_atom(12, "CG", "C", (2000, 3000, 4000), "B", 60)])
    result = select(repack(lines, source), source)
    assert result.retained_serials == (1, 2, 3, 4, 5, 11)
    assert [(a.serial, a.occupancy_hundredths) for a in result.source.parsed.atoms if a.serial in {5, 11}] == [(5, 60), (11, 40)]


def test_zero_occupancy_in_unselected_alt_is_preserved_not_repaired(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    lines[6] = change(lines[6], 54, 60, "  0.00")
    source["residues"][0]["atoms"][5]["occupancy_hundredths"] = 0
    result = select(repack(lines, source), source)
    assert dict(result.atom_reasons)[6] == "unselected_conformer"
    assert result.source.parsed.atoms[5].occupancy_hundredths == 0


def add_protein(source, lines, serial=13, chain="B"):
    from tests.test_docking_pocket_evidence import atom_line
    # Minimal independently defined synthetic context, not a real amino acid.
    key = identity("ALA", 1, chain)
    lines.insert(-1, atom_line(serial, "N", "N", (1, 2, 3), key, "ATOM"))
    source["proteins"].append(dict(model=1, chain=chain, entity_id="1", uniprot_id="P06276", taxonomy_id=9606))
    source["residues"].append(dict(identity=key, record="ATOM", entity_id="1", role="protein",
        component=dict(record_id="synthetic-minimal", version="v1", sha256="d" * 64),
        required_heavy_atoms=[dict(name="N", element="N")], atoms=[declared_atom(serial, "N", "N", (1000, 2000, 3000))]))


def test_unselected_protein_does_not_hide_its_hetero_or_limitations(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    add_protein(source, lines)
    source["limitations"] = [dict(id="gap-b", kind="missing_residues", residues=[identity("GLY", 2, "B")], source_record_id="g1")]
    choice = selection()
    result = select(repack(lines, source), source, choice)
    assert dict(result.atom_reasons)[13] == "unselected_protein"
    choice["reviewed_limitations"] = ["gap-b"]
    rejected(repack(lines, source), source, choice, "selection_invalid")
    choice["reviewed_limitations"] = []
    # Move the already independently authored ligand to the unselected chain.
    for index in (7, 8, 9): lines[index] = change(lines[index], 21, 22, "B")
    source["residues"][1]["identity"]["chain"] = "B"
    choice["reference_residue"]["chain"] = "B"
    rejected(repack(lines, source), source, choice, "reference_invalid")


@pytest.mark.parametrize("count", [62, 63])
def test_selected_chain_limit_and_canonical_source_order(full_fixture, count):
    _, source = full_fixture
    lines = fixture_lines()
    chains = ["A"] + [c for c in "BCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"] + [""]
    for index, chain in enumerate(chains[1:count], 13): add_protein(source, lines, index, chain)
    raw = repack(lines, source)
    choice = selection()
    choice["protein_selection"]["chains"] = [dict(chain=c, entity_id="1") for c in chains[:count]]
    if count == 63:
        rejected(raw, source, choice, "resource_limit")
    else:
        first = select(raw, source, choice)
        choice["protein_selection"]["chains"].reverse()
        second = select(raw, source, choice)
        assert first.canonical_json == second.canonical_json
        assert len(first.proteins) == 62


@pytest.mark.parametrize("mutation", ["common_collision", "duplicate_atom", "invalid_unselected_water", "invalid_unselected_alt"])
def test_complete_source_parser_checks_all_records_before_inspection(full_fixture, mutation):
    _, source = full_fixture
    lines = fixture_lines()
    if mutation == "common_collision": lines[5] = change(lines[5], 16, 17, " ")
    elif mutation == "duplicate_atom": lines.insert(-1, lines[5])
    elif mutation == "invalid_unselected_water": lines[10] = change(lines[10], 54, 60, " -0.01")
    else: lines[6] = change(lines[6], 54, 60, "   NaN")
    result = api().inspect_receptor_projection(repack(lines, source), source=source)
    assert result["ok"] is False and result["reason"] == "raw_invalid"
    assert "choices" not in result


def test_reference_altloc_must_be_explicit_and_complete(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    for index in (7, 8, 9):
        lines[index] = change(lines[index], 16, 17, "X")
    for atom in source["residues"][1]["atoms"]: atom["altloc"] = "X"
    raw = repack(lines, source)
    choice = selection()
    rejected(raw, source, choice, "selection_incomplete")
    choice["conformer_choices"].append(dict(residue=identity("LIG", 20), altloc="X"))
    result = select(raw, source, choice)
    assert result.reference_serials == (7, 8, 9)


def test_removed_residue_cannot_have_an_extraneous_conformer_choice(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    add_glycan(source, lines)
    choice = selection()
    choice["hetero_choices"].append(dict(residue=identity("NAG", 30), action="remove"))
    choice["conformer_choices"].append(dict(residue=identity("NAG", 30), altloc="X"))
    rejected(repack(lines, source), source, choice, "selection_invalid")


def test_conflicting_independent_connection_kinds_are_not_unambiguous(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    add_conect(source, lines, (1, 2), "covalent")
    add_conect(source, lines, (2, 1), "coordination")
    rejected(repack(lines, source), source, selection(), "connection_invalid")


@pytest.mark.parametrize("selected", [False, True])
@pytest.mark.parametrize("kind", ["disulfide", "covalent", "coordination", "unknown"])
def test_ssbond_semantics_and_both_removed_partition(full_fixture, selected, kind):
    from tests.test_receptor_projection_pdb import ssbond_line
    from tests.test_docking_pocket_evidence import atom_line
    _, source = full_fixture
    lines = fixture_lines()
    lines.insert(1, ssbond_line())
    source["proteins"].append(dict(model=1, chain="B", entity_id="4", uniprot_id="P06276", taxonomy_id=9606))
    for serial, seq in [(11, 2), (12, 3)]:
        key = identity("CYS", seq, "B")
        lines.insert(-1, atom_line(serial, "SG", "S", (1, 2, 3), key, "ATOM"))
        source["residues"].append(dict(identity=key, record="ATOM", entity_id="4", role="protein",
            component=dict(record_id="synthetic-sulfur", version="v1", sha256="d" * 64),
            required_heavy_atoms=[dict(name="SG", element="S")], atoms=[declared_atom(serial, "SG", "S", (1000, 2000, 3000))]))
    source["connections"].append(dict(record_index=2, record_type="SSBOND", atom_serials=[11, 12],
                                       kind=kind, symmetry=["identity", "identity"]))
    choice = selection()
    if selected: choice["protein_selection"]["chains"].append(dict(chain="B", entity_id="4"))
    raw = repack(lines, source)
    if kind == "disulfide":
        result = select(raw, source, choice)
        assert result.connection_edges[0].action == ("emit" if selected else "exclude")
    else:
        rejected(raw, source, choice, "connection_invalid")


def test_reference_covalent_link_to_retained_component_rejected(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    lines[10] = change(lines[10], 54, 60, "  1.00")
    source["residues"][2]["atoms"][0]["occupancy_hundredths"] = 100
    add_conect(source, lines, (7, 10))
    choice = selection()
    choice["hetero_choices"][0]["action"] = "retain"
    rejected(repack(lines, source), source, choice, "connection_invalid")


def test_all_removed_constraint_needs_no_invented_conformer(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    add_glycan(source, lines)
    source["conformer_constraints"] = [dict(id="removed", residues=[identity("NAG", 30)], allowed_assignments=[["X"]])]
    choice = selection()
    choice["hetero_choices"].append(dict(residue=identity("NAG", 30), action="remove"))
    result = select(repack(lines, source), source, choice)
    assert dict(result.atom_reasons)[11] == dict(result.atom_reasons)[12] == "explicit_hetero_removal"


def test_nonunit_retained_component_is_not_normalized(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    lines[10] = change(lines[10], 54, 60, "  0.01")
    source["residues"][2]["atoms"][0]["occupancy_hundredths"] = 1
    choice = selection()
    choice["hetero_choices"][0]["action"] = "retain"
    result = select(repack(lines, source), source, choice)
    assert 10 in result.retained_serials
    assert result.source.parsed.atoms[-1].occupancy_hundredths == 1


def test_no_hidden_default_action_for_glycan_or_metal(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    add_glycan(source, lines)
    raw = repack(lines, source)
    choice = selection()
    rejected(raw, source, choice, "selection_incomplete")
    source["residues"][-1]["role"] = "metal"
    rejected(raw, source, choice, "selection_incomplete")


@pytest.mark.parametrize("field", ["hetero_choices", "conformer_choices", "reviewed_limitations"])
def test_choice_containers_must_be_lists_even_when_empty(full_fixture, field):
    raw, source = full_fixture
    choice = selection()
    choice[field] = {}
    rejected(raw, source, choice, "selection_invalid")


def test_duplicate_protein_chain_and_reference_hetero_are_rejected(full_fixture):
    raw, source = full_fixture
    choice = selection()
    choice["protein_selection"]["chains"] *= 2
    rejected(raw, source, choice, "selection_invalid")
    choice = selection()
    choice["hetero_choices"].append(dict(residue=identity("LIG", 20), action="remove"))
    rejected(raw, source, choice, "selection_invalid")


@pytest.mark.parametrize("first_count", [1, 2, 3, 4])
@pytest.mark.parametrize("second_count", [1, 2, 3, 4])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("pair,action", [((2, 5), "emit"), ((7, 8), "exclude")])
def test_conect_relationship_multiplicity_is_consistent(full_fixture, first_count, second_count, reverse, pair, action):
    _, source = full_fixture
    lines = fixture_lines()
    left, right = pair
    add_conect(source, lines, (left,) + (right,) * first_count)
    second_left, second_right = (right, left) if reverse else pair
    add_conect(source, lines, (second_left,) + (second_right,) * second_count)
    raw = repack(lines, source)
    if first_count != second_count:
        rejected(raw, source, selection(), "connection_invalid")
    else:
        result = select(raw, source)
        assert tuple(e.atom_serials for e in result.connection_edges) == (
            (pair,) * first_count + ((second_left, second_right),) * second_count)
        assert all(e.action == action for e in result.connection_edges)
        assert all(e.kind == "covalent" and e.symmetry == () for e in result.connection_edges)


@pytest.mark.parametrize("contradictory", [(5, 2), (2, 5)])
def test_contradictory_counterpart_cannot_authorize_cut_alt_edge(full_fixture, contradictory):
    _, source = full_fixture
    lines = fixture_lines()
    for serials in [(2, 6, 6), (2, 5, 5), contradictory]:
        add_conect(source, lines, serials)
    rejected(repack(lines, source), source, selection(), "connection_invalid")


# P4 fixtures remain synthetic format/schema tests, never real preparation evidence.
def project(raw, source, choice=None):
    call = getattr(api(), "project_receptor_bytes", None)
    assert callable(call), "P4 projection callable missing"
    return call(raw, source=source, selection=selection() if choice is None else choice)


def verify(raw, source, result, choice=None):
    call = getattr(api(), "verify_receptor_projection", None)
    assert callable(call), "P4 verification callable missing"
    return call(raw, source=source, selection=selection() if choice is None else choice,
                derived_bytes=result["pdb_bytes"], projection=result["projection"])


def assert_failure(result, reason=None):
    assert result["ok"] is False
    assert set(result) == {"ok", "code", "reason", "message"}
    assert result["code"] == "RECEPTOR_PROJECTION_INVALID"
    assert result["message"] == "Receptor projection input is invalid."
    if reason: assert result["reason"] == reason


def test_p4_public_callables_exist():
    assert callable(getattr(api(), "project_receptor_bytes", None)), "P4 projection callable missing"
    assert callable(getattr(api(), "verify_receptor_projection", None)), "P4 verification callable missing"


@pytest.mark.parametrize("label", ["A", "B"])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_p4_deterministic_partition_fixed_fields_roundtrip(full_fixture, label, newline):
    from src.docking.receptor_projection_pdb import parse_original_pdb
    _, source = full_fixture
    lines = fixture_lines()
    lines[1] = change(lines[1], 60, 66, " 12.34")[:78] + "1+"
    source["residues"][0]["atoms"][0].update(bfactor_hundredths=1234, charge="1+")
    raw = pack(lines, newline)
    source["sha256"] = hashlib.sha256(raw).hexdigest()
    choice = selection()
    choice["conformer_choices"][0]["altloc"] = label
    snapshot = copy.deepcopy((raw, source, choice))
    result = project(raw, source, choice)
    assert result["ok"] is True
    assert set(result) == {"ok", "source_authentication", "pdb_bytes", "projection", "pocket"}
    assert result["source_authentication"] == "caller_responsibility"
    assert result == project(raw, source, choice)
    assert (raw, source, choice) == snapshot
    output = parse_original_pdb(result["pdb_bytes"])
    original = parse_original_pdb(raw)
    selected = select(raw, source, choice)
    retained = [a for a in original.atoms if a.serial in selected.retained_serials]
    assert len(output.atoms) == 5
    for before, after in zip(retained, output.atoms):
        assert after.altloc == ""
        for field in ["name", "element", "identity", "record", "xyz_milli", "occupancy_hundredths", "bfactor_hundredths", "charge"]:
            assert getattr(before, field) == getattr(after, field)
        a, b = before.original_line.rstrip(b"\r\n"), after.original_line.rstrip(b"\n")
        assert a[:6] == b[:6] and a[11:16] == b[11:16] and a[17:] == b[17:]
    assert b"\r" not in result["pdb_bytes"]
    assert b"MODEL        1\n" in result["pdb_bytes"]
    assert result["pdb_bytes"].endswith(b"ENDMDL\nEND\n")
    assert sum(r.record_type == "TER" for r in output.records) == 1
    descriptor = result["projection"]
    assert set(descriptor) == {"schema_version", "rule_version", "original_source", "selection", "derived_input", "lineage", "limitations"}
    assert descriptor["schema_version"] == "receptor-projection-v1"
    assert descriptor["rule_version"] == "explicit-residue-conformer-v1"
    assert descriptor["original_source"]["sha256"] == hashlib.sha256(raw).hexdigest()
    rows = descriptor["lineage"]
    assert len(rows) == len(original.atoms)
    assert [r["source_serial"] for r in rows] == [a.serial for a in original.atoms]
    for row, atom, (_, reason) in zip(rows, original.atoms, selected.atom_reasons):
        assert set(row) == {"source_serial", "derived_serial", "altloc", "occupancy_hundredths", "source_line_sha256", "disposition", "reason"}
        assert row["reason"] == reason
        assert row["source_line_sha256"] == hashlib.sha256(atom.original_line).hexdigest()
        assert (row["derived_serial"] is None) == (row["disposition"] == "removed")
        assert row["occupancy_hundredths"] == atom.occupancy_hundredths
    checked = verify(raw, source, result, choice)
    encoded = json.dumps(descriptor, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    assert checked == dict(ok=True, source_authentication="caller_responsibility", projection_sha256=hashlib.sha256(encoded).hexdigest())


def test_p4_pocket_uses_only_selected_reference_heavy(full_fixture):
    raw, source = full_fixture
    result = project(raw, source)
    pocket = result["pocket"]
    assert pocket["center"] == [3.6, 2.3, 1.5]
    assert pocket["size"] == [10.0, 10.0, 10.0]
    assert pocket["margin"] == 4.0 and pocket["units"] == "angstrom"
    assert pocket["rule_version"] == "heavy-aabb-margin4-min10-v1"
    assert pocket["residue"] == identity("LIG", 20)
    assert [a["serial"] for a in pocket["atom_ids"]] == [7, 8, 9]
    assert pocket["atom_count"] == 3


@pytest.mark.parametrize("x,ok", [(95.0, True), (95.001, False)])
def test_p4_pocket_max_rejects_not_clamps(full_fixture, x, ok):
    _, source = full_fixture
    lines = fixture_lines()
    lines[9] = change(lines[9], 30, 38, f"{x:8.3f}")
    source["residues"][1]["atoms"][2]["xyz_milli"][0] = round(x * 1000)
    result = project(repack(lines, source), source)
    assert result["ok"] is ok
    if ok: assert result["pocket"]["size"][0] == 100.0
    else: assert_failure(result, "selection_invalid")


@pytest.mark.parametrize("mutation", ["xyz", "occupancy", "bfactor", "charge", "extra_atom", "missing_atom", "lineage", "reason", "duplicate_lineage", "missing_lineage", "schema", "rule", "policy", "source", "selection", "extra_key", "bool", "float", "null", "cycle"])
def test_p4_verify_rejects_tamper(full_fixture, mutation):
    raw, source = full_fixture
    result = project(raw, source)
    assert result["ok"]
    descriptor = result["projection"]
    if mutation in {"xyz", "occupancy", "bfactor", "charge", "extra_atom", "missing_atom"}:
        lines = result["pdb_bytes"].decode().splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("ATOM"))
        if mutation == "extra_atom": lines.insert(index, lines[index])
        elif mutation == "missing_atom": lines.pop(index)
        else:
            start, end, value = {"xyz": (30, 38, "   9.999"), "occupancy": (54, 60, "  0.99"), "bfactor": (60, 66, " 99.99"), "charge": (78, 80, "1+")}[mutation]
            lines[index] = change(lines[index].ljust(80), start, end, value)
        result["pdb_bytes"] = pack(lines)
        # Even a matching claimed hash cannot authorize altered full bytes.
        descriptor["derived_input"]["sha256"] = hashlib.sha256(result["pdb_bytes"]).hexdigest()
    elif mutation == "lineage": descriptor["lineage"][0]["derived_serial"] = 2
    elif mutation == "reason": descriptor["lineage"][0]["reason"] = "reference_removed"
    elif mutation == "duplicate_lineage": descriptor["lineage"][1] = descriptor["lineage"][0]
    elif mutation == "missing_lineage": descriptor["lineage"].pop()
    elif mutation == "schema": descriptor["schema_version"] = "docking-source-v1"
    elif mutation == "rule": descriptor["rule_version"] = "forged"
    elif mutation == "policy": descriptor["derived_input"]["metadata_policy"] = "deposited-original"
    elif mutation == "source": descriptor["original_source"]["provenance"]["version"] = "old"
    elif mutation == "selection": descriptor["selection"]["sha256"] = "0" * 64
    elif mutation == "extra_key": descriptor["input_ready"] = True
    elif mutation == "bool": descriptor["lineage"][0]["source_serial"] = True
    elif mutation == "float": descriptor["lineage"][0]["source_serial"] = 1.0
    elif mutation == "null": descriptor["lineage"][0]["source_serial"] = None
    else: descriptor["lineage"][0] = descriptor
    assert_failure(verify(raw, source, result))


def test_p4_choice_order_and_interleaved_atoms(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    add_glycan(source, lines)
    add_limitations(source)
    lines.insert(3, lines.pop(7))
    choice = selection()
    choice["hetero_choices"].append(dict(residue=identity("NAG", 30), action="retain"))
    choice["conformer_choices"].append(dict(residue=identity("NAG", 30), altloc="Y"))
    choice["reviewed_limitations"] = ["variant-z", "gap-a"]
    raw = repack(lines, source)
    first = project(raw, source, choice)
    assert first["ok"]
    for key in ["hetero_choices", "conformer_choices", "reviewed_limitations"]: choice[key].reverse()
    assert first == project(raw, source, choice)
    assert [r["source_serial"] for r in first["projection"]["lineage"]][:4] == [1, 2, 7, 3]


@pytest.mark.parametrize("serials", [(1, 2), (2, 5, 5), (7, 8), (2, 6, 5)])
def test_p4_connectivity_rebuilt_no_dangling(full_fixture, serials):
    from src.docking.receptor_projection_pdb import parse_original_pdb
    _, source = full_fixture
    lines = fixture_lines()
    add_conect(source, lines, serials)
    raw = repack(lines, source)
    result = project(raw, source)
    assert result["ok"]
    connections = parse_original_pdb(result["pdb_bytes"]).connections
    expected = [] if serials == (7, 8) else [(2, 5)] if serials == (2, 6, 5) else [serials]
    assert [c.atom_serials for c in connections] == expected
    assert verify(raw, source, result)["ok"]


@pytest.mark.parametrize("field", ["pdb_bytes", "source", "selection", "derived_bytes", "projection"])
def test_p4_exact_native_public_types(full_fixture, field):
    raw, source = full_fixture
    result = project(raw, source)
    args = dict(pdb_bytes=raw, source=source, selection=selection(), derived_bytes=result["pdb_bytes"], projection=result["projection"])
    class Bytes(bytes): pass
    class Dict(dict): pass
    args[field] = Bytes(args[field]) if type(args[field]) is bytes else Dict(args[field])
    assert_failure(api().verify_receptor_projection(**args))


def test_p4_output_gate_precedes_emission(full_fixture, monkeypatch):
    raw, source = full_fixture
    module = api()
    assert callable(getattr(module, "project_receptor_bytes", None)), "P4 projection callable missing"
    selected = select(raw, source)
    monkeypatch.setattr(module.contracts, "MAX_LOGICAL_BYTES", selected.logical_charge_bytes + 1)
    def forbidden(*args): pytest.fail("output allocated before aggregate reservation")
    monkeypatch.setattr(module, "_emit", forbidden)
    assert_failure(project(raw, source), "resource_limit")


@pytest.mark.parametrize("limit", ["MAX_PROJECTION_JSON_BYTES", "MAX_DERIVED_BYTES", "MAX_EMITTED_SERIAL"])
def test_p4_output_size_serial_gates_before_emission(full_fixture, monkeypatch, limit):
    raw, source = full_fixture
    module = api()
    assert callable(getattr(module, "project_receptor_bytes", None)), "P4 projection callable missing"
    monkeypatch.setattr(module, limit, 4)
    def forbidden(*args): pytest.fail("emission before size gate")
    monkeypatch.setattr(module, "_emit", forbidden)
    assert_failure(project(raw, source), "resource_limit")


def test_p4_roundtrip_budget_precedes_second_parse(full_fixture, monkeypatch):
    raw, source = full_fixture
    module = api()
    assert callable(getattr(module, "project_receptor_bytes", None)), "P4 projection callable missing"
    parse = module.pdb.parse_original_pdb
    calls = []
    def observed(value):
        calls.append(value)
        return parse(value)
    reserve = module.pdb.AllocationBudget.reserve
    # Five atoms, one TER, four envelope records; 81-byte upper bound/record.
    amount = 810 + module.pdb.BASE_RESERVATION_BYTES + 10 * module.pdb.RECORD_RESERVATION_BYTES + 5 * module.pdb.ATOM_RESERVATION_BYTES + module.pdb.TER_RESERVATION_BYTES
    denied = []
    def deny(self, size):
        if size == amount:
            denied.append(size)
            raise module.pdb.ProjectionInvalid("resource_limit")
        return reserve(self, size)
    monkeypatch.setattr(module.pdb, "parse_original_pdb", observed)
    monkeypatch.setattr(module.pdb.AllocationBudget, "reserve", deny)
    assert_failure(project(raw, source), "resource_limit")
    assert calls == [raw]
    assert denied == [amount]


def test_p4_roundtrip_catches_corrupt_emitter(full_fixture, monkeypatch):
    raw, source = full_fixture
    module = api()
    assert callable(getattr(module, "project_receptor_bytes", None)), "P4 projection callable missing"
    emit = module._emit
    def corrupt(*args):
        return emit(*args).replace(b"  0.60", b"  1.00")
    monkeypatch.setattr(module, "_emit", corrupt)
    assert_failure(project(raw, source))


def test_p4_verification_input_and_selection_share_preallocation_budget(full_fixture, monkeypatch):
    raw, source = full_fixture
    module = api()
    result = project(raw, source)
    selected = select(raw, source)
    budget = module.pdb.AllocationBudget()
    budget.reserve(len(result["pdb_bytes"]))
    module._descriptor_resources(result["projection"], budget)
    monkeypatch.setattr(module.contracts, "MAX_LOGICAL_BYTES", budget.used + selected.logical_charge_bytes - 1)
    def forbidden(*args): pytest.fail("selection allocated before aggregate verification headroom check")
    monkeypatch.setattr(module, "_selected", forbidden)
    assert_failure(verify(raw, source, result), "resource_limit")


@pytest.mark.parametrize("record", [b"REMARK   1 STALE ANNOTATION\n", b"TITLE     ORIGINAL DEPOSIT\n"])
def test_p4_roundtrip_rejects_undeclared_metadata(full_fixture, monkeypatch, record):
    raw, source = full_fixture
    module = api()
    emit = module._emit
    def corrupt(*args): return emit(*args).replace(b"ENDMDL\n", record + b"ENDMDL\n")
    monkeypatch.setattr(module, "_emit", corrupt)
    assert_failure(project(raw, source))


def test_p4_retained_atom_order_interleaves_chains_without_split_residues(full_fixture):
    from src.docking.receptor_projection_pdb import parse_original_pdb
    _, source = full_fixture
    lines = fixture_lines()
    add_protein(source, lines)
    lines.insert(3, lines.pop(-2))
    choice = selection()
    choice["protein_selection"]["chains"].append(dict(chain="B", entity_id="1"))
    raw = repack(lines, source)
    result = project(raw, source, choice)
    assert result["ok"]
    assert [a.identity[1] for a in parse_original_pdb(result["pdb_bytes"]).atoms] == ["A", "A", "B", "A", "A", "A"]
    assert verify(raw, source, result, choice)["ok"]


def test_p4_link_rebuild_clears_selected_endpoint_altlocs(full_fixture):
    from tests.test_receptor_projection_pdb import link_line
    from src.docking.receptor_projection_pdb import parse_original_pdb
    _, source = full_fixture
    lines = fixture_lines()
    add_glycan(source, lines)
    link = link_line(alt1="A", alt2="Y")
    link = change(change(link, 47, 50, "NAG"), 52, 56, "  30")
    lines.insert(1, link)
    source["connections"] = [dict(record_index=2, record_type="LINK", atom_serials=[5, 12], kind="covalent", symmetry=["identity", "identity"])]
    choice = selection()
    choice["hetero_choices"].append(dict(residue=identity("NAG", 30), action="retain"))
    choice["conformer_choices"].append(dict(residue=identity("NAG", 30), altloc="Y"))
    raw = repack(lines, source)
    result = project(raw, source, choice)
    assert result["ok"]
    connection, = parse_original_pdb(result["pdb_bytes"]).connections
    assert connection.record_type == "LINK" and connection.atom_serials == (5, 6)
    assert all(e.altloc == "" for e in connection.endpoints)
    assert connection.original_line[59:78] == link.encode()[59:78]
    assert result["pdb_bytes"].index(b"LINK  ") < result["pdb_bytes"].index(b"MODEL ")
    assert verify(raw, source, result, choice)["ok"]


def test_p4_ssbond_rebuild_and_ter_serial_mapping(full_fixture):
    from tests.test_receptor_projection_pdb import ssbond_line
    from tests.test_docking_pocket_evidence import atom_line
    from src.docking.receptor_projection_pdb import parse_original_pdb
    _, source = full_fixture
    lines = fixture_lines()
    lines.insert(1, ssbond_line())
    source["proteins"].append(dict(model=1, chain="B", entity_id="4", uniprot_id="P06276", taxonomy_id=9606))
    for serial, seq in [(11, 2), (12, 3)]:
        key = identity("CYS", seq, "B")
        lines.insert(-1, atom_line(serial, "SG", "S", (1, 2, 3), key, "ATOM"))
        source["residues"].append(dict(identity=key, record="ATOM", entity_id="4", role="protein",
            component=dict(record_id="synthetic-sulfur", version="v1", sha256="d" * 64),
            required_heavy_atoms=[dict(name="SG", element="S")], atoms=[declared_atom(serial, "SG", "S", (1000, 2000, 3000))]))
    source["connections"] = [dict(record_index=2, record_type="SSBOND", atom_serials=[11, 12], kind="disulfide", symmetry=["identity", "identity"])]
    choice = selection()
    choice["protein_selection"]["chains"].append(dict(chain="B", entity_id="4"))
    raw = repack(lines, source)
    result = project(raw, source, choice)
    assert result["ok"]
    output = parse_original_pdb(result["pdb_bytes"])
    assert output.connections[0].atom_serials == (7, 8)
    assert [r.atom_serials for r in output.records if r.record_type == "TER"] == [(6,), (9,)]
    assert result["pdb_bytes"].index(b"SSBOND") < result["pdb_bytes"].index(b"MODEL ")
    assert verify(raw, source, result, choice)["ok"]


@pytest.mark.parametrize("cap,ok", [(5, False), (6, True)])
def test_p4_serial_cap_counts_emitted_ter(full_fixture, monkeypatch, cap, ok):
    raw, source = full_fixture
    monkeypatch.setattr(api(), "MAX_EMITTED_SERIAL", cap)
    result = project(raw, source)
    assert result["ok"] is ok
    if not ok: assert_failure(result, "resource_limit")


@pytest.mark.parametrize("value,reason", [('"\\' * 64, "source_invalid"), ('"\\' * 65, "resource_limit")])
def test_p4_escaped_source_strings_rejected_before_serialization(full_fixture, monkeypatch, value, reason):
    raw, source = full_fixture
    source["provenance"]["proof_id"] = value
    def forbidden(*args): pytest.fail("invalid source reached serialization")
    monkeypatch.setattr(api().contracts, "_descriptor_digest", forbidden)
    assert_failure(project(raw, source), reason)


@pytest.mark.parametrize("value", [True, float("nan"), b"bytes", (1,), {1}, "x" * 129, 2**64])
def test_p4_bad_descriptor_scalars_precede_recomputation(full_fixture, monkeypatch, value):
    raw, source = full_fixture
    result = project(raw, source)
    result["projection"]["lineage"][0]["source_serial"] = value
    def forbidden(*args): pytest.fail("invalid verification input reached recomputation")
    monkeypatch.setattr(api(), "_selected", forbidden)
    assert_failure(verify(raw, source, result))


@pytest.mark.parametrize("field", ["conformer_choices", "hetero_choices", "reviewed_limitations"])
def test_p4_no_implicit_choices_or_acknowledgment(full_fixture, field):
    raw, source = full_fixture
    add_limitations(source)
    choice = selection()
    choice["reviewed_limitations"] = ["gap-a", "variant-z"]
    choice[field] = []
    assert_failure(project(raw, source, choice), "selection_incomplete")


def test_p4_reference_hydrogen_excluded_from_aabb(full_fixture):
    from tests.test_docking_pocket_evidence import atom_line
    _, source = full_fixture
    lines = fixture_lines()
    lines.insert(-1, atom_line(11, "H1", "H", (500, 500, 500), identity("LIG", 20), occupancy=.5))
    source["residues"][1]["atoms"].append(declared_atom(11, "H1", "H", (500000, 500000, 500000), occupancy=50))
    raw = repack(lines, source)
    result = project(raw, source)
    assert result["ok"]
    assert result["pocket"]["center"] == [3.6, 2.3, 1.5]
    assert result["projection"]["lineage"][-1]["reason"] == "reference_removed"
    assert verify(raw, source, result)["ok"]


def test_p4_nonunit_retained_hetero_original_ter_and_metadata(full_fixture):
    from src.docking.receptor_projection_pdb import parse_original_pdb
    _, source = full_fixture
    lines = fixture_lines()
    lines[10] = change(lines[10], 54, 60, "  0.01")
    source["residues"][2]["atoms"][0]["occupancy_hundredths"] = 1
    lines.insert(7, "TER      50      ALA A   1 ")
    lines.insert(1, "TITLE     SYNTHETIC STALE ORIGINAL ONLY")
    choice = selection()
    choice["hetero_choices"][0]["action"] = "retain"
    raw = repack(lines, source)
    result = project(raw, source, choice)
    assert result["ok"]
    output = parse_original_pdb(result["pdb_bytes"])
    assert [a.serial for a in output.atoms] == [1, 2, 3, 4, 5, 7]
    assert [a.segment for a in output.atoms] == [0, 0, 0, 0, 0, 1]
    assert output.atoms[-1].occupancy_hundredths == 1
    assert b"TITLE" not in result["pdb_bytes"] and b"TITLE" in raw
    assert verify(raw, source, result, choice)["ok"]


def test_p4_explicit_model_identity_is_not_rewritten(full_fixture):
    from src.docking.receptor_projection_pdb import parse_original_pdb
    _, source = full_fixture
    lines = fixture_lines()
    lines.insert(1, "MODEL        9")
    lines.insert(-1, "ENDMDL")
    for row in source["proteins"]: row["model"] = 9
    for row in source["residues"]: row["identity"]["model"] = 9
    choice = selection()
    choice["protein_selection"]["model"] = 9
    choice["reference_residue"]["model"] = 9
    for row in choice["conformer_choices"] + choice["hetero_choices"]: row["residue"]["model"] = 9
    raw = repack(lines, source)
    result = project(raw, source, choice)
    assert result["ok"] and parse_original_pdb(result["pdb_bytes"]).model == 9
    assert verify(raw, source, result, choice)["ok"]


@pytest.mark.parametrize("seed", range(16))
def test_p4_arbitrary_original_atom_order_preserves_exact_partition(full_fixture, seed):
    import random
    from src.docking.receptor_projection_pdb import parse_original_pdb
    _, source = full_fixture
    lines = fixture_lines()
    atoms = lines[1:-1]
    random.Random(seed).shuffle(atoms)
    raw = repack([lines[0], *atoms, lines[-1]], source)
    result = project(raw, source)
    assert result["ok"]
    original = parse_original_pdb(raw)
    retained = [a for a in original.atoms if a.serial in {1, 2, 3, 4, 5}]
    derived = parse_original_pdb(result["pdb_bytes"])
    assert [a.name for a in derived.atoms] == [a.name for a in retained]
    assert [r["source_serial"] for r in result["projection"]["lineage"]] == [a.serial for a in original.atoms]
    assert verify(raw, source, result)["ok"]


@pytest.mark.parametrize("mutation", ["source_version", "source_proof", "source_component", "conformer", "extra_source", "dangling_original"])
def test_p4_current_source_and_selection_required_for_verification(full_fixture, mutation):
    raw, source = full_fixture
    result = project(raw, source)
    choice = selection()
    if mutation == "source_version": source["provenance"]["version"] = "2026-10-16"
    elif mutation == "source_proof": source["provenance"]["proof_id"] = "proof-2"
    elif mutation == "source_component": source["residues"][0]["component"]["version"] = "v2"
    elif mutation == "conformer": choice["conformer_choices"][0]["altloc"] = "B"
    elif mutation == "extra_source": source["ready"] = True
    else:
        lines = fixture_lines()
        add_conect(source, lines, (1, 99))
        raw = repack(lines, source)
    assert_failure(verify(raw, source, result, choice))


def test_p4_layout_indices_precharged(full_fixture, monkeypatch):
    raw, source = full_fixture
    module = api()
    selected = select(raw, source)
    bound = 8192 + 1024 * 10 + len(selected.canonical_json)
    monkeypatch.setattr(module.contracts, "MAX_LOGICAL_BYTES", selected.logical_charge_bytes + 12 * bound + 2048 * 10 + 1)
    def forbidden(*args): pytest.fail("layout index before reservation")
    monkeypatch.setattr(module, "set", forbidden, raising=False)
    assert_failure(project(raw, source), "resource_limit")


def test_p4_descriptor_reservation_precedes_selection_copy(full_fixture, monkeypatch):
    raw, source = full_fixture
    module = api()
    selected = select(raw, source)
    monkeypatch.setattr(module.contracts, "MAX_LOGICAL_BYTES", selected.logical_charge_bytes + 1)
    def forbidden(*args): pytest.fail("descriptor copied before reservation")
    monkeypatch.setattr(module.json, "loads", forbidden)
    assert_failure(project(raw, source), "resource_limit")


def test_p4_supplied_bytes_bounded_before_descriptor_walk(full_fixture, monkeypatch):
    raw, source = full_fixture
    result = project(raw, source)
    monkeypatch.setattr(api(), "MAX_DERIVED_BYTES", len(result["pdb_bytes"]) - 1)
    def forbidden(*args): pytest.fail("descriptor walk before derived size gate")
    monkeypatch.setattr(api(), "_descriptor_resources", forbidden)
    assert_failure(verify(raw, source, result), "resource_limit")


def test_p4_verification_headroom_bounds_connection_dense_selection(full_fixture):
    raw, source = full_fixture
    lines = fixture_lines()
    for _ in range(1500): add_conect(source, lines, (1, 2))
    raw = repack(lines, source)
    selected = select(raw, source)
    module = api()
    budget = module.pdb.AllocationBudget()
    bound = module._reserve_verification_selection(raw, source, selection(), budget)
    assert selected.logical_charge_bytes <= bound < budget.used <= 512 * 1024 * 1024
    result = project(raw, source)
    assert result["ok"]
    assert verify(raw, source, result)["ok"]


def test_p4_cannot_silently_merge_original_segments_after_removed_terminator(full_fixture):
    _, source = full_fixture
    lines = fixture_lines()
    add_glycan(source, lines)
    add_protein(source, lines, serial=13, chain="B")
    lines.insert(-1, "TER      50      ALA B   1 ")
    add_protein(source, lines, serial=14, chain="C")
    choice = selection()
    choice["protein_selection"]["chains"].append(dict(chain="C", entity_id="1"))
    choice["hetero_choices"].append(dict(residue=identity("NAG", 30), action="retain"))
    choice["conformer_choices"].append(dict(residue=identity("NAG", 30), altloc="Y"))
    raw = repack(lines, source)
    assert select(raw, source, choice).retained_serials == (1, 2, 3, 4, 5, 12, 14)
    # P1 only permits TER after ATOM. Moving TER before the glycan would change
    # its original segment; after it is invalid. Do not reorder/change records.
    assert_failure(project(raw, source, choice), "selection_invalid")
