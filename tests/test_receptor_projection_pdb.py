"""Synthetic 3D schema/format fixtures ONLY, not experimental/prediction evidence."""

from dataclasses import FrozenInstanceError, fields, is_dataclass
import importlib
import importlib.util

import pytest

from tests.test_docking_pocket_evidence import atom_line, residue


def api():
    name = "src.docking.receptor_projection_pdb"
    assert importlib.util.find_spec(name) is not None, "P1 raw parser is missing"
    return importlib.import_module(name)


def fixture_lines():
    ala = residue("ALA", 1)
    lines = ["HEADER    SYNTHETIC FORMAT ONLY".ljust(62) + "1ABC"]
    for serial, name, element, xyz in [
        (1, "N", "N", (-1.250, 0.125, 0.375)),
        (2, "CA", "C", (0.100, 0.250, 0.500)),
        (3, "C", "C", (1.400, 0.750, 0.650)),
        (4, "O", "O", (2.100, 0.100, 1.100)),
    ]:
        lines.append(atom_line(serial, name, element, xyz, ala, "ATOM"))
    lines.extend([
        atom_line(5, "CB", "C", (0.250, 1.500, -0.750), ala, "ATOM", "A", 0.6),
        atom_line(6, "CB", "C", (0.300, 1.600, -0.900), ala, "ATOM", "B", 0.4),
        atom_line(7, "C1", "C", (3.0, 2.0, 1.0), residue()),
        atom_line(8, "O1", "O", (3.5, 3.1, 1.5), residue()),
        atom_line(9, "N1", "N", (4.2, 1.5, 2.0), residue()),
        atom_line(10, "O", "O", (6.5, 3.0, 4.0), residue("HOH", 40, "Z"), occupancy=0),
        "END",
    ])
    return lines


def pack(lines, newline="\n", final=True):
    return (newline.join(lines) + (newline if final else "")).encode("ascii")


def change(line, start, end, value):
    assert len(value) == end - start
    return line[:start] + value + line[end:]


def invalid(raw, reason="raw_invalid"):
    module = api()
    with pytest.raises(module.ProjectionInvalid) as caught:
        module.parse_original_pdb(raw)
    assert caught.value.args == (reason,)


def assert_immutable(value):
    if is_dataclass(value):
        with pytest.raises(FrozenInstanceError):
            setattr(value, fields(value)[0].name, None)
        for field in fields(value):
            assert_immutable(getattr(value, field.name))
    elif type(value) is tuple:
        for item in value:
            assert_immutable(item)
    else:
        assert type(value) in (str, bytes, int, type(None))


@pytest.mark.parametrize("newline,final", [("\n", True), ("\r\n", True), ("\n", False)])
def test_complete_fixture_preserves_all_atoms_and_raw_records(newline, final):
    raw = pack(fixture_lines(), newline, final)
    result = api().parse_original_pdb(raw)
    assert result.original_bytes is raw
    assert result.header[62:66] == "1ABC" and result.model == 1
    assert len(result.atoms) == 10
    assert [(a.altloc, a.occupancy_hundredths) for a in result.atoms[4:6]] == [("A", 60), ("B", 40)]
    assert result.atoms[9].occupancy_hundredths == 0
    first = result.atoms[0]
    assert (first.serial, first.name, first.element, first.record) == (1, "N", "N", "ATOM")
    assert first.identity == (1, "A", "ALA", 1, "")
    assert first.xyz_milli == (-1250, 125, 375)
    assert first.bfactor_hundredths == 2000 and first.charge == ""
    assert first.record_index == 2 and first.original_line == raw.splitlines(keepends=True)[1]
    assert b"".join(r.original_line for r in result.records) == raw
    assert [r.record_index for r in result.records] == list(range(1, 13))
    assert result.records[1].classification == "coordinate"
    assert_immutable(result)


