"""Pure, fail-closed PDB evidence and receptor-byte producers (no I/O).

``source`` is a SERVER-OWNED descriptor, never an HTTP credential. Its closed
native-dict schema is::

    {schema_version: "docking-source-v1", sha256: <original bytes SHA256>,
     pdb_id: <uppercase 4-character entry>,
     provenance: {provider: "wwpdb", version: <token>, record_id: <pdb_id>,
                  proof_id: <server evidence token>},
     target: {uniprot_id: <accession>, taxonomy_id: <positive int>},
     proteins: [{model: <int>, chain: <one character or "">, entity_id: <token>,
                 uniprot_id: <accession>, taxonomy_id: <positive int>}],
     residues: [{identity: {model, chain, resname, resseq, icode},
                 record: "ATOM" | "HETATM", entity_id: <token>, role: <role>,
                 atoms: [{serial: <int>, name: <PDB atom name>, element: <symbol>}]}]}

All dictionaries are closed, types are exact (no bool/int coercion). Residues
and atoms must enumerate the COMPLETE deposited coordinate inventory, including
hydrogens and water. The upstream proof must independently establish completeness
and deposited roles, not merely copy whatever atoms happened to be downloaded.
Roles: protein, deposited_ligand, glycan, metal, solvent, buffer, water, cofactor,
other, unproven. Only deposited_ligand on a chain matching target facts is eligible.
The functions verify schema, hash, PDB ID, inventory and coordinates; a forged but
self-consistent descriptor is NOT authenticated here. The coordinator must bind
verified source/target/proof/ownership BEFORE calling these functions.

Supported PDB subset: ASCII, <=16 MiB, <=99999 atoms, one implicit model 1 or one
explicit MODEL, HEADER entry and final END required. All altlocs and occupancies
other than 1.00 reject. Unknown records (including unsupported ANISOU/SIGATM)
reject, never disappear silently. Known non-coordinate metadata is classified
but not interpreted as identity proof. No missing-residue repair or atom guessing.
"""

from __future__ import annotations

import copy
import hashlib
import math
import re


_ROLES = frozenset("protein deposited_ligand glycan metal solvent buffer water cofactor other unproven".split())
_ELEMENTS = frozenset(("H D He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn "
    "Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu "
    "Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am "
    "Cm Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og").split())
_METADATA = frozenset(("TITLE COMPND SOURCE KEYWDS EXPDTA AUTHOR REVDAT JRNL REMARK DBREF DBREF1 DBREF2 "
    "SEQADV SEQRES MODRES HET HETNAM HETSYN FORMUL HELIX SHEET SSBOND LINK CISPEP CRYST1 "
    "ORIGX1 ORIGX2 ORIGX3 SCALE1 SCALE2 SCALE3 MTRIX1 MTRIX2 MTRIX3 MASTER NUMMDL SPLIT OBSLTE SPRSDE").split())
_IDENTITY_KEYS = {"model", "chain", "resname", "resseq", "icode"}
_RULE = "heavy-aabb-margin4-min10-v1"


class _Invalid(ValueError):
    pass


def _require(condition):
    if not condition:
        raise _Invalid()


def _closed(value, keys):
    _require(type(value) is dict and value.keys() == set(keys) and all(type(k) is str for k in value))


def _text(value, pattern):
    _require(type(value) is str and re.fullmatch(pattern, value) is not None)


def _integer(value, low, high):
    _require(type(value) is int and low <= value <= high)


