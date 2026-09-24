"""Offline assembly/registration tests: real factory, registry and API handler."""
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agent.specialists import build_default_specialists
from src.agent.capabilities import CAPABILITY_CATALOG
from src.agent.tooling import build_tool_registry, ToolRegistry
from src.agent.tools.rag_search_tool import RAGSearchTool
from src.web.app import MolecularChatApp
from src.web.routes.agent_workflow_routes import setup_agent_workflow_routes


class Tool:
    def __init__(self, name, aliases=()):
        self.name, self.aliases = name, set(aliases)
    def execute(self, query):
        return {"success": True, "data": {"query": query}}


@pytest.mark.parametrize("ready", [True, False])
@pytest.mark.parametrize("legacy_name", [False, True])
def test_default_app_factory_rag_workflow_api(tmp_path, monkeypatch, ready, legacy_name):
    import asyncio
    import json
    import faiss
    import httpx
    import numpy as np
    import pandas as pd
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.tools import get_all_tools
    from src.web.app import RAGSystem
    from src.web.rag_index import (
        CURRENT_SCHEMA_VERSION, RAGIndexManifest, atomic_save_index_pair, file_sha256,
    )

    source = tmp_path / "molecules.csv"
    frame = pd.DataFrame([
        {"SMILES": "CCC", "source": "fixture-db"},
        {"SMILES": "CCN", "source": "skipped-source-row"},
        {"SMILES": "CCO", "source": "fixture-db"},
    ])
    frame.to_csv(source, index=False)
    store = tmp_path / "vectors"
    index_path = store.with_suffix(".index")
    index = faiss.IndexFlatIP(2)
    index.add(np.asarray([[1., 0.], [0., 1.]], dtype=np.float32))
    manifest = RAGIndexManifest(
        schema_version=CURRENT_SCHEMA_VERSION, source_path=str(source),
        source_sha256=file_sha256(source), index_sha256="0" * 64,
        embedding_model="offline-embedding", vector_dimension=2, vector_count=2,
        row_mapping=[0, 2], created_at="2026-09-23T00:00:00+00:00",
    )
    persisted = atomic_save_index_pair(index, index_path, manifest, faiss_module=faiss)
    rag = RAGSystem({"rag": {
        "csv_path": str(source), "embedding_model": "offline-embedding",
        "embedding_endpoint": "http://embedding.test/api/embeddings",
    }})
    rag.molecules_df = pd.read_csv(source)
    calls = []

    def embed(request):
        payload = json.loads(request.content)
        calls.append(payload["prompt"])
        assert ready, "uninitialized RAG must not call the embedding endpoint"
        assert request.method == "POST"
        assert str(request.url) == rag.embedding_endpoint
        assert payload["model"] == rag.embedding_model_name
        return httpx.Response(200, json={"embedding": [0., 1.]})

    # Replace only network transport: retain the real HTTP client, embedding
    # method, manifest validator, FAISS search and row projection.
    transport = httpx.MockTransport(embed)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request",
                        lambda self, request: transport.handle_request(request))
    asyncio.run(rag._load_or_create_index(str(store)))
    assert rag.index_status == "loaded"
    assert rag.manifest == persisted
    assert rag.manifest.row_mapping == [0, 2]
    assert calls == []
    rag.is_initialized = ready
    app = MolecularChatApp.__new__(MolecularChatApp)
    app.model = object()
    app.molecular_generator_model = object()
    app.rag_system = rag
    app.agent_state_store = SQLiteAgentStateStore(tmp_path / "agent.sqlite")
    app.agent_tool_registry = None
    # Keep both real application factories and tool factories; load only the
    # optional RAG adapter, with no scientific model initialization.
    monkeypatch.setattr("src.agent.tools.OPTIONAL_TOOLS", ["RAGSearchTool"])
    factory_calls = []

    def tools_factory(model, *, rag_system):
        assert model is app.molecular_generator_model
        assert rag_system is rag
        factory_calls.append(rag_system)
        tools = get_all_tools(model, rag_system=rag_system)
        if legacy_name:
            next(tool for tool in tools if tool.name == "rag_search").name = "rag_database_search"
        return tools

    monkeypatch.setattr("src.agent.tools.get_all_tools", tools_factory)
    app.agent_system = app._create_chat_agent()
    rag_tool = next(tool for tool in app.agent_system.tools.values() if isinstance(tool, RAGSearchTool))
    monkeypatch.setattr("src.agent.supervisor.build_default_tools", lambda: pytest.fail("must reuse registered tools"))
    results = []
    class Manager:
        def submit(self, task_type, payload, handler, owner_session_id):
            results.append(handler(payload))
            return SimpleNamespace(to_public_dict=lambda: {"result": results[-1]})
    monkeypatch.setattr("src.web.routes.agent_workflow_routes.get_task_manager", lambda: Manager())
    api = FastAPI()
    from src.web.agent_session_config import setup_agent_sessions
    setup_agent_sessions(api)
    setup_agent_workflow_routes(api, app._create_supervisor_agent)
    try:
        with TestClient(api, base_url="http://localhost") as client:
            response = client.post("/api/agent/workflows/run", json={
                "query": "检索知识库中的乙醇", "skill_name": "rag_search"})
        assert response.status_code == 200
        assert results[-1]["status"] == ("succeeded" if ready else "failed")
        assert calls == (["检索知识库中的乙醇"] if ready else [])
        assert factory_calls == [rag]
        assert app.agent_registration_report["errors"] == []
        adapter = app.agent_tool_registry.resolve("rag_search", require_available=False)
        assert app.agent_tool_registry.resolve("rag_database_search", require_available=False) is adapter
        assert adapter.tool is rag_tool
        assert adapter.tool.rag_system is rag
        assert adapter.health()["available"] is ready
        if ready:
            assert "fixture-db" in response.text
            tool_results = response.json()["data"]["result"]["result"]["tool_result_sequence"]
            assert len(tool_results) == 1
            assert tool_results[0]["success"] is True
            records = tool_results[0]["data"]
            assert [row["SMILES"] for row in records] == ["CCO", "CCC"]
            assert [row["source_index"] for row in records] == [2, 0]
            assert [row["provenance"]["vector_label"] for row in records] == [1, 0]
            assert records[0]["similarity_score"] == pytest.approx(1.0)
            assert records[0]["provenance"] == {
                "source_path": str(source), "source_sha256": file_sha256(source),
                "index_sha256": file_sha256(index_path),
                "embedding_model": "offline-embedding",
                "manifest_schema_version": CURRENT_SCHEMA_VERSION,
                "builder_version": persisted.builder_version, "vector_label": 1,
            }
        else:
            assert "rag_search" in app.agent_registration_report["unavailable_tools"]
            assert "tool_unavailable" in response.text
    finally:
        if app.agent_tool_registry:
            app.agent_tool_registry.close()


