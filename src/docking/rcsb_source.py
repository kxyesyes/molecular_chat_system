"""Pure, bounded wwPDB evidence comparison; neither authentication nor readiness.

Only Gemmi 0.7.5's CIF DOM is used. No structure conversion, I/O, selection,
repair, scoring or authority token. Callers must authenticate downloads and S03.

The 512 MiB ledger is a conservative *logical allocation* limit, not OS RSS.
It retains reservations across stages: input bytes, 64x CIF bytes for native DOM
and decoding, per-cell Python indexes, P1's complete preflight, output scratch,
P2 validation and serialization/IPC. No refund depends on GC. Discovery is a
separate parse whose DOM dies on return; an integrating worker must additionally
reserve that scan's peak (64x CIF bytes + indexed cells) on its root ledger, not
mistake two individually bounded calls for one aggregate allocation guarantee.
The private _discover_components/_compile seams perform this accounting when
passed the SAME root AllocationBudget; do not precharge discovery a second time.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import re

from . import receptor_projection_pdb as p1
from . import receptor_projection_source as p2


MAX_LOGICAL_BYTES = 512 * 1024 * 1024
MAX_CIF_BYTES = 16 * 1024 * 1024
MAX_COMPONENT_BYTES = 1024 * 1024
MAX_COMPONENTS = 64
MAX_ROWS = 99999
MAX_CELLS = 2_000_000
SAFE_REASONS = frozenset({"input_invalid", "resource_limit", "parser_version",
    "cif_invalid", "revision_mismatch", "inventory_mismatch", "sequence_invalid",
    "target_mismatch", "component_invalid", "context_unsupported", "connection_invalid",
    "constraint_invalid", "source_invalid"})


class _Invalid(ValueError):
    pass


def _require(condition, reason="cif_invalid"):
    if not condition:
        raise _Invalid(reason)


def _error(reason):
    return {"ok": False, "code": "RCSB_SOURCE_INVALID", "reason": reason}


def _text(value, pattern=r"[A-Za-z0-9_.:+-]{1,128}"):
    _require(type(value) is str and len(value) <= 128 and re.fullmatch(pattern, value) is not None)
    return value


def _int(value, low=0, high=99999):
    _text(value, r"-?[0-9]{1,10}")
    result = int(value)
    _require(low <= result <= high)
    return result


def _scaled(value, places):
    _text(value, r"-?[0-9]{1,7}(?:\.[0-9]{1,9})?")
    whole, _, fraction = value.partition(".")
    # CIF commonly writes occupancy 1.000. Only zero padding beyond PDB's
    # scale is representable; no rounding, tolerance or float conversion.
    _require(not fraction[places:].strip("0"), "inventory_mismatch")
    fraction = fraction[:places]
    return (-1 if whole.startswith("-") else 1) * (abs(int(whole)) * 10**places + int(fraction.ljust(places, "0")))


def _date(value):
    _text(value, r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
    date.fromisoformat(value)
    return value


def _blank(value):
    return "" if value in {".", "?"} else _text(value, r"[A-Za-z0-9]?")


def _field(row, key):
    value = row[key.lower()]
    _require(value not in {"?", ".", ""})
    return value


def _unique(rows, fields, *, integer_fields=()):
    result = {}
    for row in rows:
        # Typed integer keys must be unique before any later normalization can
        # overwrite them. Keep canonical strings for existing reference maps.
        key = tuple(str(_int(_field(row, field), 1)) if field in integer_fields else _field(row, field)
                    for field in fields.split())
        _require(key not in result)
        result[key] = row
    return result


def _gemmi():
    import gemmi
    _require(type(gemmi.__version__) is str and gemmi.__version__ == "0.7.5", "parser_version")
    return gemmi


class _Cif:
    """Low-level, bounded category rows; raw strings retain CIF null markers."""

    def __init__(self, raw, budget):
        budget.reserve(65536 + 64 * len(raw))
        gemmi = _gemmi()
        document = gemmi.cif.read_string(raw.decode("utf-8"))
        _require(len(document) == 1)
        block = document[0]
        _require(len(block.name) <= 128)
        self.name, self.categories = block.name, {}
        seen, cell_count, item_count = set(), 0, 0
        pairs = {}
        for item in block:
            item_count += 1
            _require(item_count <= 4096, "resource_limit")
            _require(item.frame is None)
            if item.pair is not None:
                tags, values, count = [item.pair[0]], [item.pair[1]], 1
            else:
                lp = item.loop
                _require(lp is not None)
                width, count = lp.width(), lp.length()
                _require(1 <= width <= 128 and 1 <= count <= MAX_ROWS, "resource_limit")
                tags, values = lp.tags, lp.values
            width = len(tags)
            cell_count += count * width
            _require(cell_count <= MAX_CELLS, "resource_limit")
            budget.reserve(count * (512 + width * 320))
            normalized = []
            for tag in tags:
                _require(len(tag) <= 128 and re.fullmatch(r"_[A-Za-z0-9_]+\.[A-Za-z0-9_-]+(?:\[[0-9]+\])*", tag))
                tag = tag.lower()
                _require(tag not in seen)
                seen.add(tag)
                normalized.append(tag.split(".", 1))
            category = normalized[0][0]
            _require(all(c == category for c, _ in normalized))
            fields = [f for _, f in normalized]
            if item.pair is not None:
                _require(category not in self.categories)
                value = values[0]
                _require(len(value) <= 16384, "resource_limit")
                pairs.setdefault(category, {})[fields[0]] = gemmi.cif.as_string(value) if value not in {"?", "."} else value
            else:
                _require(category not in self.categories and category not in pairs)
                rows, unique = [], set()
                for offset in range(0, len(values), width):
                    row = []
                    for value in values[offset:offset+width]:
                        _require(len(value) <= 16384, "resource_limit")
                        row.append(gemmi.cif.as_string(value) if value not in {"?", "."} else value)
                    signature = tuple(row)
                    _require(signature not in unique)
                    unique.add(signature)
                    rows.append(dict(zip(fields, row)))
                self.categories[category] = rows
        self.categories.update({key: [row] for key, row in pairs.items()})

    def rows(self, category, *, required=False):
        rows = self.categories.get("_" + category, [])
        _require(not required or bool(rows))
        return rows


def _admit_cif(raw, pdb_id, budget):
    _require(type(raw) is bytes and type(pdb_id) is str, "input_invalid")
    _require(0 < len(raw) <= MAX_CIF_BYTES, "resource_limit")
    _text(pdb_id, r"[0-9][A-Z0-9]{3}")
    budget.reserve(len(raw))


def _component_ids(cif, pdb_id):
    entry = cif.rows("entry", required=True)
    _require(len(entry) == 1 and _field(entry[0], "id") == pdb_id and cif.name.upper() == pdb_id)
    rows = cif.rows("chem_comp", required=True)
    _require(len(rows) <= MAX_COMPONENTS, "resource_limit")
    ids = []
    for row in rows:
        ids.append(_text(_field(row, "id"), r"[A-Z0-9]{1,3}"))
    _require(len(set(ids)) == len(ids))
    return tuple(sorted(ids))


def required_rcsb_components(cif_bytes, *, pdb_id):
    """Bounded discovery only: tuple of IDs, or the fixed safe error envelope.

    Never authority. The final compiler independently re-parses the same bytes.
    No parsed object, URL, path, or caller-owned mutable state escapes this call.
    """
    try:
        budget = p1.AllocationBudget(MAX_LOGICAL_BYTES)
        return _discover_components(cif_bytes, pdb_id, budget)
    except _Invalid as exc:
        return _error(exc.args[0])
    except p1.ProjectionInvalid:
        return _error("resource_limit")
    except (ValueError, RuntimeError, KeyError, IndexError, UnicodeError, MemoryError, ImportError):
        return _error("cif_invalid")


def _discover_components(cif_bytes, pdb_id, budget):
    """Private owned-worker seam; charge its retained root ledger, never reset.

    The worker reserves network stream/buffer peaks separately. This seam and
    _compile raise fixed-code failures for the worker's own safe error boundary.
    Only the exact native AllocationBudget is accepted, not caller hooks.
    """
    _require(type(budget) is p1.AllocationBudget, "input_invalid")
    _admit_cif(cif_bytes, pdb_id, budget)
    return _component_ids(_Cif(cif_bytes, budget), pdb_id)


def _capture(pdb_bytes, cif_bytes, component_files, pdb_id, target, budget):
    # Type identity, not isinstance or membership: hostile metaclasses, keys,
    # iterators and coercion hooks are never called before rejection.
    _admit_cif(cif_bytes, pdb_id, budget)
    _require(type(pdb_bytes) is bytes and type(component_files) is dict and type(target) is dict, "input_invalid")
    _require(0 < len(pdb_bytes) <= p1.MAX_PDB_BYTES and 0 < len(component_files) <= MAX_COMPONENTS, "resource_limit")
    _require(len(target) == 2, "input_invalid")
    budget.reserve(len(pdb_bytes) + 65536)
    captured_target = {}
    for key, value in target.items():
        _require(type(key) is str, "input_invalid")
        _require(key in {"uniprot_id", "taxonomy_id"}, "input_invalid")
        if key == "uniprot_id":
            _require(type(value) is str, "input_invalid")
            _text(value, r"[A-Z0-9]{6,10}")
        else:
            _require(type(value) is int and 1 <= value <= 2**31-1, "input_invalid")
        captured_target[key] = value
    _require(len(captured_target) == 2, "input_invalid")
    captured, total, count = [], 0, len(component_files)
    for key, value in component_files.items():
        _require(type(key) is str and type(value) is bytes, "input_invalid")
        _text(key, r"[A-Z0-9]{1,3}")
        _require(0 < len(value) <= MAX_COMPONENT_BYTES and len(captured) < count, "resource_limit")
        total += len(value)
        _require(total <= MAX_CIF_BYTES, "resource_limit")
        budget.reserve(len(value) + 1024)
        captured.append((key, value))
    _require(len(captured) == count, "input_invalid")
    # All mutable caller mappings have now been captured before native parsing.
    return tuple(captured), (captured_target["uniprot_id"], captured_target["taxonomy_id"])


def _revisions(cif, parsed, pdb_id):
    rows = cif.rows("pdbx_audit_revision_history", required=True)
    _unique(rows, "ordinal")
    history = []
    for row in rows:
        if _field(row, "data_content_type") == "Structure model":
            history.append({"ordinal": _int(_field(row, "ordinal"), 1),
                "date": _date(_field(row, "revision_date")),
                "major": _int(_field(row, "major_revision"), 1),
                "minor": _int(_field(row, "minor_revision"))})
    _require(bool(history), "revision_mismatch")
    history.sort(key=lambda r: r["ordinal"])
    _require([r["ordinal"] for r in history] == list(range(1, len(history)+1)), "revision_mismatch")
    _require(all(a["date"] <= b["date"] and (a["major"], a["minor"]) < (b["major"], b["minor"])
                 for a, b in zip(history, history[1:])), "revision_mismatch")
    _require(parsed.header[62:66] == pdb_id, "revision_mismatch")
    first, previous, continuation = {}, None, 1
    months = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()
    for record in parsed.records:
        if record.record_type != "REVDAT":
            continue
        line = record.original_line.decode("ascii").rstrip("\r\n").ljust(80)
        _require(line[6] == line[12] == line[22] == " " and line[27:31] == "    ", "revision_mismatch")
        number = _int(line[7:10].strip(), 1)
        suffix = line[10:12].strip()
        if not suffix:
            _require(number not in first and line[23:27] == pdb_id, "revision_mismatch")
            _require(line[31] in "0123456789", "revision_mismatch")
            _require(re.fullmatch(r"[0-9]{2}-[A-Z]{3}-[0-9]{2}", line[13:22]), "revision_mismatch")
            _require(1 <= number <= len(history), "revision_mismatch")
            expected = history[number-1]["date"]
            formatted = expected[8:10] + "-" + months[int(expected[5:7])-1] + "-" + expected[2:4]
            _require(line[13:22] == formatted, "revision_mismatch")
            first[number], previous, continuation = line, number, 1
        else:
            _require(previous == number and _int(suffix, 2) == continuation+1, "revision_mismatch")
            # Continuations may leave shared fields blank, but may not disagree.
            for start, end in ((13, 22), (23, 27), (31, 32)):
                _require(not line[start:end].strip() or line[start:end] == first[number][start:end], "revision_mismatch")
            continuation += 1
    _require(set(first) == set(range(1, len(history)+1)), "revision_mismatch")
    return history


def _chemistry(cif, component_files, latest, budget):
    embedded = {r["id"]: r for r in cif.rows("chem_comp", required=True)}
    atom_rows, bond_rows = {}, {}
    for row in cif.rows("chem_comp_atom", required=True):
        atom_rows.setdefault(_field(row, "comp_id"), []).append(row)
    for row in cif.rows("chem_comp_bond"):
        bond_rows.setdefault(_field(row, "comp_id"), []).append(row)
    _require(set(atom_rows) == set(embedded) and set(bond_rows) <= set(embedded), "component_invalid")
    components, evidence = {}, []
    for component_id, raw in component_files:
        ccd = _Cif(raw, budget)
        rows = ccd.rows("chem_comp", required=True)
        _require(len(rows) == 1 and ccd.name == component_id and _field(rows[0], "id") == component_id, "component_invalid")
        definition = rows[0]
        _require(_field(definition, "pdbx_release_status") == "REL", "component_invalid")
        modified = _date(_field(definition, "pdbx_modified_date"))
        _require(modified <= latest, "component_invalid")
        kind = _field(definition, "type").lower()
        _require(kind == _field(embedded[component_id], "type").lower(), "component_invalid")

        def atoms(rows, *, ccd=False):
            result, leaving = {}, set()
            for row in rows:
                _require(_field(row, "comp_id") == component_id, "component_invalid")
                name = _text(_field(row, "atom_id"), r"[A-Za-z0-9'_*+-]{1,4}")
                _require(name not in result, "component_invalid")
                element = _field(row, "type_symbol").capitalize()
                aromatic, stereo = _field(row, "pdbx_aromatic_flag"), _field(row, "pdbx_stereo_config")
                _require(element in p1._ELEMENTS and aromatic in {"Y", "N"} and stereo in {"R", "S", "N"}, "component_invalid")
                result[name] = (element, aromatic, stereo)
                if ccd:
                    flag = _field(row, "pdbx_leaving_atom_flag")
                    _require(flag in {"Y", "N"}, "component_invalid")
                    if flag == "Y":
                        leaving.add(name)
            return result, leaving

        official_atoms, leaving = atoms(ccd.rows("chem_comp_atom", required=True), ccd=True)
        entry_atoms, _ = atoms(atom_rows[component_id])
        _require(entry_atoms == official_atoms, "component_invalid")

        def bonds(rows):
            result = {}
            for row in rows:
                _require(_field(row, "comp_id") == component_id, "component_invalid")
                a, b = _field(row, "atom_id_1"), _field(row, "atom_id_2")
                key = tuple(sorted((a, b)))
                _require(a in official_atoms and b in official_atoms and a != b and key not in result, "component_invalid")
                order = _field(row, "value_order").upper()
                aromatic, stereo = _field(row, "pdbx_aromatic_flag"), _field(row, "pdbx_stereo_config")
                _require(order in {"SING", "DOUB", "TRIP", "AROM", "DELO"} and aromatic in {"Y", "N"} and stereo in {"E", "Z", "N"}, "component_invalid")
                result[key] = order, aromatic, stereo
            return result

        official_bonds = bonds(ccd.rows("chem_comp_bond"))
        _require(bonds(bond_rows.get(component_id, [])) == official_bonds, "component_invalid")
        digest = hashlib.sha256(raw).hexdigest()
        components[component_id] = {"atoms": official_atoms, "bonds": official_bonds,
            "leaving": leaving, "type": kind,
            "source": {"record_id": component_id, "version": modified, "sha256": digest}}
        evidence.append({"id": component_id, "date": modified, "sha256": digest})
    return components, evidence


def _inventory(cif, parsed, components, budget):
    rows = cif.rows("atom_site", required=True)
    _require(len(rows) == len(parsed.atoms) <= MAX_ROWS, "inventory_mismatch")
    budget.reserve(len(rows) * 12288)
    _unique(rows, "id", integer_fields=("id",))
    raw_index = {(a.identity, a.name, a.altloc): a for a in parsed.atoms}
    residues, annotations, atoms, seen = {}, {}, {}, set()
    for row in rows:
        _int(_field(row, "id"), 1)
        model = _int(_field(row, "pdbx_pdb_model_num"), 1, 9999)
        comp = _text(_field(row, "label_comp_id"), r"[A-Z0-9]{1,3}")
        name = _text(_field(row, "label_atom_id"), r"[A-Za-z0-9'_*+-]{1,4}")
        element = _field(row, "type_symbol").capitalize()
        definition = components.get(comp)
        _require(definition is not None and name in definition["atoms"]
                 and definition["atoms"][name][0] == element, "component_invalid")
        _require(_field(row, "auth_comp_id") == comp and _field(row, "auth_atom_id") == name, "inventory_mismatch")
        identity = (model, _blank(row["auth_asym_id"]), comp,
                    _int(_field(row, "auth_seq_id"), -999, 9999), _blank(row["pdbx_pdb_ins_code"]))
        alt = _blank(row["label_alt_id"])
        key = identity, name, alt
        _require(key in raw_index and key not in seen, "inventory_mismatch")
        seen.add(key)
        charge = row["pdbx_formal_charge"]
        charge = 0 if charge in {"?", "."} else _int(charge, -9, 9)
        atom = {"serial": raw_index[key].serial, "name": name,
            "element": element, "altloc": alt,
            "occupancy_hundredths": _scaled(_field(row, "occupancy"), 2),
            "xyz_milli": [_scaled(_field(row, axis), 3) for axis in ("cartn_x", "cartn_y", "cartn_z")],
            "bfactor_hundredths": _scaled(_field(row, "b_iso_or_equiv"), 2),
            "charge": (str(abs(charge)) + ("+" if charge > 0 else "-")) if charge else ""}
        _require(all((tuple(value) if field == "xyz_milli" else value) == getattr(raw_index[key], field)
                     for field, value in atom.items()), "inventory_mismatch")
        record = _field(row, "group_pdb")
        _require(record == raw_index[key].record, "inventory_mismatch")
        annotation = (_field(row, "label_entity_id"), _field(row, "label_asym_id"), row["label_seq_id"])
        _require(identity not in annotations or annotations[identity] == annotation, "inventory_mismatch")
        annotations[identity] = annotation
        if identity not in residues:
            residues[identity] = {"identity": p2.identity_dict(identity), "record": record,
                "entity_id": annotation[0], "role": "unproven", "atoms": [], "required_heavy_atoms": []}
        _require(residues[identity]["record"] == record, "inventory_mismatch")
        residues[identity]["atoms"].append(atom)
        atoms[key] = atom
    _require(seen == raw_index.keys(), "inventory_mismatch")
    return residues, annotations, atoms


def _sequences(cif, residues, annotations, components, target, pdb_id, model, budget):
    budget.reserve(8192 * (len(cif.rows("entity_poly_seq")) + len(cif.rows("pdbx_poly_seq_scheme"))
                         + len(cif.rows("struct_ref_seq_dif")) + len(cif.rows("pdbx_unobs_or_zero_occ_residues"))))
    entities = {key[0]: row for key, row in _unique(cif.rows("entity", required=True), "id").items()}
    asym = {key[0]: _field(row, "entity_id") for key, row in _unique(cif.rows("struct_asym", required=True), "id").items()}
    _require(all(entity in entities for entity in asym.values()), "sequence_invalid")
    polymers = {key[0]: row for key, row in _unique(cif.rows("entity_poly", required=True), "entity_id").items()}
    _require(all(entity in entities and _field(entities[entity], "type") == "polymer" for entity in polymers), "sequence_invalid")
    sequences = _unique(cif.rows("entity_poly_seq", required=True), "entity_id num", integer_fields=("num",))
    schemes = _unique(cif.rows("pdbx_poly_seq_scheme", required=True), "asym_id seq_id", integer_fields=("seq_id",))
    references = {key[0]: row for key, row in _unique(cif.rows("struct_ref", required=True), "id").items()}
    _require(all(_field(row, "entity_id") in entities for row in references.values()), "sequence_invalid")
    alignments = cif.rows("struct_ref_seq", required=True)
    budget.reserve(8192 * len(alignments))
    _unique(alignments, "align_id")
    taxes = {}
    for category, field in (("entity_src_gen", "pdbx_gene_src_ncbi_taxonomy_id"), ("entity_src_nat", "pdbx_ncbi_taxonomy_id")):
        for row in cif.rows(category):
            entity = _field(row, "entity_id")
            _require(entity in polymers, "sequence_invalid")
            tax = _int(_field(row, field), 1, 2**31-1)
            _require(entity not in taxes or taxes[entity] == tax, "target_mismatch")
            taxes[entity] = tax
    seq_by_entity = {}
    for (entity, number), row in sequences.items():
        _require(entity in polymers and _field(row, "mon_id") in components and row.get("hetero", "n").lower() == "n", "sequence_invalid")
        seq_by_entity.setdefault(entity, {})[_int(number, 1)] = _field(row, "mon_id")
    # Validate EVERY alignment before per-chain filtering. Declared chains are
    # reconciled with actual scheme/asym chains below, so no unmatched row can
    # bypass entry/accession/range checks or disappear through an early continue.
    for align in alignments:
        ref = references.get(_field(align, "ref_id"))
        _require(ref is not None and _field(ref, "entity_id") in polymers, "sequence_invalid")
        entity = _field(ref, "entity_id")
        chains = _field(align, "pdbx_strand_id").split(",")
        declared = _field(polymers[entity], "pdbx_strand_id").split(",")
        _require(all(chains) and len(chains) == len(set(chains)) and set(chains) <= set(declared), "sequence_invalid")
        _require(_field(ref, "db_name") == "UNP", "target_mismatch")
        accession = _text(_field(ref, "pdbx_db_accession"), r"[A-Z0-9]{6,10}")
        _require(_field(align, "pdbx_pdb_id_code") == pdb_id
                 and _field(align, "pdbx_db_accession") == accession, "sequence_invalid")
        start, end = _int(_field(align, "seq_align_beg"), 1), _int(_field(align, "seq_align_end"), 1)
        db_start, db_end = _int(_field(align, "db_align_beg"), 1), _int(_field(align, "db_align_end"), 1)
        _require(start <= end <= len(seq_by_entity.get(entity, {})) and db_end-db_start == end-start, "sequence_invalid")
    chain_map, mappings, proteins, explanation, limitations = {}, {}, {}, [], []
    for entity, poly in polymers.items():
        _require(_field(poly, "type") in {"polypeptide(L)", "polypeptide(D)"}, "context_unsupported")
        seq = seq_by_entity.get(entity, {})
        _require(bool(seq) and sorted(seq) == list(range(1, len(seq)+1)), "sequence_invalid")
        _require(entity in taxes, "target_mismatch")
        declared_chains = set(_field(poly, "pdbx_strand_id").split(","))
        actual_chains = set()
        for label, owner in asym.items():
            if owner != entity:
                continue
            relevant = {_int(number, 1): row for (chain, number), row in schemes.items() if chain == label}
            _require(set(relevant) == set(seq), "sequence_invalid")
            auth_chain = None
            for number, row in relevant.items():
                chain = _blank(row["pdb_strand_id"])
                _require(_field(row, "entity_id") == entity and _field(row, "mon_id") == seq[number], "sequence_invalid")
                _require(row.get("hetero", "n").lower() == "n", "context_unsupported")
                _require(auth_chain is None or auth_chain == chain, "sequence_invalid")
                auth_chain = chain
                ident = (model, chain, seq[number], _int(_field(row, "pdb_seq_num"), -999, 9999), _blank(row["pdb_ins_code"]))
                mappings[label, str(number)] = ident
                if row.get("pdb_mon_id", "?") not in {"?", "."}:
                    _require(row["pdb_mon_id"] == seq[number], "sequence_invalid")
            _require(auth_chain not in chain_map or chain_map[auth_chain] == label, "sequence_invalid")
            chain_map[auth_chain] = label
            actual_chains.add(auth_chain)
            covered, accession = set(), None
            for align in alignments:
                ref = references.get(_field(align, "ref_id"))
                _require(ref is not None, "sequence_invalid")
                if _field(ref, "entity_id") != entity or auth_chain not in _field(align, "pdbx_strand_id").split(","):
                    continue
                _require(_field(ref, "db_name") == "UNP", "target_mismatch")
                own_accession = _text(_field(ref, "pdbx_db_accession"), r"[A-Z0-9]{6,10}")
                _require(accession is None or accession == own_accession, "target_mismatch")
                accession = own_accession
                _require(_field(align, "pdbx_pdb_id_code") == pdb_id and _field(align, "pdbx_db_accession") == accession, "sequence_invalid")
                start, end = _int(_field(align, "seq_align_beg"), 1), _int(_field(align, "seq_align_end"), 1)
                db_start, db_end = _int(_field(align, "db_align_beg"), 1), _int(_field(align, "db_align_end"), 1)
                _require(start <= end <= len(seq) and db_end-db_start == end-start, "sequence_invalid")
                positions = set(range(start, end+1))
                _require(not positions & covered, "sequence_invalid")
                covered.update(positions)
                explanation.append({"chain": auth_chain, "entity_id": entity, "accession": accession,
                    "label_begin": start, "label_end": end, "database_begin": db_start, "database_end": db_end})
                if start == 1 and db_start > 1:
                    # The alignment proves an N-terminal cut, not a full UniProt
                    # length or identities of the omitted residues.
                    limitations.append({"id": "truncation-" + label, "kind": "construct_truncation",
                        "residues": [p2.identity_dict(mappings[label, str(start)])], "source_record_id": "alignment-" + _field(align, "align_id")})
            _require(bool(covered), "sequence_invalid")
            # Unmapped engineered residues are only allowed with explicit dif
            # annotations; handled below, never silently assigned UniProt IDs.
            dif_positions = {_int(_field(r, "seq_num"), 1) for r in cif.rows("struct_ref_seq_dif")
                             if r.get("pdbx_pdb_strand_id") == auth_chain}
            _require(set(seq) <= covered | dif_positions, "sequence_invalid")
            proteins[model, auth_chain] = {"model": model, "chain": auth_chain, "entity_id": entity,
                "uniprot_id": accession, "taxonomy_id": taxes[entity]}
        _require(actual_chains == declared_chains, "sequence_invalid")
    _require(all(label in asym and asym[label] in polymers for label, _ in schemes), "sequence_invalid")
    _require(any((row["uniprot_id"], row["taxonomy_id"]) == target for row in proteins.values()), "target_mismatch")
    for ident, row in residues.items():
        entity, label, number = annotations[ident]
        _require(entity in entities and asym.get(label) == entity, "sequence_invalid")
        kind = _field(entities[entity], "type")
        if kind == "polymer":
            _require(entity in polymers and mappings.get((label, number)) == ident, "sequence_invalid")
            row["role"] = "protein"
        elif kind == "water":
            _require(ident[2] in {"HOH", "WAT", "DOD"} and components[ident[2]]["type"] == "non-polymer", "component_invalid")
            row["role"] = "water"
        else:
            _require(row["record"] == "HETATM" and kind in {"non-polymer", "branched"}, "sequence_invalid")
            _require(number in {".", "?"}, "sequence_invalid")
    for feature in cif.rows("pdbx_entity_instance_feature"):
        if _field(feature, "feature_type") != "SUBJECT OF INVESTIGATION":
            continue
        matches = [ident for ident in residues if ident[2] == _field(feature, "comp_id")
                   and (feature.get("auth_asym_id", "?") in {"?", "."} or ident[1] == feature["auth_asym_id"])
                   and (feature.get("auth_seq_num", "?") in {"?", "."} or ident[3] == _int(feature["auth_seq_num"], -999, 9999))
                   and (feature.get("asym_id", "?") in {"?", "."} or annotations[ident][1] == feature["asym_id"])
                   and (feature.get("seq_num", "?") in {"?", "."} or annotations[ident][2] == feature["seq_num"])]
        _require(len(matches) == 1, "sequence_invalid")
        ident = matches[0]
        _require(feature.get("auth_comp_id", ident[2]) in {"?", ".", ident[2]}, "sequence_invalid")
        _require(_field(entities[annotations[ident][0]], "type") == "non-polymer"
                 and components[ident[2]]["type"] == "non-polymer", "context_unsupported")
        residues[ident]["role"] = "deposited_ligand"
    return list(proteins.values()), mappings, seq_by_entity, entities, limitations, explanation


def _sequence_limitations(cif, residues, mappings, proteins, pdb_id, limitations, explanation):
    """Preserve deposited absence/variant facts without inventing UniProt length."""
    protein_index = {(row["model"], row["chain"]): row for row in proteins}
    mapped_identities = set(mappings.values())
    missing = set()
    rows = cif.rows("pdbx_unobs_or_zero_occ_residues")
    _unique(rows, "id")
    for row in rows:
        _require(_field(row, "polymer_flag") == "Y", "context_unsupported")
        ident = (_int(_field(row, "pdb_model_num"), 1, 9999), _blank(row["auth_asym_id"]),
                 _field(row, "auth_comp_id"), _int(_field(row, "auth_seq_id"), -999, 9999), _blank(row["pdb_ins_code"]))
        _require(ident in mapped_identities and ident not in missing, "sequence_invalid")
        missing.add(ident)
        if row.get("label_asym_id", "?") not in {"?", "."}:
            _require(mappings.get((row["label_asym_id"], _field(row, "label_seq_id"))) == ident
                     and _field(row, "label_comp_id") == ident[2], "sequence_invalid")
        flag = _field(row, "occupancy_flag")
        _require(flag in {"0", "1"}, "sequence_invalid")
        if ident in residues:
            _require(flag == "0" and all(a["occupancy_hundredths"] == 0 for a in residues[ident]["atoms"]), "sequence_invalid")
        else:
            _require(flag == "1", "sequence_invalid")
        limitations.append({"id": "missing-" + _field(row, "id"), "kind": "missing_residues",
            "residues": [p2.identity_dict(ident)], "source_record_id": "unobserved-" + _field(row, "id")})
    _require(mapped_identities - residues.keys() <= missing, "sequence_invalid")
    alignments = {r["align_id"]: r for r in cif.rows("struct_ref_seq")}
    rows = cif.rows("struct_ref_seq_dif")
    _unique(rows, "pdbx_ordinal")
    seen = set()
    for row in rows:
        alignment = alignments.get(_field(row, "align_id"))
        _require(alignment is not None, "sequence_invalid")
        chain = _field(row, "pdbx_pdb_strand_id")
        _require(chain in _field(alignment, "pdbx_strand_id").split(",")
                 and _field(row, "pdbx_pdb_id_code") == pdb_id, "sequence_invalid")
        candidates = [ident for (label, number), ident in mappings.items()
                      if ident[1] == chain and number == _field(row, "seq_num")]
        _require(len(candidates) == 1, "sequence_invalid")
        ident = candidates[0]
        _require(ident not in seen and _field(row, "mon_id") == ident[2]
                 and _int(_field(row, "pdbx_auth_seq_num"), -999, 9999) == ident[3]
                 and _blank(row["pdbx_pdb_ins_code"]) == ident[4], "sequence_invalid")
        seen.add(ident)
        protein = protein_index[ident[:2]]
        _require(_field(row, "pdbx_seq_db_accession_code") == protein["uniprot_id"]
                 and _field(row, "pdbx_seq_db_name") == "UNP", "sequence_invalid")
        _require(ident in residues, "context_unsupported")  # P2 variants require an observed residue.
        db_monomer = row["db_mon_id"]
        _require(db_monomer in {"?", "."} or re.fullmatch(r"[A-Z0-9]{1,3}", db_monomer), "sequence_invalid")
        position = _int(_field(row, "seq_num"), 1)
        start, end = _int(_field(alignment, "seq_align_beg"), 1), _int(_field(alignment, "seq_align_end"), 1)
        if start <= position <= end:
            db_position = _int(_field(row, "pdbx_seq_db_seq_num"), 1)
            _require(db_position == _int(_field(alignment, "db_align_beg"), 1) + position-start
                     and db_monomer not in {"?", "."}, "sequence_invalid")
        else:
            _require(row["pdbx_seq_db_seq_num"] in {"?", "."} and db_monomer in {"?", "."}, "sequence_invalid")
        description = _field(row, "details")
        _require(len(description) <= 128, "resource_limit")
        limitations.append({"id": "variant-" + _field(row, "pdbx_ordinal"), "kind": "sequence_variant",
            "residues": [p2.identity_dict(ident)], "source_record_id": "seq-dif-" + _field(row, "pdbx_ordinal")})
        explanation.append({"residue": p2.identity_dict(ident), "database_monomer": db_monomer, "details": description})


def _connections(cif, parsed, residues, annotations, atoms, components, budget):
    """Resolve each deposited edge in both directions, without distance guesses."""
    rows = cif.rows("struct_conn")
    budget.reserve(16384 * (len(rows) + len(parsed.connections)) + 1024 * len(atoms))
    _unique(rows, "id")
    endpoint_index, serial_index = {}, {}
    for (ident, name, alt), atom in atoms.items():
        entity, label, number = annotations[ident]
        key = label, ident[2], number, name, alt
        endpoint_index.setdefault(key, []).append((ident, name, alt))
        serial_index[atom["serial"]] = ident, name, alt
    edges, applicable, link_groups = {}, set(), {}
    types = {"covale": "covalent", "disulf": "disulfide", "metalc": "coordination",
             "hydrog": "unknown", "saltbr": "unknown"}
    orders = {"SING": 1, "DOUB": 2, "TRIP": 3}
    for row in rows:
        category = _field(row, "conn_type_id")
        _require(category in types, "connection_invalid")
        endpoints = []
        for partner in (1, 2):
            prefix = f"ptnr{partner}_"
            alt = _blank(row.get(f"pdbx_ptnr{partner}_label_alt_id", "."))
            key = (_field(row, prefix+"label_asym_id"), _field(row, prefix+"label_comp_id"),
                   row[prefix+"label_seq_id"], _field(row, prefix+"label_atom_id"), alt)
            candidates = endpoint_index.get(key, [])
            chain, number = _blank(row[prefix+"auth_asym_id"]), _int(_field(row, prefix+"auth_seq_id"), -999, 9999)
            insertion = _blank(row.get(f"pdbx_ptnr{partner}_pdb_ins_code", "?"))
            candidates = [key for key in candidates if key[0][1] == chain and key[0][3:] == (number, insertion)]
            _require(len(candidates) == 1, "connection_invalid")
            endpoint = candidates[0]
            _require(_field(row, prefix+"symmetry") == "1_555", "connection_invalid")
            for field, expected in (("auth_comp_id", endpoint[0][2]), ("auth_atom_id", endpoint[1])):
                if prefix+field in row:
                    _require(_field(row, prefix+field) == expected, "connection_invalid")
            endpoints.append(endpoint)
        a, b = endpoints
        _require(a != b, "connection_invalid")
        serials = tuple(sorted((atoms[a]["serial"], atoms[b]["serial"])))
        _require(serials not in edges, "connection_invalid")
        order_value = row.get("pdbx_value_order", "?").upper()
        # CIF's unspecified order does not grant any guessed multiplicity.
        order = orders.get(order_value) if order_value not in {"?", "."} else None
        _require(order_value in {"?", ".", "SING", "DOUB", "TRIP"}, "connection_invalid")
        if a[0] == b[0]:
            bond = components[a[0][2]]["bonds"].get(tuple(sorted((a[1], b[1]))))
            _require(bond is not None and category == "covale"
                     and order_value in {"?", ".", bond[0]}, "connection_invalid")
        if category == "disulf":
            _require(a[0][2] == b[0][2] == "CYS" and a[1] == b[1] == "SG", "connection_invalid")
        edge = {"a": a, "b": b, "kind": types[category], "order": order, "row": row}
        edges[serials] = edge
        if category not in {"hydrog", "saltbr"}:
            applicable.add(serials)
        if a[0] != b[0] and category not in {"hydrog", "saltbr"}:
            pair = tuple(sorted((a[0], b[0])))
            assignment = (a[2], b[2]) if a[0] == pair[0] else (b[2], a[2])
            link_groups.setdefault(pair, set()).add(assignment)
    connections, accounted, directed, annotated = [], set(), {}, set()
    for connection in parsed.connections:
        center = connection.atom_serials[0]
        counts = Counter(connection.atom_serials[1:])
        kinds = set()
        for other, multiplicity in counts.items():
            pair = tuple(sorted((center, other)))
            edge = edges.get(pair)
            a, b = serial_index[center], serial_index[other]
            if edge is None:
                _require(a[0] == b[0], "connection_invalid")
                bond = components[a[0][2]]["bonds"].get(tuple(sorted((a[1], b[1]))))
                _require(bond is not None, "connection_invalid")
                kind, order = "covalent", orders.get(bond[0])
            else:
                kind, order = edge["kind"], edge["order"]
                _require(pair in applicable, "connection_invalid")
                accounted.add(pair)
            if connection.record_type == "CONECT":
                _require((center, other) not in directed, "connection_invalid")
                directed[center, other] = multiplicity
                # wwPDB format33/sect10: each neighbor is listed once per
                # direction; reciprocal records are redundant topology, not
                # bond order. CCD's exact order remains independently checked.
                # Repetition is not independent evidence of a double bond.
                _require(multiplicity == 1, "connection_invalid")
            else:
                _require(multiplicity == 1, "connection_invalid")
                annotation = connection.record_type, pair
                _require(annotation not in annotated, "connection_invalid")
                annotated.add(annotation)
                _require(connection.record_type != "SSBOND" or kind == "disulfide", "connection_invalid")
            kinds.add(kind)
        _require(len(kinds) == 1, "connection_invalid")
        connections.append({"record_index": connection.record_index, "record_type": connection.record_type,
            "atom_serials": list(connection.atom_serials), "kind": next(iter(kinds)), "symmetry": list(connection.symmetry)})
    for (a, b), count in directed.items():
        _require((b, a) not in directed or directed[b, a] == count, "connection_invalid")
    _require(accounted == applicable, "connection_invalid")
    constraints = []
    for pair, assignments in link_groups.items():
        labels = [{a["altloc"] for a in residues[ident]["atoms"] if a["altloc"]} for ident in pair]
        if not any(labels):
            continue
        # Pairwise deposited alternatives encode joint choices directly. Never
        # enumerate a product or infer same-letter coupling from name equality.
        _require(len(assignments) <= p2.MAX_ASSIGNMENTS and len(constraints) < p2.MAX_GROUPS, "resource_limit")
        for index in (0, 1):
            expected = labels[index] or {""}
            _require({a[index] for a in assignments} == expected, "constraint_invalid")
        constraints.append({"id": "connection-choice-" + str(len(constraints)+1),
            "residues": [p2.identity_dict(ident) for ident in pair],
            "allowed_assignments": [list(choice) for choice in sorted(assignments)]})
    return connections, constraints, edges


def _reserve_required_atoms(residues, components, budget):
    """Count the instance x CCD expansion BEFORE creating per-residue copies.

    512 bytes per expanded heavy atom covers its dict/list slot, the temporary
    name index and normalization scratch; 1024/residue covers component copies
    and contextual explanation rows. P2 traversal, frozen facts and IPC remain
    separately charged later on this SAME ledger, never replacing this charge.
    Count once per CCD, then sum instance counts without building a product.
    """
    budget.reserve(1024 + 256 * len(components))
    counts = {key: sum(attrs[0] not in {"H", "D"} for attrs in component["atoms"].values())
              for key, component in components.items()}
    total = 0
    for ident in residues:
        total += counts[ident[2]]
        _require(total <= p1.MAX_ATOMS, "resource_limit")
    budget.reserve(total * 512 + len(residues) * 1024)


def _required_atoms(residues, annotations, components, sequences, explanations):
    for ident, row in residues.items():
        component = components[ident[2]]
        required = {name: attrs[0] for name, attrs in component["atoms"].items() if attrs[0] not in {"H", "D"}}
        if row["role"] == "protein":
            entity, _, number = annotations[ident]
            number = _int(number, 1)
            _require(component["type"] in {"l-peptide linking", "d-peptide linking", "peptide linking"}
                     and component["atoms"].get("OXT", (None,))[0] == "O"
                     and "OXT" in component["leaving"] and ("C", "OXT") in component["bonds"], "context_unsupported")
            if number < len(sequences[entity]):
                following = components[sequences[entity][number+1]]
                _require(component["type"] in {"l-peptide linking", "d-peptide linking", "peptide linking"}
                         and following["type"] in {"l-peptide linking", "d-peptide linking", "peptide linking"}, "context_unsupported")
                _require(component["atoms"].get("OXT", (None,))[0] == "O"
                         and "OXT" in component["leaving"] and ("C", "OXT") in component["bonds"]
                         and following["atoms"].get("N", (None,))[0] == "N", "context_unsupported")
                del required["OXT"]
                explanations.append({"residue": p2.identity_dict(ident), "removed_atom": "OXT", "basis": "complete-linear-polymer-sequence"})
        row["component"] = dict(component["source"])
        row["required_heavy_atoms"] = [{"name": name, "element": element} for name, element in required.items()]


def _glycan_context(cif, residues, annotations, components, entities, edges, explanations, budget):
    """Apply explicit deposited leaving-atom context, never absence heuristics."""
    branches = _unique(cif.rows("pdbx_entity_branch"), "entity_id")
    lists = _unique(cif.rows("pdbx_entity_branch_list"), "entity_id num")
    scheme_rows = cif.rows("pdbx_branch_scheme")
    _unique(scheme_rows, "asym_id num")
    links = cif.rows("pdbx_entity_branch_link")
    _unique(links, "link_id")
    budget.reserve(8192 * (len(scheme_rows) + len(links) + len(cif.rows("pdbx_modification_feature"))))
    branch_map, seen_scheme = {}, set()
    for (entity,), row in branches.items():
        _require(entity in entities and _field(entities[entity], "type") == "branched"
                 and _field(row, "type") == "oligosaccharide", "context_unsupported")
    _require({entity for entity, row in entities.items() if _field(row, "type") == "branched"}
             == {key[0] for key in branches}, "context_unsupported")
    for (entity, number), row in lists.items():
        _require((entity,) in branches and _field(row, "comp_id") in components
                 and row.get("hetero", "n").lower() == "n", "context_unsupported")
        _int(number, 1)
        _require("saccharide" in components[_field(row, "comp_id")]["type"], "context_unsupported")
    for row in scheme_rows:
        entity, label, number = _field(row, "entity_id"), _field(row, "asym_id"), _field(row, "num")
        definition = lists.get((entity, number))
        _require(definition is not None and _field(row, "mon_id") == _field(definition, "comp_id"), "sequence_invalid")
        _require(row.get("hetero", "n").lower() == "n", "context_unsupported")
        comp = _field(row, "mon_id")
        candidates = [ident for ident, annotation in annotations.items()
                      if annotation[:2] == (entity, label) and ident[2] == comp
                      and ident[1] == _blank(row["pdb_asym_id"])
                      and ident[3] == _int(_field(row, "pdb_seq_num"), -999, 9999)
                      and ident[4] == _blank(row.get("pdb_ins_code", "."))]
        _require(len(candidates) == 1 and _field(row, "auth_mon_id") == comp and _field(row, "pdb_mon_id") == comp, "sequence_invalid")
        ident = candidates[0]
        _require(ident not in seen_scheme, "sequence_invalid")
        seen_scheme.add(ident)
        branch_map[label, number] = ident
        residues[ident]["role"] = "glycan"
    _require({ident for ident in residues if _field(entities[annotations[ident][0]], "type") == "branched"} == seen_scheme, "sequence_invalid")
    for label in {key[0] for key in branch_map}:
        entity = annotations[next(ident for (chain, _), ident in branch_map.items() if chain == label)][0]
        _require({number for chain, number in branch_map if chain == label} == {number for e, number in lists if e == entity}, "sequence_invalid")
    edge_by_context = {}
    for pair, edge in edges.items():
        a, b = edge["a"], edge["b"]
        context = frozenset(((a[0], a[1]), (b[0], b[1])))
        edge_by_context.setdefault(context, []).append(edge)
    removed, supported_edges = set(), set()

    def remove_leaving(ident, link_atom, leaving, basis):
        component = components[ident[2]]
        _require(leaving in component["leaving"] and link_atom in component["atoms"]
                 and tuple(sorted((link_atom, leaving))) in component["bonds"], "context_unsupported")
        _require((ident, leaving) not in removed, "context_unsupported")
        row = residues[ident]
        # Any displaced atom still present (including H/D) contradicts the
        # deposited context; validation never silently deletes coordinates.
        _require(all(a["name"] != leaving for a in row["atoms"]), "context_unsupported")
        removed.add((ident, leaving))
        if component["atoms"][leaving][0] not in {"H", "D"}:
            budget.reserve(512 + 64 * len(row["required_heavy_atoms"]))
            row["required_heavy_atoms"] = [a for a in row["required_heavy_atoms"] if a["name"] != leaving]
        explanations.append({"residue": p2.identity_dict(ident), "removed_atom": leaving, "basis": basis})

    for row in links:
        entity = _field(row, "entity_id")
        _require((entity,) in branches, "sequence_invalid")
        labels = {label for (label, _), ident in branch_map.items() if annotations[ident][0] == entity}
        _require(bool(labels), "sequence_invalid")
        for label in labels:
            endpoints = []
            for side in (1, 2):
                ident = branch_map.get((label, _field(row, f"entity_branch_list_num_{side}")))
                _require(ident is not None and ident[2] == _field(row, f"comp_id_{side}"), "sequence_invalid")
                endpoints.append((ident, _field(row, f"atom_id_{side}")))
            matches = edge_by_context.get(frozenset(endpoints), [])
            _require(bool(matches) and all(edge["kind"] == "covalent" for edge in matches), "connection_invalid")
            declared_order = _field(row, "value_order").upper()
            _require(declared_order in {"SING", "DOUB", "TRIP"}, "connection_invalid")
            for edge in matches:
                _require(edge["row"].get("pdbx_value_order", "?").upper() in {"?", ".", declared_order}, "connection_invalid")
                # Both branch endpoints explicitly name a displaced atom,
                # including hydrogens. Unknown flags do not contradict those
                # names; explicit 'none' or 'one' does, and cannot waive CCD.
                _require(edge["row"].get("pdbx_leaving_atom_flag", "?") in {"?", ".", "both"}, "context_unsupported")
                supported_edges.add(edge["row"]["id"])
            for side, (ident, atom) in enumerate(endpoints, 1):
                remove_leaving(ident, atom, _field(row, f"leaving_atom_id_{side}"), "branch-link-" + _field(row, "link_id"))
    modifications = cif.rows("pdbx_modification_feature")
    _unique(modifications, "ordinal")
    for feature in modifications:
        feature_type, category = _field(feature, "type"), _field(feature, "category")
        glycosylation = feature_type in {"N-Glycosylation", "O-Glycosylation"} and category == "Carbohydrate"
        disulfide = category == "Disulfide bridge"
        _require(glycosylation or disulfide, "context_unsupported")
        candidates = []

        def matches(endpoint, prefix):
            ident, atom, alt = endpoint
            entity, label, number = annotations[ident]
            if (_field(feature, prefix+"label_comp_id") != ident[2]
                    or _field(feature, prefix+"label_asym_id") != label
                    or feature[prefix+"label_seq_id"] != number):
                return False
            given_alt = _blank(feature[prefix+"label_alt_id"])
            if given_alt != alt:
                return False
            if _field(feature, prefix+"auth_comp_id") != ident[2]:
                return False
            auth = (_blank(feature[prefix+"auth_asym_id"]), _int(_field(feature, prefix+"auth_seq_id"), -999, 9999))
            # The branch scheme's pdb_* fields point to atom_site.auth_*;
            # its auth_* fields describe original-author numbering instead.
            # Do not substitute that second naming system into PDB identity.
            if auth != (ident[1], ident[3]):
                return False
            if _blank(feature[prefix+"pdb_ins_code"]) != ident[4]:
                return False
            return _field(feature, prefix+"symmetry") == "1_555"

        for edge in edges.values():
            if edge["kind"] != ("covalent" if glycosylation else "disulfide"):
                continue
            for a, b in ((edge["a"], edge["b"]), (edge["b"], edge["a"])):
                if (a[1] == _field(feature, "comp_id_linking_atom")
                        and b[1] == _field(feature, "modified_residue_id_linking_atom")
                        and matches(a, "") and matches(b, "modified_residue_")):
                    candidates.append((edge, a, b))
        _require(len(candidates) == 1, "context_unsupported")
        edge, a, b = candidates[0]
        supported_edges.add(edge["row"]["id"])
        if disulfide:
            continue
        _require(edge["row"].get("pdbx_role") == feature_type
                 and edge["row"].get("pdbx_leaving_atom_flag") == "one", "context_unsupported")
        sugar, protein = a[0], b[0]
        _require("saccharide" in components[sugar[2]]["type"] and residues[protein]["role"] == "protein"
                 and residues[sugar]["role"] in {"unproven", "glycan"}, "context_unsupported")
        _require(components[protein[2]]["atoms"][b[1]][0] == ("N" if feature_type == "N-Glycosylation" else "O")
                 and components[sugar[2]]["atoms"][a[1]][0] == "C", "context_unsupported")
        component = components[sugar[2]]
        leaving = [name for name in component["leaving"] if component["atoms"][name][0] not in {"H", "D"}
                   and tuple(sorted((a[1], name))) in component["bonds"]]
        _require(len(leaving) == 1, "context_unsupported")
        residues[sugar]["role"] = "glycan"
        remove_leaving(sugar, a[1], leaving[0], "modification-" + _field(feature, "ordinal"))
    for edge in edges.values():
        a, b = edge["a"][0], edge["b"][0]
        if a != b and edge["kind"] == "covalent" and (residues[a]["role"] == "glycan" or residues[b]["role"] == "glycan"):
            _require(edge["row"]["id"] in supported_edges, "context_unsupported")
        if edge["row"].get("pdbx_leaving_atom_flag", "?") not in {"?", ".", "none"}:
            _require(edge["row"]["id"] in supported_edges, "context_unsupported")


def _compile(pdb_bytes, cif_bytes, component_files, pdb_id, target, *, budget=None):
    budget = p1.AllocationBudget(MAX_LOGICAL_BYTES) if budget is None else budget
    _require(type(budget) is p1.AllocationBudget, "input_invalid")
    captured, target = _capture(pdb_bytes, cif_bytes, component_files, pdb_id, target, budget)
    cif = _Cif(cif_bytes, budget)
    ids = _component_ids(cif, pdb_id)
    _require(set(ids) == {key for key, _ in captured}, "component_invalid")
    # P1's unchanged implementation uses a private local ledger. Its entire
    # preflight peak is reserved here BEFORE P1 allocates anything substantial.
    budget.reserve(p1._preflight(pdb_bytes).used)
    parsed = p1.parse_original_pdb(pdb_bytes)
    history = _revisions(cif, parsed, pdb_id)
    latest = history[-1]["date"]
    components, component_evidence = _chemistry(cif, captured, latest, budget)
    residues, annotations, atoms = _inventory(cif, parsed, components, budget)
    proteins, mappings, sequences, entities, limitations, sequence_evidence = _sequences(
        cif, residues, annotations, components, target, pdb_id, parsed.model, budget)
    _sequence_limitations(cif, residues, mappings, proteins, pdb_id, limitations, sequence_evidence)
    chemical_evidence = []
    _reserve_required_atoms(residues, components, budget)
    _required_atoms(residues, annotations, components, sequences, chemical_evidence)
    connections, constraints, edges = _connections(cif, parsed, residues, annotations, atoms, components, budget)
    _glycan_context(cif, residues, annotations, components, entities, edges, chemical_evidence, budget)
    evidence = {"schema_version": "rcsb-source-evidence-v1", "pdb_sha256": hashlib.sha256(pdb_bytes).hexdigest(),
        "cif_sha256": hashlib.sha256(cif_bytes).hexdigest(), "revision_history": history,
        "latest_revision_date": latest, "revision_version": f'{history[-1]["major"]}.{history[-1]["minor"]}',
        "components": sorted(component_evidence, key=lambda row: row["id"]),
        "sequence_context": sequence_evidence, "chemical_context": chemical_evidence}
    resources = p2.check_projection_resources(evidence, budget=budget)
    budget.reserve(24 * resources.json_bytes + 128 * resources.nodes)
    proof = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    source = {"schema_version": "receptor-source-v1", "sha256": evidence["pdb_sha256"], "pdb_id": pdb_id,
        "provenance": {"provider": "wwpdb", "version": latest, "record_id": pdb_id, "proof_id": proof},
        "target": {"uniprot_id": target[0], "taxonomy_id": target[1]}, "proteins": proteins,
        "residues": list(residues.values()), "connections": connections, "conformer_constraints": constraints, "limitations": limitations}
    # Reserve P2's complete additional peak, plus detached output/IPC, on this
    # same ledger before invoking its unchanged separately bounded validator.
    resources = p2.check_projection_resources(source, budget=budget)
    budget.reserve(p2._parsed_charge(parsed) + len(parsed.atoms) * 4096 + len(residues) * 8192
                   + len(proteins) * 2048 + len(connections) * 4096 + len(limitations) * 2048
                   + len(constraints) * 131072 + 65536 + resources.nodes * 128 + resources.json_bytes * 24)
    p2.validate_projection_source(parsed, source)
    return {"ok": True, "source": source, "evidence": evidence}


def compile_rcsb_source(pdb_bytes, cif_bytes, component_files, *, pdb_id, target):
    """Compile complete compared facts; never authenticate, select, or authorize."""
    try:
        return _compile(pdb_bytes, cif_bytes, component_files, pdb_id, target)
    except _Invalid as exc:
        return _error(exc.args[0])
    except p1.ProjectionInvalid as exc:
        return _error("resource_limit" if exc.args == ("resource_limit",) else "source_invalid")
    except (ValueError, RuntimeError, KeyError, IndexError, UnicodeError, MemoryError, ImportError):
        return _error("cif_invalid")