@pytest.mark.parametrize("field,places,expected", [
    (" -0.001", 3, -1), ("  1.00", 2, 100), ("-0.00", 2, 0), ("123.456", 3, 123456),
])
def test_decimal_is_exact_base10(field, places, expected):
    value = api().parse_scaled_decimal(field, places)
    assert type(value) is int and value == expected


@pytest.mark.parametrize("field,places", [
    ("1e2", 2), ("NaN", 2), ("inf", 2), ("1.00 ", 2), ("1 .00", 2),
    ("+1.00", 2), (".50", 2), ("1.0", 2), ("\t1.00", 2), ("１.00", 2),
    (b"1.00", 2), ("1.00", True), ("1.00", -1), ("1.00", 1000000),
])
def test_decimal_rejects_non_native_or_unsupported_syntax(field, places):
    with pytest.raises(api().ProjectionInvalid, match="^raw_invalid$"):
        api().parse_scaled_decimal(field, places)


@pytest.mark.parametrize("index,start,end,value", [
    (2, 6, 11, "    1"), (6, 16, 17, "A"), (5, 17, 20, "GLY"),
    (2, 0, 6, "HETATM"), (5, 16, 17, "!"), (10, 21, 22, "!"),
    (5, 54, 60, "   NaN"), (6, 54, 60, "   inf"), (10, 54, 60, " -0.01"),
    (6, 54, 60, "  1.01"), (10, 30, 38, "     NaN"), (6, 38, 46, "     inf"),
    (1, 60, 66, " -0.01"), (1, 78, 80, "0+"), (1, 78, 80, "+1"),
    (1, 11, 12, "X"), (1, 20, 21, "X"), (1, 27, 30, " X "),
    (1, 66, 76, " X        "), (1, 12, 16, " A B"), (1, 76, 78, " X"),
    (1, 6, 11, "    0"), (1, 22, 26, " 1  "),
])
def test_invalid_atom_anywhere_is_not_filtered(index, start, end, value):
    lines = fixture_lines()
    lines[index] = change(lines[index], start, end, value)
    invalid(pack(lines))


@pytest.mark.parametrize("extra", ["ANISOU", "SIGATM", "CAVEAT", "BOGUS "])
def test_unknown_records_reject(extra):
    lines = fixture_lines()
    lines.insert(1, extra)
    invalid(pack(lines))


@pytest.mark.parametrize("mutate", [
    lambda b: b.replace(b"ATOM  ", b"ATOM\t ", 1),
    lambda b: b.replace(b"SYNTHETIC", b"SYN\x00HETIC"),
    lambda b: b.replace(b"SYNTHETIC", b"SYN\x7fHETIC"),
    lambda b: b.replace(b"SYNTHETIC", b"SYN\x80HETIC"),
    lambda b: b.replace(b"\n", b"\r", 1),
    lambda b: b.replace(b"\n", b"\r\r\n", 1),
    lambda b: b + b"\n", lambda b: b[:-4], lambda b: b"\n" + b,
    lambda b: b.replace(b"END\n", b"END X\n"),
    lambda b: b.replace(b"1ABC", b"abcd"),
    lambda b: b.replace(b"1ABC", b"1AB"),
])
def test_ascii_control_line_and_envelope_rejections(mutate):
    invalid(mutate(pack(fixture_lines())))


def test_truncated_and_oversize_atom_line():
    for replacement in (fixture_lines()[1][:77], fixture_lines()[1] + " "):
        lines = fixture_lines()
        lines[1] = replacement
        invalid(pack(lines))


def test_explicit_model_ter_segments_metadata_and_charge():
    lines = fixture_lines()
    lines[1] = change(lines[1], 78, 80, "1+")
    lines.insert(7, "TER      11      ALA A   1 ")
    lines.insert(8, atom_line(12, "N", "N", (3, 2, 1), residue("GLY", 2), "ATOM"))
    lines.insert(1, "MODEL        7")
    lines.insert(1, "NUMMDL       1")
    lines.insert(1, "REMARK schema fixture")
    lines.insert(-1, "ENDMDL")
    result = api().parse_original_pdb(pack(lines))
    assert result.model == 7 and result.atoms[0].identity[0] == 7
    assert result.atoms[0].charge == "1+"
    assert result.atoms[5].segment == 0 and result.atoms[6].segment == 1
    ter = next(r for r in result.records if r.record_type == "TER")
    assert ter.classification == "boundary" and ter.atom_serials == (11,)
    assert ter.identity == (7, "A", "ALA", 1, "") and ter.segment == 0
    assert result.records[1].classification == "metadata"