@pytest.mark.parametrize("order", ["alias-first", "canonical-first", "self-alias"])
def test_registration_conflicts_are_atomic(order):
    registry = ToolRegistry()
    first = build_tool_registry([Tool("property_calculator", ["shared"])]).resolve("property_calculator")
    second = build_tool_registry([Tool("admet_predictor")]).resolve("admet_predictor")
    registry.register(first)
    if order == "alias-first":
        second.spec = replace(second.spec, name="shared")
    elif order == "canonical-first":
        second.spec = replace(second.spec, aliases={"property_calculator"})
    else:
        second.spec = replace(second.spec, aliases={"admet_predictor"})
    before = registry.as_mapping()
    with pytest.raises(ValueError):
        registry.register(second)
    assert registry.as_mapping() == before
    assert registry.resolve("shared") is first


def test_unknown_factory_tool_is_not_silently_filtered():
    with pytest.raises(ValueError, match="owner"):
        build_tool_registry([Tool("misspelled_rag")])


@pytest.mark.parametrize("name", ["rag_search", "rag_database_search"])
def test_default_rag_owner_accepts_canonical_and_alias(name):
    from src.agent.specialists import AgentTask
    from src.agent.tooling import RetryPolicy
    # Keep the ownership test on the actual RAG tool/adapter boundary. These
    # source fields are synthetic contract fixtures, not scientific evidence.
    rows = [{"source_index": 0, "similarity_score": 0.75,
             "source": "synthetic-db", "SMILES": "CCO",
             "provenance": {
                 "source_path": "synthetic.csv", "source_sha256": "a" * 64,
                 "index_sha256": "b" * 64, "embedding_model": "offline-test",
                 "manifest_schema_version": 2, "builder_version": "1",
                 "vector_label": 0,
             }}]
    calls = []

    class Service:
        is_initialized = True
        vector_index = object()
        embedding_model_name = "offline-test"

        def search_similar_molecules_sync(self, query, k=3):
            calls.append((query, k))
            return rows

    registry = build_tool_registry([RAGSearchTool(Service())])
    specialist = build_default_specialists()["rag"]
    task = AgentTask(task_id="rag-1", trace_id="trace-1", agent_name="rag", objective="retrieve", inputs={"query": "CCO"},
        allowed_tools=[name], dependencies=[], retry_policy=RetryPolicy(),
        timeout_seconds=5, idempotency_key="rag-1", metadata={})
    result = specialist.execute_task(task, registry)
    assert result.status == "succeeded"
    assert result.tool_results[0].data == rows
    assert calls == [("CCO", 3)]


