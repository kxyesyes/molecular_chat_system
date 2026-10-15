"""Bounded explicit receptor projection; no repair, I/O or execution grant.

P4 preserves fixed atom fields, rebuilding only serials, selected altlocs and
supported connectivity. Original bytes remain evidence, never overwritten.
Logical reservations are conservative aggregate peaks, not OS RSS guarantees.
"""

import hashlib
import json

from . import receptor_projection_pdb as pdb
from . import receptor_projection_source as contracts


MAX_PROJECTION_JSON_BYTES = 64 * 1024 * 1024
MAX_DERIVED_BYTES = 16 * 1024 * 1024
MAX_EMITTED_SERIAL = 99999
METADATA_POLICY = "selected_coordinates_rebuilt_connectivity_no_annotations-v1"


def _require(condition, reason="selection_invalid"):
    if not condition:
        raise pdb.ProjectionInvalid(reason)


def _failure(error):
    return dict(ok=False, code="RECEPTOR_PROJECTION_INVALID", reason=error.args[0],
                message="Receptor projection input is invalid.")


def _canonical(value):
    # Every caller reserves escaped Unicode/UTF-8/encoder capacity first.
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _descriptor_resources(value, budget):
    """No-copy bounded native walk, including lineage's sole null field.

    Schema/value closure is checked against the recomputed descriptor separately.
    This walk never invokes user hooks or constructs a flattened index/copy.
    """
    budget.reserve(65536)
    nodes = size = 0
    active = set()

    def walk(item, depth):
        nonlocal nodes, size
        _require(depth <= 12, "resource_limit")
        nodes += 1
        _require(nodes <= 4_000_000, "resource_limit")
        budget.reserve(128)
        native = type(item)
        _require(native in (dict, list, str, int, type(None)))
        if native is str:
            size += contracts._string_size(item)
        elif native is int:
            _require(-(2**63) <= item < 2**63, "resource_limit")
            size += len(str(item))
        elif item is None:
            size += 4
        else:
            _require(id(item) not in active)
            count = len(item)
            _require(count <= (11 if native is dict else 99999), "resource_limit")
            _require(nodes + count * (2 if native is dict else 1) <= 4_000_000, "resource_limit")
            budget.reserve(256 + 32 * count)
            size += 2 + max(0, count - 1) + (count if native is dict else 0)
            active.add(id(item))
            if native is dict:
                for key, child in item.items():
                    _require(type(key) is str)
                    walk(key, depth + 1)
                    walk(child, depth + 1)
            else:
                for child in item:
                    walk(child, depth + 1)
            active.remove(id(item))
        _require(size <= min(MAX_PROJECTION_JSON_BYTES, 64 * 1024 * 1024), "resource_limit")

    walk(value, 0)


def _closed_equal(actual, expected):
    # Both trees have been bounded; native types are part of equality (1 != True).
    _require(type(actual) is type(expected))
    if type(expected) is dict:
        _require(actual.keys() == expected.keys())
        for key in expected:
            _closed_equal(actual[key], expected[key])
    elif type(expected) is list:
        _require(len(actual) == len(expected))
        for left, right in zip(actual, expected):
            _closed_equal(left, right)
    else:
        _require(actual == expected)