@pytest.mark.parametrize("kind", [
    "model_zero", "model_negative", "model_trailing", "two_models", "missing_endmdl",
    "orphan_endmdl", "outside_model", "empty_model", "late_header", "duplicate_header",
    "nummdl_two", "ter_first", "ter_water", "ter_duplicate", "ter_serial_collision",
    "ter_reserved", "ter_wrong_identity", "atom_after_ter", "ter_after_endmdl",
])
def test_invalid_model_and_ter_order(kind):
    lines = fixture_lines()
    ter = "TER      11      ALA A   1 "
    if kind == "model_zero": lines.insert(1, "MODEL        0")
    elif kind == "model_negative": lines.insert(1, "MODEL       -1")
    elif kind == "model_trailing": lines.insert(1, "MODEL        1 x")
    elif kind == "two_models": lines[1:1] = ["MODEL        1", "MODEL        2"]
    elif kind == "missing_endmdl": lines.insert(1, "MODEL        1")
    elif kind == "orphan_endmdl": lines.insert(-1, "ENDMDL")
    elif kind == "outside_model": lines.insert(7, "MODEL        1")
    elif kind == "empty_model": lines[1:1] = ["MODEL        1", "ENDMDL"]
    elif kind == "late_header": lines.insert(2, lines.pop(0))
    elif kind == "duplicate_header": lines.insert(1, lines[0])
    elif kind == "nummdl_two": lines.insert(1, "NUMMDL       2")
    elif kind == "ter_first": lines.insert(1, ter)
    elif kind == "ter_water": lines.insert(-1, "TER      11      HOH Z  40 ")
    elif kind == "ter_duplicate": lines[7:7] = [ter, ter]
    elif kind == "ter_serial_collision": lines.insert(7, change(ter, 6, 11, "    7"))
    elif kind == "ter_reserved": lines.insert(7, change(ter, 11, 17, "X     "))
    elif kind == "ter_wrong_identity": lines.insert(7, change(ter, 17, 20, "GLY"))
    elif kind == "atom_after_ter": lines.insert(6, ter)
    elif kind == "ter_after_endmdl":
        lines.insert(1, "MODEL        1")
        lines[-1:-1] = ["ENDMDL", ter]
    invalid(pack(lines))


def test_native_input_and_exception_reasons_only():
    class BytesSubclass(bytes): pass
    class StrSubclass(str): pass
    class IntSubclass(int): pass
    for raw in (True, [], bytearray(b"END"), BytesSubclass(pack(fixture_lines()))):
        invalid(raw)
    for reason in ("secret caller text", StrSubclass("raw_invalid"), [], True):
        with pytest.raises(ValueError, match="^raw_invalid$"):
            api().ProjectionInvalid(reason)
    for field, places in [(StrSubclass("1.00"), 2), ("1.00", IntSubclass(2))]:
        with pytest.raises(api().ProjectionInvalid):
            api().parse_scaled_decimal(field, places)


def link_line(name1="CB", alt1="A", name2="C1", alt2=" ", sym1="  1555", sym2="      "):
    line = "LINK  ".ljust(80)
    for start, end, value in [
        (12, 16, f"{name1:>4}"), (16, 17, alt1), (17, 20, "ALA"), (21, 22, "A"),
        (22, 26, "   1"), (42, 46, f"{name2:>4}"), (46, 47, alt2),
        (47, 50, "LIG"), (51, 52, "A"), (52, 56, "  20"),
        (59, 65, sym1), (66, 72, sym2), (73, 78, " 2.10"),
    ]:
        line = change(line, start, end, value)
    return line


