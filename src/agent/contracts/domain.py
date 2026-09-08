from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
from copy import deepcopy
from typing import Any


@dataclass
class WorkflowArtifact:
    artifact_type: str
    path: str
    label: str
    mime_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "WorkflowArtifact":
        if not isinstance(payload, Mapping):
            raise ValueError("Artifact must be an object")
        artifact = cls(**deepcopy(dict(payload)))
        if any(not isinstance(value, str) or not value.strip()
               for value in (artifact.artifact_type, artifact.path)):
            raise ValueError("Artifact type and path must be non-empty strings")
        if (not isinstance(artifact.label, str) or not isinstance(artifact.metadata, dict)
                or (artifact.mime_type is not None and not isinstance(artifact.mime_type, str))):
            raise ValueError("Invalid artifact metadata")
        return artifact

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "path": self.path,
            "label": self.label,
            "mime_type": self.mime_type,
            "metadata": self.metadata,
        }


@dataclass
class MoleculeCandidate:
    smiles: str
    source: str
    name: str | None = None
    rank: int | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    admet: dict[str, Any] = field(default_factory=dict)
    activity: dict[str, Any] = field(default_factory=dict)
    docking: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "smiles": self.smiles,
            "source": self.source,
            "name": self.name,
            "rank": self.rank,
            "properties": self.properties,
            "admet": self.admet,
            "activity": self.activity,
            "docking": self.docking,
            "warnings": self.warnings,
            "evidence": self.evidence,
        }


@dataclass
class TargetCandidate:
    gene_symbol: str
    protein_name: str | None = None
    uniprot_id: str | None = None
    organism: str | None = None
    confidence: float | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gene_symbol": self.gene_symbol,
            "protein_name": self.protein_name,
            "uniprot_id": self.uniprot_id,
            "organism": self.organism,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
class StructureCandidate:
    structure_id: str
    source: str
    local_file_path: str | None = None
    score: float | None = None
    docking_recommended: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "structure_id": self.structure_id,
            "source": self.source,
            "local_file_path": self.local_file_path,
            "score": self.score,
            "docking_recommended": self.docking_recommended,
            "metadata": self.metadata,
        }


@dataclass
class DockingCandidate:
    ligand_smiles: str
    target: str
    score: float | None = None
    receptor_file: str | None = None
    ligand_file: str | None = None
    pose_file: str | None = None
    interactions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ligand_smiles": self.ligand_smiles,
            "target": self.target,
            "score": self.score,
            "receptor_file": self.receptor_file,
            "ligand_file": self.ligand_file,
            "pose_file": self.pose_file,
            "interactions": self.interactions,
            "warnings": self.warnings,
        }