@pytest.mark.parametrize("capability", CAPABILITY_CATALOG, ids=lambda item: item.name)
def test_capability_owner_policy_registry_are_consistent(capability):
    from src.agent.tooling.registration import audit_registration
    from src.agent.tooling.factory import TOOL_AGENT_OWNERS
    tools = [Tool(name, ["rag_database_search"] if name == "rag_search" else [])
             for name in TOOL_AGENT_OWNERS if name != "rag_database_search"]
    registry = build_tool_registry(tools)
    report = audit_registration(registry, build_default_specialists())
    assert report["errors"] == []
    assert registry.by_capability(capability.name, require_available=False)
    for name in capability.tool_names:
        assert registry.resolve(name, agent_name=TOOL_AGENT_OWNERS[name], require_available=False)


@pytest.mark.parametrize("fault", ["owner", "required_tool", "allowlist", "workflow"])
def test_registration_audit_rejects_logic_errors(fault):
    from src.agent.tooling.registration import audit_registration, REQUIRED_TOOLS
    from src.agent.workflows import WorkflowCatalog
    from src.agent.workflows.catalog import WorkflowPolicy
    registry = build_tool_registry([Tool(name) for name in REQUIRED_TOOLS
                                    if fault != "required_tool" or name != "property_calculator"])
    specialists = build_default_specialists()
    catalog = WorkflowCatalog()
    if fault == "owner":
        specialists.pop("property_admet")
    if fault == "allowlist":
        specialists["property_admet"].allowed_tools = set()
    if fault == "workflow":
        catalog = WorkflowCatalog([WorkflowPolicy("bad", "bad", ("unknown_tool",))])
    assert audit_registration(registry, specialists, catalog=catalog)["errors"]


def test_optional_missing_registration_report_does_not_block_core():
    from src.agent.tooling.registration import audit_registration, REQUIRED_TOOLS
    from src.agent.tools.property_calculator import PropertyCalculator
    registry = build_tool_registry([PropertyCalculator() if name == "property_calculator" else Tool(name)
                                    for name in REQUIRED_TOOLS])
    report = audit_registration(registry, build_default_specialists())
    assert not report["errors"]
    assert "activity_predictor" in report["unavailable_tools"]
    assert registry.resolve("property_calculator").execute({"query": "CCO"}).success


