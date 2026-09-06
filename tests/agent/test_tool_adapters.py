from __future__ import annotations

from pydantic import BaseModel

from src.agent.contracts import AgentErrorCode
from src.agent.tooling import (
    LegacyPythonToolAdapter,
    MCPToolAdapter,
    RetryPolicy,
    ToolSpec,
)


class QueryInput(BaseModel):
    query: str


class ValueOutput(BaseModel):
    value: int


class LegacyValueTool:
    name = "legacy_value"

    def __init__(self):
        self.calls = 0

    def execute(self, query):
        self.calls += 1
        return {
            "success": True,
            "message": "ok",
            "data": {"value": len(query)},
            "formatted": "value",
        }


def make_spec(**overrides):
    values = {
        "name": "legacy_value",
        "version": "1.0",
        "description": "Return the query length",
        "input_schema": QueryInput,
        "output_schema": ValueOutput,
        "capabilities": {"property"},
        "timeout_seconds": 1.0,
        "retry_policy": RetryPolicy(max_attempts=1),
        "side_effects": "none",
        "idempotent": True,
        "sensitive_fields": set(),
        "owner_agents": {"property_admet"},
    }
    values.update(overrides)
    return ToolSpec(**values)


def test_legacy_adapter_validates_input_and_output():
    tool = LegacyValueTool()
    adapter = LegacyPythonToolAdapter(make_spec(), tool)

    result = adapter.execute({"query": "CCO"})

    assert result.success is True
    assert result.data == {"value": 3}
    assert tool.calls == 1


def test_adapter_rejects_invalid_input_without_calling_tool():
    tool = LegacyValueTool()
    adapter = LegacyPythonToolAdapter(make_spec(), tool)

    result = adapter.execute({"not_query": "CCO"})

    assert result.success is False
    assert result.error.code == AgentErrorCode.INVALID_INPUT
    assert tool.calls == 0


def test_adapter_rejects_invalid_output_schema():
    class InvalidOutputTool:
        name = "legacy_value"

        def execute(self, query):
            return {"success": True, "message": "ok", "data": {"wrong": 1}}

    result = LegacyPythonToolAdapter(make_spec(), InvalidOutputTool()).execute(
        {"query": "CCO"}
    )

    assert result.success is False
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT


def test_adapter_retries_declared_retryable_errors():
    class FlakyTool:
        name = "legacy_value"

        def __init__(self):
            self.calls = 0

        def execute(self, query):
            self.calls += 1
            if self.calls == 1:
                raise ConnectionError("temporary")
            return {"success": True, "message": "ok", "data": {"value": 3}}

    tool = FlakyTool()
    spec = make_spec(
        retry_policy=RetryPolicy(
            max_attempts=2,
            retryable_error_codes={AgentErrorCode.PROVIDER_ERROR.value},
        )
    )

    result = LegacyPythonToolAdapter(spec, tool).execute({"query": "CCO"})

    assert result.success is True
    assert tool.calls == 2


def test_adapter_does_not_retry_connection_error_when_policy_excludes_it():
    class OfflineTool:
        name = "legacy_value"

        def __init__(self):
            self.calls = 0

        def execute(self, query):
            self.calls += 1
            raise ConnectionError("offline")

    tool = OfflineTool()
    spec = make_spec(
        retry_policy=RetryPolicy(
            max_attempts=2,
            retryable_error_codes={AgentErrorCode.TOOL_TIMEOUT.value},
        )
    )

    result = LegacyPythonToolAdapter(spec, tool).execute({"query": "CCO"})

    assert result.success is False
    assert result.error.code == AgentErrorCode.PROVIDER_ERROR
    assert tool.calls == 1


def test_adapter_redacts_declared_sensitive_fields():
    class EchoTool:
        name = "legacy_value"

        def execute(self, query):
            return {
                "success": True,
                "message": "ok",
                "data": {"value": 3, "api_key": "secret"},
            }

    spec = make_spec(output_schema=None, sensitive_fields={"api_key"})
    result = LegacyPythonToolAdapter(spec, EchoTool()).execute({"query": "CCO"})

    assert result.data["api_key"] == "[REDACTED]"


def test_mcp_adapter_is_reserved_but_reports_unavailable():
    adapter = MCPToolAdapter(make_spec())

    assert adapter.health()["available"] is False
    result = adapter.execute({"query": "CCO"})
    assert result.success is False
    assert result.error.code == AgentErrorCode.TOOL_UNAVAILABLE
