"""Reproducibility checks for docking validation."""

from __future__ import annotations

import math
import hashlib
import json
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
        molecules = list(supplier)
        if len(molecules) != 1 or molecules[0] is None:
            raise ValueError("structure file must contain exactly one valid molecule")
        return molecules[0]
    if suffix == ".mol":
        molecule = Chem.MolFromMolFile(str(path), removeHs=False)
        if molecule is None:
            raise ValueError("invalid MOL structure")
        return molecule
    if suffix == ".mol2":
        molecule = Chem.MolFromMol2File(str(path), removeHs=False)
        if molecule is None:
            raise ValueError("invalid MOL2 structure")
        return molecule
    if suffix == ".pdb":
        molecule = Chem.MolFromPDBFile(str(path), removeHs=False)
        if molecule is None:
            raise ValueError("invalid PDB structure")
        return molecule
    raise ValueError(f"unsupported structure format: {suffix or 'unknown'}")


def symmetry_aware_heavy_atom_rmsd(reference: Any, candidate: Any) -> float:
    """Return symmetry-aware RMSD for two 3-D ligand structures.

    RDKit's ``CalcRMS`` searches equivalent atom mappings without aligning the
    probe to the reference. This matters for docking: both structures must
    already be in the receptor frame, so a translated pose must not be made
    to look correct by a best-fit rotation/translation.
    """
    from rdkit import Chem
    from rdkit.Chem import rdMolAlign

    reference_mol = _load_molecule(reference)
    candidate_mol = _load_molecule(candidate)
    if reference_mol is None or candidate_mol is None:
        raise ValueError("invalid structure for RMSD validation")

    def validate_conformer(molecule, label: str) -> None:
        if molecule.GetNumConformers() != 1:
            raise ValueError(f"{label} must contain exactly one 3D conformer")
        conformer = molecule.GetConformer()
        if not conformer.Is3D():
            raise ValueError(f"{label} must contain a 3D conformer")
        for atom_index in range(molecule.GetNumAtoms()):
            point = conformer.GetAtomPosition(atom_index)
            if not all(math.isfinite(float(value)) for value in (point.x, point.y, point.z)):
                raise ValueError(f"{label} contains non-finite coordinates")

    validate_conformer(reference_mol, "reference")
    validate_conformer(candidate_mol, "candidate")

    reference_heavy = Chem.RemoveHs(Chem.Mol(reference_mol))
    candidate_heavy = Chem.RemoveHs(Chem.Mol(candidate_mol))
    if reference_heavy.GetNumAtoms() != candidate_heavy.GetNumAtoms():
        raise ValueError("heavy-atom counts do not match for RMSD validation")
    if reference_heavy.GetNumConformers() == 0 or candidate_heavy.GetNumConformers() == 0:
        raise ValueError("3D conformer is required after hydrogen removal")
    if reference_heavy.GetNumAtoms() == 0:
        raise ValueError("structures must contain at least one heavy atom")

    reference_smiles = Chem.MolToSmiles(reference_heavy, isomericSmiles=True)
    candidate_smiles = Chem.MolToSmiles(candidate_heavy, isomericSmiles=True)
    if reference_smiles != candidate_smiles:
        raise ValueError("structures have different chemical identity")

    # Explicitly bound the symmetry search. Unbounded mappings can become a
    # denial-of-service vector for highly symmetric ligands.
    matches = reference_heavy.GetSubstructMatches(
        candidate_heavy,
        uniquify=False,
        useChirality=True,
        maxMatches=10001,
    )
    if not matches:
        raise ValueError("structures are not compatible for symmetry-aware RMSD")
    if len(matches) > 10000:
        raise ValueError("too many symmetry mappings for RMSD validation")
    atom_maps = [
        [(candidate_atom, reference_atom) for candidate_atom, reference_atom in enumerate(match)]
        for match in matches
    ]

    try:
        rmsd = float(rdMolAlign.CalcRMS(candidate_heavy, reference_heavy, map=atom_maps))
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
    if type(minimum_runs) is not int or minimum_runs < 2:
        raise ValueError("minimum_runs must be an integer greater than or equal to 2")

    # Import lazily to avoid coupling the small validation module to the
    # service's adapter initialization at import time.
    from .molecular_docking_service import MolecularDockingService

    records = list(runs)
    failures: list[dict[str, Any]] = []
    energies: list[float] = []
    artifact_checks: list[bool] = []
    seen_seeds: set[int] = set()
    seen_artifacts: set[str] = set()

    for index, record in enumerate(records, 1):
        if not isinstance(record, dict) or record.get("success") is not True:
            failures.append({"index": index, "reason": "run_not_successful"})
            artifact_checks.append(False)
            continue

        seed = record.get("seed")
        if type(seed) is not int or seed <= 0 or seed > 2**31 - 1:
            failures.append({"index": index, "reason": "seed_invalid"})
            artifact_checks.append(False)
            continue
        if seed in seen_seeds:
            failures.append({"index": index, "reason": "seed_not_distinct"})
            artifact_checks.append(False)
            continue
        seen_seeds.add(seed)

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
        artifact_path = str(Path(pose_file).resolve())
        if artifact_path in seen_artifacts:
            failures.append({"index": index, "reason": "pose_artifact_reused"})
            continue
        seen_artifacts.add(artifact_path)

        manifest_path = record.get("manifest_path")
        if not isinstance(manifest_path, (str, Path)) or not Path(manifest_path).is_file():
            failures.append({"index": index, "reason": "execution_manifest_missing"})
            continue
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
            execution = manifest.get("execution", {})
            search = manifest.get("search", {})
            if not isinstance(execution, dict) or not isinstance(search, dict):
                failures.append({"index": index, "reason": "execution_manifest_invalid"})
                continue
            if (
                execution.get("status") != "completed"
                or execution.get("returncode") != 0
            ):
                failures.append({"index": index, "reason": "execution_not_completed"})
                continue
            if search.get("random_seed") != seed:
                failures.append({"index": index, "reason": "seed_manifest_mismatch"})
                continue
            digest = hashlib.sha256(Path(artifact_path).read_bytes()).hexdigest()
            if execution.get("output_sha256") != digest:
                failures.append({"index": index, "reason": "pose_artifact_hash_mismatch"})
                continue
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
            failures.append({"index": index, "reason": "execution_manifest_invalid"})
            continue

        diagnostics: list[str] = []
        parsed_poses = MolecularDockingService.parse_vina_results(
            artifact_path,
            diagnostics=diagnostics,
        )
        if not parsed_poses:
            failure = {"index": index, "reason": "pose_artifact_invalid"}
            if diagnostics:
                failure["detail"] = diagnostics[0]
            failures.append(failure)
            continue
        best_energy = float(parsed_poses[0].binding_energy)
        if not math.isclose(float(energy), best_energy, rel_tol=0.0, abs_tol=1e-6):
            failures.append({"index": index, "reason": "binding_energy_mismatch"})
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
        "validation_scope": "pose_artifact_and_record_consistency",
        "scientific_execution_verified": False,
    }
    if len(records) < minimum_runs:
        result["failures"].append(
            {"reason": "minimum_runs_not_reached", "minimum_runs": minimum_runs}
        )
    if energies:
        result["energy_range_kcal_per_mol"] = max(energies) - min(energies)
    return result
