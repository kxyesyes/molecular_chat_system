"""P3 source inspection only: no projection, repair, I/O or execution readiness."""

from . import receptor_projection_pdb as pdb
from . import receptor_projection_source as contracts


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
