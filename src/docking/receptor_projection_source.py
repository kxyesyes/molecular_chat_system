"""Pure complete-inventory contracts for receptor-source-v1 (P2 only).

These internal facts are not authenticated evidence, selected conformers or an
execution grant. The caller must supply P1's ParsedPdb and independently verified
component/inventory evidence. P3 must check selected-conformer completeness and
target/species; P6 must enforce the actual source/owner authority boundary.

No I/O, repair, chemistry inference, Cartesian products or legacy D1 calls.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re

from . import receptor_projection_pdb as pdb
from .receptor_projection_pdb import ProjectionInvalid


MAX_LOGICAL_BYTES = 512 * 1024 * 1024
MAX_SOURCE_JSON_BYTES = 64 * 1024 * 1024
MAX_SELECTION_JSON_BYTES = 8 * 1024 * 1024
MAX_DEPTH = 12
MAX_NODES = 4_000_000
MAX_SCALAR = 128
MAX_GROUPS = 256
MAX_ASSIGNMENTS = 256
MAX_GROUP_RESIDUES = 64
_ROLES = frozenset("protein deposited_ligand glycan metal solvent buffer water cofactor other unproven".split())
_ROOT = frozenset("schema_version sha256 pdb_id provenance target proteins residues connections conformer_constraints limitations".split())
_IDENTITY = frozenset("model chain resname resseq icode".split())
_ATOM = frozenset("serial name element altloc occupancy_hundredths xyz_milli bfactor_hundredths charge".split())


def _require(condition, reason="source_invalid"):
    if not condition:
        raise ProjectionInvalid(reason)


@dataclass(frozen=True, slots=True)
class ResourceFacts:
    nodes: int
    json_bytes: int


def _string_size(value):
    _require(len(value) <= MAX_SCALAR, "resource_limit")
    size = 2
    for char in value:
        code = ord(char)
        _require(not 0xD800 <= code <= 0xDFFF)
        if char in '\\"\b\f\n\r\t':
            size += 2
        elif code < 32:
            size += 6
        else:
            size += 1 if code < 128 else 2 if code < 2048 else 3 if code < 65536 else 4
    return size


def _list_cap(context):
    # Container lengths are checked before creating iterators, scratch or copies.
    # A complete source can have 62 alphanumeric chains AND the blank chain;
    # the <=62 SELECTED-chain rule belongs to P3.
    return {"proteins": 63, "conformer_constraints": MAX_GROUPS,
            "constraint_residues": MAX_GROUP_RESIDUES,
            "allowed_assignments": MAX_ASSIGNMENTS, "assignment": MAX_GROUP_RESIDUES,
            "xyz_milli": 3, "symmetry": 2, "atom_serials": 5}.get(context, 99999)


def check_projection_resources(value, *, budget=None, kind="source", _capture=False):
    """Iterative exact-native walk BEFORE hashing/copying/serialization/IPC.

    Root depth is zero; dict keys count as scalar nodes. Only dict/list/str/int
    are admitted (no bool, floats, subclasses, arbitrary iterables or hooks).
    Ancestors detect cycles without rejecting harmless shared immutable values
    or acyclic aliases; every occurrence is charged. At most 13 DFS frames live.

    Ledger: 64 KiB fixed traversal/hash scratch + 128 bytes/node accounts for
    source/native scalars, traversal, encoder chunk references and normalization
    scratch. Containers add 256 bytes + 32/child for growth and temporary refs.
    Actual indexes/frozen facts and serialization capacity are reserved separately
    on the SAME ledger. Reservations are never refunded or reset per stage.

    Private P5 mode _capture=True returns (facts, detached native tree). Capture
    happens DURING this same validated walk, reserving each copied container
    before allocation; immutable scalars are shared, mutable aliases are not.
    Default callers still receive ResourceFacts without constructing a copy.
    """
    _require(type(kind) is str and kind in {"source", "selection"})
    _require(type(_capture) is bool)
    budget = pdb.AllocationBudget(MAX_LOGICAL_BYTES) if budget is None else budget
    _require(type(budget) is pdb.AllocationBudget)
    budget.reserve(64 * 1024)
    ceiling = MAX_SOURCE_JSON_BYTES if kind == "source" else MAX_SELECTION_JSON_BYTES
    stack, active = [], set()
    nodes = size = atom_count = 0
    current, depth, context = value, 0, "root"
    captured = destination = slot = None
    while True:
        _require(depth <= MAX_DEPTH, "resource_limit")
        nodes += 1
        _require(nodes <= MAX_NODES, "resource_limit")
        budget.reserve(128)
        native = type(current)
        # Equality-based type membership can invoke an untrusted metaclass.
        _require(any(native is allowed for allowed in (dict, list, str, int)))
        copied = current if _capture else None
        if native is str:
            size += _string_size(current)
        elif native is int:
            _require(-(2**63) <= current < 2**63, "resource_limit")
            size += len(str(current))
        else:
            _require(id(current) not in active)
            count = len(current)
            _require(count <= (11 if native is dict else _list_cap(context)), "resource_limit")
            if native is list and context == "atoms":
                atom_count += count
                _require(atom_count <= pdb.MAX_ATOMS, "resource_limit")
            _require(nodes + count * (2 if native is dict else 1) <= MAX_NODES, "resource_limit")
            budget.reserve(256 + 32 * count)
            size += 2 + max(0, count - 1) + (count if native is dict else 0)
            active.add(id(current))
            if _capture:
                budget.reserve(256 + 32 * count)
                copied = {} if native is dict else []
            # In capture mode, validate/charge each key when actually consumed,
            # not in a separate pass over a caller-mutable dictionary.
            if native is dict:
                if not _capture:
                    for key in current:
                        _require(type(key) is str)
                        nodes += 1
                        _require(nodes <= MAX_NODES, "resource_limit")
                        budget.reserve(128)
                        size += _string_size(key)
                iterator = iter(current.items())
            else:
                iterator = enumerate(current)
            stack.append((current, iterator, depth, context, copied, count))
        _require(size <= ceiling, "resource_limit")
        if _capture:
            if destination is None:
                captured = copied
            elif type(destination) is dict:
                destination[slot] = copied
            else:
                destination.append(copied)
        while stack:
            parent, iterator, parent_depth, parent_context, parent_copy, count = stack[-1]
            item = next(iterator, None)
            if item is None:
                if _capture:
                    _require(len(parent_copy) == count, "resource_limit")
                active.remove(id(parent))
                stack.pop()
                continue
            key, current = item
            if _capture:
                # Reject concurrent growth/shrink; never outgrow the copy's
                # reserved capacity or iterate an unbounded growing list.
                _require(len(parent_copy) < count, "resource_limit")
                destination, slot = parent_copy, key
            if type(parent) is dict:
                _require(type(key) is str)
                if _capture:
                    nodes += 1
                    _require(nodes <= MAX_NODES, "resource_limit")
                    budget.reserve(128)
                    size += _string_size(key)
                    _require(size <= ceiling, "resource_limit")
                context = "constraint_residues" if key == "residues" and parent_context == "constraint" else key
            else:
                context = {"conformer_constraints": "constraint", "allowed_assignments": "assignment"}.get(parent_context, "item")
            depth = parent_depth + 1
            break
        else:
            facts = ResourceFacts(nodes, size)
            return (facts, captured) if _capture else facts


def _closed(value, keys):
    _require(type(value) is dict and value.keys() == keys)


def _text(value, pattern):
    _require(type(value) is str and re.fullmatch(pattern, value) is not None)
    return value


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high)
    return value


def _token(value):
    return _text(value, r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _identity(value):
    _closed(value, _IDENTITY)
    return (_integer(value["model"], 1, 9999), _text(value["chain"], r"[A-Za-z0-9]?"),
            _text(value["resname"], r"[A-Z0-9]{1,3}"), _integer(value["resseq"], -999, 9999),
            _text(value["icode"], r"[A-Za-z0-9]?"))


def _list(value, cap, *, nonempty=False):
    _require(type(value) is list)
    _require(len(value) <= cap, "resource_limit")
    _require(not nonempty or bool(value))


@dataclass(frozen=True, slots=True)
class Target:
    uniprot_id: str
    taxonomy_id: int


@dataclass(frozen=True, slots=True)
class Provenance:
    provider: str
    version: str
    record_id: str
    proof_id: str


@dataclass(frozen=True, slots=True)
class Protein:
    model: int
    chain: str
    entity_id: str
    uniprot_id: str
    taxonomy_id: int


@dataclass(frozen=True, slots=True)
class Component:
    record_id: str
    version: str
    sha256: str


@dataclass(frozen=True, slots=True)
class HeavyAtom:
    name: str
    element: str


@dataclass(frozen=True, slots=True)
class Residue:
    identity: pdb.Identity
    record: str
    entity_id: str
    role: str
    component: Component
    atoms: tuple[pdb.ParsedAtom, ...]
    required_heavy_atoms: tuple[HeavyAtom, ...]
    altlocs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Connection:
    parsed: pdb.ParsedConnection
    kind: str


@dataclass(frozen=True, slots=True)
class ConformerConstraint:
    id: str
    residues: tuple[pdb.Identity, ...]
    allowed_assignments: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class Limitation:
    id: str
    kind: str
    residues: tuple[pdb.Identity, ...]
    source_record_id: str


@dataclass(frozen=True, slots=True)
class ValidatedSource:
    parsed: pdb.ParsedPdb
    source_sha256: str
    descriptor_sha256: str
    pdb_id: str
    provenance: Provenance
    target: Target
    proteins: tuple[Protein, ...]
    residues: tuple[Residue, ...]
    connections: tuple[Connection, ...]
    conformer_constraints: tuple[ConformerConstraint, ...]
    limitations: tuple[Limitation, ...]
    logical_charge_bytes: int
    source_authentication: str = "caller_responsibility"


def _parsed_charge(parsed):
    """Conservatively retain P1's WHOLE original logical peak, not a second copy.

    ParsedPdb is an internal P1 result, not an untrusted deserialization format.
    Reconstruct its ledger from counts since P1 deliberately has no budget field.
    Frozen parsed atoms/records/raw bytes are shared, never silently deep-copied.
    """
    _require(type(parsed) is pdb.ParsedPdb)
    return (len(parsed.original_bytes) + pdb.BASE_RESERVATION_BYTES
            + len(parsed.records) * pdb.RECORD_RESERVATION_BYTES
            + len(parsed.atoms) * pdb.ATOM_RESERVATION_BYTES
            + len(parsed.connections) * pdb.CONNECTION_RESERVATION_BYTES
            + sum(r.record_type == "TER" for r in parsed.records) * pdb.TER_RESERVATION_BYTES)


def _index_parsed(parsed):
    by_serial, groups = {}, {}
    for atom in parsed.atoms:
        by_serial[atom.serial] = atom
        groups.setdefault(atom.identity, []).append(atom)
    return by_serial, groups


def _check_atom(value, actual):
    _closed(value, _ATOM)
    _integer(value["serial"], 1, 99999)
    _text(value["name"], r"[A-Za-z0-9'_*+-]{1,4}")
    _require(type(value["element"]) is str and value["element"] in pdb._ELEMENTS)
    _text(value["altloc"], r"[A-Za-z0-9]?")
    _integer(value["occupancy_hundredths"], 0, 100)
    _integer(value["bfactor_hundredths"], 0, 99999)
    _text(value["charge"], r"(?:[1-9][+-])?")
    _list(value["xyz_milli"], 3)
    _require(len(value["xyz_milli"]) == 3)
    for coord in value["xyz_milli"]:
        _integer(coord, -999999, 9999999)
    _require(actual is not None)
    for key in _ATOM:
        expected = tuple(value[key]) if key == "xyz_milli" else value[key]
        _require(expected == getattr(actual, key))


def _descriptor_digest(source):
    # Called only after traversal AND aggregate serialization reservation.
    text = json.dumps(source, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _connections(parsed, rows, budget):
    _list(rows, 99999)
    _require(len(rows) == len(parsed.connections))
    budget.reserve(2048 * len(rows))
    actual = {c.record_index: c for c in parsed.connections}
    result = {}
    for row in rows:
        _closed(row, {"record_index", "record_type", "atom_serials", "kind", "symmetry"})
        index = _integer(row["record_index"], 1, len(parsed.records))
        _require(index in actual and index not in result)
        _text(row["record_type"], r"LINK|SSBOND|CONECT")
        _text(row["kind"], r"covalent|disulfide|coordination|unknown")
        _list(row["atom_serials"], 5, nonempty=True)
        for serial in row["atom_serials"]:
            _integer(serial, 1, 99999)
        _list(row["symmetry"], 2)
        for operator in row["symmetry"]:
            _text(operator, r"identity|nonidentity")
        connection = actual[index]
        _require(row["record_type"] == connection.record_type
                 and tuple(row["atom_serials"]) == connection.atom_serials
                 and tuple(row["symmetry"]) == connection.symmetry)
        # Chemistry is supplied independently. In particular LINK/CONECT never
        # imply a guessed kind from distance, element or record spelling.
        result[index] = Connection(connection, row["kind"])
    return tuple(result[c.record_index] for c in parsed.connections)


def _constraints(rows, residues, budget):
    _list(rows, MAX_GROUPS)
    budget.reserve(1024 * len(rows))
    result, ids = [], set()
    for row in rows:
        _closed(row, {"id", "residues", "allowed_assignments"})
        _token(row["id"])
        _require(row["id"] not in ids)
        ids.add(row["id"])
        _list(row["residues"], MAX_GROUP_RESIDUES, nonempty=True)
        _list(row["allowed_assignments"], MAX_ASSIGNMENTS, nonempty=True)
        budget.reserve(512 * len(row["residues"]) + 256 * len(row["allowed_assignments"]))
        keys, seen = [], set()
        for value in row["residues"]:
            key = _identity(value)
            _require(key in residues and key not in seen)
            keys.append(key)
            seen.add(key)
        assignments, unique = [], set()
        for assignment in row["allowed_assignments"]:
            _list(assignment, MAX_GROUP_RESIDUES)
            _require(len(assignment) == len(keys))
            budget.reserve(64 * len(assignment) + 128)
            for label, key in zip(assignment, keys):
                _text(label, r"[A-Za-z0-9]?")
                _require(label in (residues[key].altlocs or ("",)))
            value = tuple(assignment)
            _require(value not in unique)
            unique.add(value)
            assignments.append(value)
        result.append(ConformerConstraint(row["id"], tuple(keys), tuple(assignments)))
    return tuple(result)


def _limitations(rows, residues, proteins, budget):
    _list(rows, 99999)
    budget.reserve(1024 * len(rows) + 256 * len(residues))
    positions = {(k[0], k[1], k[3], k[4]): k for k in residues}
    result, ids, declared_missing = [], set(), set()
    for row in rows:
        _closed(row, {"id", "kind", "residues", "source_record_id"})
        _token(row["id"])
        _token(row["source_record_id"])
        _text(row["kind"], r"missing_residues|construct_truncation|sequence_variant")
        _require(row["id"] not in ids)
        ids.add(row["id"])
        _list(row["residues"], 99999, nonempty=True)
        budget.reserve(512 * len(row["residues"]))
        keys, seen = [], set()
        for value in row["residues"]:
            key = _identity(value)
            _require(key[:2] in proteins and key not in seen)
            position = key[0], key[1], key[3], key[4]
            _require(position not in positions or positions[position] == key)
            # Absence is allowed only on an explicitly inventoried protein chain.
            # An absent variant must also have an exact missing-residue fact;
            # check that after all rows so source order grants no authority.
            # An observed residue must actually be protein, never ligand/water.
            if key in residues:
                _require(residues[key].role == "protein")
            if row["kind"] == "missing_residues":
                declared_missing.add(key)
            seen.add(key)
            keys.append(key)
        result.append(Limitation(row["id"], row["kind"], tuple(keys), row["source_record_id"]))
    for row in result:
        if row.kind == "sequence_variant":
            _require(all(key in residues or key in declared_missing for key in row.residues))
    return tuple(result)


def validate_projection_source(parsed: pdb.ParsedPdb, source: dict) -> ValidatedSource:
    """Return detached frozen inventory facts, never readiness or authenticity.

    Source list order is not authority: atoms/residues/proteins are normalized to
    original physical order; descriptor_sha256 binds the supplied canonical JSON.
    Required heavy atoms come ONLY from the independent component definition.
    Missing required atoms are retained facts for P3 to reject when selected.
    """
    budget = pdb.AllocationBudget(MAX_LOGICAL_BYTES)
    budget.reserve(_parsed_charge(parsed))
    resources = check_projection_resources(source, budget=budget)
    _closed(source, _ROOT)
    _require(source["schema_version"] == "receptor-source-v1")
    _text(source["sha256"], r"[0-9a-f]{64}")
    _text(source["pdb_id"], r"[0-9][A-Z0-9]{3}")
    _require(source["pdb_id"] == parsed.header[62:66])
    _require(source["sha256"] == hashlib.sha256(parsed.original_bytes).hexdigest())
    origin = source["provenance"]
    _closed(origin, {"provider", "version", "record_id", "proof_id"})
    _require(origin["provider"] == "wwpdb" and origin["record_id"] == source["pdb_id"])
    _token(origin["version"])
    _token(origin["proof_id"])
    target = source["target"]
    _closed(target, {"uniprot_id", "taxonomy_id"})
    _text(target["uniprot_id"], r"[A-Z0-9]{6,10}")
    _integer(target["taxonomy_id"], 1, 2**31 - 1)
    _list(source["proteins"], 63, nonempty=True)
    _list(source["residues"], 99999, nonempty=True)
    # Precharge all parsed/source indexing, hash-table resizing, group list+tuple
    # overlap, detached residue/component records and ordering scratch.
    budget.reserve(2048 * len(parsed.atoms) + 2048 * len(source["residues"])
                   + 1024 * len(source["proteins"]) + 4096)
    by_serial, groups = _index_parsed(parsed)
    proteins, entities = {}, {}
    for row in source["proteins"]:
        _closed(row, {"model", "chain", "entity_id", "uniprot_id", "taxonomy_id"})
        _integer(row["model"], 1, 9999)
        _require(row["model"] == parsed.model)
        _text(row["chain"], r"[A-Za-z0-9]?")
        _token(row["entity_id"])
        _text(row["uniprot_id"], r"[A-Z0-9]{6,10}")
        _integer(row["taxonomy_id"], 1, 2**31 - 1)
        key = row["model"], row["chain"]
        _require(key not in proteins)
        entity_identity = row["uniprot_id"], row["taxonomy_id"]
        _require(row["entity_id"] not in entities or entities[row["entity_id"]] == entity_identity)
        entities[row["entity_id"]] = entity_identity
        proteins[key] = Protein(**row)
    residues, seen_serials, seen_proteins = {}, set(), set()
    for row in source["residues"]:
        _closed(row, {"identity", "record", "entity_id", "role", "component", "atoms", "required_heavy_atoms"})
        key = _identity(row["identity"])
        _require(key in groups and key not in residues)
        _token(row["entity_id"])
        _require(type(row["role"]) is str and row["role"] in _ROLES)
        _text(row["record"], r"ATOM|HETATM")
        _require(row["record"] == groups[key][0].record)
        _require(row["record"] != "ATOM" or row["role"] == "protein")
        # A declared protein entity is never removable just because it is HETATM.
        _require(row["entity_id"] not in entities or row["role"] == "protein")
        if row["role"] == "protein":
            protein = proteins.get(key[:2])
            _require(protein is not None and protein.entity_id == row["entity_id"])
            seen_proteins.add(key[:2])
        if key[2] in {"HOH", "WAT", "DOD"}:
            _require(row["role"] == "water")
        component = row["component"]
        _closed(component, {"record_id", "version", "sha256"})
        _token(component["record_id"])
        _token(component["version"])
        _text(component["sha256"], r"[0-9a-f]{64}")
        _list(row["required_heavy_atoms"], 99999)
        budget.reserve(512 * len(row["required_heavy_atoms"]))
        required = {}
        for atom in row["required_heavy_atoms"]:
            _closed(atom, {"name", "element"})
            _text(atom["name"], r"[A-Za-z0-9'_*+-]{1,4}")
            _require(type(atom["element"]) is str and atom["element"] in pdb._ELEMENTS
                     and atom["element"] not in {"H", "D"})
            _require(atom["name"] not in required)
            required[atom["name"]] = HeavyAtom(**atom)
        _list(row["atoms"], 99999, nonempty=True)
        _require(len(row["atoms"]) == len(groups[key]))
        for atom in row["atoms"]:
            _closed(atom, _ATOM)
            serial = _integer(atom["serial"], 1, 99999)
            actual = by_serial.get(serial)
            _check_atom(atom, actual)
            _require(serial not in seen_serials and actual.identity == key)
            seen_serials.add(serial)
            if actual.element not in {"H", "D"}:
                heavy = required.get(actual.name)
                _require(heavy is not None and heavy.element == actual.element)
        residues[key] = Residue(key, row["record"], row["entity_id"], row["role"],
                                Component(**component), tuple(groups[key]), tuple(required.values()),
                                tuple(dict.fromkeys(a.altloc for a in groups[key] if a.altloc)))
    _require(residues.keys() == groups.keys() and seen_serials == by_serial.keys()
             and seen_proteins == proteins.keys())
    connections = _connections(parsed, source["connections"], budget)
    constraints = _constraints(source["conformer_constraints"], residues, budget)
    limitations = _limitations(source["limitations"], residues, proteins, budget)
    # Worst-case concurrent encoder chunks, joined Unicode (up to 4 bytes/code
    # point), UTF-8 bytes and sorting scratch. Exact escaped UTF-8 size was counted
    # WITHOUT building text. No full descriptor deepcopy is ever made.
    budget.reserve(12 * resources.json_bytes + 64 * resources.nodes)
    digest = _descriptor_digest(source)
    ordered = tuple(residues[key] for key in groups)
    chain_order = dict.fromkeys(r.identity[:2] for r in ordered if r.role == "protein")
    return ValidatedSource(parsed, source["sha256"], digest, source["pdb_id"],
                           Provenance(**origin), Target(**target),
                           tuple(proteins[key] for key in chain_order), ordered,
                           connections, constraints, limitations, budget.used)


# P3 selection facts are internal, immutable and detached. They are neither a
# materialized receptor nor authentication. canonical_json contains only the
# closed native choice contract, normalized to original source order.
@dataclass(frozen=True, slots=True)
class SelectedConnectionEdge:
    record_index: int
    atom_serials: tuple[int, int]
    kind: str
    symmetry: tuple[str, ...]
    action: str
    reason: str


@dataclass(frozen=True, slots=True)
class ValidatedSelection:
    source: ValidatedSource
    canonical_json: bytes
    selection_sha256: str
    proteins: tuple[Protein, ...]
    reference_residue: pdb.Identity
    hetero_choices: tuple[tuple[pdb.Identity, str], ...]
    conformer_choices: tuple[tuple[pdb.Identity, str], ...]
    reviewed_limitations: tuple[str, ...]
    retained_serials: tuple[int, ...]
    reference_serials: tuple[int, ...]
    atom_reasons: tuple[tuple[int, str], ...]
    connection_edges: tuple[SelectedConnectionEdge, ...]
    logical_charge_bytes: int


def identity_dict(identity):
    """Native detached representation of an internal residue identity."""
    return dict(zip(("model", "chain", "resname", "resseq", "icode"), identity))


def protein_matches_target(protein, source):
    return (protein.uniprot_id == source.target.uniprot_id
            and protein.taxonomy_id == source.target.taxonomy_id)


def reference_eligible(residue, proteins, source):
    protein = proteins.get(residue.identity[:2])
    return (residue.role == "deposited_ligand" and bool(residue.required_heavy_atoms)
            and protein is not None and protein_matches_target(protein, source))


def _selection_contract(source, selection, budget):
    _closed(selection, {"protein_selection", "reference_residue", "hetero_choices",
                        "conformer_choices", "reviewed_limitations"})
    protein_selection = selection["protein_selection"]
    _closed(protein_selection, {"model", "chains"})
    _integer(protein_selection["model"], 1, 9999)
    _require(protein_selection["model"] == source.parsed.model)
    _list(protein_selection["chains"], 62, nonempty=True)
    _list(selection["hetero_choices"], 99999)
    _list(selection["conformer_choices"], 99999)
    _list(selection["reviewed_limitations"], 99999)
    budget.reserve(2048 * len(source.residues) + 1024 * len(source.parsed.atoms)
                   + 1024 * len(source.proteins) + 1024 * len(source.limitations))
    proteins = {(p.model, p.chain): p for p in source.proteins}
    selected = set()
    for row in protein_selection["chains"]:
        _closed(row, {"chain", "entity_id"})
        _text(row["chain"], r"[A-Za-z0-9]?")
        _token(row["entity_id"])
        key = protein_selection["model"], row["chain"]
        protein = proteins.get(key)
        _require(key not in selected and protein is not None
                 and protein.entity_id == row["entity_id"] and protein_matches_target(protein, source))
        selected.add(key)
    residues = {r.identity: r for r in source.residues}
    reference = _identity(selection["reference_residue"])
    _require(reference in residues)
    _require(reference[:2] in selected and reference_eligible(residues[reference], proteins, source),
             "reference_invalid")
    hetero = {}
    for row in selection["hetero_choices"]:
        _closed(row, {"residue", "action"})
        key = _identity(row["residue"])
        _text(row["action"], r"retain|remove")
        _require(key in residues and key != reference and key not in hetero and residues[key].role != "protein")
        hetero[key] = row["action"]
    _require(len(hetero) == sum(r.role != "protein" and r.identity != reference for r in source.residues),
             "selection_incomplete")
    active = {r.identity for r in source.residues if
              (r.role == "protein" and r.identity[:2] in selected)
              or r.identity == reference or hetero.get(r.identity) == "retain"}
    conformers = {}
    for row in selection["conformer_choices"]:
        _closed(row, {"residue", "altloc"})
        key = _identity(row["residue"])
        _text(row["altloc"], r"[A-Za-z0-9]")
        _require(key in active and key not in conformers and row["altloc"] in residues[key].altlocs)
        conformers[key] = row["altloc"]
    _require(len(conformers) == sum(bool(residues[key].altlocs) for key in active), "selection_incomplete")
    return selected, reference, hetero, active, conformers


def _selected_chemistry(source, active, conformers, reference):
    for residue in source.residues:
        if residue.identity not in active:
            continue
        label = conformers.get(residue.identity, "")
        chosen = [a for a in residue.atoms if a.altloc in ("", label)]
        _require(len({a.name for a in chosen}) == len(chosen), "selection_invalid")
        heavy = {(a.name, a.element) for a in chosen if a.element not in {"H", "D"}}
        required = {(a.name, a.element) for a in residue.required_heavy_atoms}
        _require(heavy == required, "incomplete_heavy_atoms")
        _require(all(a.occupancy_hundredths > 0 for a in chosen), "occupancy_invalid")
        if residue.identity == reference:
            _require(all(a.occupancy_hundredths == 100 for a in chosen if a.element not in {"H", "D"}),
                     "reference_invalid")


def _selected_constraints(source, active, conformers):
    for group in source.conformer_constraints:
        if not any(key in active for key in group.residues):
            continue
        # Do not guess a label for a removed partner, nor existentially complete
        # the group by exploring combinations. That recorded dependency remains
        # unresolved until all its participants have explicit applicable choices.
        _require(all(key in active for key in group.residues), "selection_incomplete")
        assignment = tuple(conformers.get(key, "") for key in group.residues)
        _require(assignment in group.allowed_assignments, "constraint_invalid")


def _reviewed_limitations(source, selected, rows):
    applicable = {x.id for x in source.limitations if any(k[:2] in selected for k in x.residues)}
    reviewed = set()
    for value in rows:
        _token(value)
        _require(value not in reviewed and value in applicable)
        reviewed.add(value)
    _require(reviewed == applicable, "selection_incomplete")
    return tuple(sorted(reviewed))


def _edge_key(left, right, kind, symmetry, multiplicity):
    # Relations are undirected; endpoint-associated symmetry follows reversal.
    if left > right:
        left, right, symmetry = right, left, symmetry[::-1]
    return left, right, kind, symmetry, multiplicity


def _selected_connections(source, atom_reasons, budget):
    # At most four star edges per P1 record. This covers edge facts, signatures,
    # multiplicity counters, index resizing and concurrent list/tuple output.
    budget.reserve(8192 * len(source.connections) + 1024 * len(source.parsed.atoms))
    atoms = {a.serial: a for a in source.parsed.atoms}
    reasons = dict(atom_reasons)
    chosen = {}
    for atom in source.parsed.atoms:
        if reasons[atom.serial] == "retained":
            key = atom.identity, atom.name, atom.element
            _require(key not in chosen, "connection_invalid")
            chosen[key] = atom.serial
    edges, verified, relation_kinds, conect_multiplicities = [], set(), {}, {}
    for connection in source.connections:
        record, kind = connection.parsed, connection.kind
        _require(kind in {"covalent", "disulfide", "coordination"}, "connection_invalid")
        _require(record.record_type != "SSBOND" or kind == "disulfide", "connection_invalid")
        _require(all(op == "identity" for op in record.symmetry), "connection_invalid")
        left = record.atom_serials[0]
        counts = {}
        for right in record.atom_serials[1:]:
            counts[right] = counts.get(right, 0) + 1
        for right in record.atom_serials[1:]:
            pair = (left, right) if left < right else (right, left)
            _require(pair not in relation_kinds or relation_kinds[pair] == kind, "connection_invalid")
            relation_kinds[pair] = kind
            if kind == "disulfide":
                _require(atoms[left].element == atoms[right].element == "S", "connection_invalid")
            multiplicity = counts[right]
            _require(kind == "covalent" or multiplicity == 1, "connection_invalid")
            if record.record_type == "CONECT":
                # Repeated and reciprocal declarations describe the same
                # unordered relation, not alternative bond multiplicities.
                # Reject conflicts before they can supply a verified counterpart.
                _require(pair not in conect_multiplicities
                         or conect_multiplicities[pair] == multiplicity, "connection_invalid")
                conect_multiplicities[pair] = multiplicity
            edges.append((record, kind, left, right, multiplicity))
            verified.add(_edge_key(left, right, kind, record.symmetry, multiplicity))
    result = []
    for record, kind, left, right, multiplicity in edges:
        left_kept, right_kept = reasons[left] == "retained", reasons[right] == "retained"
        if left_kept and right_kept:
            action, reason = "emit", "both_retained"
        elif not left_kept and not right_kept:
            action, reason = "exclude", "both_removed"
        else:
            removed, kept = (right, left) if left_kept else (left, right)
            _require(reasons[removed] == "unselected_conformer", "connection_invalid")
            atom = atoms[removed]
            counterpart = chosen.get((atom.identity, atom.name, atom.element))
            _require(counterpart is not None, "connection_invalid")
            new_left, new_right = (kept, counterpart) if left_kept else (counterpart, kept)
            _require(_edge_key(new_left, new_right, kind, record.symmetry, multiplicity) in verified,
                     "connection_invalid")
            action, reason = "exclude", "unselected_conformer"
        result.append(SelectedConnectionEdge(record.record_index, (left, right), kind,
                                             record.symmetry, action, reason))
    return tuple(result)


def validate_projection_selection(parsed: pdb.ParsedPdb, validated_source: ValidatedSource,
                                  selection: dict) -> ValidatedSelection:
    """Validate explicit scientific choices, without creating an executable input.

    Internal P1/P2 objects must belong to the same parse. Carry the complete P2
    peak (already including P1) into traversal, selection indexes and canonical
    serialization; no stage resets or refunds the aggregate logical ledger.
    """
    _require(type(validated_source) is ValidatedSource and type(parsed) is pdb.ParsedPdb
             and validated_source.parsed is parsed)
    source = validated_source
    budget = pdb.AllocationBudget(MAX_LOGICAL_BYTES)
    budget.reserve(source.logical_charge_bytes)
    try:
        resources = check_projection_resources(selection, budget=budget, kind="selection")
        selected, reference, hetero, active, conformers = _selection_contract(source, selection, budget)
        budget.reserve(12 * resources.json_bytes + 64 * resources.nodes)
        # Required definitions can be much larger than the observed inventory.
        # Charge their comparison sets independently of the per-observed-atom
        # reservation, and bound the explicit constraint-assignment tuples too.
        budget.reserve(256 * sum(len(r.required_heavy_atoms) for r in source.residues)
                       + 128 * sum(len(c.residues) for c in source.conformer_constraints))
        reviewed = _reviewed_limitations(source, selected, selection["reviewed_limitations"])
        _selected_chemistry(source, active, conformers, reference)
        _selected_constraints(source, active, conformers)
        reasons_by_serial = {}
        for residue in source.residues:
            key = residue.identity
            for atom in residue.atoms:
                if key not in active:
                    reason = "unselected_protein" if residue.role == "protein" else "explicit_hetero_removal"
                elif atom.altloc and atom.altloc != conformers[key]:
                    reason = "unselected_conformer"
                elif key == reference:
                    reason = "reference_removed"
                else:
                    reason = "retained"
                reasons_by_serial[atom.serial] = reason
        reasons = [(a.serial, reasons_by_serial[a.serial]) for a in parsed.atoms]
        retained = [serial for serial, reason in reasons if reason == "retained"]
        reference_atoms = [serial for serial, reason in reasons if reason == "reference_removed"]
        edges = _selected_connections(source, reasons, budget)
        canonical = dict(
            protein_selection=dict(model=parsed.model, chains=[dict(chain=p.chain, entity_id=p.entity_id)
                for p in source.proteins if (p.model, p.chain) in selected]),
            reference_residue=identity_dict(reference),
            hetero_choices=[dict(residue=identity_dict(r.identity), action=hetero[r.identity])
                for r in source.residues if r.identity in hetero],
            conformer_choices=[dict(residue=identity_dict(r.identity), altloc=conformers[r.identity])
                for r in source.residues if r.identity in conformers],
            reviewed_limitations=list(reviewed))
        text = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        encoded = text.encode("utf-8")
        return ValidatedSelection(source, encoded, hashlib.sha256(encoded).hexdigest(),
            tuple(p for p in source.proteins if (p.model, p.chain) in selected), reference,
            tuple((r.identity, hetero[r.identity]) for r in source.residues if r.identity in hetero),
            tuple((r.identity, conformers[r.identity]) for r in source.residues if r.identity in conformers),
            tuple(canonical["reviewed_limitations"]), tuple(retained), tuple(reference_atoms), tuple(reasons), edges, budget.used)
    except ProjectionInvalid as error:
        # Shared exact-native/schema helpers use source_invalid by default.
        # Only that fixed code is translated; never expose input or exceptions.
        if error.args == ("source_invalid",):
            raise ProjectionInvalid("selection_invalid") from None
        raise
