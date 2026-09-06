from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError, fields

import pytest

from src.agent.harness import (
    LANGGRAPH_CANARY_WORKFLOWS,
    CanaryDecision,
    LangGraphCanarySelector,
)
from src.agent.harness.canary import MAX_CANARY_KEY_LENGTH


ALLOWLIST = frozenset(
    {
        "admet_assessment",
        "activity_prediction",
        "reverse_target_prediction",
        "target_database_search",
        "rag_search",
    }
)


class ExplodingKey:
    def __bool__(self):
        raise AssertionError("PRIVATE key __bool__ must not be called")

    def __str__(self):
        raise AssertionError("PRIVATE key __str__ must not be called")


class ExplodingStrSubclass(str):
    def __bool__(self):
        raise AssertionError("PRIVATE str subclass __bool__ must not be called")

    def __str__(self):
        raise AssertionError("PRIVATE str subclass __str__ must not be called")

    def encode(self, *args, **kwargs):
        raise AssertionError("PRIVATE str subclass encode must not be called")


class IntSubclass(int):
    pass


class ExplodingPercent:
    def __bool__(self):
        raise AssertionError("PRIVATE percent __bool__ must not be called")

    def __le__(self, other):
        raise AssertionError("PRIVATE percent comparison must not be called")

    def __ge__(self, other):
        raise AssertionError("PRIVATE percent comparison must not be called")

    def __repr__(self):
        raise AssertionError("PRIVATE percent repr must not be called")


