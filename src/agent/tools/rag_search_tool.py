"""Agent adapter for the injected, manifest-validated RAG service."""
import logging
from typing import Any, Dict

from src.web.rag_presentation import format_rag_context
from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


class RAGSearchTool(BaseMolecularTool):
    def __init__(self, rag_system=None, embedding_endpoint: str | None = None):
        super().__init__(
            name="rag_search",
            description="Searches the local molecular knowledge base for similar molecules. Input: query or SMILES.",
        )
        self.aliases = {"rag_database_search"}
        self.rag_system = rag_system
        # Compatibility argument may confirm, but never override, service config.
        if embedding_endpoint is not None and (
            rag_system is None or embedding_endpoint != rag_system.embedding_endpoint
        ):
            raise ValueError("Configure the embedding endpoint on the injected RAG service")

    def registration_health(self):
        """Local readiness only; never initialize an index or contact a model."""
        ready = bool(self.rag_system is not None
                     and getattr(self.rag_system, "is_initialized", False)
                     and getattr(self.rag_system, "vector_index", None) is not None)
        return {"available": ready, "message": "ready" if ready else "RAG index is not initialized"}

    def should_use(self, query: str) -> bool:
        keywords = ["database", "knowledge base", "similar molecule", "search in database",
                    "数据库", "库里", "类似分子", "检索", "查一下"]
        return any(word in query.lower() for word in keywords)

    def execute(self, query: str, **kwargs) -> Dict[str, Any]:
        if self.rag_system is None:
            return {"success": False, "data": [], "error": "RAG service has not been injected"}
        try:
            results = self.rag_system.search_similar_molecules_sync(query, k=kwargs.get("k", 3))
        except Exception:
            logger.warning("RAG tool retrieval failed; no unverified records returned")
            return {"success": False, "data": [],
                    "error": "RAG retrieval unavailable: initialization, source/index compatibility or embedding failure"}
        return {
            "success": True,
            "data": results,
            "summary": format_rag_context(results),
            "message": (f"Found {len(results)} similar molecules." if results
                        else "No similar molecules found in the local database."),
            "evidence": [{
                "source": "local_rag_vector_index",
                "record_count": len(results),
                "embedding_model": self.rag_system.embedding_model_name,
                "records": [dict(row["provenance"], source_index=row["source_index"]) for row in results],
            }],
            "quality": {"tool_name": "rag_search", "alias": "rag_database_search",
                        "database_initialized": True},
        }
