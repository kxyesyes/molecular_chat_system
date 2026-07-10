from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.agent.tooling import (
    LegacyPythonToolAdapter,
    RetryPolicy,
    ToolRegistry,
    ToolSpec,
)


class QueryInput(BaseModel):
    query: str


class DummyTool:
    name = "property_calculator"

    def execute(self, query):
        return {"success": True, "message": "ok", "data": {"query": query}}


def build_adapter():
    spec = ToolSpec(
        name="property_calculator",
        version="1",
        description="properties",
        input_schema=QueryInput,
        output_schema=None,
        capabilities={"property", "chemistry"},
        timeout_seconds=1,
        retry_policy=RetryPolicy(),
        side_effects="none",
        idempotent=True,
        sensitive_fields=set(),
        owner_agents={"property_admet"},
        aliases={"properties"},
    )
    return LegacyPythonToolAdapter(spec, DummyTool())


def test_registry_resolves_aliases_and_capabilities():
    registry = ToolRegistry()
    registry.register(build_adapter())

    assert registry.resolve("properties").spec.name == "property_calculator"
    assert [item.spec.name for item in registry.by_capability("property")] == [
        "property_calculator"
    ]


def test_registry_enforces_agent_ownership():
    registry = ToolRegistry()
    registry.register(build_adapter())

    assert registry.resolve(
        "property_calculator", agent_name="property_admet"
    ).spec.name == "property_calculator"
    with pytest.raises(PermissionError, match="not authorized"):
        registry.resolve("property_calculator", agent_name="molecular_design")


def test_registry_rejects_unavailable_tools_during_plan_validation():
    adapter = build_adapter()
    adapter.set_available(False, "dependency missing")
    registry = ToolRegistry()
    registry.register(adapter)

    with pytest.raises(RuntimeError, match="unavailable"):
        registry.validate_plan(
            ["property_calculator"], agent_name="property_admet"
        )


def test_registry_rejects_duplicate_canonical_names():
    registry = ToolRegistry()
    registry.register(build_adapter())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(build_adapter())