def ssbond_line():
    line = "SSBOND".ljust(80)
    for start, end, value in [
        (7, 10, "  1"), (11, 14, "CYS"), (15, 16, "B"), (17, 21, "   2"),
        (25, 28, "CYS"), (29, 30, "B"), (31, 35, "   3"),
        (59, 65, "  1555"), (66, 72, "  1555"), (73, 78, " 2.03"),
    ]:
        line = change(line, start, end, value)
    return line


def connection_fixture(record):
    lines = fixture_lines()
    if record == "SSBOND":
        lines.insert(1, ssbond_line())
        lines[-1:-1] = [
            atom_line(11, "SG", "S", (1, 2, 3), residue("CYS", 2, "B"), "ATOM"),
            atom_line(12, "SG", "S", (2, 3, 4), residue("CYS", 3, "B"), "ATOM"),
        ]
    elif record == "LINK":
        lines.insert(1, link_line())
    else:
        lines.insert(-1, "CONECT    7    5    5    6    8")
    return lines


@pytest.mark.parametrize("record,serials", [
    ("LINK", (5, 7)), ("SSBOND", (11, 12)), ("CONECT", (7, 5, 5, 6, 8)),
])
def test_connection_preserves_exact_endpoint_order_multiplicity_and_raw(record, serials):
    raw = pack(connection_fixture(record), "\r\n")
    result = api().parse_original_pdb(raw)
    connection, = result.connections
    assert connection.record_type == record and connection.atom_serials == serials
    assert connection.original_line == raw.splitlines(keepends=True)[connection.record_index - 1]
    assert connection.symmetry == (() if record == "CONECT" else ("identity", "identity"))
    assert not hasattr(connection, "chemistry_kind")
    entry = result.records[connection.record_index - 1]
    assert entry.classification == "connection" and entry.atom_serials == serials
    assert tuple(e.name for e in connection.endpoints) == tuple(
        next(a.name for a in result.atoms if a.serial == s) for s in serials)
    if record == "LINK":
        assert connection.raw_operators == ("  1555", "      ")
        assert connection.endpoints[0].altloc == "A"
        assert connection.endpoints[0].identity == (1, "A", "ALA", 1, "")
    assert_immutable(result)


@pytest.mark.parametrize("record,start,end,value", [
    ("LINK", 6, 12, "X     "), ("LINK", 16, 17, "!"), ("LINK", 12, 16, " C B"),
    ("LINK", 20, 21, "X"), ("LINK", 27, 42, "X              "),
    ("LINK", 50, 51, "X"), ("LINK", 57, 59, " X"), ("LINK", 65, 66, "X"),
    ("LINK", 72, 73, "X"), ("LINK", 78, 80, " X"),
    ("LINK", 59, 65, "  2555"), ("LINK", 66, 72, "  1455"),
    ("LINK", 59, 65, " 1555 "), ("LINK", 59, 65, "abcdef"),
    ("LINK", 73, 78, "  NaN"), ("LINK", 73, 78, "-1.00"),
    ("SSBOND", 7, 10, "  0"), ("SSBOND", 11, 14, "SEC"), ("SSBOND", 25, 28, "ALA"),
    ("SSBOND", 22, 25, " X "), ("SSBOND", 36, 59, "X                      "),
    ("SSBOND", 59, 65, "  2555"), ("SSBOND", 66, 72, "  1556"),
])
def test_connection_field_syntax_and_nonidentity_symmetry_reject(record, start, end, value):
    lines = connection_fixture(record)
    lines[1] = change(lines[1], start, end, value)
    invalid(pack(lines), "connection_invalid")


@pytest.mark.parametrize("record", ["LINK", "SSBOND"])
def test_truncated_connection_rejects(record):
    lines = connection_fixture(record)
    lines[1] = lines[1][:71]
    invalid(pack(lines), "connection_invalid")


