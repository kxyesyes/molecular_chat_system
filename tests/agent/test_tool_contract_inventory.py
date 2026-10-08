"""Closed registry inventory; registration fixtures are not scientific results."""
from types import SimpleNamespace

import pytest

from src.agent.capabilities.catalog import CAPABILITY_CATALOG, TOOL_ALIASES, capability_for_tool
from src.agent.contracts import AgentContext
from src.agent.harness.decision_policy import authorized_catalog
from src.agent.tooling.factory import (
    LegacyQueryInput, TOOL_AGENT_OWNERS, build_tool_registry,
)
from src.agent.tools.docking_tools import (
    PrepareReceptorTool, PrepareLigandTool, RunDockingTool, GetDockingResultTool,
)


CAPABILITY_TOOLS = {name for item in CAPABILITY_CATALOG for name in item.tool_names}
HELPERS = (PrepareReceptorTool, PrepareLigandTool, RunDockingTool, GetDockingResultTool)
HELPER_NAMES = {"prepare_receptor", "prepare_ligand", "run_docking", "get_docking_result"}


def test_registry_inventory_has_no_unclassified_canonical_tools():
    canonical = {TOOL_ALIASES.get(name, name) for name in TOOL_AGENT_OWNERS}
    assert canonical == CAPABILITY_TOOLS | HELPER_NAMES
    assert CAPABILITY_TOOLS.isdisjoint(HELPER_NAMES)


@pytest.mark.parametrize("name", sorted(CAPABILITY_TOOLS))
def test_each_capability_has_explicit_input_and_output_contract(name):
    # Construction only, without calling or loading an actual scientific tool.
    source = SimpleNamespace(name=name)
    registry = build_tool_registry([source])
    try:
        adapter = registry.resolve(name, require_available=False)
        assert adapter.spec.input_schema is not None
        assert adapter.spec.input_schema is not LegacyQueryInput
        assert adapter.spec.output_schema is not None
        assert capability_for_tool(name).name in adapter.spec.capabilities
    finally:
        registry.close()


def test_alias_is_one_typed_entry():
    registry = build_tool_registry([SimpleNamespace(name="rag_database_search")])
    try:
        assert set(registry.as_mapping()) == {"rag_search"}
        assert registry.resolve("rag_search") is registry.resolve("rag_database_search")
        assert registry.resolve("rag_search").spec.output_schema is not None
    finally:
        registry.close()


@pytest.mark.parametrize("tool_class", HELPERS)
def test_staged_helper_refuses_agent_execution_and_is_not_model_capability(tool_class):
    class NoService:
        def __getattr__(self, name):
            pytest.fail("Agent helper path accessed a docking domain service")

    source = tool_class(service=NoService())
    registry = build_tool_registry([source])
    try:
        adapter = registry.resolve(source.name, require_available=False)
        assert adapter.spec.input_schema is LegacyQueryInput
        assert adapter.spec.output_schema is None
        with pytest.raises(KeyError):
            capability_for_tool(source.name)
        context = AgentContext("Prepare ligand CCO", "helper-boundary")
        catalog, adapters = authorized_catalog(registry, context, "scientific", HELPER_NAMES)
        assert catalog == [] and adapters == {}
        result = adapter.execute({"query": "CCO"})
        assert result.success is False and result.error is not None
        assert result.artifacts == [] and result.evidence == []
        assert result.message  # explicit input/structured-interface requirement
    finally:
        registry.close()