def _layout(selected, budget):
    """Original retained order, original TER segments, safe chain boundaries.

    Arbitrary valid atom interleaving must not terminate/split a residue which
    occurs later. A synthetic chain separator is emitted only at a closed prefix.
    Original TER boundaries always close a prefix because P1 forbids recurrence.
    """
    parsed = selected.source.parsed
    budget.reserve(65536 + 2048 * len(parsed.atoms) + 1024 * len(parsed.connections))
    kept = set(selected.retained_serials)
    atoms = [a for a in parsed.atoms if a.serial in kept]
    _require(bool(atoms))
    last = {a.identity: i for i, a in enumerate(atoms)}
    mapping, boundaries = {}, set()
    serial = closed_at = 0
    for index, atom in enumerate(atoms):
        serial += 1
        mapping[atom.serial] = serial
        closed_at = max(closed_at, last[atom.identity])
        following = atoms[index + 1] if index + 1 < len(atoms) else None
        if following is not None and atom.segment != following.segment:
            # A removed original terminator may leave a retained HETATM last.
            # TER there is invalid in P1; moving it earlier changes the retained
            # segment. Reject rather than merge segments or rewrite atom records.
            _require(atom.record == "ATOM" and closed_at == index)
        boundary = (following is None or atom.segment != following.segment
                    or atom.identity[1] != following.identity[1])
        if atom.record == "ATOM" and boundary and closed_at == index:
            boundaries.add(atom.serial)
            serial += 1
    _require(serial <= min(MAX_EMITTED_SERIAL, 99999), "resource_limit")
    edges = {}
    for edge in selected.connection_edges:
        if edge.action == "emit":
            edges.setdefault(edge.record_index, []).append(edge)
    # PDB annotation connections precede MODEL; serial CONECT follows ENDMDL.
    # Keep original order within each of those two format-defined sections.
    connections = [c.parsed for conect in (False, True) for c in selected.source.connections
                   if c.parsed.record_index in edges and (c.parsed.record_type == "CONECT") == conect]
    # 81 bytes/record includes LF; TER <=28 bytes, envelope <=4*81 bytes.
    records = len(atoms) + len(boundaries) + len(connections) + 4
    byte_bound = 81 * records
    _require(byte_bound <= min(MAX_DERIVED_BYTES, pdb.MAX_PDB_BYTES, 16 * 1024 * 1024), "resource_limit")
    # Lines/list growth, join, field-rewrite scratch and simultaneous PDB copies.
    budget.reserve(1024 * records + 4 * byte_bound)
    # Entire P1 roundtrip peak is charged on THIS ledger before emitter or parser.
    budget.reserve(byte_bound + pdb.BASE_RESERVATION_BYTES
                   + records * pdb.RECORD_RESERVATION_BYTES
                   + len(atoms) * pdb.ATOM_RESERVATION_BYTES
                   + len(connections) * pdb.CONNECTION_RESERVATION_BYTES
                   + len(boundaries) * pdb.TER_RESERVATION_BYTES)
    return atoms, mapping, boundaries, connections, edges


def _connection_line(connection, edges, mapping):
    line = connection.original_line.rstrip(b"\r\n")
    if connection.record_type == "CONECT":
        serials = [mapping[edges[0].atom_serials[0]]]
        serials.extend(mapping[e.atom_serials[1]] for e in edges)
        return b"CONECT" + b"".join(f"{n:5d}".encode("ascii") for n in serials) + b"\n"
    if connection.record_type == "LINK":
        line = line[:16] + b" " + line[17:46] + b" " + line[47:]
    return line + b"\n"


def _emit(selected, layout):
    atoms, mapping, boundaries, connections, edges = layout
    parsed = selected.source.parsed
    # No stale deposited annotations in the derivative. The ID binds identity,
    # not authenticity; original header and all annotation bytes remain in source.
    lines = [("HEADER    RECEPTOR PROJECTION".ljust(62) + selected.source.pdb_id + "\n").encode("ascii")]
    for connection in connections:
        if connection.record_type != "CONECT":
            lines.append(_connection_line(connection, edges[connection.record_index], mapping))
    lines.append(f"MODEL     {parsed.model:4d}\n".encode("ascii"))
    for atom in atoms:
        raw = atom.original_line.rstrip(b"\r\n")
        serial = mapping[atom.serial]
        lines.append(raw[:6] + f"{serial:5d}".encode("ascii") + raw[11:16] + b" " + raw[17:] + b"\n")
        if atom.serial in boundaries:
            lines.append(b"TER   " + f"{serial + 1:5d}".encode("ascii") + b"      " + raw[17:27] + b"\n")
    lines.append(b"ENDMDL\n")
    for connection in connections:
        if connection.record_type == "CONECT":
            lines.append(_connection_line(connection, edges[connection.record_index], mapping))
    lines.append(b"END\n")
    return b"".join(lines)


