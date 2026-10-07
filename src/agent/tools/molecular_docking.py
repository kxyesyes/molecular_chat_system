"""Compatibility exports for the docking-owned molecular docking adapter."""

from src.docking.molecular_docking_adapter import (
    MOLECULAR_DOCKING_ADAPTER_VERSION,
    MolecularDocking,
    _docking_lineage,
    _normalize_warning_strings,
    _runtime_model_version,
    _safe_environment_diagnostics,
)

__all__ = [
    "MOLECULAR_DOCKING_ADAPTER_VERSION",
    "MolecularDocking",
    "_docking_lineage",
    "_normalize_warning_strings",
    "_runtime_model_version",
    "_safe_environment_diagnostics",
]
