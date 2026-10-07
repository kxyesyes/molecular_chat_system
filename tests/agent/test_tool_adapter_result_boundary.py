from src.agent.contracts import AgentErrorCode, ObservationStatus
from src.agent.tooling.adapters import ToolAdapter
from src.agent.tooling.spec import RetryPolicy, ToolSpec


def _adapter(raw):
    class RawAdapter(ToolAdapter):
        def invoke(self, _payload):
            return raw

    return RawAdapter(ToolSpec(
        name="raw_boundary",
        version="1",
        description="test",
        input_schema=None,
        output_schema=None,
        capabilities=set(),
        timeout_seconds=1.0,
        retry_policy=RetryPolicy(),
        side_effects="none",
        idempotent=True,
        sensitive_fields=set(),
    ))


def test_generic_adapter_rejects_success_failure_status_conflict():
    result = _adapter({
        "success": True,
        "status": "failed",
        "error": {"code": "provider_error", "message": "upstream failed"},
    }).execute("input")

    assert result.success is False
    assert result.status is ObservationStatus.FAILED
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT


def test_generic_adapter_keeps_partial_as_partial_not_succeeded():
    result = _adapter({
        "success": True,
        "status": "partial",
        "data": {"accepted": 1},
        "warnings": ["one item rejected"],
    }).execute("input")

    assert result.success is True
    assert result.status is ObservationStatus.PARTIAL
    assert result.to_legacy_dict()["success"] is False
    assert result.to_legacy_dict()["status"] == "partial"
