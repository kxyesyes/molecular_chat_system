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


ALLOWLIST = frozenset(
    {
        "admet_assessment",
        "activity_prediction",
        "reverse_target_prediction",
        "target_database_search",
        "rag_search",
    }
)


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
    assert "private-trace-id" not in repr(vars(selector))
    assert "private-request-key" not in repr(vars(selector))


def test_canary_decision_is_frozen():
    decision = _select(100)

    with pytest.raises(FrozenInstanceError):
        decision.backend = "legacy"


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
