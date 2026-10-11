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
import math
from numbers import Integral, Real
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Mapping


_RECEPTOR_EXTENSIONS = {".pdb", ".cif", ".mmcif"}
_LIGAND_EXTENSIONS = {".sdf", ".mol2"}
_TYPE_NAMES = {
    "HBDonor": "hydrogen_bond",
    "HBAcceptor": "hydrogen_bond",
    "Hydrophobic": "hydrophobic",
    "PiStacking": "pi_pi",
    "CationPi": "cation_pi",
    "PiCation": "cation_pi",
    "Anionic": "ionic",
    "Cationic": "ionic",
    "VdWContact": "van_der_waals",
    "WaterBridge": "water_bridge",
}
_DISTANCE_KEYS = ("distance", "distance_angstrom")
_ANGLE_KEYS = ("angle", "angle_degrees")
_NAMED_ANGLE_KEYS = ("DHA_angle", "plane_angle", "normal_to_centroid_angle")
_ANGLE_DEVIATION_KEYS = ("angle_deviation", "angle_deviation_degrees")
_INDEX_KEYS = {
    "protein": ("protein_indices", "protein_atom_indices"),
    "ligand": ("ligand_indices", "ligand_atom_indices"),
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
    geometry_status: str = "unavailable",
) -> dict[str, Any]:
    return {
        "status": status,
        "reason_code": reason_code,
        "interactions": interactions or [],
        "warnings": warnings or [],
        "geometry_status": geometry_status,
        "provenance": provenance or {"analyzer": "prolif", "tool_derived": False},
    }


