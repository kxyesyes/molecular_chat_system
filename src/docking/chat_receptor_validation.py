"""C-only preservation check, not source authorization or a repair pipeline.

The approved single-model PDB is already selected/authorized upstream. ADFR may
renumber atoms, type aromatic/acceptor atoms and change hydrogens, but must not
change any heavy atom's record, name, residue, chain, element or coordinates.
PDBQT has no retained model/connection table: component identity is checked by
the complete heavy-atom inventory, not by inventing bonds or chemistry.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from .receptor_projection_pdb import parse_original_pdb, parse_scaled_decimal


MAX_PREPARATION_BYTES = 16 * 1024 * 1024
MAX_PREPARATION_ATOMS = 99999
# Deliberately closed conventional AutoDock/Vina types. Unknown retained
# elements/types fail closed; no guessed typing or metal substitution.
_TYPE_ELEMENT = {
    "C": "C", "A": "C", "N": "N", "NA": "N", "O": "O", "OA": "O",
    "S": "S", "SA": "S", "P": "P", "F": "F", "Cl": "Cl", "Br": "Br",
    "I": "I", "Mg": "Mg", "Mn": "Mn", "Zn": "Zn", "Ca": "Ca", "Fe": "Fe",
    "H": "H", "HD": "H",
}


class ReceptorPreservationError(ValueError):
    def __init__(self):
        super().__init__("receptor_preservation_failed")


def _require(condition):
    if not condition:
        raise ReceptorPreservationError()


def _stamp(value):
    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode), value.st_size,
            value.st_mtime_ns, value.st_ctime_ns, value.st_nlink)


def _read_bounded(path):
    """Reject links, non-files, growth, partial reads and oversized files."""
    path = Path(path)
    before = path.lstat()
    _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
             and not getattr(before, "st_file_attributes", 0) & 0x400
             and 0 < before.st_size <= MAX_PREPARATION_BYTES)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        _require(_stamp(os.fstat(stream.fileno())) == _stamp(before))
        raw = stream.read(before.st_size + 1)
        _require(len(raw) == before.st_size
                 and _stamp(os.fstat(stream.fileno())) == _stamp(before)
                 and _stamp(path.lstat()) == _stamp(before))
    return raw


def approved_heavy_atoms(path):
    """Snapshot strict approved PDB facts BEFORE preparation can change files."""
    try:
        _require(Path(path).suffix.lower() == ".pdb")
        parsed = parse_original_pdb(_read_bounded(path))
        heavy = {}
        for atom in parsed.atoms:
            _require(not atom.altloc)  # upstream must resolve alternates
            if atom.element in {"H", "D"}:
                continue
            _require(atom.element in _TYPE_ELEMENT.values())
            key = (atom.record, *atom.identity[1:], atom.name)
            _require(key not in heavy and len(heavy) < MAX_PREPARATION_ATOMS)
            heavy[key] = (atom.element, atom.xyz_milli)
        _require(bool(heavy))
        return heavy
    except (OSError, ValueError, UnicodeError):
        raise ReceptorPreservationError() from None


def validate_prepared_receptor(path, approved):
    """Check the entire actual PDBQT, never a prefix or subprocess return code.

    Exact milli-Angstrom equality matches the fixed three-decimal PDB/PDBQT
    writers. Serial numbering/order and hydrogen addition/removal may differ.
    At most 16 MiB / 99999 atom records, 82 bytes per physical line; indexes
    retain bounded identity tuples only, not copies of all text lines.
    """
    try:
        raw = _read_bounded(path)
        seen, serials = set(), set()
        heavy_seen = set()
        components = {key[:5] for key in approved}
        start, count, ended = 0, 0, False
        while start < len(raw):
            stop = raw.find(b"\n", start, start + 83)
            _require(stop >= 0 and not ended)
            line = raw[start:stop].removesuffix(b"\r").decode("ascii")
            start = stop + 1
            _require(0 < len(line) <= 80 and all(32 <= ord(c) <= 126 for c in line))
            if line[:6] == "TER   ":
                _require(count > 0 and len(line) >= 27 and not line[27:].strip()
                         and line[11:17] == "      " and line[20] == " ")
                continue
            if line.strip() == "END":
                _require(count > 0)
                ended = True
                continue
            _require(line[:6] in {"ATOM  ", "HETATM"} and 78 <= len(line) <= 80)
            count += 1
            _require(count <= MAX_PREPARATION_ATOMS)
            _require(line[11] == line[16] == line[20] == line[76] == " "
                     and line[27:30] == "   " and line[66:70] == "    ")
            _require(re.fullmatch(r" *[0-9]+", line[6:11]) is not None)
            serial = int(line[6:11])
            _require(1 <= serial <= 99999 and serial not in serials)
            serials.add(serial)
            _require(re.fullmatch(r" *-?[0-9]+", line[22:26]) is not None)
            key = (line[:6].strip(), line[21].strip(), line[17:20].strip(),
                   int(line[22:26]), line[26].strip(), line[12:16].strip())
            _require(bool(key[-1]) and key not in seen and key[:5] in components)
            seen.add(key)
            xyz = tuple(parse_scaled_decimal(line[i:i+8], 3) for i in (30, 38, 46))
            # Validate complete numeric fields, including charges, even on H.
            _require(0 <= parse_scaled_decimal(line[54:60], 2) <= 100)
            _require(parse_scaled_decimal(line[60:66], 2) >= 0)
            parse_scaled_decimal(line[70:76], 3)
            element = _TYPE_ELEMENT.get(line[77:].strip())
            _require(element is not None)
            if element == "H":
                _require(key not in approved)
            else:
                _require(approved.get(key) == (element, xyz))
                heavy_seen.add(key)
        _require(heavy_seen == approved.keys())
    except (OSError, ValueError, UnicodeError):
        raise ReceptorPreservationError() from None
