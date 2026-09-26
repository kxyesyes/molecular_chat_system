"""Agent adapter for the injected, manifest-validated RAG service."""
from copy import deepcopy
import re
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

    @staticmethod
    def _validate_current_projection(value):
        fields = {'kind', 'generation_id', 'epoch', 'configuration_sha256',
                  'source_identity_sha256'}
        if (type(value) is not dict or set(value) != fields
                or type(value['kind']) is not str or value['kind'] != 'rag'
                or type(value['epoch']) is not int or value['epoch'] < 0):
            raise ValueError('current_source_unavailable')
        for field in fields - {'kind', 'epoch'}:
            size = 32 if field == 'generation_id' else 64
            if (type(value[field]) is not str
                    or re.fullmatch('[0-9a-f]{%d}' % size, value[field]) is None):
                raise ValueError('current_source_unavailable')

    def validate_current_observation(self, data, evidence, *, input_data,
                                     expected_source=None) -> dict:
        """Revalidate original proof/current source, not result authority or seals.

        Uses only the attached service. Its full CSV freshness checks may do I/O;
        callers must schedule this hook as owned work, not on the event loop.
        """
        try:
            from src.agent.harness.decision_bounds import validate_json

            value = dict(data=data, evidence=evidence, input_data=input_data,
                         expected_source=expected_source)
            validate_json(value, max_bytes=64 * 1024, reason='current_source_unavailable')
            if expected_source is not None:
                self._validate_current_projection(expected_source)
            if (type(input_data) is not str or not input_data.strip()
                    or len(input_data.encode('utf-8')) > 16 * 1024
                    or type(evidence) is not list):
                raise ValueError('current_source_unavailable')
            # No caller-owned records or receipt are exposed to provider calls.
            value = deepcopy(value)
            expected_source = value['expected_source']
            service = self.rag_system
            if service is None or getattr(service, 'is_initialized', False) is not True:
                raise ValueError('current_source_unavailable')
            capture = getattr(service, 'capture_retrieval_eligibility', None)
            validate_source = getattr(service, 'validate_retrieval_source', None)
            if not callable(capture) or not callable(validate_source):
                raise ValueError('current_source_unavailable')
            entries = [entry for entry in value['evidence']
                       if type(entry) is dict and 'retrieval_receipt' in entry]
            if len(entries) != 1:
                raise ValueError('current_source_unavailable')
            envelope = validate_retrieval_envelope(
                dict(records=value['data'], receipt=entries[0]['retrieval_receipt']),
                query=input_data, k=3)
            if envelope['receipt']['diagnostics']['status'] not in ('valid_hits', 'valid_empty'):
                raise ValueError('current_source_unavailable')
            fresh = capture()
            validate_source(envelope, query=input_data, k=3, expected=fresh)
            projection = dict(kind='rag', generation_id=fresh.generation_id, epoch=fresh.epoch,
                              configuration_sha256=fresh.configuration_sha256,
                              source_identity_sha256=fresh.source_identity_sha256)
            self._validate_current_projection(projection)
            if expected_source is not None and expected_source != projection:
                raise ValueError('current_source_unavailable')
            if self.rag_system is not service or service.is_initialized is not True:
                raise ValueError('current_source_unavailable')
            return projection
        except Exception:
            raise ValueError('current_source_unavailable') from None

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