def _roundtrip(selected, layout, derived):
    atoms, mapping, boundaries, connections, edges = layout
    parsed = pdb.parse_original_pdb(derived)
    _require(parsed.model == selected.source.parsed.model and parsed.header[62:66] == selected.source.pdb_id)
    _require(len(parsed.atoms) == len(atoms) and len(parsed.connections) == len(connections))
    record_types = ["HEADER"]
    record_types.extend(c.record_type for c in connections if c.record_type != "CONECT")
    record_types.append("MODEL")
    for atom in atoms:
        record_types.append(atom.record)
        if atom.serial in boundaries:
            record_types.append("TER")
    record_types.append("ENDMDL")
    record_types.extend(c.record_type for c in connections if c.record_type == "CONECT")
    record_types.append("END")
    _require(len(parsed.records) == len(record_types))
    _require(all(r.record_type == kind and r.original_line.endswith(b"\n")
                 and not r.original_line.endswith(b"\r\n") for r, kind in zip(parsed.records, record_types)))
    _require(parsed.header == "HEADER    RECEPTOR PROJECTION".ljust(62) + selected.source.pdb_id)
    for original, actual in zip(atoms, parsed.atoms):
        _require(actual.serial == mapping[original.serial] and actual.altloc == "")
        for field in ("record", "identity", "name", "element", "xyz_milli", "occupancy_hundredths", "bfactor_hundredths", "charge"):
            _require(getattr(actual, field) == getattr(original, field))
        before, after = original.original_line.rstrip(b"\r\n"), actual.original_line.rstrip(b"\n")
        _require(before[:6] == after[:6] and before[11:16] == after[11:16] and before[17:] == after[17:])
    actual_ters = [r for r in parsed.records if r.record_type == "TER"]
    _require(len(actual_ters) == len(boundaries))
    for original, actual in zip((a for a in atoms if a.serial in boundaries), actual_ters):
        _require(actual.identity == original.identity and actual.atom_serials == (mapping[original.serial] + 1,))
    for original, actual in zip(connections, parsed.connections):
        selected_edges = edges[original.record_index]
        serials = (mapping[selected_edges[0].atom_serials[0]],) + tuple(mapping[e.atom_serials[1]] for e in selected_edges)
        _require(actual.record_type == original.record_type and actual.atom_serials == serials
                 and actual.symmetry == original.symmetry and actual.raw_operators == original.raw_operators)
        _require(actual.original_line == _connection_line(original, selected_edges, mapping))


def _pocket(selected):
    wanted = set(selected.reference_serials)
    atoms = [a for a in selected.source.parsed.atoms if a.serial in wanted and a.element not in {"H", "D"}]
    _require(bool(atoms) and all(a.occupancy_hundredths == 100 for a in atoms), "reference_invalid")
    low = [min(a.xyz_milli[i] for a in atoms) for i in range(3)]
    high = [max(a.xyz_milli[i] for a in atoms) for i in range(3)]
    size = [max(10.0, (hi - lo) / 1000.0 + 8.0) for lo, hi in zip(low, high)]
    _require(all(n <= 100.0 for n in size))
    return dict(source_sha256=selected.source.source_sha256,
                residue=contracts.identity_dict(selected.reference_residue),
                atom_ids=[dict(serial=a.serial, name=a.name, element=a.element, altloc=a.altloc,
                               occupancy_hundredths=a.occupancy_hundredths) for a in atoms],
                atom_count=len(atoms), center=[(lo + hi) / 2000.0 for lo, hi in zip(low, high)],
                size=size, margin=4.0, units="angstrom", rule_version="heavy-aabb-margin4-min10-v1")