@pytest.mark.parametrize("value", [
    "CONECT", "CONECT    7", "CONECT    7    0", "CONECT    7   99",
    "CONECT    7         5", "CONECT    7    5         6", "CONECT    7    5    6    8    9   10",
    "CONECT    7    5X", "CONECT    7    5    6   8", "CONECT    7  5  ",
])
def test_conect_malformed_dangling_holes_and_fifth_destination_reject(value):
    lines = fixture_lines()
    lines.insert(-1, value)
    invalid(pack(lines), "connection_invalid")


def test_connections_cannot_reference_ter_serial():
    lines = fixture_lines()
    lines.insert(7, "TER      11      ALA A   1 ")
    lines.insert(-1, "CONECT    7   11")
    invalid(pack(lines), "connection_invalid")


@pytest.mark.parametrize("alt", [" ", "C"])
def test_link_does_not_guess_blank_or_missing_altloc(alt):
    lines = fixture_lines()
    lines.insert(1, link_line(alt1=alt))
    invalid(pack(lines), "connection_invalid")


def test_blank_link_altloc_matches_only_exact_blank_atom():
    lines = fixture_lines()
    lines.insert(1, link_line(name1="CA", alt1=" "))
    assert api().parse_original_pdb(pack(lines)).connections[0].atom_serials == (2, 7)


def test_link_and_ssbond_dangling_endpoints():
    for record, start, end in [("LINK", 52, 56), ("SSBOND", 31, 35)]:
        lines = connection_fixture(record)
        lines[1] = change(lines[1], start, end, " 999")
        invalid(pack(lines), "connection_invalid")


def test_ssbond_ambiguous_sg_fails_closed_but_single_alternate_is_exact():
    lines = connection_fixture("SSBOND")
    lines[-3] = change(lines[-3], 16, 17, "A")
    assert api().parse_original_pdb(pack(lines)).connections[0].atom_serials == (11, 12)
    lines.insert(-1, atom_line(13, "SG", "S", (1, 2, 4), residue("CYS", 2, "B"), "ATOM", "B", 0.4))
    invalid(pack(lines), "connection_invalid")


def test_explicit_model_connection_metadata_before_model_resolves_final_model():
    lines = connection_fixture("LINK")
    lines.insert(2, "MODEL        7")
    lines.insert(-1, "ENDMDL")
    parsed = api().parse_original_pdb(pack(lines))
    assert all(e.identity[0] == 7 for e in parsed.connections[0].endpoints)


def test_two_alt_edges_both_survive_no_global_a_selection():
    lines = connection_fixture("LINK")
    lines.insert(2, link_line(alt1="B"))
    assert [c.atom_serials for c in api().parse_original_pdb(pack(lines)).connections] == [(5, 7), (6, 7)]


def test_byte_cap_is_checked_before_line_decode_or_slice(monkeypatch):
    module = api()
    raw = pack(fixture_lines())
    monkeypatch.setattr(module, "MAX_PDB_BYTES", len(raw) - 1)
    def forbidden(*args):
        pytest.fail("oversize input reached line allocation")
    monkeypatch.setattr(module, "_lines", forbidden)
    invalid(raw, "resource_limit")


def test_atom_cap_is_preflighted_before_first_atom_allocation(monkeypatch):
    module = api()
    monkeypatch.setattr(module, "MAX_ATOMS", 9)
    def forbidden(*args):
        pytest.fail("atom cap must fail before constructing inventory")
    monkeypatch.setattr(module, "_atom", forbidden)
    invalid(pack(fixture_lines()), "resource_limit")


def test_budget_fixed_ceiling_native_types_and_atomic_reservation():
    module = api()
    assert module.MAX_LOGICAL_BYTES == 512 * 1024 * 1024
    budget = module.AllocationBudget(100)
    budget.reserve(99)
    assert budget.used == 99
    with pytest.raises(module.ProjectionInvalid, match="^resource_limit$"):
        budget.reserve(2)
    assert budget.used == 99
    budget.reserve(1)
    assert budget.used == 100
    class IntSubclass(int): pass
    for value in (True, -1, IntSubclass(10), module.MAX_LOGICAL_BYTES + 1):
        with pytest.raises(module.ProjectionInvalid, match="^resource_limit$"):
            module.AllocationBudget(value)
    for value in (True, -1, IntSubclass(1)):
        with pytest.raises(module.ProjectionInvalid, match="^resource_limit$"):
            budget.reserve(value)


