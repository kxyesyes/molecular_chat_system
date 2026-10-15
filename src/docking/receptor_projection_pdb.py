"""Bounded raw PDB syntax facts, NOT input authorization or receptor selection.

No I/O, repair, chemistry inference, scoring or conformer preference. All atoms,
including zero occupancy and every alternate, survive parsing. D1 remains strict
and independent: this module never normalizes input into D1's old parser.

Identity is the immutable tuple (model, chain, resname, resseq, icode); blank
chain/icode/altloc is ''. Coordinates and decimals are exact scaled integers.
Records preserve physical order and original newline bytes. A zero-based segment
advances at TER; the TER record retains its preceding identity and serial.
These are internal frozen facts, not constructors for trusted source evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


MAX_PDB_BYTES = 16 * 1024 * 1024
MAX_ATOMS = 99999
MAX_LOGICAL_BYTES = 512 * 1024 * 1024
# Conservative logical peak-allocation accounting (not process RSS):
# - raw input is retained and charged once, without copying it;
# - 64 KiB covers bounded scanner/regex/decimal scratch and empty containers;
# - 1 KiB/record covers <=82 byte line copies, <=80 character ASCII text,
#   ParsedRecord, physical index/segment ints, list growth and final tuple copies;
# - 4 KiB/atom ADDITIONALLY covers ParsedAtom, identity/name/xyz tuples, native
#   strings/ints, all five validation indexes (including name-presence and
#   name/altloc keys) and all three resolution indexes,
#   including hash-table growth/reallocation and simultaneous list/tuple refs;
# - 3 KiB/connection ADDITIONALLY covers up to five resolved endpoint dataclasses,
#   endpoint/serial/operator tuples, normalized text, replacement ParsedRecord,
#   output list/tuple overlap and bounded resolution scratch;
# - 1 KiB/TER ADDITIONALLY covers identity, serial and termination-index entries.
# Preflight reserves the WHOLE peak before decoding or allocating inventory. No
# refund relies on garbage-collection timing. Unknown metadata is never admitted
# by the budget pass; the subsequent syntax pass still validates every record.
BASE_RESERVATION_BYTES = 64 * 1024
RECORD_RESERVATION_BYTES = 1024
ATOM_RESERVATION_BYTES = 4096
CONNECTION_RESERVATION_BYTES = 3072
TER_RESERVATION_BYTES = 1024
_ELEMENTS = frozenset((
    "H D He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu "
    "Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am "
    "Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
).split())
_METADATA = frozenset((
    "TITLE COMPND SOURCE KEYWDS EXPDTA AUTHOR REVDAT JRNL REMARK DBREF DBREF1 DBREF2 "
    "SEQADV SEQRES MODRES HET HETNAM HETSYN FORMUL HELIX SHEET CISPEP CRYST1 "
    "ORIGX1 ORIGX2 ORIGX3 SCALE1 SCALE2 SCALE3 MTRIX1 MTRIX2 MTRIX3 MASTER NUMMDL SPLIT OBSLTE SPRSDE"
).split())
_REASONS = frozenset(("raw_invalid", "resource_limit", "connection_invalid", "source_invalid"))
Identity = tuple[int, str, str, int, str]


class ProjectionInvalid(ValueError):
    """Only fixed reason codes; never interpolate caller input into an error."""

    def __init__(self, reason="raw_invalid"):
        if type(reason) is not str or reason not in _REASONS:
            raise ValueError("raw_invalid")
        super().__init__(reason)


def _require(condition, reason="raw_invalid"):
    if not condition:
        raise ProjectionInvalid(reason)


class AllocationBudget:
    """Native-integer, fail-before-allocation ledger; never above 512 MiB."""

    __slots__ = ("limit", "used")

    def __init__(self, limit=None):
        limit = MAX_LOGICAL_BYTES if limit is None else limit
        _require(type(limit) is int and 0 <= limit <= min(MAX_LOGICAL_BYTES, 512 * 1024 * 1024),
                 "resource_limit")
        self.limit, self.used = limit, 0

    def reserve(self, amount):
        _require(type(amount) is int and 0 <= amount <= self.limit - self.used, "resource_limit")
        self.used += amount


def parse_scaled_decimal(field: str, places: int) -> int:
    """D1's leading spaces, optional minus, digits.dot.exact precision grammar.

    Bounded to a PDB line (80 characters) and precision 1..6; no float conversion,
    plus sign, Unicode digits, trailing whitespace, exponent or coercion.
    """
    _require(type(field) is str and len(field) <= 80)
    _require(type(places) is int and 1 <= places <= 6)
    _require(re.fullmatch(r" *-?[0-9]+\.[0-9]{" + str(places) + "}", field) is not None)
    return int(field.replace(".", ""))


def _integer(field, low, high):
    _require(re.fullmatch(r" *-?[0-9]+", field) is not None)
    value = int(field)
    _require(low <= value <= high)
    return value


def _text(field, pattern):
    value = field.strip(" ")
    _require(re.fullmatch(pattern, value) is not None)
    return value


def _identity(model, chain, name, seq, icode):
    return (model, _text(chain, r"[A-Za-z0-9]?"), _text(name, r"[A-Z0-9]{1,3}"),
            _integer(seq, -999, 9999), _text(icode, r"[A-Za-z0-9]?"))


@dataclass(frozen=True, slots=True)
class ParsedAtom:
    serial: int
    name: str
    element: str
    identity: Identity
    record: str
    altloc: str
    occupancy_hundredths: int
    xyz_milli: tuple[int, int, int]
    bfactor_hundredths: int
    charge: str
    original_line: bytes
    record_index: int
    segment: int


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    record_index: int
    record_type: str
    classification: str
    original_line: bytes
    segment: int
    identity: Identity | None = None
    atom_serials: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ParsedEndpoint:
    """Resolved atom identity/name/altloc (SSBOND itself has no altloc field)."""

    identity: Identity
    name: str
    altloc: str


@dataclass(frozen=True, slots=True)
class ParsedConnection:
    """Syntax only: LINK does not imply covalency or any other chemistry kind.

    symmetry is ('identity', 'identity') for LINK/SSBOND and () for CONECT.
    Nonidentity operators reject; raw_operators retains the two exact six-column
    fields (blank and right-aligned 1555 both normalize to identity). Endpoints
    are resolved atom facts; original_line retains the deposited endpoint syntax.
    """

    record_index: int
    record_type: str
    atom_serials: tuple[int, ...]
    symmetry: tuple[str, ...]
    raw_operators: tuple[str, ...]
    original_line: bytes
    endpoints: tuple[ParsedEndpoint, ...]


@dataclass(frozen=True, slots=True)
class ParsedPdb:
    original_bytes: bytes
    header: str
    model: int
    atoms: tuple[ParsedAtom, ...]
    records: tuple[ParsedRecord, ...]
    connections: tuple[ParsedConnection, ...]


def _line_bounds(raw, start):
    """Inspect bytes in place; no raw-line or Unicode copies."""
    end = raw.find(b"\n", start, start + 83)
    if end < 0:
        _require(len(raw) - start <= 80)
        end = len(raw)
        content_end = end
    else:
        end += 1
        content_end = end - (2 if end >= 2 and raw[end - 2] == 13 else 1)
    _require(0 < content_end - start <= 80)
    _require(all(32 <= raw[i] <= 126 for i in range(start, content_end)))
    return end, content_end


def _preflight(raw):
    budget = AllocationBudget()
    budget.reserve(len(raw) + BASE_RESERVATION_BYTES)
    start = atoms = ters = 0
    while start < len(raw):
        end, _ = _line_bounds(raw, start)
        cost = RECORD_RESERVATION_BYTES
        if raw.startswith((b"ATOM  ", b"HETATM"), start):
            atoms += 1
            _require(atoms <= MAX_ATOMS, "resource_limit")
            cost += ATOM_RESERVATION_BYTES
        elif raw.startswith(b"TER   ", start):
            ters += 1
            cost += TER_RESERVATION_BYTES
        elif raw.startswith((b"LINK  ", b"SSBOND", b"CONECT"), start):
            cost += CONNECTION_RESERVATION_BYTES
        _require(atoms + ters <= 99999, "resource_limit")
        budget.reserve(cost)
        start = end
    return budget


def _lines(raw):
    """Called only after preflight has reserved every retained fact and index."""
    start = 0
    while start < len(raw):
        end, content_end = _line_bounds(raw, start)
        yield raw[start:end], raw[start:content_end].decode("ascii")
        start = end


def _atom(line, raw, index, model, segment):
    _require(len(line) >= 78)
    _require(line[11] == line[20] == " " and line[27:30] == "   " and not line[66:76].strip())
    identity = _identity(model, line[21], line[17:20], line[22:26], line[26])
    occupancy = parse_scaled_decimal(line[54:60], 2)
    bfactor = parse_scaled_decimal(line[60:66], 2)
    _require(0 <= occupancy <= 100 and bfactor >= 0)
    charge = line[78:]
    _require(not charge.strip() or re.fullmatch(r"[1-9][+-]", charge) is not None)
    element = line[76:78].strip().capitalize()
    _require(element in _ELEMENTS)
    return ParsedAtom(
        _integer(line[6:11], 1, 99999), _text(line[12:16], r"[A-Za-z0-9'_*+-]{1,4}"),
        element, identity, line[:6].strip(), _text(line[16], r"[A-Za-z0-9]?"), occupancy,
        tuple(parse_scaled_decimal(line[i:i+8], 3) for i in (30, 38, 46)), bfactor,
        charge.strip(), raw, index, segment,
    )


def _connection(line, entry, model, by_serial, by_endpoint, by_name):
    """Resolve against O(1) indexes only after the complete inventory is parsed."""
    try:
        record = entry.record_type
        operators, symmetry = (), ()
        if record == "CONECT":
            _require(len(line) >= 16 and not line[31:].strip())
            serials, blank = [], False
            for start in range(6, min(len(line), 31), 5):
                field = line[start:start+5]
                if not field.strip():
                    blank = True
                    continue
                _require(len(field) == 5 and not blank)
                serials.append(_integer(field, 1, 99999))
            _require(len(serials) >= 2 and all(n in by_serial for n in serials))
            selected = tuple(by_serial[n] for n in serials)
        else:
            _require(len(line) >= 72)
            _require(not line[65:66].strip() and not line[72:73].strip() and not line[78:].strip())
            if line[73:78].strip():
                _require(len(line) >= 78 and parse_scaled_decimal(line[73:78], 2) >= 0)
            operators = line[59:65], line[66:72]
            _require(all(op == "      " or re.fullmatch(r" *1555", op) is not None for op in operators))
            symmetry = ("identity", "identity")
            if record == "LINK":
                _require(line[6:12] == "      " and line[20] == line[50] == " ")
                _require(line[27:42] == " " * 15 and line[57:59] == "  ")
                keys = []
                for start in (12, 42):
                    identity = _identity(model, line[start+9], line[start+5:start+8],
                                         line[start+10:start+14], line[start+14])
                    name = _text(line[start:start+4], r"[A-Za-z0-9'_*+-]{1,4}")
                    altloc = _text(line[start+4], r"[A-Za-z0-9]?")
                    keys.append((identity, name, altloc))
                _require(all(key in by_endpoint for key in keys))
                selected = tuple(by_endpoint[key] for key in keys)
            else:
                _require(line[6] == line[10] == line[14] == line[16] == line[28] == line[30] == " ")
                _require(line[22:25] == "   " and line[36:59] == " " * 23)
                _integer(line[7:10], 1, 999)
                _require(line[11:14] == line[25:28] == "CYS")
                keys = [(_identity(model, line[15], line[11:14], line[17:21], line[21]), "SG"),
                        (_identity(model, line[29], line[25:28], line[31:35], line[35]), "SG")]
                _require(all(by_name.get(key) is not None for key in keys))
                selected = tuple(by_name[key] for key in keys)
        # CONECT is a source-to-each-destination star, not a clique: repeated
        # destinations retain multiplicity. Alternate labels are comparable
        # only within the exact same residue; cross-residue constraints are P2.
        source = selected[0]
        for destination in selected[1:]:
            _require(source.serial != destination.serial)
            _require(source.identity != destination.identity
                     or not source.altloc or not destination.altloc
                     or source.altloc == destination.altloc)
        return ParsedConnection(entry.record_index, record, tuple(a.serial for a in selected),
                                symmetry, operators, entry.original_line,
                                tuple(ParsedEndpoint(a.identity, a.name, a.altloc) for a in selected))
    except ProjectionInvalid:
        raise ProjectionInvalid("connection_invalid") from None


def _resolve_connections(records, atoms, model):
    by_serial, by_endpoint, by_name = {}, {}, {}
    for atom in atoms:
        by_serial[atom.serial] = atom
        by_endpoint[atom.identity, atom.name, atom.altloc] = atom
        key = atom.identity, atom.name
        by_name[key] = None if key in by_name else atom
    connections = []
    for offset, entry in enumerate(records):
        if entry.classification != "connection":
            continue
        line = entry.original_line.rstrip(b"\r\n").decode("ascii")
        connection = _connection(line, entry, model, by_serial, by_endpoint, by_name)
        connections.append(connection)
        records[offset] = ParsedRecord(entry.record_index, entry.record_type, entry.classification,
                                      entry.original_line, entry.segment, None, connection.atom_serials)
    return tuple(connections)


def parse_original_pdb(pdb_bytes: bytes) -> ParsedPdb:
    """Validate the ENTIRE raw inventory; no source, selection or filesystem input."""
    _require(type(pdb_bytes) is bytes and len(pdb_bytes) > 0)
    _require(len(pdb_bytes) <= MAX_PDB_BYTES, "resource_limit")
    _preflight(pdb_bytes)
    atoms, records = [], []
    serials, names, positions, groups = set(), set(), {}, {}
    terminated = set()
    header, last = None, None
    model, segment = 1, 0
    explicit = in_model = ended_model = ended = False
    for index, (raw, line) in enumerate(_lines(pdb_bytes), 1):
        _require(not ended)
        record = line[:6].strip()
        _require(line[:6] == record.ljust(6)
                 or (record == "END" and line.startswith("END") and not line[3:].strip()))
        classification, identity, ids = "envelope", None, ()
        if record == "HEADER":
            _require(header is None and not atoms and len(line) >= 66)
            _require(line[6:10] == "    ")
            _require(re.fullmatch(r"[0-9][A-Z0-9]{3}", line[62:66]) is not None)
            header = line
        elif record == "MODEL":
            _require(not explicit and not atoms and len(line) >= 14)
            _require(line[:10] == "MODEL     " and not line[14:].strip())
            model = _integer(line[10:14], 1, 9999)
            explicit = in_model = True
        elif record == "NUMMDL":
            _require(not atoms and len(line) >= 14 and line[6:10] == "    " and not line[14:].strip())
            _require(_integer(line[10:14], 1, 1) == 1)
            classification = "metadata"
        elif record == "ENDMDL":
            _require(explicit and in_model and bool(atoms) and not line[6:].strip())
            in_model, ended_model = False, True
        elif record == "END":
            _require(bool(atoms) and not in_model and not line[6:].strip())
            ended = True
        elif record in {"ATOM", "HETATM"}:
            _require(header is not None and not ended_model and (not explicit or in_model))
            _require(len(atoms) < MAX_ATOMS, "resource_limit")
            atom = _atom(line, raw, index, model, segment)
            identity = atom.identity
            position = identity[0], identity[1], identity[3], identity[4]
            _require(position not in positions or positions[position] == identity[2])
            _require(identity not in groups or groups[identity] == (record, segment))
            _require(identity not in terminated and atom.serial not in serials)
            name_key = identity, atom.name, atom.altloc
            _require(name_key not in names)
            # A common (blank) atom cannot also be a named alternate. Presence
            # keys make this order-independent without scanning a residue.
            common_key = identity, atom.name, ""
            presence_key = identity, atom.name
            _require(common_key not in names if atom.altloc else presence_key not in names)
            positions[position] = identity[2]
            groups[identity] = record, segment
            names.add(name_key)
            names.add(presence_key)
            serials.add(atom.serial)
            atoms.append(atom)
            last = atom
            classification, ids = "coordinate", (atom.serial,)
        elif record == "TER":
            _require(last is not None and last.record == "ATOM" and not ended_model and (not explicit or in_model))
            _require(len(line) >= 27 and line[11:17] == "      " and line[20] == " " and not line[27:].strip())
            serial = _integer(line[6:11], 1, 99999)
            _require(serial not in serials and last.identity not in terminated)
            identity = _identity(model, line[21], line[17:20], line[22:26], line[26])
            _require(identity == last.identity)
            serials.add(serial)
            terminated.add(identity)
            classification, ids = "boundary", (serial,)
        elif record in {"LINK", "SSBOND", "CONECT"}:
            _require(record != "CONECT" or bool(atoms), "connection_invalid")
            classification = "connection"
        elif record in _METADATA:
            classification = "metadata"
        else:
            raise ProjectionInvalid()
        records.append(ParsedRecord(index, record, classification, raw, segment, identity, ids))
        if record == "TER":
            segment += 1
    _require(ended and header is not None and bool(atoms))
    connections = _resolve_connections(records, atoms, model)
    return ParsedPdb(pdb_bytes, header, model, tuple(atoms), tuple(records), connections)
