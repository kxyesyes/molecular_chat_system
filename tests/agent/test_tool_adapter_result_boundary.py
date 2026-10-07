import pytest

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


@pytest.mark.parametrize("status", ["timeout", "not_calculated"])
def test_generic_adapter_rejects_success_flag_with_non_success_status(status):
    result = _adapter({
        "success": True,
        "status": status,
        "data": {"accepted": 1},
    }).execute("input")

    assert result.success is False
    assert result.status.value == status
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT


@pytest.mark.parametrize(
    ("legacy_status", "canonical_status"),
    [("timed_out", "timeout"), ("canceled", "cancelled"), ("not-calculated", "not_calculated")],
)
def test_generic_adapter_normalizes_legacy_status_aliases(legacy_status, canonical_status):
    result = _adapter({
        "success": False,
        "status": legacy_status,
        "error": {"code": "internal_error", "message": "fixture"},
    }).execute("input")

    assert result.success is False
    assert result.status.value == canonical_status
