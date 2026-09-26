"""Agent adapter for the injected, manifest-validated RAG service."""
from typing import Any, Dict

from src.rag.receipt import validate_retrieval_envelope
from src.rag.index import RAGIndexCompatibilityError
from src.web.rag_presentation import format_rag_context
from .base_tool import BaseMolecularTool


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
                     and getattr(self.rag_system, "vector_index", None) is not None
                     and all(callable(getattr(self.rag_system, name, None)) for name in (
                         "capture_retrieval_eligibility", "search_similar_molecules_sync_with_receipt",
                         "validate_retrieval_source")))
        return {"available": ready, "message": "ready" if ready else "RAG index is not initialized"}

    def should_use(self, query: str) -> bool:
        keywords = ["database", "knowledge base", "similar molecule", "search in database",
                    "数据库", "库里", "类似分子", "检索", "查一下"]
        return any(word in query.lower() for word in keywords)

    def execute(self, query: str, **kwargs) -> Dict[str, Any]:
        unavailable = "RAG retrieval unavailable: initialization, source/index compatibility or embedding failure"
        k = kwargs.get("k", 3)
        try:
            capture = getattr(self.rag_system, "capture_retrieval_eligibility", None)
            strict_search = getattr(self.rag_system, "search_similar_molecules_sync_with_receipt", None)
            validate_source = getattr(self.rag_system, "validate_retrieval_source", None)
            if (not all(callable(method) for method in (capture, strict_search, validate_source))
                    or not getattr(self.rag_system, "is_initialized", False)):
                return self._failure("tool_unavailable", unavailable, "strict_source_unavailable")
            expected = capture()
            envelope = strict_search(query, k=k)
        except Exception:
            return self._failure("tool_unavailable", unavailable, "strict_source_unavailable")
        try:
            # None means "not supplied" only in the pure validation API, not
            # for an explicit legacy execute(k=None) request.
            if type(query) is not str or not query.strip() or type(k) is not int or k < 1:
                raise ValueError("Invalid RAG result count")
            envelope = validate_retrieval_envelope(envelope, query=query, k=k)
        except RAGIndexCompatibilityError:
            return self._failure("tool_unavailable", unavailable, "strict_source_unavailable")
        except ValueError:
            return self._failure("invalid_output", "RAG output validation failed", "invalid_receipt")
        results, receipt = envelope['records'], envelope['receipt']
        retrieval_status = receipt['diagnostics']['status']
        partial = retrieval_status == 'invalid_discard'
        result = {
            "success": not partial,
            "data": results,
            "evidence": [{
                "source": "local_rag_vector_index",
                "record_count": len(results),
                "embedding_model": receipt['embedding_model'],
                "records": [dict(row["provenance"], source_index=row["source_index"]) for row in results],
                "retrieval_receipt": receipt,
            }],
            "quality": {"tool_name": "rag_search", "alias": "rag_database_search",
                        "database_initialized": True, "retrieval_status": retrieval_status},
        }
        if partial:
            message = "RAG retrieval contained invalid results; accepted rows are diagnostic partial data only."
            result.update(status="partial", message=message, warnings=[message],
                          error={"code": "invalid_output", "message": message,
                                 "details": {"reason": "invalid_discard"}})
        else:
            try:
                summary = format_rag_context(results)
            except Exception:
                return self._failure("tool_unavailable", unavailable, "formatting_unavailable")
            result.update(summary=summary,
                          message=(f"Found {len(results)} similar molecules." if results else
                                   "The verified vector index has no searchable vectors."))
        try:
            # Formatting grants no authority: check once more at publication.
            validate_source(envelope, query=query, k=k, expected=expected)
        except RAGIndexCompatibilityError:
            return self._failure("tool_unavailable", unavailable, "strict_source_unavailable")
        except ValueError:
            return self._failure("invalid_output", "RAG output validation failed", "invalid_receipt")
        except Exception:
            return self._failure("tool_unavailable", unavailable, "strict_source_unavailable")
        return result

    @staticmethod
    def _failure(code, message, reason):
        return {"success": False, "data": [],
                "error": {"code": code, "message": message, "details": {"reason": reason}}}