def _finite_positive_number(value: Any, *, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0 or number > maximum:
        return None
    return number


def _finite_angle(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > 360:
        return None
    return number


def _atom_indices(value: Any) -> list[int] | None:
    if not isinstance(value, (list, tuple)) or not value:
        return None
    if any(isinstance(item, bool) or not isinstance(item, Integral) or item < 0 for item in value):
        return None
    return [int(item) for item in value]


def _first_valid_number(
    occurrence: Mapping[str, Any], keys: Sequence[str], *, angle: bool
) -> tuple[float | None, bool, bool]:
    """Return a validated value and whether any candidate key was present."""

    present = False
    invalid = False
    valid_values: list[float] = []
    for key in keys:
        if key not in occurrence:
            continue
        present = True
        value = _finite_angle(occurrence[key]) if angle else _finite_positive_number(
            occurrence[key], maximum=20.0
        )
        if value is not None:
            valid_values.append(value)
        else:
            invalid = True
    if len({round(value, 12) for value in valid_values}) > 1:
        return None, True, True
    return (valid_values[0] if valid_values else None), present, invalid


def _first_valid_indices(
    occurrence: Mapping[str, Any], keys: Sequence[str]
) -> tuple[list[int] | None, bool, bool]:
    present = False
    invalid = False
    valid_values: list[list[int]] = []
    for key in keys:
        if key not in occurrence:
            continue
        present = True
        value = _atom_indices(occurrence[key])
        if value is not None:
            valid_values.append(value)
        else:
            invalid = True
    if len({tuple(value) for value in valid_values}) > 1:
        return None, True, True
    return (valid_values[0] if valid_values else None), present, invalid


def _normalize_geometry(
    occurrence: Mapping[str, Any], warnings: list[str]
) -> tuple[dict[str, Any], bool]:
    geometry: dict[str, Any] = {}
    available = False

    distance, distance_present, distance_invalid = _first_valid_number(
        occurrence, _DISTANCE_KEYS, angle=False
    )
    if distance is not None:
        geometry["distance_angstrom"] = distance
        available = True
    if distance_invalid or (distance is None and distance_present):
        warnings.append("invalid_geometry_metadata")

    angle, angle_present, angle_invalid = _first_valid_number(occurrence, _ANGLE_KEYS, angle=True)
    if angle is not None:
        geometry["angle_degrees"] = angle
        available = True
    if angle_invalid or (angle is None and angle_present):
        warnings.append("invalid_geometry_metadata")

    named_angles = {}
    for key in _NAMED_ANGLE_KEYS:
        if key in occurrence:
            value = _finite_angle(occurrence[key])
            if value is None:
                warnings.append("invalid_geometry_metadata")
            else:
                named_angles[key] = value
    if named_angles:
        geometry["angles_degrees"] = named_angles
        available = True

    angle_deviation, deviation_present, deviation_invalid = _first_valid_number(
        occurrence, _ANGLE_DEVIATION_KEYS, angle=True
    )
    if angle_deviation is not None:
        geometry["angle_deviation_degrees"] = angle_deviation
        available = True
    if deviation_invalid or (angle_deviation is None and deviation_present):
        warnings.append("invalid_geometry_metadata")

    nested_indices = occurrence.get("indices")
    if "indices" in occurrence and not isinstance(nested_indices, Mapping):
        warnings.append("invalid_geometry_metadata")
    if isinstance(nested_indices, Mapping):
        index_sources: dict[str, Any] = dict(occurrence)
        index_sources.update(
            {
                "protein_indices": nested_indices.get("protein"),
                "ligand_indices": nested_indices.get("ligand"),
            }
        )
    else:
        index_sources = dict(occurrence)

    for side, output_key in (
        ("protein", "protein_atom_indices"),
        ("ligand", "ligand_atom_indices"),
    ):
        indices, indices_present, indices_invalid = _first_valid_indices(
            index_sources, _INDEX_KEYS[side]
        )
        if indices is not None:
            geometry[output_key] = indices
            available = True
        if indices_invalid or (indices is None and indices_present):
            warnings.append("invalid_geometry_metadata")
    if "parent_indices" in occurrence:
        parent = occurrence["parent_indices"]
        if isinstance(parent, Mapping):
            validated = {side: _atom_indices(parent.get(side)) for side in ("ligand", "protein")}
            if all(value is not None for value in validated.values()):
                geometry["parent_atom_indices"] = validated
                available = True
            else:
                warnings.append("invalid_geometry_metadata")
        else:
            warnings.append("invalid_geometry_metadata")
    return geometry, available


def _occurrences(raw_value: Any) -> tuple[list[Mapping[str, Any]], int | None]:
    if isinstance(raw_value, Mapping):
        return [raw_value], None
    if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes, bytearray)):
        mappings = [item for item in raw_value if isinstance(item, Mapping)]
        if mappings and len(mappings) == len(raw_value):
            return mappings, None
    if isinstance(raw_value, Integral) and raw_value >= 0:
        return [], int(raw_value)
    if isinstance(raw_value, (list, tuple)) and not raw_value:
        return [], 0
    return [], None


def _iter_interaction_maps(output: Mapping[Any, Any]) -> list[Mapping[Any, Any]]:
    """Flatten ProLIF's optional frame layer without interpreting raw values."""

    if output and all(isinstance(key, int) and isinstance(value, Mapping) for key, value in output.items()):
        flattened: list[Mapping[Any, Any]] = []
        for frame in output.values():
            flattened.extend(_iter_interaction_maps(frame))
        return flattened
    return [output]