def _materialize(selected, budget):
    source = selected.source
    # Upper bound includes JSON escaping, lineage, detached selection and every
    # limitation identity. 12x accounts for native containers, Unicode/UTF-8 and
    # encoder chunks simultaneously; per-atom scratch covers pocket and checking.
    bound = (8192 + 1024 * len(source.parsed.atoms) + len(selected.canonical_json)
             + 2048 * len(source.limitations) + 256 * sum(len(x.residues) for x in source.limitations))
    _require(bound <= min(MAX_PROJECTION_JSON_BYTES, 64 * 1024 * 1024), "resource_limit")
    budget.reserve(12 * bound + 2048 * len(source.parsed.atoms))
    layout = _layout(selected, budget)
    pocket = _pocket(selected)
    derived = _emit(selected, layout)
    _require(len(derived) <= min(MAX_DERIVED_BYTES, pdb.MAX_PDB_BYTES, 16 * 1024 * 1024), "resource_limit")
    _roundtrip(selected, layout, derived)
    mapping = layout[1]
    descriptor = dict(schema_version="receptor-projection-v1", rule_version="explicit-residue-conformer-v1",
        original_source=dict(schema_version="receptor-source-v1", sha256=source.source_sha256,
            descriptor_sha256=source.descriptor_sha256, pdb_id=source.pdb_id,
            target=dict(uniprot_id=source.target.uniprot_id, taxonomy_id=source.target.taxonomy_id),
            provenance=dict(provider=source.provenance.provider, version=source.provenance.version,
                            record_id=source.provenance.record_id, proof_id=source.provenance.proof_id)),
        selection=dict(choices=json.loads(selected.canonical_json), sha256=selected.selection_sha256),
        derived_input=dict(sha256=hashlib.sha256(derived).hexdigest(), byte_count=len(derived),
                           atom_count=len(layout[0]), metadata_policy=METADATA_POLICY),
        lineage=[dict(source_serial=a.serial, derived_serial=mapping.get(a.serial), altloc=a.altloc,
            occupancy_hundredths=a.occupancy_hundredths, source_line_sha256=hashlib.sha256(a.original_line).hexdigest(),
            disposition="retained" if reason == "retained" else "removed", reason=reason)
            for a, (_, reason) in zip(source.parsed.atoms, selected.atom_reasons)],
        limitations=[dict(id=x.id, kind=x.kind, residues=[contracts.identity_dict(k) for k in x.residues],
                          source_record_id=x.source_record_id) for x in source.limitations])
    return dict(ok=True, source_authentication="caller_responsibility", pdb_bytes=derived,
                projection=descriptor, pocket=pocket)


def _selected(pdb_bytes, source, selection):
    parsed = pdb.parse_original_pdb(pdb_bytes)
    validated = contracts.validate_projection_source(parsed, source)
    return contracts.validate_projection_selection(parsed, validated, selection)


def _reserve_verification_selection(raw, source, selection, budget):
    """Reserve P1/P2/P3 BEFORE recomputation alongside the caller descriptor.

    Their immutable APIs carry their own cumulative ledger, without an external
    ledger parameter. A no-copy preflight therefore reserves a conservative upper
    bound first: P1's full scan charge, 128 KiB traversal bases, and 2048/node for
    P2/P3 traversals, indexes and frozen facts plus 12x escaped serialization.
    Each validated connection has >=13 nodes (covers its 10240-byte edge/index
    charge plus traversal/containers); each atom/residue/required-atom/constraint/limitation similarly has
    enough schema nodes for all its per-item charges. No allocation is credited
    back. The returned P3 ledger is checked against this reserved bound as well.
    Invalid schemas fail during validation, never enlarge this capacity.
    """
    _require(type(raw) is bytes and bool(raw), "raw_invalid")
    _require(len(raw) <= pdb.MAX_PDB_BYTES, "resource_limit")
    parsed_charge = pdb._preflight(raw).used
    budget.reserve(parsed_charge)
    source_size = contracts.check_projection_resources(source, budget=budget)
    try:
        selection_size = contracts.check_projection_resources(selection, budget=budget, kind="selection")
    except pdb.ProjectionInvalid as error:
        if error.args == ("source_invalid",):
            raise pdb.ProjectionInvalid("selection_invalid") from None
        raise
    rest = (128 * 1024 + 4096 + 2048 * (source_size.nodes + selection_size.nodes)
            + 12 * (source_size.json_bytes + selection_size.json_bytes))
    budget.reserve(rest)
    return parsed_charge + rest


def project_receptor_bytes(pdb_bytes: bytes, *, source: dict, selection: dict) -> dict:
    """Materialize an explicit validated choice; never authenticate or grant use."""
    try:
        selected = _selected(pdb_bytes, source, selection)
        budget = pdb.AllocationBudget(contracts.MAX_LOGICAL_BYTES)
        budget.reserve(selected.logical_charge_bytes)
        return _materialize(selected, budget)
    except pdb.ProjectionInvalid as error:
        return _failure(error)


