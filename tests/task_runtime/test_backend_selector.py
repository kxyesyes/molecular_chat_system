from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError

import pytest

from src.task_runtime.selector import BackendDecision, TemporalDockingSelector


def _bucket(task_id: str) -> int:
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100


def _task_id_for_bucket(predicate) -> str:
    for index in range(100_000):
        task_id = f"boundary-{index}"
        if predicate(_bucket(task_id)):
            return task_id
    raise AssertionError("unable to find a task id for bucket predicate")


def test_only_healthy_docking_enters_temporal() -> None:
    selector = TemporalDockingSelector(100)

    selected = selector.select("docking", "task-1", True)
    wrong_type = selector.select("agent_workflow", "task-1", True)
    unavailable = selector.select("docking", "task-1", False)

    assert selected.backend == "temporal"
    assert selected.reason == "canary_selected"
    assert (wrong_type.backend, wrong_type.reason) == (
        "local",
        "task_type_not_allowlisted",
    )
    assert (unavailable.backend, unavailable.reason) == (
        "local",
        "temporal_unavailable",
    )


@pytest.mark.parametrize("available", [1, 0, "true", object()])
def test_temporal_availability_must_be_literal_true(available: object) -> None:
    decision = TemporalDockingSelector(100).select("docking", "task-1", available)

    assert (decision.backend, decision.reason) == (
        "local",
        "temporal_unavailable",
    )


@pytest.mark.parametrize("percent", [-1, 101, 1.5, True, "100", None, object()])
def test_invalid_selector_percent_fails_closed(percent: object) -> None:
    selector = TemporalDockingSelector(percent)

    decision = selector.select("docking", "task-1", True)

    assert decision == BackendDecision(
        backend="local",
        reason="invalid_canary_percent",
        bucket=None,
        percent=0,
    )


def test_selector_is_stable_and_does_not_serialize_key() -> None:
    selector = TemporalDockingSelector(37)

    first = selector.select("docking", "private-task", True)
    second = selector.select("docking", "private-task", True)

    assert first == second
    assert first.bucket == _bucket("private-task")
    assert "private-task" not in repr(first)


def test_bucket_boundary_is_strictly_less_than_percent() -> None:
    below = _task_id_for_bucket(lambda bucket: bucket == 36)
    at_boundary = _task_id_for_bucket(lambda bucket: bucket == 37)
    selector = TemporalDockingSelector(37)

    assert selector.select("docking", below, True).backend == "temporal"
    assert selector.select("docking", at_boundary, True).backend == "local"


def test_zero_and_one_hundred_percent_boundaries() -> None:
    task_id = "task-boundary"

    zero = TemporalDockingSelector(0).select("docking", task_id, True)
    full = TemporalDockingSelector(100).select("docking", task_id, True)

    assert zero.backend == "local"
    assert zero.reason == "canary_not_selected"
    assert full.backend == "temporal"


class _StrSubclass(str):
    pass


class _IntSubclass(int):
    pass


@pytest.mark.parametrize(
    "task_id",
    [
        None,
        1,
        True,
        _IntSubclass(1),
        _StrSubclass("subclass"),
        "",
        "\ud800",
        "x" * 4097,
        b"task-id",
    ],
)
def test_adversarial_task_ids_fail_closed_without_serialization(task_id: object) -> None:
    decision = TemporalDockingSelector(100).select("docking", task_id, True)

    assert decision == BackendDecision(
        backend="local",
        reason="invalid_task_id",
        bucket=None,
        percent=100,
    )
    assert "subclass" not in repr(decision)
    assert "4097" not in repr(decision)


def test_task_id_length_boundary_is_accepted() -> None:
    task_id = "x" * 4096

    decision = TemporalDockingSelector(100).select("docking", task_id, True)

    assert decision.backend == "temporal"
    assert decision.bucket == _bucket(task_id)


def test_task_type_must_be_exact_docking_string() -> None:
    selector = TemporalDockingSelector(100)

    for task_type in (_StrSubclass("docking"), b"docking", 1, None):
        decision = selector.select(task_type, "task-1", True)
        assert (decision.backend, decision.reason) == (
            "local",
            "task_type_not_allowlisted",
        )


def test_backend_decision_is_frozen() -> None:
    decision = TemporalDockingSelector(100).select("docking", "task-1", True)

    with pytest.raises(FrozenInstanceError):
        decision.backend = "local"  # type: ignore[misc]
