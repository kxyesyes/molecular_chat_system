"""Server-only structure reference. Never deserialize this from browser metadata."""
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedScientificMolecule:
    trace_id: str
    presentation_id: str
    revision: str
    observation_id: str
    candidate_id: str
    canonical_smiles: str

    def pointer(self):
        return {"trace_id": self.trace_id, "presentation_id": self.presentation_id,
                "revision": self.revision}

    def routing_query(self, query):
        return query + "\nSMILES: " + self.canonical_smiles

    def revalidate(self, store, session_id):
        if store is None:
            return False
        try:
            view = store.get_scientific_presentation(**self.pointer(), session_id=session_id)
            return bool(view and any(
                row["observation_id"] == self.observation_id
                and row["candidate"]["candidate_id"] == self.candidate_id
                and row["candidate"]["canonical_smiles"] == self.canonical_smiles
                for row in view["ordered_candidates"]))
        except Exception:
            return False
