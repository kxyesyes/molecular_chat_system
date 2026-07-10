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


class StructuredLegacyTool:
    name = "structured"

    def execute(self, query):
        return {
            "success": True,
            "message": "structured ok",
            "data": {"value": 3},
            "formatted": "STRUCTURED",
            "warnings": ["low confidence"],
            "evidence": [{"source": "unit-test", "value": 3}],
            "artifacts": [
                {
                    "artifact_type": "report",
                    "path": "outputs/report.json",
                    "label": "normalized report",
                    "mime_type": "application/json",
                    "metadata": {"validated": True},
                }
            ],
            "quality": {"validated": True, "score": 0.9},
        }


class StructuredFailingLegacyTool:
    name = "structured_failure"

    def execute(self, query):
        return {
            "success": False,
            "message": "invalid scientific output",
            "error": {
                "code": "invalid_output",
                "message": "binding energy lacks evidence",
                "details": {"claim": "binding_energy"},
            },
            "warnings": ["claim removed"],
            "evidence": [{"source": "validator"}],
            "quality": {"validated": False},
        }


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


def test_legacy_tool_preserves_structured_fields():
    wrapped = execute_tool_compat(StructuredLegacyTool(), "CCO")

    assert wrapped.warnings == ["low confidence"]
    assert wrapped.evidence == [{"source": "unit-test", "value": 3}]
    assert wrapped.quality == {"validated": True, "score": 0.9}
    assert len(wrapped.artifacts) == 1
    assert wrapped.artifacts[0].artifact_type == "report"
    assert wrapped.artifacts[0].path == "outputs/report.json"
    assert wrapped.artifacts[0].metadata == {"validated": True}


def test_failed_legacy_tool_preserves_error_and_validation_fields():
    wrapped = execute_tool_compat(StructuredFailingLegacyTool(), "CCO")

    assert wrapped.success is False
    assert wrapped.error.code == AgentErrorCode.INVALID_OUTPUT
    assert wrapped.error.details == {"claim": "binding_energy"}
    assert wrapped.warnings == ["claim removed"]
    assert wrapped.evidence == [{"source": "validator"}]
    assert wrapped.quality == {"validated": False}


def test_failed_legacy_tool_accepts_string_error_field():
    class StringErrorLegacyTool:
        name = "rag_search"

        def execute(self, query):
            return {"success": False, "error": "RAG system not available globally"}

    wrapped = execute_tool_compat(StringErrorLegacyTool(), "query")

    assert wrapped.success is False
    assert wrapped.tool_name == "rag_search"
    assert wrapped.message == "RAG system not available globally"