def verify_receptor_projection(pdb_bytes: bytes, *, source: dict, selection: dict,
                               derived_bytes: bytes, projection: dict) -> dict:
    """Recompute full bytes and a closed descriptor, never trusting claimed hashes."""
    try:
        _require(type(derived_bytes) is bytes and type(projection) is dict)
        _require(0 < len(derived_bytes) <= min(MAX_DERIVED_BYTES, pdb.MAX_PDB_BYTES, 16 * 1024 * 1024), "resource_limit")
        budget = pdb.AllocationBudget(contracts.MAX_LOGICAL_BYTES)
        budget.reserve(len(derived_bytes))
        _descriptor_resources(projection, budget)
        selection_bound = _reserve_verification_selection(pdb_bytes, source, selection, budget)
        selected = _selected(pdb_bytes, source, selection)
        _require(selected.logical_charge_bytes <= selection_bound, "resource_limit")
        result = _materialize(selected, budget)
        _require(derived_bytes == result["pdb_bytes"])
        _closed_equal(projection, result["projection"])
        digest = hashlib.sha256(_canonical(result["projection"])).hexdigest()
        return dict(ok=True, source_authentication="caller_responsibility", projection_sha256=digest)
    except pdb.ProjectionInvalid as error:
        return _failure(error)


def _inspection(source):
    # Reserve an upper bound BEFORE making any detached public containers. The
    # bound includes escaped 128-character tokens, output/container references,
    # and simultaneous canonical Unicode/UTF-8/IPC serialization capacity.
    size = (4096 + 4096 * len(source.residues) + 512 * len(source.parsed.atoms)
            + 2048 * len(source.proteins) + 2048 * len(source.limitations)
            + 256 * sum(len(x.residues) for x in source.limitations)
            + 2048 * len(source.conformer_constraints)
            + 256 * sum(len(x.residues) for x in source.conformer_constraints)
            + 32 * sum(len(a) for x in source.conformer_constraints for a in x.allowed_assignments)
            + 512 * sum(len(r.required_heavy_atoms) for r in source.residues)
            + 512 * len(source.connections))
    budget = pdb.AllocationBudget(contracts.MAX_LOGICAL_BYTES)
    budget.reserve(source.logical_charge_bytes)
    if size > contracts.MAX_SOURCE_JSON_BYTES:
        raise pdb.ProjectionInvalid("resource_limit")
    budget.reserve(12 * size)
    identity = contracts.identity_dict
    proteins = {(p.model, p.chain): p for p in source.proteins}
    choices = dict(
        protein_selection=[dict(model=p.model, chain=p.chain, entity_id=p.entity_id,
            uniprot_id=p.uniprot_id, taxonomy_id=p.taxonomy_id,
            eligible=contracts.protein_matches_target(p, source)) for p in source.proteins],
        reference_residue=[identity(r.identity) for r in source.residues
                          if contracts.reference_eligible(r, proteins, source)],
        hetero_choices=[dict(residue=identity(r.identity), role=r.role, actions=["retain", "remove"])
                        for r in source.residues if r.role != "protein"],
        conformer_choices=[dict(residue=identity(r.identity), altlocs=list(r.altlocs))
                           for r in source.residues if r.altlocs],
        residues=[dict(residue=identity(r.identity), role=r.role,
            required_heavy_atoms=[dict(name=a.name, element=a.element) for a in r.required_heavy_atoms],
            atoms=[dict(serial=a.serial, name=a.name, element=a.element, altloc=a.altloc,
                        occupancy_hundredths=a.occupancy_hundredths) for a in r.atoms]) for r in source.residues],
        conformer_constraints=[dict(id=c.id, residues=[identity(k) for k in c.residues],
            allowed_assignments=[list(a) for a in c.allowed_assignments]) for c in source.conformer_constraints],
        connections=[dict(record_index=c.parsed.record_index, record_type=c.parsed.record_type,
            atom_serials=list(c.parsed.atom_serials), kind=c.kind, symmetry=list(c.parsed.symmetry))
            for c in source.connections])
    limitations = [dict(id=x.id, kind=x.kind, residues=[identity(k) for k in x.residues],
                        source_record_id=x.source_record_id) for x in source.limitations]
    return dict(ok=True, source_authentication="caller_responsibility",
                source_sha256=source.source_sha256, choices=choices, limitations=limitations, input_ready=False)


def inspect_receptor_projection(pdb_bytes: bytes, *, source: dict) -> dict:
    """Complete bounded source facts/options; eligibility is NOT readiness."""
    try:
        parsed = pdb.parse_original_pdb(pdb_bytes)
        validated = contracts.validate_projection_source(parsed, source)
        return _inspection(validated)
    except pdb.ProjectionInvalid as error:
        return dict(ok=False, code="RECEPTOR_PROJECTION_INVALID", reason=error.args[0],
                    message="Receptor projection input is invalid.")
