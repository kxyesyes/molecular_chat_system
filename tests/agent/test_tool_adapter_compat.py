from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.tools.base_tool import execute_tool_compat


class DummyLegacyTool:
    name = "dummy"

    def execute(self, query):
        return {
            "success": True,
            "message": "ok",
            "data": {"value": 1},
            "formatted": "OK",
        }


class DummyToolResultTool:
    name = "already_standard"

    def execute(self, query):
        return ToolResult.success_result(
            tool_name=self.name,
            data={"value": 2},
            message="standard",
        )


class FailingLegacyTool:
    name = "failing"

    def execute(self, query):
        raise RuntimeError("boom")


def test_legacy_tool_is_wrapped_as_tool_result():
    wrapped = execute_tool_compat(DummyLegacyTool(), "CCO")

    assert wrapped.success is True
    assert wrapped.tool_name == "dummy"
    assert wrapped.data == {"value": 1}
    assert wrapped.formatted == "OK"
    assert isinstance(wrapped.elapsed_ms, int)


def test_standard_tool_result_passes_through_with_elapsed_time():
    wrapped = execute_tool_compat(DummyToolResultTool(), "CCO")

    assert wrapped.success is True
    assert wrapped.tool_name == "already_standard"
    assert wrapped.data == {"value": 2}
    assert isinstance(wrapped.elapsed_ms, int)


def test_legacy_tool_exception_becomes_internal_error():
    wrapped = execute_tool_compat(FailingLegacyTool(), "CCO")

    assert wrapped.success is False
    assert wrapped.tool_name == "failing"
    assert wrapped.error.code == AgentErrorCode.INTERNAL_ERROR
    assert "boom" in wrapped.message
