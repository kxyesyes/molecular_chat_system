"""Fail-closed docking interaction analysis adapter.

The viewer does not have enough chemical topology to make reliable interaction
claims.  This module is the boundary for a mature backend analyzer (currently
ProLIF when installed).  It deliberately returns an explicit unavailable
result when required inputs or dependencies are missing; it never falls back
to distance-only guesses.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any, Mapping


_RECEPTOR_EXTENSIONS = {".pdb", ".cif", ".mmcif"}
_LIGAND_EXTENSIONS = {".sdf", ".mol2"}
_TYPE_NAMES = {
    "HBDonor": "hydrogen_bond",
    "HBAcceptor": "hydrogen_bond",
    "Hydrophobic": "hydrophobic",
    "PiStacking": "pi_pi",
    "CationPi": "cation_pi",
    "Anionic": "ionic",
    "Cationic": "ionic",
    "VdWContact": "van_der_waals",
}


def _input_summary(path: Path) -> dict[str, Any]:
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        size = path.stat().st_size
    except OSError:
        digest, size = None, None
    return {"name": path.name, "sha256": digest, "size_bytes": size}


def _result(
    status: str,
    reason_code: str,
    *,
    interactions: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason_code": reason_code,
        "interactions": interactions or [],
        "warnings": warnings or [],
        "provenance": provenance or {"analyzer": "prolif", "tool_derived": False},
    }


def normalize_prolif_output(output: Any) -> dict[str, Any]:
    """Convert ProLIF's nested residue/interaction output to a safe DTO.

    Only interaction identity and counts are projected.  Distances and angles
    are not invented when the analyzer did not provide them.
    """

    if output is None:
        output = {}
    if hasattr(output, "to_dict"):
        try:
            output = output.to_dict()
        except Exception:
            output = {}
    if not isinstance(output, Mapping):
        output = {}

    interactions: list[dict[str, Any]] = []
    for pair, values in output.items():
        if not isinstance(values, Mapping):
            continue
        pair_values = list(pair) if isinstance(pair, (tuple, list)) else [pair]
        protein_residue = str(pair_values[0]) if pair_values else "?"
        ligand_residue = str(pair_values[1]) if len(pair_values) > 1 else "?"
        for raw_name, raw_count in values.items():
            interaction_type = _TYPE_NAMES.get(str(raw_name))
            if interaction_type is None:
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                count = 1 if raw_count else 0
            if count <= 0:
                continue
            interactions.append(
                {
                    "type": interaction_type,
                    "protein_residue": protein_residue,
                    "ligand_residue": ligand_residue,
                    "count": count,
                    "method": "prolif",
                    "quality": "tool_derived",
                }
            )
    return {
        "interactions": interactions,
        "warnings": [] if interactions else ["no_interactions_reported_by_prolif"],
    }


def _dependency_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def analyze_docking_interactions(
    receptor_path: str | Path,
    ligand_path: str | Path,
    *,
    pose_index: int = 1,
) -> dict[str, Any]:
    """Analyze one prepared pose with ProLIF, or return an honest unavailable result."""

    receptor = Path(receptor_path)
    ligand = Path(ligand_path)
    if not receptor.is_file() or not ligand.is_file():
        return _result(
            "unavailable",
            "analysis_input_missing",
            warnings=["receptor_and_ligand_analysis_artifacts_are_required"],
        )
    if type(pose_index) is not int or pose_index <= 0:
        return _result("failed", "invalid_pose_index", warnings=["pose_index_must_be_positive"])
    if receptor.suffix.lower() not in _RECEPTOR_EXTENSIONS or ligand.suffix.lower() not in _LIGAND_EXTENSIONS:
        return _result(
            "unavailable",
            "unsupported_topology",
            warnings=["analysis_requires_explicit_bonds_and_supported_structure_formats"],
            provenance={"analyzer": "prolif", "tool_derived": False},
        )
    if not _dependency_available("prolif") or not _dependency_available("MDAnalysis"):
        return _result(
            "unavailable",
            "dependency_missing",
            warnings=["prolif_and_mdanalysis_are_required_for_interaction_analysis"],
            provenance={"analyzer": "prolif", "tool_derived": False},
        )

    try:
        import MDAnalysis as mda
        import prolif as plf

        universe = mda.Universe(str(receptor))
        if not any(
            str(getattr(atom, "element", "")).upper() == "H"
            or str(getattr(atom, "name", "")).upper().startswith("H")
            for atom in universe.atoms
        ):
            return _result(
                "unavailable",
                "explicit_hydrogens_missing",
                warnings=["protein_explicit_hydrogens_are_required_by_prolif"],
                provenance={"analyzer": "prolif", "tool_derived": False},
            )

        protein = plf.Molecule.from_mda(universe)
        poses = plf.sdf_supplier(str(ligand))
        selected_pose = None
        for index, candidate in enumerate(poses, start=1):
            if index == pose_index:
                selected_pose = candidate
                break
        if selected_pose is None:
            return _result("failed", "pose_not_found", warnings=["requested_pose_is_not_in_ligand_artifact"])

        fingerprint = plf.Fingerprint(count=True)
        fingerprint.run_from_iterable([selected_pose], protein)
        frame = fingerprint.to_dataframe()
        row = frame.iloc[0].to_dict() if len(frame.index) else {}
        normalized = normalize_prolif_output(row)
        provenance = {
            "analyzer": "prolif",
            "tool_derived": True,
            "prolif_version": getattr(plf, "__version__", "unknown"),
            "inputs": {
                "receptor": _input_summary(receptor),
                "ligand": _input_summary(ligand),
            },
        }
        return _result("success", "analysis_completed", provenance=provenance, **normalized)
    except Exception as exc:
        return _result(
            "failed",
            "analysis_execution_failed",
            warnings=[f"prolif_analysis_failed:{type(exc).__name__}"],
            provenance={"analyzer": "prolif", "tool_derived": False},
        )


def resolve_analysis_inputs(job_dir: str | Path, pose_index: int) -> tuple[Path | None, Path | None]:
    """Resolve only explicitly prepared analysis artifacts; never infer from PDBQT."""

    root = Path(job_dir)
    receptor = next(
        (
            root / name
            for name in (
                "analysis_receptor.pdb",
                "analysis_receptor.cif",
                "analysis_receptor.mmcif",
                "receptor_analysis.pdb",
            )
            if (root / name).is_file()
        ),
        None,
    )
    ligand = next(
        (
            root / name
            for name in (
                f"analysis_pose_{pose_index}.sdf",
                f"pose_{pose_index}.sdf",
                f"ligand_pose_{pose_index}.sdf",
            )
            if (root / name).is_file()
        ),
        None,
    )
    return receptor, ligand