@pytest.mark.parametrize("kind", ["atoms", "metadata", "connections", "ter"])
def test_real_budget_exact_boundary_precedes_all_output_allocation(monkeypatch, kind):
    module = api()
    lines = connection_fixture("LINK") if kind == "connections" else fixture_lines()
    if kind == "metadata": lines[1:1] = ["REMARK bounded schema"] * 30
    if kind == "ter": lines.insert(7, "TER      11      ALA A   1 ")
    raw = pack(lines)
    reservations = []
    original = module.AllocationBudget.reserve
    def spy(self, amount):
        original(self, amount)
        reservations.append((amount, self.used))
    monkeypatch.setattr(module.AllocationBudget, "reserve", spy)
    module.parse_original_pdb(raw)
    required = reservations[-1][1]
    expected = (len(raw) + module.BASE_RESERVATION_BYTES
                + len(lines) * module.RECORD_RESERVATION_BYTES
                + 10 * module.ATOM_RESERVATION_BYTES
                + (module.CONNECTION_RESERVATION_BYTES if kind == "connections" else 0)
                + (module.TER_RESERVATION_BYTES if kind == "ter" else 0))
    assert required == expected  # includes all object/index/scratch costs
    monkeypatch.setattr(module, "MAX_LOGICAL_BYTES", required)
    assert len(module.parse_original_pdb(raw).atoms) == 10
    monkeypatch.setattr(module, "MAX_LOGICAL_BYTES", required - 1)
    def forbidden(*args):
        pytest.fail("allocation began before complete budget reservation")
    monkeypatch.setattr(module, "_lines", forbidden)
    invalid(raw, "resource_limit")


def test_budget_stops_metadata_flood_without_constructing_objects(monkeypatch):
    module = api()
    # Tiny input/lowered ceiling demonstrates the same early gate; no huge alloc.
    raw = pack([fixture_lines()[0]] + ["REMARK"] * 100 + fixture_lines()[1:])
    limit = len(raw) + module.BASE_RESERVATION_BYTES + 2 * module.RECORD_RESERVATION_BYTES
    monkeypatch.setattr(module, "MAX_LOGICAL_BYTES", limit)
    calls = []
    original = module.AllocationBudget.reserve
    def spy(self, amount):
        calls.append(amount)
        return original(self, amount)
    monkeypatch.setattr(module.AllocationBudget, "reserve", spy)
    def forbidden(*args):
        pytest.fail("metadata flood reached decode/output objects")
    monkeypatch.setattr(module, "_lines", forbidden)
    invalid(raw, "resource_limit")
    assert len(calls) <= 5


@pytest.mark.parametrize("width", [3, 4, 5, 6, 80])
def test_end_accepts_optional_space_padding(width):
    lines = fixture_lines()
    lines[-1] = "END".ljust(width)
    assert len(api().parse_original_pdb(pack(lines)).atoms) == 10


def test_header_reserved_columns_are_not_free_text():
    lines = fixture_lines()
    lines[0] = change(lines[0], 6, 10, "   X")
    invalid(pack(lines))


@pytest.mark.parametrize("width", [0, 1, 5, 6, 13, 26])
def test_truncated_ter_rejects_before_indexing(width):
    lines = fixture_lines()
    lines.insert(7, "TER      11      ALA A   1 "[:width])
    invalid(pack(lines))


@pytest.mark.parametrize("value", ["    0", "   -1", " 1  1"])
def test_invalid_ter_serials(value):
    lines = fixture_lines()
    lines.insert(7, change("TER      11      ALA A   1 ", 6, 11, value))
    invalid(pack(lines))