def test_uninitialized_rag_health_is_honest_without_external_calls(monkeypatch):
    monkeypatch.setattr("requests.post", lambda *a, **k: pytest.fail("health must be local"))
    tool = RAGSearchTool(SimpleNamespace(is_initialized=False, vector_index=None))
    registry = build_tool_registry([tool])
    assert registry.health()[0]["available"] is False
    with pytest.raises(RuntimeError, match="unavailable"):
        registry.resolve("rag_database_search")


def test_actual_tool_factory_preflight_is_lazy(monkeypatch):
    from src.agent.tools import get_all_tools
    from src.agent.tooling.registration import audit_registration
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.agent.tools.reverse_target_tool import ReverseTargetTool
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    monkeypatch.setenv("RXN_API_KEY", "")
    def forbidden(*args, **kwargs):
        pytest.fail("registration must not load scientific models or call providers")
    monkeypatch.setattr(ActivityPredictorTool, "_get_predictor", forbidden)
    monkeypatch.setattr(ReverseTargetTool, "_get_predictor", forbidden)
    monkeypatch.setattr(TargetDatabaseTool, "_get_service", forbidden)
    monkeypatch.setattr("requests.post", forbidden)
    tools = get_all_tools(object())
    registry = build_tool_registry(tools)
    try:
        report = audit_registration(registry, build_default_specialists())
        assert not report["errors"]
        assert "rag_search" in report["unavailable_tools"]
        assert len(registry.as_mapping()) == len(CAPABILITY_CATALOG)
    finally:
        registry.close()


def test_rag_specialist_rejects_other_domain_tool():
    from src.agent.specialists import AgentTask
    from src.agent.tooling import RetryPolicy
    task = AgentTask("1", "1", "rag", "reject", {"query": "CCO"},
        ["property_calculator"], [], RetryPolicy(), 5, "1")
    result = build_default_specialists()["rag"].execute_task(task,
        build_tool_registry([Tool("property_calculator")]))
    assert result.status == "failed" and result.error.code.value == "unauthorized_tool"


def test_app_precheck_rejects_missing_required_tool_without_constructing_models(monkeypatch):
    app = MolecularChatApp.__new__(MolecularChatApp)
    app.agent_state_store = object()
    app.agent_system = SimpleNamespace(tools={"rag_search": RAGSearchTool()})
    app.agent_tool_registry = None
    monkeypatch.setattr("src.agent.supervisor.build_default_tools", lambda: pytest.fail("must not load"))
    try:
        with pytest.raises(ValueError, match="Required tool not registered"):
            app._create_supervisor_agent()
    finally:
        if app.agent_tool_registry:
            app.agent_tool_registry.close()


def test_rag_readiness_can_recover_without_reregistering():
    state = SimpleNamespace(is_initialized=False, vector_index=None)
    registry = build_tool_registry([RAGSearchTool(state)])
    assert not registry.health()[0]["available"]
    state.is_initialized, state.vector_index = True, object()
    assert registry.resolve("rag_database_search").health()["available"]


def test_optional_adapter_unavailable_is_reported_without_blocking_others():
    from src.agent.tooling.registration import REQUIRED_TOOLS, audit_registration
    from src.agent.tools.property_calculator import PropertyCalculator
    registry = build_tool_registry([PropertyCalculator() if name == "property_calculator" else Tool(name)
                                    for name in REQUIRED_TOOLS | {"activity_predictor"}])
    adapter = registry.resolve("activity_predictor")
    adapter.set_available(False, "model missing")
    report = audit_registration(registry, build_default_specialists())
    assert not report["errors"]
    assert "activity_predictor" in report["unavailable_tools"]
    assert next(item for item in report["capabilities"] if item["name"] == "molecule.activity")["readiness"] == "unavailable"
    result = adapter.execute({"query": "CCO"})
    assert not result.success and result.error.code.value == "tool_unavailable"
    assert registry.resolve("property_calculator").execute({"query": "CCO"}).success


