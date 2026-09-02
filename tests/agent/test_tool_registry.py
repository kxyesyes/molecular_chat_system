from __future__ import annotations

import pytest
from pydantic import BaseModel

from src.agent.tooling import (
    build_tool_registry,
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


def test_registry_resolves_versioned_scientific_capability():
    registry = ToolRegistry()
    registry.register(build_adapter())

    adapter = registry.resolve_capability(
        "molecule.properties",
        agent_name="property_admet",
    )

    assert adapter.spec.name == "property_calculator"


def test_registry_rejects_capability_without_registered_implementation():
    registry = ToolRegistry()

    with pytest.raises(RuntimeError, match="No available tool implements capability"):
        registry.resolve_capability("molecule.properties")


def test_production_registry_preserves_retryable_provider_failure_contract():
    class ProviderUnavailableTargetTool:
        name = "target_database_search"

        def __init__(self):
            self.calls = 0

        def execute(self, query):
            self.calls += 1
            return {
                "success": False,
                "status": "unavailable",
                "message": "Authoritative target providers are unavailable",
                "error": {
                    "code": "provider_error",
                    "message": "Authoritative target providers are unavailable",
                },
                "warnings": ["UniProt temporarily unavailable"],
                "evidence": [
                    {
                        "source": "UniProt",
                        "source_record_id": "P00533",
                        "stale": True,
                    }
                ],
                "quality": {"status": "partial", "retryable": True},
            }

    tool = ProviderUnavailableTargetTool()
    registry = build_tool_registry([tool])

    result = registry.resolve("target_database_search").execute({"query": "EGFR"})

    assert tool.calls == 2
    assert result.success is False
    assert result.error.code.value == "provider_error"
    assert result.warnings == ["UniProt temporarily unavailable"]
    assert result.evidence == [
        {
            "source": "UniProt",
            "source_record_id": "P00533",
            "stale": True,
        }
    ]
    assert result.quality == {"status": "partial", "retryable": True}


def test_production_registry_does_not_retry_nonretryable_provider_failure():
    class PermanentTargetFailure:
        name = "target_database_search"

        def __init__(self):
            self.calls = 0

        def execute(self, query):
            self.calls += 1
            return {
                "success": False,
                "message": "Authoritative target evidence was invalid",
                "error": {
                    "code": "provider_error",
                    "message": "Authoritative target evidence was invalid",
                },
                "warnings": ["authoritative_normalization_failed"],
                "quality": {"status": "partial", "retryable": False},
            }

    tool = PermanentTargetFailure()
    result = build_tool_registry([tool]).resolve(
        "target_database_search"
    ).execute({"query": "EGFR"})

    assert tool.calls == 1
    assert result.error.code.value == "provider_error"
    assert result.quality["retryable"] is False


def test_production_registry_preserves_actual_target_tool_service_evidence():
    from src.agent.tools.target_database_tool import TargetDatabaseTool

    class UnavailableService:
        def __init__(self):
            self.calls = 0

        def search_targets(self, query):
            self.calls += 1
            return {
                "query": query,
                "status": "unavailable",
                "retryable": True,
                "results": [],
                "warnings": ["UniProt:provider_unavailable"],
                "lookup_path": ["local", "UniProt"],
                "cache": {"target": "miss", "structures": "miss"},
                "evidence": [
                    {
                        "source": "UniProt",
                        "status": "unavailable",
                        "stale": False,
                    }
                ],
            }

    tool = TargetDatabaseTool()
    service = UnavailableService()
    tool._service = service

    result = build_tool_registry([tool]).resolve(
        "target_database_search"
    ).execute({"query": "EGFR"})

    assert service.calls == 2
    assert result.error.code.value == "provider_error"
    assert result.warnings == ["UniProt:provider_unavailable"]
    assert result.evidence == [
        {"source": "UniProt", "status": "unavailable", "stale": False}
    ]
    assert result.quality == {
        "status": "partial",
        "service_statuses": ["unavailable"],
        "retryable": True,
    }


def test_production_registry_closes_long_lived_legacy_tools():
    class ClosableTargetTool:
        name = "target_database_search"

        def __init__(self):
            self.close_calls = 0

        def execute(self, query):
            return {"success": True, "data": []}

        def close(self):
            self.close_calls += 1

    tool = ClosableTargetTool()
    registry = build_tool_registry([tool])

    registry.close()

    assert tool.close_calls == 1