def test_model_bounds_insertion_blank_chain_negative_resseq_and_native_integer_facts():
    lines = [fixture_lines()[0], "MODEL     9999",
             atom_line(99999, "CA", "C", (-999.999, 9999.999, 0),
                       residue("ALA", -999, "", "z"), "ATOM", "0", 0.01),
             "ENDMDL", "END"]
    parsed = api().parse_original_pdb(pack(lines))
    atom, = parsed.atoms
    assert atom.identity == (9999, "", "ALA", -999, "z")
    assert atom.xyz_milli == (-999999, 9999999, 0)
    assert atom.altloc == "0" and atom.occupancy_hundredths == 1
    assert_immutable(parsed)


def test_parser_does_not_apply_later_selected_chain_cap_or_protein_completeness():
    chains = " ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    lines = [fixture_lines()[0]] + [
        atom_line(i, "O", "O", (i, 1, 2), residue("HOH", 1, chain))
        for i, chain in enumerate(chains, 1)
    ] + ["END"]
    parsed = api().parse_original_pdb(pack(lines))
    assert len({a.identity[1] for a in parsed.atoms}) == 63


@pytest.mark.parametrize("record", (
    "TITLE COMPND SOURCE KEYWDS EXPDTA AUTHOR REVDAT JRNL REMARK DBREF DBREF1 DBREF2 "
    "SEQADV SEQRES MODRES HET HETNAM HETSYN FORMUL HELIX SHEET CISPEP CRYST1 "
    "ORIGX1 ORIGX2 ORIGX3 SCALE1 SCALE2 SCALE3 MTRIX1 MTRIX2 MTRIX3 MASTER SPLIT OBSLTE SPRSDE"
).split())
def test_d1_known_metadata_is_preserved_and_classified_not_proof(record):
    lines = fixture_lines()
    lines.insert(1, record.ljust(6))
    parsed = api().parse_original_pdb(pack(lines))
    assert parsed.records[1].classification == "metadata"
    assert parsed.records[1].original_line == lines[1].encode() + b"\n"


def test_raw_parser_never_calls_old_d1_parser(monkeypatch):
    donor = importlib.import_module("src.docking.pocket_evidence")
    def forbidden(*args):
        pytest.fail("P1 must not strip columns and delegate to D1")
    monkeypatch.setattr(donor, "_parse", forbidden)
    assert len(api().parse_original_pdb(pack(fixture_lines())).atoms) == 10


def test_caps_are_not_raised_and_exact_small_caps_pass(monkeypatch):
    module = api()
    assert module.MAX_PDB_BYTES == 16 * 1024 * 1024 and module.MAX_ATOMS == 99999
    raw = pack(fixture_lines())
    monkeypatch.setattr(module, "MAX_PDB_BYTES", len(raw))
    monkeypatch.setattr(module, "MAX_ATOMS", 10)
    assert len(module.parse_original_pdb(raw).atoms) == 10


def test_explicit_coordinates_after_endmdl_are_rejected():
    lines = fixture_lines()
    lines.insert(1, "MODEL        1")
    lines.insert(7, "ENDMDL")
    invalid(pack(lines))


def test_same_name_in_different_insertion_residues_is_not_duplicate():
    lines = fixture_lines()
    lines.insert(-1, atom_line(11, "CA", "C", (1, 2, 3), residue("ALA", 1, "A", "B"), "ATOM"))
    parsed = api().parse_original_pdb(pack(lines))
    assert parsed.atoms[-1].identity == (1, "A", "ALA", 1, "B")


@pytest.mark.parametrize("blank_index", [5, 6])
def test_same_residue_same_name_blank_and_nonblank_altloc_reject_in_either_order(blank_index):
    lines = fixture_lines()
    lines[blank_index] = change(lines[blank_index], 16, 17, " ")
    invalid(pack(lines))


