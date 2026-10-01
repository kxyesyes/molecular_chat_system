"""Reproducibility checks for docking validation."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Iterable


def _load_molecule(value: Any):
    """Load one molecule from an RDKit molecule or a supported structure file."""
    from rdkit import Chem

    if hasattr(value, "GetNumAtoms"):
        return value

    path = Path(value)
    suffix = path.suffix.lower()
    if suffix in {".sdf", ".sd"}:
        supplier = Chem.SDMolSupplier(str(path), removeHs=False)
        molecules = [mol for mol in supplier if mol is not None]
        if len(molecules) != 1:
            raise ValueError("structure file must contain exactly one valid molecule")
        return molecules[0]
    if suffix == ".mol":
        return Chem.MolFromMolFile(str(path), removeHs=False)
    if suffix == ".mol2":
        return Chem.MolFromMol2File(str(path), removeHs=False)
    if suffix == ".pdb":
        return Chem.MolFromPDBFile(str(path), removeHs=False)
    raise ValueError(f"unsupported structure format: {suffix or 'unknown'}")


def symmetry_aware_heavy_atom_rmsd(reference: Any, candidate: Any) -> float:
    """Return symmetry-aware RMSD for two 3-D ligand structures.

    RDKit's ``GetBestRMS`` searches equivalent atom mappings. Hydrogens are
    removed before comparison, and unusable structures fail closed.
    """
    from rdkit import Chem
    from rdkit.Chem import rdMolAlign

    reference_mol = _load_molecule(reference)
    candidate_mol = _load_molecule(candidate)
    if reference_mol is None or candidate_mol is None:
        raise ValueError("invalid structure for RMSD validation")
    if reference_mol.GetNumConformers() == 0 or candidate_mol.GetNumConformers() == 0:
        raise ValueError("3D conformer is required for RMSD validation")

    reference_heavy = Chem.RemoveHs(Chem.Mol(reference_mol))
    candidate_heavy = Chem.RemoveHs(Chem.Mol(candidate_mol))
    if reference_heavy.GetNumAtoms() != candidate_heavy.GetNumAtoms():
        raise ValueError("heavy-atom counts do not match for RMSD validation")
    if reference_heavy.GetNumConformers() == 0 or candidate_heavy.GetNumConformers() == 0:
        raise ValueError("3D conformer is required after hydrogen removal")

    try:
        rmsd = float(rdMolAlign.GetBestRMS(candidate_heavy, reference_heavy))
    except Exception as error:
        raise ValueError("structures are not compatible for symmetry-aware RMSD") from error
    if not math.isfinite(rmsd) or rmsd < 0:
        raise ValueError("RMSD validation produced a non-finite value")
    return rmsd


def assess_seed_stability(
    runs: Iterable[dict[str, Any]],
    *,
    minimum_runs: int = 2,
) -> dict[str, Any]:
    """Validate repeated docking runs and report score variation.

    A run is accepted only when it is explicitly successful, has a finite
    numeric score, and points to an existing pose artifact. The score range
    is reported for a caller-specific stability policy; it is not called an
    experimental affinity or a universal pass threshold.
    """
    records = list(runs)
    failures: list[dict[str, Any]] = []
    energies: list[float] = []
    artifact_checks: list[bool] = []

    for index, record in enumerate(records, 1):
        if not isinstance(record, dict) or record.get("success") is not True:
            failures.append({"index": index, "reason": "run_not_successful"})
            artifact_checks.append(False)
            continue

        energy = record.get("binding_energy")
        if type(energy) not in (int, float) or not math.isfinite(float(energy)):
            failures.append({"index": index, "reason": "binding_energy_not_finite"})
            artifact_checks.append(False)
            continue

        pose_file = record.get("pose_file")
        artifact_ok = isinstance(pose_file, (str, Path)) and Path(pose_file).is_file()
        artifact_checks.append(artifact_ok)
        if not artifact_ok:
            failures.append({"index": index, "reason": "pose_artifact_missing"})
            continue
        energies.append(float(energy))

    result: dict[str, Any] = {
        "status": "passed" if not failures and len(records) >= minimum_runs else "failed",
        "run_count": len(records),
        "successful_runs": len(energies),
        "artifact_checks": artifact_checks,
        "failures": failures,
        "energies_kcal_per_mol": energies,
        "energy_range_kcal_per_mol": None,
    }
    if len(records) < minimum_runs:
        result["status"] = "insufficient_data"
        result["failures"] = [{"reason": "minimum_runs_not_reached", "minimum_runs": minimum_runs}]
    if energies:
        result["energy_range_kcal_per_mol"] = max(energies) - min(energies)
    return result
