"""Factory-level contracts; provenance here is synthetic, not scientific evidence."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, get_ident

import pytest

from src.agent.contracts import (
    AgentErrorCode, ObservationStatus, ToolProvenance, ToolResult, WorkflowArtifact,
)
from src.agent.tooling.adapters import LegacyPythonToolAdapter
from src.agent.tooling.factory import build_tool_registry, LegacyQueryInput
from src.agent.tools.rag_search_tool import RAGSearchTool
from test_rag_receipt_consumption import SyntheticSourceContract


def record():
    return {
        "source_index": 0, "similarity_score": -1.25,
        "arbitrary_csv_column": {"nested": ["preserved", 3]},
        "provenance": {
            "source_path": "synthetic.csv", "source_sha256": "a" * 64,
            "index_sha256": "b" * 64, "embedding_model": "synthetic-only",
            "manifest_schema_version": 1, "builder_version": "test",
            "vector_label": 0, "extension": {"keep": True},
        },
    }


class InjectedService(SyntheticSourceContract):
    """Synthetic strict-envelope fixture only, never scientific ownership proof."""
    is_initialized = True
    vector_index = object()
    embedding_model_name = "synthetic-only"

    def __init__(self, rows=None):
        self.rows = deepcopy([record()] if rows is None else rows)
        for row in self.rows:
            row['provenance'].update(manifest_schema_version=2, builder_version='1')
        self.calls = []
        self.set_synthetic_source(self._synthetic_envelope('', 3)['receipt'])

    def search_similar_molecules_sync_with_receipt(self, query, k=3):
        self.calls.append((query, k))
        return self._synthetic_envelope(query, k)

    def _synthetic_envelope(self, query, k):
        import hashlib
        from src.rag.receipt import canonical_digest
        count = len(self.rows)
        return {'records': self.rows, 'receipt': {
            'schema_version': '1', 'validation_revision': 'rag-owned-generation-v1',
            'invocation_id': 'c' * 32, 'generation_id': 'd' * 32,
            'input_sha256': hashlib.sha256(query.encode('utf-8')).hexdigest(),
            'source_path': 'synthetic.csv', 'source_sha256': 'a' * 64,
            'source_row_count': count, 'index_sha256': 'b' * 64,
            'row_mapping_sha256': canonical_digest(list(range(count))),
            'vector_dimension': 2, 'vector_count': count,
            'manifest_schema_version': 2, 'builder_version': '1',
            'embedding_model': 'synthetic-only', 'embedding_endpoint_sha256': 'e' * 64,
            'embedding_weights_verified': False, 'index_embedding_endpoint_sha256': None,
            'result_sha256': canonical_digest(self.rows), 'diagnostics': {
                'version': '1', 'status': 'valid_hits' if count else 'valid_empty',
                'requested_k': k, 'effective_k': count, 'index_search_executed': bool(count),
                'score_count': count, 'label_count': count, 'accepted_count': count,
                'discarded_count': 0, 'reason_codes': [],
            },
        }}


class ResultTool:
    name = "rag_search"

    def __init__(self, raw):
        self.raw = raw
        self.calls = 0

    def execute(self, query):
        self.calls += 1
        return self.raw


def adapter_for(tool):
    return build_tool_registry([tool]).resolve("rag_search", require_available=False)


@pytest.mark.parametrize("query", [123, True, None, [], {}, b"private-query"])
def test_factory_rejects_nontext_without_execution(query):
    service = InjectedService()
    result = adapter_for(RAGSearchTool(service)).execute({"query": query})
    assert result.success is False
    assert result.error.code is AgentErrorCode.INVALID_INPUT
    assert service.calls == []
    assert "private-query" not in repr(result)


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("rows", [[], [record()]])
def test_actual_rag_preserves_text_default_k_and_rows(wrapped, rows):
    service = InjectedService(rows)
    registry = build_tool_registry([RAGSearchTool(service)])
    adapter = registry.resolve("rag_search", require_available=False)
    assert registry.resolve("rag_database_search", require_available=False) is adapter
    query = "  CCO\n检索  "
    result = adapter.execute({"query": query} if wrapped else query)
    assert result.success is True
    assert result.status is ObservationStatus.SUCCEEDED
    assert result.data == service.rows
    assert service.calls == [(query, 3)]
    assert result.evidence[0]["record_count"] == len(rows)


def test_rag_factory_has_typed_schemas_and_preserves_spec():
    adapter = adapter_for(RAGSearchTool(InjectedService()))
    assert adapter.spec.input_schema.model_fields["query"].annotation is str
    assert adapter.spec.output_schema is not None
    assert adapter.spec.output_schema.schema_version == "1"
    assert "schema_version" not in adapter.spec.output_schema.model_fields
    assert adapter.spec.owner_agents == {"rag"}
    assert adapter.spec.timeout_seconds == 180
    assert adapter.spec.max_concurrency == 1
    assert adapter.spec.retry_policy.max_attempts == 1


LEGAL_STATES = [(True, ObservationStatus.SUCCEEDED),
                (True, ObservationStatus.PARTIAL),
                (False, ObservationStatus.PARTIAL)] + [
    (False, status) for status in ObservationStatus
    if status not in {ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL}
]


def envelope(success, status, data):
    return {
        "success": success, "status": status.value, "data": data,
        "message": "synthetic result", "formatted": "validated text",
        "error": None if success else {
            "code": "tool_unavailable", "message": "dependency unavailable",
            "details": {"reason": "synthetic-only"},
        },
        "warnings": ["synthetic-only"], "evidence": [{"extra": {"keep": 1}}],
        "quality": {"extension": [1, 2]},
        "artifacts": [{"artifact_type": "report", "path": "synthetic.json",
                       "label": "test", "metadata": {"extension": [3]}}],
        "provenance": ToolProvenance(tool_name="rag_search").to_dict(),
    }


def normalized(raw):
    from src.agent.tools.base_tool import execute_tool_compat
    return execute_tool_compat(ResultTool(deepcopy(raw)), "CCO")


@pytest.mark.parametrize("success,status", LEGAL_STATES)
@pytest.mark.parametrize("as_result", [False, True])
def test_legal_states_and_metadata_survive_without_projection(success, status, as_result):
    raw = envelope(success, status, [record()])
    expected = normalized(raw)
    raw = normalized(raw) if as_result else raw
    tool = ResultTool(raw)
    result = adapter_for(tool).execute({"query": "CCO"})
    assert result.success is success
    assert result.status is status
    for field in ("data", "message", "formatted", "error", "warnings", "evidence",
                  "artifacts", "quality", "provenance"):
        assert getattr(result, field) == getattr(expected, field), field
    if as_result:
        assert result is raw
    assert tool.calls == 1


BAD_ROWS = [None, {}, "untrusted-science", ["untrusted-science"],
            [{"source_index": 0, "similarity_score": 1}]]


@pytest.mark.parametrize("success,status", LEGAL_STATES)
@pytest.mark.parametrize("as_result", [False, True])
@pytest.mark.parametrize("data", BAD_ROWS)
def test_all_terminal_states_validate_records_before_discard(success, status, as_result, data):
    raw = envelope(success, status, [])
    raw = normalized(raw) if as_result else raw
    if as_result:
        raw.data = data
    else:
        raw["data"] = data
    tool = ResultTool(raw)
    result = adapter_for(tool).execute({"query": "CCO"})
    if data is None and not success:
        assert result.success is False
        assert result.status is status
        assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
        assert result.data is None
        assert tool.calls == 1
        return
    assert result.success is False
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None
    assert result.formatted == ""
    assert "untrusted-science" not in repr(result)
    assert tool.calls == 1


@pytest.mark.parametrize("field,value", [
    ("source_index", True), ("source_index", "0"), ("source_index", -1),
    ("similarity_score", float("nan")), ("similarity_score", float("inf")),
    ("similarity_score", float("-inf")), ("similarity_score", True),
    ("similarity_score", "0.5"), ("provenance", None),
])
def test_record_fields_are_strict(field, value):
    row = record()
    row[field] = value
    result = adapter_for(ResultTool({"success": True, "data": [row]})).execute({"query": "CCO"})
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("field,value", [
    ("source_path", ""), ("source_sha256", None), ("index_sha256", 42),
    ("embedding_model", ""), ("manifest_schema_version", True),
    ("builder_version", None), ("vector_label", -1), ("vector_label", True),
    ("vector_label", "0"),
])
def test_provenance_fields_are_required_and_strict(field, value):
    row = record()
    row["provenance"][field] = value
    result = adapter_for(ResultTool({"success": False, "data": [row]})).execute({"query": "CCO"})
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    del row["provenance"][field]
    result = adapter_for(ResultTool({"success": True, "data": [row]})).execute({"query": "CCO"})
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize("success,status", [(True, "failed"), (False, "succeeded"),
                                           (True, "bogus"), ("yes", "succeeded")])
@pytest.mark.parametrize("as_result", [False, True])
def test_conflicting_or_invalid_status_rejected(success, status, as_result):
    raw = {"success": success, "status": status, "data": [record()]}
    if as_result:
        raw = ToolResult.success_result("rag_search", [record()])
        raw.success = success
        raw.status = ObservationStatus(status) if status != "bogus" else status
    result = adapter_for(ResultTool(raw)).execute({"query": "CCO"})
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None


@pytest.mark.parametrize("raw", [
    {"success": False, "data": [], "error": "unavailable"},
    {"success": False, "data": None, "error": {"message": "unavailable"}},
    {"success": False, "error": {"code": "provider_error"}},
    {"success": False, "status": "unavailable", "data": []},
])
def test_legacy_failure_envelopes_keep_core_semantics(raw):
    expected = normalized(raw)
    result = adapter_for(ResultTool(raw)).execute({"query": "CCO"})
    assert result.error == expected.error
    assert result.status is expected.status


def test_raw_validator_is_composed_inside_worker_and_data_is_redacted():
    row = record()
    row["token"] = "synthetic-secret"
    raw = {"success": True, "data": [row]}
    seen = []
    caller_thread = get_ident()

    def validate(value):
        seen.append((value, get_ident()))

    tool = ResultTool(raw)
    result = adapter_for(tool).execute({"query": "CCO"}, raw_validator=validate)
    assert result.success is True
    assert seen == [(raw, seen[0][1])]
    assert seen[0][1] != caller_thread
    assert tool.calls == 1
    assert result.data[0]["token"] == "[REDACTED]"
    assert row["token"] == "synthetic-secret"
    assert result.data[0]["arbitrary_csv_column"] == row["arbitrary_csv_column"]


def test_caller_validator_failure_is_not_ignored():
    def reject(raw):
        raise ValueError("caller rejected")

    tool = ResultTool({"success": True, "data": [record()]})
    result = adapter_for(tool).execute({"query": "CCO"}, raw_validator=reject)
    assert result.error.code is AgentErrorCode.INTERNAL_ERROR
    assert result.message == "caller rejected"
    assert tool.calls == 1


def test_validation_stays_inside_timeout_and_holds_slot_until_worker_finishes():
    entered, release, finished = Event(), Event(), Event()
    tool = ResultTool({"success": True, "data": [record()]})
    tool.timeout_seconds = 0.05
    adapter = adapter_for(tool)

    def validate(raw):
        entered.set()
        release.wait(3)
        finished.set()

    with ThreadPoolExecutor(max_workers=1) as callers:
        pending = callers.submit(adapter.execute, {"query": "CCO"}, raw_validator=validate)
        try:
            assert entered.wait(3)
            first = pending.result(timeout=3)
            assert first.error.code is AgentErrorCode.TOOL_TIMEOUT
            assert first.quality["invocation_may_still_be_running"] is True
            second = adapter.execute({"query": "CCO"})
            assert second.error.code is AgentErrorCode.TOOL_UNAVAILABLE
            assert second.quality["capacity_exhausted"] is True
            assert tool.calls == 1
        finally:
            release.set()
            assert finished.wait(3)


def test_non_rag_factory_retains_legacy_schema_and_payload():
    tool = ResultTool({"success": True, "data": {"arbitrary": 1}})
    tool.name = "candidate_ranker"
    # Generic transport probe, not scientific ranking: retain the old numeric
    # query acceptance without bypassing any production typed factory contract.
    spec = build_tool_registry([tool]).resolve(tool.name, require_available=False).spec
    adapter = LegacyPythonToolAdapter(
        replace(spec, input_schema=LegacyQueryInput, output_schema=None), tool)
    assert type(adapter) is LegacyPythonToolAdapter
    assert adapter.spec.output_schema is None
    result = adapter.execute({"query": 123})
    assert result.success is True
    assert result.data == {"arbitrary": 1}


@pytest.mark.parametrize("payload", [
    {"query": {"private": "private-query"}},
    {"missing": "private-query"}, ["private-query"],
])
def test_invalid_input_diagnostics_and_health_do_not_reflect_query(payload):
    tool = ResultTool({"success": True, "data": []})
    adapter = adapter_for(tool)
    result = adapter.execute(payload)
    assert result.error.code is AgentErrorCode.INVALID_INPUT
    assert result.error.details is None
    assert "private-query" not in repr(result)
    assert "private-query" not in repr(adapter.health())
    assert adapter.health()["last_error_code"] == "invalid_input"
    assert adapter.health()["last_execution_status"] == "failed"
    assert tool.calls == 0


def test_constructed_input_model_cannot_bypass_text_validation():
    tool = ResultTool({"success": True, "data": []})
    adapter = adapter_for(tool)
    invalid = adapter.spec.input_schema.model_construct(query=123)
    result = adapter.execute(invalid)
    assert result.error is not None
    assert result.error.code is AgentErrorCode.INVALID_INPUT
    assert tool.calls == 0


@pytest.mark.parametrize("field,value", [
    ("message", ["untrusted-science"]), ("code", "not-a-code"),
    ("details", ["untrusted-science"]),
])
def test_mutated_canonical_errors_are_revalidated(field, value):
    raw = ToolResult.error_result("rag_search", AgentErrorCode.TOOL_UNAVAILABLE, "unavailable")
    setattr(raw.error, field, value)
    result = adapter_for(ResultTool(raw)).execute({"query": "CCO"})
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert "untrusted-science" not in repr(result)


@pytest.mark.parametrize("field,value", [
    ("message", ["untrusted-science"]), ("details", ["untrusted-science"]),
])
def test_raw_structured_error_fields_are_validated(field, value):
    raw = {"success": False, "data": [], "error": {field: value}}
    result = adapter_for(ResultTool(raw)).execute({"query": "CCO"})
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert "untrusted-science" not in repr(result)


def test_caller_validator_cannot_mutate_records_past_contract():
    raw = {"success": True, "data": [record()]}

    def mutate(value):
        value["data"][0]["similarity_score"] = float("nan")

    result = adapter_for(ResultTool(raw)).execute({"query": "CCO"}, raw_validator=mutate)
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None


@pytest.mark.parametrize("score", [3.5, 2, -2, 0])
def test_finite_similarity_is_not_restricted_to_probability(score):
    row = record()
    row["similarity_score"] = score
    result = adapter_for(ResultTool([row])).execute("CCO")
    assert result.success is True
    assert result.data == [row]


@pytest.mark.parametrize("raw", ["untrusted-science", 42, None])
def test_non_record_bare_output_is_not_scientific_success(raw):
    result = adapter_for(ResultTool(raw)).execute("CCO")
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert "untrusted-science" not in repr(result)


@pytest.mark.parametrize("exception", [
    ConnectionError("offline"), TimeoutError("deadline"), RuntimeError("execution failed"),
])
def test_real_execution_errors_keep_existing_codes(exception):
    class RaisingTool(ResultTool):
        def execute(self, query):
            self.calls += 1
            raise exception

    tool = RaisingTool(None)
    adapter = adapter_for(tool)
    # Python 3.10 has distinct builtin/futures TimeoutError classes. Preserve
    # the existing adapter mapping, rather than inventing a RAG-only mapping.
    baseline_tool = RaisingTool(None)
    baseline = LegacyPythonToolAdapter(
        replace(adapter.spec, input_schema=None, output_schema=None), baseline_tool,
    ).execute("CCO")
    result = adapter.execute("CCO")
    assert result.error.code is baseline.error.code
    assert result.message == baseline.message
    assert result.quality == baseline.quality
    assert result.data is None
    assert tool.calls == 1


def test_unavailable_adapter_does_not_execute_and_retains_telemetry():
    tool = ResultTool(None)
    adapter = adapter_for(tool)
    adapter.set_available(False, "offline")
    result = adapter.execute("CCO")
    assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
    assert result.message == "offline"
    assert adapter.health()["last_error_code"] == "tool_unavailable"
    assert tool.calls == 0


@pytest.mark.parametrize("success,status", LEGAL_STATES)
@pytest.mark.parametrize("field,value", [
    ("warnings", {"untrusted-science": 1}),
    ("evidence", ["untrusted-science"]),
    ("quality", ["untrusted-science"]),
    ("artifacts", ["untrusted-science"]),
])
def test_metadata_containers_are_validated_in_all_states(success, status, field, value):
    raw = envelope(success, status, [record()])
    raw[field] = value
    result = adapter_for(ResultTool(raw)).execute("CCO")
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert "untrusted-science" not in repr(result)


@pytest.mark.parametrize("shape", ["raw_dict", "raw_dataclass", "canonical"])
@pytest.mark.parametrize("field,value", [
    ("artifact_type", 123), ("path", ["untrusted-science"]),
    ("label", {"untrusted-science": True}), ("mime_type", ["untrusted-science"]),
    ("metadata", [["untrusted-science", 123]]),
])
def test_artifact_members_are_validated_before_compat_coercion(shape, field, value):
    raw = envelope(False, ObservationStatus.PARTIAL, [record()])
    if shape == "canonical":
        raw = normalized(raw)
        setattr(raw.artifacts[0], field, value)
    elif shape == "raw_dataclass":
        artifact = WorkflowArtifact(**raw["artifacts"][0])
        setattr(artifact, field, value)
        raw["artifacts"] = [artifact]
    else:
        raw["artifacts"][0][field] = value
    result = adapter_for(ResultTool(raw)).execute("CCO")
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None
    assert result.artifacts == []
    assert "untrusted-science" not in repr(result)


@pytest.mark.parametrize("as_result", [False, True])
def test_malformed_artifact_is_not_silently_dropped(as_result):
    raw = envelope(True, ObservationStatus.SUCCEEDED, [record()])
    raw = normalized(raw) if as_result else raw
    if as_result:
        raw.artifacts.append("untrusted-science")
    else:
        raw["artifacts"].append("untrusted-science")
    result = adapter_for(ResultTool(raw)).execute("CCO")
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None
    assert result.artifacts == []


def test_legacy_artifact_defaults_still_belong_to_compat():
    raw = {"success": True, "data": [], "artifacts": [{"path": "synthetic.json"}]}
    result = adapter_for(ResultTool(raw)).execute("CCO")
    assert result.success is True
    assert result.artifacts == normalized(raw).artifacts


@pytest.mark.parametrize("success,status", LEGAL_STATES)
@pytest.mark.parametrize("as_result", [False, True])
@pytest.mark.parametrize("kind", ["record", "provenance"])
@pytest.mark.parametrize("malformed", [False, True])
def test_nested_record_views_rejected_before_normalization_in_all_states(
    success, status, as_result, kind, malformed, monkeypatch,
):
    from src.agent.tooling.rag_contract import RAGRecord, RAGRecordProvenance
    from src.agent.tools import base_tool

    row = record()
    if kind == "record":
        values = dict(source_index=-1, similarity_score=float("nan"), provenance=None)
        view = RAGRecord.model_construct(**values) if malformed else RAGRecord.model_validate(row)
        row = view
    else:
        values = dict(row["provenance"], vector_label=-1, source_path=None)
        view = (RAGRecordProvenance.model_construct(**values) if malformed
                else RAGRecordProvenance.model_validate(row["provenance"]))
        row["provenance"] = view

    raw = envelope(success, status, [])
    if as_result:
        raw = normalized(raw)
        raw.data = [row]
        original_data = raw.data
    else:
        raw["data"] = [row]
        original_data = raw["data"]
    compat_calls = []
    original_compat = base_tool.execute_tool_compat

    def observe_compat(*args, **kwargs):
        compat_calls.append(True)
        return original_compat(*args, **kwargs)

    monkeypatch.setattr(base_tool, "execute_tool_compat", observe_compat)
    tool = ResultTool(raw)
    adapter = adapter_for(tool)
    result = adapter.execute("CCO")
    assert result.success is False
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None
    assert result.formatted == ""
    assert result.evidence == []
    assert result.artifacts == []
    assert compat_calls == []
    assert tool.calls == 1
    assert adapter.health()["last_error_code"] == "invalid_output"
    assert (raw.data if as_result else raw["data"]) is original_data
    assert (original_data[0] if kind == "record" else original_data[0]["provenance"]) is view


@pytest.mark.parametrize("success,status", LEGAL_STATES)
@pytest.mark.parametrize("kind", ["error", "artifact"])
@pytest.mark.parametrize("malformed", [False, True])
def test_raw_metadata_views_cannot_bypass_validation_or_be_dropped(
    success, status, kind, malformed, monkeypatch,
):
    from src.agent.tooling.rag_contract import RAGArtifactFields, RAGErrorFields
    from src.agent.tools import base_tool

    raw = envelope(success, status, [record()])
    if kind == "error":
        view = (RAGErrorFields.model_construct(message=["untrusted-science"], details=[])
                if malformed else RAGErrorFields(message="synthetic-only"))
        raw["error"] = view
    else:
        view = (RAGArtifactFields.model_construct(path=["untrusted-science"], metadata=[])
                if malformed else RAGArtifactFields(path="synthetic.json"))
        raw["artifacts"] = [view]
    compat_calls = []
    original_compat = base_tool.execute_tool_compat

    def observe_compat(*args, **kwargs):
        compat_calls.append(True)
        return original_compat(*args, **kwargs)

    monkeypatch.setattr(base_tool, "execute_tool_compat", observe_compat)
    tool = ResultTool(raw)
    result = adapter_for(tool).execute("CCO")
    assert result.success is False
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None
    assert result.artifacts == []
    assert "untrusted-science" not in repr(result)
    assert compat_calls == []
    assert tool.calls == 1
    assert (raw["error"] if kind == "error" else raw["artifacts"][0]) is view