def test_same_name_blank_and_nonblank_in_distinct_residues_is_not_ambiguous():
    lines = fixture_lines()
    lines.insert(-1, atom_line(11, "CB", "C", (1, 2, 3), residue("ALA", 2), "ATOM"))
    parsed = api().parse_original_pdb(pack(lines))
    assert [(a.serial, a.altloc) for a in parsed.atoms if a.name == "CB"] == [(5, "A"), (6, "B"), (11, "")]


def link_to_ala(name2, alt2, seq=1, chain="A", icode=" "):
    line = link_line(name2=name2, alt2=alt2)
    for start, end, value in [(47, 50, "ALA"), (51, 52, chain), (52, 56, f"{seq:4d}"), (56, 57, icode)]:
        line = change(line, start, end, value)
    return line


@pytest.mark.parametrize("record", ["CONECT", "LINK", "SSBOND"])
def test_resolved_connection_self_edge_is_rejected(record):
    lines = connection_fixture(record)
    if record == "CONECT":
        lines[-2] = "CONECT    5    5"
    elif record == "LINK":
        lines[1] = link_to_ala("CB", "A")
    else:
        lines[1] = change(lines[1], 31, 35, "   2")
    invalid(pack(lines), "connection_invalid")


@pytest.mark.parametrize("record", ["CONECT", "LINK"])
@pytest.mark.parametrize("same_name", [True, False])
def test_same_residue_incompatible_altloc_edge_rejects_even_for_different_names(record, same_name):
    lines = connection_fixture(record)
    if not same_name:
        lines.insert(-1, atom_line(11, "CG", "C", (1, 2, 3), residue("ALA", 1), "ATOM", "B", 0.4))
    if record == "CONECT":
        index = next(i for i, line in enumerate(lines) if line.startswith("CONECT"))
        lines[index] = "CONECT    5" + f"{6 if same_name else 11:5d}"
    else:
        lines[1] = link_to_ala("CB" if same_name else "CG", "B")
    invalid(pack(lines), "connection_invalid")


@pytest.mark.parametrize("line", [
    "CONECT    6    5", "CONECT    5    2    5", "CONECT    5    2    2    2    6",
])
def test_every_conect_source_destination_pair_is_checked(line):
    lines = fixture_lines()
    lines.insert(-1, line)
    invalid(pack(lines), "connection_invalid")


@pytest.mark.parametrize("record", ["CONECT", "LINK"])
@pytest.mark.parametrize("altloc", [" ", "A"])
def test_same_residue_common_or_matching_altloc_edges_remain_valid(record, altloc):
    lines = fixture_lines()
    lines.insert(-1, atom_line(11, "CG", "C", (1, 2, 3), residue("ALA", 1), "ATOM", altloc))
    if record == "CONECT":
        lines.insert(-1, "CONECT    5   11   11")
        expected = (5, 11, 11)
    else:
        lines.insert(1, link_to_ala("CG", altloc))
        expected = (5, 11)
    assert api().parse_original_pdb(pack(lines)).connections[0].atom_serials == expected


@pytest.mark.parametrize("record", ["CONECT", "LINK"])
@pytest.mark.parametrize("seq,chain,icode", [(2, "A", ""), (1, "B", ""), (1, "A", "B")])
def test_different_residue_altloc_labels_are_not_assumed_correlated(record, seq, chain, icode):
    lines = fixture_lines()
    lines.insert(-1, atom_line(11, "CB", "C", (1, 2, 3), residue("ALA", seq, chain, icode), "ATOM", "B", 0.4))
    if record == "CONECT":
        lines.insert(-1, "CONECT    5   11")
    else:
        lines.insert(1, link_to_ala("CB", "B", seq, chain, icode or " "))
    assert api().parse_original_pdb(pack(lines)).connections[0].atom_serials == (5, 11)


def test_ssbond_different_residue_sg_altloc_labels_are_not_assumed_correlated():
    lines = connection_fixture("SSBOND")
    lines[-3] = change(lines[-3], 16, 17, "A")
    lines[-2] = change(lines[-2], 16, 17, "B")
    assert api().parse_original_pdb(pack(lines)).connections[0].atom_serials == (11, 12)