def _token(value):
    _text(value, r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def _identity(value):
    _closed(value, _IDENTITY_KEYS)
    _integer(value["model"], 1, 9999)
    _integer(value["resseq"], -999, 9999)
    _text(value["chain"], r"[A-Za-z0-9]?")
    _text(value["icode"], r"[A-Za-z0-9]?")
    _text(value["resname"], r"[A-Z0-9]{1,3}")
    return tuple(value[k] for k in ("model", "chain", "resname", "resseq", "icode"))


def _atom_id(value):
    _closed(value, {"serial", "name", "element"})
    _integer(value["serial"], 1, 99999)
    _text(value["name"], r"[A-Za-z0-9'_*+-]{1,4}")
    _require(type(value["element"]) is str and value["element"] in _ELEMENTS)
    return value["serial"], value["name"], value["element"]


def _failure():
    return {"ok": False, "code": "POCKET_EVIDENCE_INVALID",
            "message": "Pocket evidence is unavailable or invalid."}


def _number(field, precision):
    _require(re.fullmatch(r" *-?\d+\.\d{" + str(precision) + r"}", field) is not None)
    value = float(field)
    _require(math.isfinite(value))
    return value


def _pdb_int(field):
    _require(re.fullmatch(r" *-?\d+", field) is not None)
    return int(field)


def _parse(pdb_bytes):
    _require(type(pdb_bytes) is bytes and 0 < len(pdb_bytes) <= 16 * 1024 * 1024)
    text = pdb_bytes.decode("ascii")
    _require(all(c in "\r\n" or 32 <= ord(c) <= 126 for c in text))
    groups, serials, connections, positions = {}, set(), [], {}
    last_atom_key = None
    model, explicit, in_model, ended_model, ended = 1, False, False, False, False
    header = None
    for raw in pdb_bytes.splitlines(keepends=True):
        line = raw.rstrip(b"\r\n").decode("ascii")
        _require(0 < len(line) <= 80 and not ended)
        record = line[:6].strip()
        if record == "HEADER":
            _require(header is None and not groups and len(line) >= 66)
            header = line
        elif record == "MODEL":
            _require(not explicit and not groups and len(line) >= 14)
            _require(line[:10] == "MODEL     " and not line[14:].strip())
            model = _pdb_int(line[10:14])
            _integer(model, 1, 9999)
            explicit, in_model = True, True
        elif record == "NUMMDL":
            _require(not groups and len(line) >= 14 and not line[14:].strip())
            _require(_pdb_int(line[10:14]) == 1)
        elif record == "ENDMDL":
            _require(explicit and in_model and bool(groups) and not line[6:].strip())
            in_model, ended_model = False, True
        elif record == "END":
            _require(bool(groups) and not in_model and not line[6:].strip())
            ended = True
        elif record in {"ATOM", "HETATM"}:
            _require(line[:6] in {"ATOM  ", "HETATM"} and len(line) >= 78)
            _require(header is not None and not ended_model and (not explicit or in_model))
            _require(line[11] == " " and line[16] == " " and line[20] == " " and line[27:30] == "   ")
            identity = dict(model=model, chain=line[21].strip(), resname=line[17:20].strip(),
                            resseq=_pdb_int(line[22:26]), icode=line[26].strip())
            key = _identity(identity)
            position = key[0], key[1], key[3], key[4]
            _require(position not in positions or positions[position] == key[2])
            positions[position] = key[2]
            atom = dict(serial=_pdb_int(line[6:11]), name=line[12:16].strip(), element=line[76:78].strip().capitalize())
            _atom_id(atom)
            _require(atom["serial"] not in serials and len(serials) < 99999)
            serials.add(atom["serial"])
            xyz = [_number(line[start:start+8], 3) for start in (30, 38, 46)]
            _require(_number(line[54:60], 2) == 1.0 and _number(line[60:66], 2) >= 0)
            _require(not line[78:].strip() or re.fullmatch(r"[1-9][+-]", line[78:]) is not None)
            group = groups.setdefault(key, dict(identity=identity, record=record, atoms=[], coordinates=[], lines=[], terminated=False))
            _require(group["record"] == record and atom["name"] not in {a["name"] for a in group["atoms"]})
            _require(not group["terminated"])
            group["atoms"].append(atom)
            group["coordinates"].append(xyz)
            group["lines"].append(raw)
            last_atom_key = key
        elif record == "TER":
            _require(bool(groups) and not ended_model and (not explicit or in_model))
            _require(len(line) >= 27)
            _integer(_pdb_int(line[6:11]), 1, 99999)
            _require(last_atom_key is not None and groups[last_atom_key]["record"] == "ATOM")
            _require(not groups[last_atom_key]["terminated"])
            _require((line[21].strip(), line[17:20].strip(), _pdb_int(line[22:26]), line[26].strip()) == last_atom_key[1:])
            groups[last_atom_key]["terminated"] = True
        elif record == "CONECT":
            _require(bool(groups) and len(line) >= 16 and not line[31:].strip())
            fields = [line[i:i+5] for i in range(6, min(len(line), 31), 5)]
            numbers = [_pdb_int(field) for field in fields if field.strip()]
            _require(len(numbers) >= 2)
            connections.extend(numbers)
        elif record in _METADATA:
            # This is explicitly classified metadata, never coordinate evidence.
            _require(len(line) >= 6)
        else:
            raise _Invalid()
    _require(ended and header is not None and bool(groups) and all(n in serials for n in connections))
    return header, groups


def _validated(pdb_bytes, source):
    _closed(source, {"schema_version", "sha256", "pdb_id", "provenance", "target", "proteins", "residues"})
    _text(source["schema_version"], r"docking-source-v1")
    _text(source["sha256"], r"[0-9a-f]{64}")
    _text(source["pdb_id"], r"[0-9][A-Z0-9]{3}")
    _require(type(pdb_bytes) is bytes and hashlib.sha256(pdb_bytes).hexdigest() == source["sha256"])
    provenance = source["provenance"]
    _closed(provenance, {"provider", "version", "record_id", "proof_id"})
    _text(provenance["provider"], r"wwpdb")
    _text(provenance["record_id"], r"[0-9][A-Z0-9]{3}")
    _require(provenance["record_id"] == source["pdb_id"])
    _token(provenance["version"])
    _token(provenance["proof_id"])
    _closed(source["target"], {"uniprot_id", "taxonomy_id"})
    _text(source["target"]["uniprot_id"], r"[A-Z0-9]{6,10}")
    _integer(source["target"]["taxonomy_id"], 1, 2**31-1)
    _require(type(source["proteins"]) is list and 0 < len(source["proteins"]) <= 62)
    proteins = {}
    for protein in source["proteins"]:
        _closed(protein, {"model", "chain", "entity_id", "uniprot_id", "taxonomy_id"})
        _integer(protein["model"], 1, 9999)
        _text(protein["chain"], r"[A-Za-z0-9]?")
        _token(protein["entity_id"])
        _text(protein["uniprot_id"], r"[A-Z0-9]{6,10}")
        _integer(protein["taxonomy_id"], 1, 2**31-1)
        key = protein["model"], protein["chain"]
        _require(key not in proteins)
        proteins[key] = protein
    header, groups = _parse(pdb_bytes)
    _require(header[62:66] == source["pdb_id"])
    _require(type(source["residues"]) is list and 0 < len(source["residues"]) <= 99999)
    inventory, seen_proteins = {}, set()
    for entry in source["residues"]:
        _closed(entry, {"identity", "record", "entity_id", "role", "atoms"})
        key = _identity(entry["identity"])
        _require(key not in inventory and key in groups)
        _token(entry["entity_id"])
        _require(type(entry["role"]) is str and entry["role"] in _ROLES)
        _require(type(entry["atoms"]) is list and 0 < len(entry["atoms"]) <= 99999)
        expected = [_atom_id(a) for a in entry["atoms"]]
        actual = [_atom_id(a) for a in groups[key]["atoms"]]
        _require(len(set(expected)) == len(expected) and set(expected) == set(actual))
        _text(entry["record"], r"ATOM|HETATM")
        _require(entry["record"] == groups[key]["record"])
        _require((entry["record"] == "ATOM") == (entry["role"] == "protein"))
        if entry["record"] == "ATOM":
            protein = proteins.get((key[0], key[1]))
            _require(protein is not None and entry["entity_id"] == protein["entity_id"])
            seen_proteins.add((key[0], key[1]))
        if key[2] in {"HOH", "WAT", "DOD"}:
            _require(entry["role"] == "water")
        inventory[key] = entry
    _require(inventory.keys() == groups.keys() and seen_proteins == proteins.keys())
    return header, groups, inventory, proteins


def _eligible(key, inventory, proteins, source):
    protein = proteins.get((key[0], key[1]))
    return (inventory[key]["role"] == "deposited_ligand" and protein is not None
            and any(atom["element"] not in {"H", "D"} for atom in inventory[key]["atoms"])
            and all(protein[k] == source["target"][k] for k in ("uniprot_id", "taxonomy_id")))


def _base(source):
    return dict(ok=True, source_sha256=source["sha256"], pdb_id=source["pdb_id"],
                provenance=copy.deepcopy(source["provenance"]), target=copy.deepcopy(source["target"]),
                source_authentication="caller_responsibility")


def inspect_pocket_candidates(pdb_bytes: bytes, *, source: dict) -> dict:
    """List all non-water HETATM identities; no automatic selection or authority."""
    try:
        _, groups, inventory, proteins = _validated(pdb_bytes, source)
        candidates = [dict(residue=copy.deepcopy(group["identity"]), role=inventory[key]["role"],
                           eligible=_eligible(key, inventory, proteins, source), atom_count=len(group["atoms"]))
                      for key, group in groups.items() if group["record"] == "HETATM" and inventory[key]["role"] != "water"]
        _require(any(item["eligible"] for item in candidates))
        return {**_base(source), "candidates": candidates}
    except (ValueError, TypeError, KeyError, OverflowError):
        return _failure()


def _proposal(groups, inventory, proteins, source, residue):
    key = _identity(residue)
    _require(key in inventory and _eligible(key, inventory, proteins, source))
    atoms = [(atom, xyz) for atom, xyz in zip(groups[key]["atoms"], groups[key]["coordinates"])
             if atom["element"] not in {"H", "D"}]
    _require(bool(atoms))
    minimum = [min(xyz[axis] for _, xyz in atoms) for axis in range(3)]
    maximum = [max(xyz[axis] for _, xyz in atoms) for axis in range(3)]
    size = [max(10.0, hi-lo+8.0) for lo, hi in zip(minimum, maximum)]
    _require(all(s <= 100.0 for s in size))
    return {**_base(source), "center": [(lo+hi)/2.0 for lo, hi in zip(minimum, maximum)],
            "size": size, "margin": 4.0, "units": "angstrom", "rule_version": _RULE,
            "residue": copy.deepcopy(residue), "atom_ids": [copy.deepcopy(a) for a, _ in atoms],
            "atom_count": len(atoms)}


def propose_pocket(pdb_bytes: bytes, *, source: dict, residue: dict) -> dict:
    """Validate exactly one explicit deposited residue and compute heavy AABB+4Å."""
    try:
        _, groups, inventory, proteins = _validated(pdb_bytes, source)
        return _proposal(groups, inventory, proteins, source, residue)
    except (ValueError, TypeError, KeyError, OverflowError):
        return _failure()


def prepare_receptor_bytes(pdb_bytes: bytes, *, source: dict, protein_selection: dict,
                           reference_residue: dict, retained_hetero_residues: list) -> dict:
    """Derive NEW coordinate-only PDB bytes, without repair or cofactor guessing.

    protein_selection is exactly {model: int, chains: [{chain, entity_id}]}.
    Every selected protein must match the source target; reference ligand's chain
    must be selected. Retention is an explicit unique list of hetero identities;
    omitted hetero/water and unselected protein residues are removed and recorded.
    Hashes in retained/removed cover concatenated ORIGINAL atom lines including
    their original line endings. Output uses LF, original coordinate fields and
    TER separators, no stale ligand metadata or dangling connectivity records.
    """
    try:
        header, groups, inventory, proteins = _validated(pdb_bytes, source)
        proposal = _proposal(groups, inventory, proteins, source, reference_residue)
        _closed(protein_selection, {"model", "chains"})
        _integer(protein_selection["model"], 1, 9999)
        _require(type(protein_selection["chains"]) is list and 0 < len(protein_selection["chains"]) <= 62)
        selected = set()
        for chain in protein_selection["chains"]:
            _closed(chain, {"chain", "entity_id"})
            _text(chain["chain"], r"[A-Za-z0-9]?")
            _token(chain["entity_id"])
            key = protein_selection["model"], chain["chain"]
            protein = proteins.get(key)
            _require(key not in selected and protein is not None and protein["entity_id"] == chain["entity_id"])
            _require(all(protein[k] == source["target"][k] for k in ("uniprot_id", "taxonomy_id")))
            selected.add(key)
        reference_key = _identity(reference_residue)
        _require(reference_key[:2] in selected)
        _require(type(retained_hetero_residues) is list and len(retained_hetero_residues) <= len(groups))
        retained_keys = set()
        for item in retained_hetero_residues:
            key = _identity(item)
            _require(key not in retained_keys and key != reference_key and key in groups)
            _require(groups[key]["record"] == "HETATM" and key[0] == protein_selection["model"])
            retained_keys.add(key)
        lines = [header.encode("ascii") + b"\n", f"MODEL     {protein_selection['model']:4d}\n".encode("ascii")]
        retained, removed = [], []
        last_protein = None
        ter_serial = max(atom["serial"] for group in groups.values() for atom in group["atoms"])

        def terminate_group(group):
            nonlocal ter_serial
            ter_serial += 1
            _integer(ter_serial, 1, 99999)
            identity = group["identity"]
            return (f"TER   {ter_serial:5d}      {identity['resname']:>3} {identity['chain']:1}"
                    f"{identity['resseq']:4d}{identity['icode']:1}\n").encode("ascii")

        for key, group in groups.items():
            keep = (group["record"] == "ATOM" and key[:2] in selected) or key in retained_keys
            lineage = dict(residue=copy.deepcopy(group["identity"]), record=group["record"],
                           role=inventory[key]["role"], entity_id=inventory[key]["entity_id"],
                           atom_ids=copy.deepcopy(group["atoms"]), atom_count=len(group["atoms"]),
                           sha256=hashlib.sha256(b"".join(group["lines"])).hexdigest())
            (retained if keep else removed).append(lineage)
            if keep:
                if last_protein is not None and (key[:2] != _identity(last_protein["identity"])[:2] or group["record"] != "ATOM"):
                    lines.append(terminate_group(last_protein))
                    last_protein = None
                lines.extend(raw.rstrip(b"\r\n") + b"\n" for raw in group["lines"])
                if group["record"] == "ATOM":
                    last_protein = group
                    if group["terminated"]:
                        lines.append(terminate_group(group))
                        last_protein = None
        if last_protein is not None:
            lines.append(terminate_group(last_protein))
        lines.extend([b"ENDMDL\n", b"END\n"])
        output = b"".join(lines)
        return {**_base(source), "pdb_bytes": output, "sha256": hashlib.sha256(output).hexdigest(),
                "rule_version": "explicit-receptor-selection-v1", "protein_selection": copy.deepcopy(protein_selection),
                "reference_residue": proposal["residue"], "retained": retained, "removed": removed,
                "metadata_policy": "coordinate_only_no_connectivity", "repair_performed": False}
    except (ValueError, TypeError, KeyError, OverflowError):
        return _failure()
