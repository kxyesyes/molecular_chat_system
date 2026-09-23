"""Application service for the molecular design module."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from . import ai
from .chemistry import calculate_properties, detect_sites, substitute_fragment
from .fragments import FragmentRepository
from .storage import DesignStorage
from src.web.model_lifecycle import model_request


class MolecularDesignService:
    def __init__(
        self,
        fragment_csv_path: str | Path,
        save_dir: str | Path,
        model=None,
        fragment_repository: Optional[FragmentRepository] = None,
        storage: Optional[DesignStorage] = None,
        model_provider=None,
        model_request_gate=None,
    ):
        self.fragments = fragment_repository or FragmentRepository(fragment_csv_path)
        self.storage = storage or DesignStorage(save_dir)
        self.model = model
        self.model_provider = model_provider
        self.model_request_gate = model_request_gate

    def query_fragments(self, **kwargs) -> Dict[str, Any]:
        return self.fragments.query(**kwargs)

    def detect_sites(self, smiles: str) -> Dict[str, Any]:
        return detect_sites(smiles)

    def substitute_fragment(self, parent_smiles: str, fragment_smiles: str) -> Dict[str, Any]:
        return substitute_fragment(parent_smiles, fragment_smiles)

    def calculate_properties(
        self,
        smiles: str,
        command: str = "",
        reference_smiles: str = "",
    ) -> Dict[str, Any]:
        return calculate_properties(smiles, command=command, reference_smiles=reference_smiles)

    @model_request
    async def ai_recommend(self, command: str, current_smiles: str, current_props: Dict[str, Any]) -> Dict[str, Any]:
        # Resolve once per request. The application owns the borrowed client;
        # switching configuration must not mutate an in-flight recommendation.
        model = self.model_provider() if self.model_provider is not None else self.model
        recommended_fragments = self.fragments.recommend_for_command(command)
        return await ai.recommend(
            model=model,
            command=command,
            current_smiles=current_smiles,
            current_props=current_props or {},
            recommended_fragments=recommended_fragments,
        )

    def save_molecule(self, smiles: str, properties: Dict[str, Any]) -> Dict[str, Any]:
        return self.storage.save_molecule(smiles, properties or {})

    def export_history_csv(self, history):
        return self.storage.export_history_csv(history)