@pytest.mark.parametrize("mode", ["missing", "different"])
def test_precheck_requires_alias_identity(mode):
    from src.agent.tooling.registration import REQUIRED_TOOLS, audit_registration
    registry = build_tool_registry([Tool(name) for name in REQUIRED_TOOLS])
    rag = build_tool_registry([Tool("rag_search")]).resolve("rag_search", require_available=False)
    rag.spec = replace(rag.spec, aliases=set())
    registry.register(rag)
    if mode == "different":
        other = build_tool_registry([Tool("rag_search")]).resolve("rag_search", require_available=False)
        other.spec = replace(other.spec, name="rag_database_search", aliases=set())
        registry.register(other)
    assert audit_registration(registry, build_default_specialists())["errors"]


def test_real_lazy_wrappers_have_unknown_not_false_readiness(monkeypatch):
    import json
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.agent.tools.molecular_docking import MolecularDocking
    from src.agent.tooling.registration import REQUIRED_TOOLS, audit_registration
    monkeypatch.setattr(ActivityPredictorTool, "_get_predictor", lambda *a: pytest.fail("no weights"))
    registry = build_tool_registry([Tool(name) for name in REQUIRED_TOOLS] + [ActivityPredictorTool(), MolecularDocking()])
    for name, capability in [("activity_predictor", "molecule.activity"), ("molecular_docking", "docking.execute")]:
        adapter = registry.resolve(name)
        health = adapter.health()
        assert health["available"] is None and health["readiness"] == "not_probed"
        assert '"available": null' in json.dumps(health)
        assert registry.resolve_capability(capability) is adapter
        assert registry.by_capability(capability) == [adapter]
    assert "activity_predictor" not in audit_registration(registry, build_default_specialists())["unavailable_tools"]


def test_failed_lazy_invocation_does_not_poison_later_calls(monkeypatch):
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    tool = ActivityPredictorTool()
    calls = []
    def predictor():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("fixture-model-unavailable")
        # Synthetic contract metadata only; no checkpoint is loaded or verified.
        return SimpleNamespace(demo_mode=False, current_model_metadata={
            "model_id": "synthetic-recovery", "weights_sha256": "a" * 64,
            "task_type": "regression", "endpoint": "pIC50", "units": "pIC50"},
            predict=lambda smiles: [{"success": True, "smiles": smiles[0],
                "task_type": "regression", "endpoint": "pIC50", "units": "pIC50", "value": 5.1}])
    monkeypatch.setattr(tool, "_get_predictor", predictor)
    registry = build_tool_registry([tool])
    adapter = registry.resolve("activity_predictor")
    first = adapter.execute({"query": "预测 CCO 的活性"})
    assert not first.success
    health = adapter.health()
    assert health["available"] is None
    assert health["last_execution_status"] == "failed"
    assert "fixture-model-unavailable" not in str(health)
    second = registry.resolve("activity_predictor").execute({"query": "预测 CCO 的活性"})
    assert second.success
    assert calls == [1, 1]
    assert adapter.health()["available"] is None
    assert adapter.health()["last_execution_status"] == "succeeded"
    assert adapter.health()["last_error_code"] is None


def test_factory_rejects_self_alias():
    with pytest.raises(ValueError, match="alias"):
        build_tool_registry([Tool("property_calculator", ["property_calculator"])])


def test_decision_catalog_preserves_unprobed_state():
    from src.agent.contracts import AgentContext
    from src.agent.harness.decision_policy import authorized_catalog, decision_system_message
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    registry = build_tool_registry([ActivityPredictorTool()])
    catalog, _ = authorized_catalog(registry, AgentContext(query="CCO", trace_id="health"),
                                    "scientific", {"activity_predictor"})
    assert catalog[0]["available"] is None
    message = decision_system_message("scientific", [], catalog, {})
    assert '"available":null' in message["content"] or '"available": null' in message["content"]
    assert "unverified" in message["content"]