def _bucket(key) -> int:
    digest = hashlib.sha256(str(key).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def _select(
    percent,
    *,
    workflow_name="admet_assessment",
    trace_id="trace-id",
    idempotency_key=None,
    executor_supported=True,
):
    return LangGraphCanarySelector(percent=percent).select(
        workflow_name=workflow_name,
        trace_id=trace_id,
        idempotency_key=idempotency_key,
        executor_supported=executor_supported,
    )


def test_canary_symbols_are_public_and_allowlist_is_fixed():
    assert LANGGRAPH_CANARY_WORKFLOWS == ALLOWLIST
    assert isinstance(LANGGRAPH_CANARY_WORKFLOWS, frozenset)
    assert [field.name for field in fields(CanaryDecision)] == [
        "backend",
        "reason",
        "bucket",
        "percent",
    ]


def test_zero_and_one_hundred_percent_are_deterministic():
    expected_bucket = _bucket("trace-id")

    disabled = _select(0)
    enabled = _select(100)

    assert disabled == CanaryDecision(
        backend="legacy",
        reason="canary_not_selected",
        bucket=expected_bucket,
        percent=0,
    )
    assert enabled == CanaryDecision(
        backend="langgraph",
        reason="canary_selected",
        bucket=expected_bucket,
        percent=100,
    )


def test_bucket_boundary_uses_strict_less_than_percent():
    expected_bucket = _bucket("boundary-key")

    at_boundary = _select(expected_bucket, trace_id="boundary-key")
    above_boundary = _select(expected_bucket + 1, trace_id="boundary-key")

    assert at_boundary.bucket == above_boundary.bucket == expected_bucket
    assert (at_boundary.backend, at_boundary.reason) == (
        "legacy",
        "canary_not_selected",
    )
    assert (above_boundary.backend, above_boundary.reason) == (
        "langgraph",
        "canary_selected",
    )


@pytest.mark.parametrize(
    "percent",
    [-1, 101, 1.0, "10", None, True, False],
)
def test_invalid_percent_has_highest_priority_and_fails_closed(percent):
    decision = _select(
        percent,
        workflow_name="not-allowlisted",
        trace_id=None,
        idempotency_key=None,
        executor_supported=False,
    )

    assert decision == CanaryDecision(
        backend="legacy",
        reason="invalid_canary_percent",
        bucket=None,
        percent=0,
    )


def test_invalid_int_subclass_percent_wins_before_adversarial_key_checks():
    decision = LangGraphCanarySelector(percent=IntSubclass(100)).select(
        workflow_name="admet_assessment",
        trace_id=ExplodingKey(),
        idempotency_key=ExplodingKey(),
        executor_supported=True,
    )

    assert decision == CanaryDecision(
        backend="legacy",
        reason="invalid_canary_percent",
        bucket=None,
        percent=0,
    )


def test_malicious_percent_is_rejected_without_protocol_calls_or_leaks():
    selector = LangGraphCanarySelector(percent=ExplodingPercent())
    decision = selector.select(
        workflow_name="admet_assessment",
        trace_id=ExplodingKey(),
        idempotency_key=ExplodingKey(),
        executor_supported=True,
    )

    assert decision.reason == "invalid_canary_percent"
    assert decision.percent == 0
    assert "PRIVATE" not in repr(selector)
    assert "_valid_percent" not in repr(selector)

    assert decision == CanaryDecision(
        backend="legacy",
        reason="invalid_canary_percent",
        bucket=None,
        percent=0,
    )


def test_non_allowlisted_workflow_fails_closed_before_executor_check():
    decision = _select(
        100,
        workflow_name="docking_simulation",
        executor_supported=False,
    )

    assert (decision.backend, decision.reason) == (
        "legacy",
        "workflow_not_allowlisted",
    )
    assert decision.bucket is None


@pytest.mark.parametrize("executor_supported", [False, None, 0, 1, "true"])
def test_executor_must_be_literal_true(executor_supported):
    decision = _select(100, executor_supported=executor_supported)

    assert (decision.backend, decision.reason) == (
        "legacy",
        "unsupported_delegated_executor",
    )
    assert decision.bucket is None


@pytest.mark.parametrize(
    ("trace_id", "idempotency_key"),
    [(None, None), ("", ""), (None, ""), ("", None)],
)
def test_missing_canary_key_fails_closed(trace_id, idempotency_key):
    decision = _select(
        100,
        trace_id=trace_id,
        idempotency_key=idempotency_key,
    )

    assert (decision.backend, decision.reason, decision.bucket) == (
        "legacy",
        "missing_canary_key",
        None,
    )


@pytest.mark.parametrize("invalid_key", [7, object()])
@pytest.mark.parametrize("field", ["trace_id", "idempotency_key"])
def test_non_string_keys_fail_closed(invalid_key, field):
    values = {
        "trace_id": "valid-trace",
        "idempotency_key": None,
    }
    values[field] = invalid_key

    decision = _select(100, **values)

    assert (decision.backend, decision.reason, decision.bucket) == (
        "legacy",
        "invalid_canary_key",
        None,
    )


@pytest.mark.parametrize(
    "invalid_key_factory",
    [ExplodingKey, lambda: ExplodingStrSubclass("PRIVATE-subclass")],
    ids=["object", "str-subclass"],
)
@pytest.mark.parametrize("field", ["trace_id", "idempotency_key"])
def test_adversarial_keys_are_rejected_without_calling_user_protocols(
    invalid_key_factory,
    field,
):
    values = {
        "trace_id": "valid-trace",
        "idempotency_key": None,
    }
    values[field] = invalid_key_factory()

    decision = _select(100, **values)

    assert decision == CanaryDecision(
        backend="legacy",
        reason="invalid_canary_key",
        bucket=None,
        percent=100,
    )
    assert "PRIVATE" not in json.dumps(decision.to_dict())


def test_invalid_nonempty_idempotency_key_never_falls_back_to_valid_trace():
    decision = _select(
        100,
        trace_id="valid-fallback-trace",
        idempotency_key=7,
    )

    assert decision.reason == "invalid_canary_key"
    assert decision.bucket is None


def test_isolated_surrogate_fails_closed_without_unicode_error():
    decision = _select(
        100,
        trace_id="\ud800",
        idempotency_key=None,
    )

    assert decision.reason == "invalid_canary_key"
    assert decision.bucket is None


def test_canary_key_length_limit_is_inclusive():
    assert MAX_CANARY_KEY_LENGTH == 4096

    maximum = _select(100, trace_id="a" * MAX_CANARY_KEY_LENGTH)
    oversized = _select(100, trace_id="a" * (MAX_CANARY_KEY_LENGTH + 1))

    assert maximum.backend == "langgraph"
    assert maximum.bucket == _bucket("a" * MAX_CANARY_KEY_LENGTH)
    assert oversized.reason == "invalid_canary_key"
    assert oversized.bucket is None


def test_unicode_key_is_stable_and_utf8_hashed():
    key = "稳定分桶🧪"

    first = _select(100, trace_id=key)
    second = _select(100, trace_id=key)

    assert first.bucket == second.bucket == _bucket(key)


def test_idempotency_key_takes_priority_and_is_stable_across_traces():
    first = _select(
        100,
        trace_id="trace-a",
        idempotency_key="private-request-key",
    )
    second = _select(
        100,
        trace_id="trace-b",
        idempotency_key="private-request-key",
    )

    assert first.backend == second.backend == "langgraph"
    assert first.bucket == second.bucket == _bucket("private-request-key")
    assert first.bucket != _bucket("trace-a") or first.bucket != _bucket("trace-b")


def test_empty_idempotency_key_falls_back_to_trace_id():
    decision = _select(
        100,
        trace_id="fallback-trace",
        idempotency_key="",
    )

    assert decision.bucket == _bucket("fallback-trace")


def test_decision_serialization_is_public_and_does_not_leak_keys():
    selector = LangGraphCanarySelector(percent=100)
    decision = selector.select(
        workflow_name="admet_assessment",
        trace_id="private-trace-id",
        idempotency_key="private-request-key",
        executor_supported=True,
    )

    serialized = decision.to_dict()
    encoded = json.dumps(serialized, sort_keys=True)

    assert serialized == {
        "backend": "langgraph",
        "reason": "canary_selected",
        "bucket": _bucket("private-request-key"),
        "percent": 100,
    }
    assert "private-trace-id" not in encoded
    assert "private-request-key" not in encoded
    assert "private-trace-id" not in repr(selector)
    assert "private-request-key" not in repr(selector)


def test_canary_decision_is_frozen():
    decision = _select(100)

    with pytest.raises(FrozenInstanceError):
        decision.backend = "legacy"


def test_selector_configuration_is_frozen():
    selector = LangGraphCanarySelector(percent=0)

    with pytest.raises((FrozenInstanceError, AttributeError)):
        selector.percent = 100
    with pytest.raises((FrozenInstanceError, AttributeError)):
        selector._valid_percent = True


def test_selectors_do_not_share_mutable_state():
    first = LangGraphCanarySelector(percent=0)
    second = LangGraphCanarySelector(percent=100)

    first_decision = first.select(
        workflow_name="admet_assessment",
        trace_id="shared-trace",
        idempotency_key=None,
        executor_supported=True,
    )
    second_decision = second.select(
        workflow_name="admet_assessment",
        trace_id="shared-trace",
        idempotency_key=None,
        executor_supported=True,
    )

    assert first_decision.percent == 0
    assert second_decision.percent == 100
    assert first_decision.backend == "legacy"
    assert second_decision.backend == "langgraph"
    assert isinstance(LANGGRAPH_CANARY_WORKFLOWS, frozenset)