def normalize_prolif_output(
    output: Any, *, pair_order: str = "protein_ligand"
) -> dict[str, Any]:
    """Convert ProLIF's nested residue/interaction output to a safe DTO.

    Legacy count callers use protein-first pairs. Native ProLIF metadata uses
    ligand-first pairs and must explicitly select that order. Atom indices are
    residue-local; parent_atom_indices are molecule-global, both zero-based.
    """

    if pair_order not in ("protein_ligand", "ligand_protein"):
        raise ValueError("invalid_pair_order")
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
    warnings: list[str] = []
    unsupported_interaction_types: list[str] = []
    geometry_available = False
    for interaction_map in _iter_interaction_maps(output):
        for pair, values in interaction_map.items():
            if not isinstance(values, Mapping):
                continue
            if not isinstance(pair, tuple) or len(pair) != 2:
                warnings.append("invalid_geometry_metadata")
                continue
            first, second = map(str, pair)
            protein_residue, ligand_residue = (
                (second, first) if pair_order == "ligand_protein" else (first, second)
            )
            for raw_name, raw_value in values.items():
                interaction_type = _TYPE_NAMES.get(str(raw_name))
                if interaction_type is None:
                    if str(raw_name) not in unsupported_interaction_types:
                        unsupported_interaction_types.append(str(raw_name))
                    continue
                occurrence_values, count = _occurrences(raw_value)
                if count is None and not occurrence_values:
                    warnings.append("invalid_geometry_metadata")
                if occurrence_values:
                    geometries: list[dict[str, Any]] = []
                    for occurrence in occurrence_values:
                        geometry, occurrence_has_geometry = _normalize_geometry(
                            occurrence, warnings
                        )
                        geometries.append(geometry)
                        geometry_available = geometry_available or occurrence_has_geometry
                    interaction = {
                        "type": interaction_type,
                        "protein_residue": protein_residue,
                        "ligand_residue": ligand_residue,
                        "count": len(occurrence_values),
                        "method": "prolif",
                        "quality": "tool_derived",
                    }
                    if len(geometries) == 1:
                        interaction.update(geometries[0])
                    elif any(geometries):
                        interaction["geometry_occurrences"] = geometries
                    interactions.append(interaction)
                elif count is not None and count > 0:
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
    if not interactions:
        warnings.append("no_interactions_reported_by_prolif")
    if unsupported_interaction_types:
        warnings.append("unsupported_interaction_type")
    result = {
        "interactions": interactions,
        "warnings": list(dict.fromkeys(warnings)),
        "geometry_status": "available" if geometry_available else "unavailable",
    }
    if unsupported_interaction_types:
        result["unsupported_interaction_types"] = unsupported_interaction_types
    return result


def _dependency_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _attach_display_geometry(interactions, protein, ligand) -> None:
    """Resolve analyzer-global indices against the exact analyzed conformers.

    Viewer parsers may discard hydrogens or reorder atoms. Never ask the browser
    to reinterpret these indices. Heavy-atom centroids represent ring contacts;
    for H-bonds they yield donor/acceptor endpoints, not the donor-H midpoint.
    """
    def endpoint(molecule, indices):
        indices = _atom_indices(indices)
        if indices is None or any(i >= molecule.GetNumAtoms() for i in indices):
            return None
        heavy = [i for i in indices if molecule.GetAtomWithIdx(i).GetAtomicNum() > 1]
        if not heavy:
            return None
        conformer = molecule.GetConformer()
        points = [conformer.GetAtomPosition(i) for i in heavy]
        values = {axis: sum(getattr(p, axis) for p in points) / len(points)
                  for axis in ("x", "y", "z")}
        return values if all(math.isfinite(v) for v in values.values()) else None

    for item in interactions:
        for geometry in item.get("geometry_occurrences", [item]):
            indices = geometry.get("parent_atom_indices")
            if not isinstance(indices, Mapping):
                continue
            start = endpoint(protein, indices.get("protein"))
            end = endpoint(ligand, indices.get("ligand"))
            if start is not None and end is not None:
                geometry["display_geometry"] = {
                    "protein": start, "ligand": end,
                    "source": "analyzed_conformer",
                    "endpoint_rule": "heavy_atom_centroid",
                }


def _valid_metadata(output: Any) -> bool:
    """Validate the sparse one-pose ProLIF v2 shape, not a count dataframe."""
    if not isinstance(output, Mapping):
        return False
    for pair, interactions in output.items():
        if not isinstance(pair, tuple) or len(pair) != 2 or not isinstance(interactions, Mapping):
            return False
        for occurrences in interactions.values():
            if not isinstance(occurrences, (list, tuple)) or not all(
                isinstance(occurrence, Mapping) for occurrence in occurrences
            ):
                return False
    return True


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

    universe = None
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
        supplier_name = "mol2_supplier" if ligand.suffix.lower() == ".mol2" else "sdf_supplier"
        supplier = getattr(plf, supplier_name, None)
        if not callable(supplier):
            return _result(
                "unavailable",
                "geometry_metadata_unavailable",
                warnings=[f"prolif_{supplier_name}_is_required_for_analysis"],
                provenance={
                    "analyzer": "prolif",
                    "tool_derived": False,
                    "metadata_mode": "unavailable",
                    "prolif_version": getattr(plf, "__version__", "unknown"),
                },
            )
        poses = supplier(str(ligand))
        selected_pose = None
        for index, candidate in enumerate(poses, start=1):
            if index == pose_index:
                selected_pose = candidate
                break
        if selected_pose is None:
            return _result("failed", "pose_not_found", warnings=["requested_pose_is_not_in_ligand_artifact"])

        fingerprint = plf.Fingerprint(count=True)
        generate = getattr(fingerprint, "generate", None)
        if not callable(generate):
            return _result(
                "unavailable",
                "geometry_metadata_unavailable",
                warnings=["prolif_metadata_is_required_for_geometry_claims"],
                provenance={
                    "analyzer": "prolif",
                    "tool_derived": False,
                    "metadata_mode": "unavailable",
                    "prolif_version": getattr(plf, "__version__", "unknown"),
                },
            )
        try:
            metadata = generate(selected_pose, protein, metadata=True)
        except TypeError:
            return _result(
                "unavailable",
                "geometry_metadata_unavailable",
                warnings=["prolif_metadata_api_is_required_for_geometry_claims"],
                provenance={
                    "analyzer": "prolif",
                    "tool_derived": False,
                    "metadata_mode": "unavailable",
                    "prolif_version": getattr(plf, "__version__", "unknown"),
                },
            )
        if not _valid_metadata(metadata):
            return _result(
                "unavailable",
                "geometry_metadata_unavailable",
                warnings=["prolif_metadata_is_required_for_geometry_claims"],
                provenance={
                    "analyzer": "prolif",
                    "tool_derived": False,
                    "metadata_mode": "unavailable",
                    "prolif_version": getattr(plf, "__version__", "unknown"),
                },
            )
        normalized = normalize_prolif_output(metadata, pair_order="ligand_protein")
        _attach_display_geometry(normalized["interactions"], protein, selected_pose)
        provenance = {
            "analyzer": "prolif",
            "tool_derived": True,
            "metadata_mode": "metadata",
            "prolif_version": getattr(plf, "__version__", "unknown"),
            "inputs": {
                "receptor": _input_summary(receptor),
                "ligand": _input_summary(ligand),
            },
        }
        if normalized.get("unsupported_interaction_types"):
            return _result(
                "unavailable",
                "unsupported_interaction_type",
                warnings=["prolif_returned_unsupported_interaction_type"],
                provenance={**provenance, "tool_derived": False},
            )
        return _result("success", "analysis_completed", provenance=provenance, **normalized)
    except Exception as exc:
        return _result(
            "failed",
            "analysis_execution_failed",
            warnings=[f"prolif_analysis_failed:{type(exc).__name__}"],
            provenance={"analyzer": "prolif", "tool_derived": False},
        )
    finally:
        # MDAnalysis may keep the receptor file open through its Reader.  The
        # route runs this analyzer inside a leased staging snapshot on Windows,
        # so release the reader before the snapshot cleanup verifies/removes it.
        if universe is not None:
            try:
                close = getattr(universe, "close", None)
                if callable(close):
                    close()
                else:
                    trajectory = getattr(universe, "trajectory", None)
                    close_trajectory = getattr(trajectory, "close", None)
                    if callable(close_trajectory):
                        close_trajectory()
            except Exception:
                pass


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
