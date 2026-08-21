from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from collections.abc import Iterator, Mapping
from types import MappingProxyType

import pytest

from src.task_runtime.observation import (
    ObservationPolicy,
    build_observation_report,
    percentile,
)
from src.task_runtime.rollout import EvidenceError, RolloutEvidence


END = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
GENERATED_AT = datetime(2026, 8, 21, 12, 1, tzinfo=timezone.utc)
BLOCKING_ALERT = "TemporalArtifactValidationFailure"
_DEFAULT_ALERTS = object()


def _sample(index: int, latency: float = 30.0) -> dict[str, object]:
    return {
        "task_fingerprint": f"private-task-{index:02d}",
        "workflow_fingerprint": f"private-workflow-{index:02d}",
        "status": "succeeded",
        "attempt": 1,
        "terminal_event_count": 1,
        "pose_count": 10,
        "binding_energy_valid": True,
        "binding_energy": -7.5,
        "artifact_exists": True,
        "artifact_hash_matches": True,
        "provenance_complete": True,
        "demo_mode": False,
        "fallback_used": False,
        "latency_seconds": latency,
        "error_code": None,
    }


def _report(
    samples: list[dict[str, object]] | None = None,
    *,
    current_level: int = 5,
    window_start: datetime | None = None,
    window_end: datetime = END,
    worker_health: dict[str, object] | None = None,
    infrastructure: dict[str, object] | None = None,
    blocking_alerts: list[object] | None | object = _DEFAULT_ALERTS,
    backup_verified: bool | None = True,
    policy: ObservationPolicy | None = None,
    generated_at: datetime = GENERATED_AT,
) -> dict[str, object]:
    return build_observation_report(
        samples if samples is not None else [_sample(index) for index in range(20)],
        current_level=current_level,
        window_start=window_start or END - timedelta(hours=2),
        window_end=window_end,
        worker_health=worker_health or {"available": True, "age_seconds": 5.0},
        infrastructure=infrastructure
        or {"temporal": True, "namespace": True, "queue": True},
        blocking_alerts=(
            [] if blocking_alerts is _DEFAULT_ALERTS else blocking_alerts
        ),
        backup_verified=backup_verified,
        policy=policy or ObservationPolicy(baseline_p95_seconds=40.0),
        generated_at=generated_at,
    )


def test_twenty_real_tasks_pass_all_observation_gates() -> None:
    report = _report()

    assert report["status"] == "passed"
    assert report["sample_count"] == 20
    assert report["counts"] == {
        "succeeded": 20,
        "failed": 0,
        "canceled": 0,
        "timed_out": 0,
    }
    assert report["rates"] == {"failure": 0.0}
    assert report["latency_seconds"] == {"p50": 30.0, "p95": 30.0}
    assert report["error_code_distribution"] == {}
    assert report["gates"]["all_required_gates"] == "passed"


def test_three_tasks_pass_at_exactly_twenty_four_hours() -> None:
    report = _report(
        [_sample(index) for index in range(3)],
        window_start=END - timedelta(hours=24),
    )

    assert report["status"] == "passed"
    assert report["gates"]["sample_window"] == "passed"


@pytest.mark.parametrize(
    ("duration", "count", "expected"),
    [
        (timedelta(hours=23, minutes=59, seconds=59), 3, "partial"),
        (timedelta(hours=24), 2, "partial"),
        (timedelta(hours=24), 0, "partial"),
    ],
)
def test_incomplete_sample_evidence_is_partial(
    duration: timedelta, count: int, expected: str
) -> None:
    report = _report(
        [_sample(index) for index in range(count)],
        window_start=END - duration,
    )

    assert report["status"] == expected
    assert report["gates"]["sample_window"] == "insufficient_evidence"
    assert report["gates"]["all_required_gates"] == expected


@pytest.mark.parametrize(
    ("sample_update", "gate"),
    [
        ({"attempt": 2}, "single_vina_attempt"),
        ({"terminal_event_count": 2}, "single_terminal_event"),
        ({"pose_count": 0}, "successful_pose"),
        ({"binding_energy_valid": False}, "finite_binding_energy"),
        ({"artifact_exists": False}, "artifact_integrity"),
        ({"artifact_hash_matches": False}, "artifact_integrity"),
        ({"provenance_complete": False}, "provenance_complete"),
        ({"demo_mode": True}, "real_execution"),
        ({"fallback_used": True}, "real_execution"),
    ],
)
def test_sample_violation_fails_even_when_quantity_is_insufficient(
    sample_update: dict[str, object], gate: str
) -> None:
    samples = [_sample(index) for index in range(3)]
    samples[0].update(sample_update)

    report = _report(samples)

    assert report["status"] == "failed"
    assert report["gates"][gate] == "failed"
    assert report["gates"]["sample_window"] == "insufficient_evidence"
    assert report["gates"]["all_required_gates"] == "failed"


def test_unknown_provenance_booleans_are_preserved_and_fail_closed() -> None:
    samples = [_sample(index) for index in range(20)]
    samples[0].update(
        {
            "provenance_complete": None,
            "demo_mode": None,
            "fallback_used": None,
        }
    )

    report = _report(samples)

    assert report["status"] == "failed"
    assert report["gates"]["provenance_complete"] == "failed"
    assert report["gates"]["real_execution"] == "failed"


@pytest.mark.parametrize(
    ("kwargs", "gate"),
    [
        ({"worker_health": {"available": False, "age_seconds": 1.0}}, "worker_health"),
        ({"worker_health": {"available": True, "age_seconds": 60.000001}}, "worker_health"),
        (
            {"infrastructure": {"temporal": False, "namespace": True, "queue": True}},
            "temporal_available",
        ),
        (
            {"infrastructure": {"temporal": True, "namespace": False, "queue": True}},
            "namespace_available",
        ),
        (
            {"infrastructure": {"temporal": True, "namespace": True, "queue": False}},
            "queue_available",
        ),
        ({"blocking_alerts": [BLOCKING_ALERT]}, "blocking_alerts"),
        ({"backup_verified": False}, "backup_verified"),
    ],
)
def test_operational_violation_fails_before_sample_insufficiency(
    kwargs: dict[str, object], gate: str
) -> None:
    report = _report([_sample(index) for index in range(3)], **kwargs)

    assert report["status"] == "failed"
    assert report["gates"][gate] == "failed"
    assert report["gates"]["sample_window"] == "insufficient_evidence"


def test_unknown_operational_evidence_is_partial_without_fabricating_failure() -> None:
    report = _report(
        worker_health={"available": None, "age_seconds": None},
        infrastructure={"temporal": None, "namespace": None, "queue": None},
        blocking_alerts=None,
        backup_verified=None,
    )

    assert report["status"] == "partial"
    assert report["worker_health"] == {"available": None, "age_seconds": None}
    assert report["infrastructure"] == {
        "temporal": None,
        "namespace": None,
        "queue": None,
    }
    assert report["blocking_alerts"] is None
    assert report["backup_verified"] is None
    for gate in (
        "worker_health",
        "temporal_available",
        "namespace_available",
        "queue_available",
        "blocking_alerts",
        "backup_verified",
    ):
        assert report["gates"][gate] == "insufficient_evidence"


def test_failure_rate_equality_passes_and_just_over_fails() -> None:
    at_limit = [_sample(index) for index in range(20)]
    at_limit[0].update(
        {"status": "failed", "pose_count": 0, "error_code": "DOCKING_PROCESS_FAILED"}
    )
    over_limit = [dict(sample) for sample in at_limit]
    over_limit[1].update(
        {"status": "timed_out", "pose_count": 0, "error_code": "TASK_HEARTBEAT_TIMEOUT"}
    )

    passing = _report(at_limit)
    failing = _report(over_limit)

    assert passing["rates"] == {"failure": 0.05}
    assert passing["gates"]["failure_rate"] == "passed"
    assert passing["status"] == "passed"
    assert failing["rates"] == {"failure": 0.1}
    assert failing["gates"]["failure_rate"] == "failed"
    assert failing["status"] == "failed"
    assert failing["error_code_distribution"] == {
        "DOCKING_PROCESS_FAILED": 1,
        "TASK_HEARTBEAT_TIMEOUT": 1,
    }


def test_unsuccessful_tasks_do_not_require_success_artifacts() -> None:
    samples = [_sample(index) for index in range(20)]
    samples[0].update(
        {
            "status": "failed",
            "pose_count": 0,
            "binding_energy_valid": False,
            "artifact_exists": False,
            "artifact_hash_matches": False,
            "error_code": "DOCKING_PROCESS_FAILED",
        }
    )

    report = _report(samples)

    assert report["status"] == "passed"
    assert report["gates"]["successful_pose"] == "passed"
    assert report["gates"]["finite_binding_energy"] == "passed"
    assert report["gates"]["artifact_integrity"] == "passed"


@pytest.mark.parametrize("attempt", [0, 1])
def test_unsuccessful_task_allows_zero_or_one_vina_attempt(attempt: int) -> None:
    samples = [_sample(index) for index in range(20)]
    samples[0].update(
        {
            "status": "failed",
            "attempt": attempt,
            "pose_count": 0,
            "binding_energy": None,
            "binding_energy_valid": False,
            "artifact_exists": False,
            "artifact_hash_matches": False,
            "error_code": "DOCKING_PROCESS_FAILED",
        }
    )

    report = _report(samples)

    assert report["status"] == "passed"
    assert report["gates"]["single_vina_attempt"] == "passed"
    assert report["rates"] == {"failure": 0.05}


def test_succeeded_zero_attempt_is_single_vina_attempt_failure() -> None:
    samples = [_sample(index) for index in range(20)]
    samples[0]["attempt"] = 0

    report = _report(samples)

    assert report["status"] == "failed"
    assert report["gates"]["single_vina_attempt"] == "failed"


@pytest.mark.parametrize("attempt", [2, 17])
def test_duplicate_vina_attempts_produce_hashed_failed_report(attempt: int) -> None:
    samples = [_sample(index) for index in range(20)]
    samples[0]["attempt"] = attempt

    report = _report(samples)

    assert report["status"] == "failed"
    assert report["gates"]["single_vina_attempt"] == "failed"
    assert report["gates"]["all_required_gates"] == "failed"
    payload = dict(report)
    digest = payload.pop("sha256")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert digest == hashlib.sha256(canonical).hexdigest()
    with pytest.raises(EvidenceError, match="evidence is not passed"):
        RolloutEvidence.from_payload(payload, digest)


@pytest.mark.parametrize("attempt", [True, False, -1, 1.0, "2", None])
def test_invalid_attempt_type_or_negative_is_rejected(attempt: object) -> None:
    sample = _sample(0)
    sample["attempt"] = attempt

    with pytest.raises(ValueError, match="invalid observation sample"):
        _report([sample])


@pytest.mark.parametrize(
    ("energy_present", "energy", "validity"),
    [
        (False, None, True),
        (True, None, True),
        (True, "-7.5", True),
        (True, True, True),
        (True, math.nan, True),
        (True, math.inf, True),
        (True, -7.5, False),
        (True, -7.5, 1),
    ],
)
def test_succeeded_task_requires_actual_finite_binding_energy_and_validity(
    energy_present: bool, energy: object, validity: object
) -> None:
    samples = [_sample(index) for index in range(20)]
    if energy_present:
        samples[0]["binding_energy"] = energy
    else:
        samples[0].pop("binding_energy")
    samples[0]["binding_energy_valid"] = validity

    report = _report(samples)

    assert report["status"] == "failed"
    assert report["gates"]["finite_binding_energy"] == "failed"
    assert all("binding_energy" not in task for task in report["tasks"])
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("binding_energy", [10**1000, -(10**1000)])
def test_unrepresentable_binding_energy_produces_hashed_failed_report(
    binding_energy: int,
) -> None:
    samples = [_sample(index) for index in range(20)]
    samples[0]["binding_energy"] = binding_energy

    report = _report(samples)

    assert report["status"] == "failed"
    assert report["gates"]["finite_binding_energy"] == "failed"
    payload = dict(report)
    digest = payload.pop("sha256")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert digest == hashlib.sha256(canonical).hexdigest()


def test_normal_finite_integer_binding_energy_passes() -> None:
    samples = [_sample(index) for index in range(20)]
    samples[0]["binding_energy"] = -8

    report = _report(samples)

    assert report["status"] == "passed"
    assert report["gates"]["finite_binding_energy"] == "passed"


@pytest.mark.parametrize("status", ["failed", "canceled", "timed_out"])
def test_unsuccessful_task_energy_is_not_required_or_exposed(status: str) -> None:
    samples = [_sample(index) for index in range(20)]
    samples[0].update(
        {
            "status": status,
            "attempt": 0,
            "pose_count": 0,
            "binding_energy": "private-energy-value",
            "binding_energy_valid": {"private": "value"},
            "artifact_exists": False,
            "artifact_hash_matches": False,
        }
    )

    report = _report(samples)

    assert report["status"] == "passed"
    assert report["gates"]["finite_binding_energy"] == "passed"
    assert "private-energy-value" not in json.dumps(report)


def test_latency_and_worker_threshold_equality_passes() -> None:
    report = _report(
        [_sample(index, latency=60.0) for index in range(20)],
        worker_health={"available": True, "age_seconds": 60.0},
    )

    assert report["status"] == "passed"
    assert report["gates"]["latency_p95"] == "passed"
    assert report["gates"]["worker_health"] == "passed"


@pytest.mark.parametrize("latency", [60.000001, 60.01])
def test_latency_just_over_effective_threshold_fails(latency: float) -> None:
    report = _report([_sample(index, latency=latency) for index in range(20)])

    assert report["status"] == "failed"
    assert report["gates"]["latency_p95"] == "failed"


def test_latency_uses_lower_baseline_multiplier_threshold() -> None:
    passing = _report(
        [_sample(index, latency=15.0) for index in range(20)],
        policy=ObservationPolicy(10.0),
    )
    failing = _report(
        [_sample(index, latency=15.000001) for index in range(20)],
        policy=ObservationPolicy(10.0),
    )

    assert passing["status"] == "passed"
    assert failing["gates"]["latency_p95"] == "failed"


def test_nearest_rank_percentile_has_deterministic_boundaries() -> None:
    values = list(range(1, 21))

    assert percentile([], 0.95) is None
    assert percentile(values, 0.01) == 1.0
    assert percentile(values, 0.50) == 10.0
    assert percentile(values, 0.95) == 19.0
    assert percentile(values, 1.0) == 20.0


@pytest.mark.parametrize("fraction", [False, 0, -0.1, 1.00001, math.nan, math.inf])
def test_percentile_rejects_invalid_fraction(fraction: object) -> None:
    with pytest.raises(ValueError, match="percentile"):
        percentile([1.0], fraction)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [True, math.nan, math.inf, -math.inf, "30"])
def test_percentile_rejects_non_finite_or_non_numeric_values(value: object) -> None:
    with pytest.raises(ValueError, match="percentile"):
        percentile([value], 0.5)  # type: ignore[list-item]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("baseline_p95_seconds", True),
        ("baseline_p95_seconds", math.nan),
        ("baseline_p95_seconds", math.inf),
        ("baseline_p95_seconds", 0),
        ("max_failure_rate", -0.01),
        ("max_failure_rate", 1.01),
        ("worker_stale_seconds", 0),
        ("max_absolute_p95_seconds", -1),
        ("min_task_count", True),
        ("min_task_count", 0),
        ("min_window_task_count", 1.5),
        ("min_window_hours", math.nan),
    ],
)
def test_observation_policy_rejects_invalid_thresholds(field: str, value: object) -> None:
    values: dict[str, object] = {"baseline_p95_seconds": 40.0}
    values[field] = value

    with pytest.raises(ValueError, match="observation policy"):
        ObservationPolicy(**values)  # type: ignore[arg-type]


def test_observation_policy_is_immutable() -> None:
    policy = ObservationPolicy(40.0)

    with pytest.raises(FrozenInstanceError):
        policy.max_failure_rate = 0.1  # type: ignore[misc]


@pytest.mark.parametrize("level", [True, 0, 15, 25.0, "5"])
def test_report_rejects_invalid_current_level(level: object) -> None:
    with pytest.raises(ValueError, match="observation level"):
        _report(current_level=level)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 8, 21, 1), END),
        (END - timedelta(hours=1), datetime(2026, 8, 21, 13)),
        (END, END),
        (END + timedelta(seconds=1), END),
    ],
)
def test_report_rejects_invalid_windows(start: datetime, end: datetime) -> None:
    with pytest.raises(ValueError, match="observation window"):
        _report(window_start=start, window_end=end)


def test_report_rejects_naive_generated_at() -> None:
    with pytest.raises(ValueError, match="generated_at"):
        _report(generated_at=datetime(2026, 8, 21, 12, 1))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        (
            "window_start",
            datetime.min.replace(tzinfo=timezone(timedelta(hours=14))),
            "invalid observation window",
        ),
        (
            "window_end",
            datetime.max.replace(tzinfo=timezone(-timedelta(hours=12))),
            "invalid observation window",
        ),
        (
            "generated_at",
            datetime.max.replace(tzinfo=timezone(-timedelta(hours=12))),
            "invalid generated_at",
        ),
    ],
)
def test_fixed_offset_datetime_overflow_is_generic(
    field: str, value: datetime, message: str
) -> None:
    with pytest.raises(ValueError, match=rf"^{message}$") as error:
        _report(**{field: value})

    assert str(value) not in str(error.value)


def test_valid_extreme_utc_datetimes_remain_supported() -> None:
    start = datetime.min.replace(tzinfo=timezone.utc)
    end = datetime.max.replace(tzinfo=timezone.utc)

    report = _report(window_start=start, window_end=end, generated_at=end)

    assert report["status"] == "passed"
    assert report["window_start"] == start.isoformat()
    assert report["window_end"] == end.isoformat()
    assert report["generated_at"] == end.isoformat()


def test_generated_at_equal_to_window_end_is_valid_across_timezones() -> None:
    report = _report(
        generated_at=datetime(2026, 8, 21, 20, 0, tzinfo=timezone(timedelta(hours=8)))
    )

    assert report["status"] == "passed"
    assert report["generated_at"] == END.isoformat()


def test_generated_at_one_microsecond_before_window_end_is_rejected() -> None:
    with pytest.raises(ValueError, match="generated_at"):
        _report(generated_at=END - timedelta(microseconds=1))


def test_future_window_cannot_produce_passed_evidence() -> None:
    with pytest.raises(ValueError, match="generated_at"):
        _report(window_end=END + timedelta(hours=1), generated_at=END)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("latency_seconds", True),
        ("latency_seconds", math.nan),
        ("latency_seconds", math.inf),
        ("latency_seconds", -0.1),
        ("pose_count", True),
        ("pose_count", 1.5),
        ("attempt", False),
        ("terminal_event_count", 1.0),
    ],
)
def test_report_rejects_invalid_sample_numeric_values(field: str, value: object) -> None:
    sample = _sample(0)
    sample[field] = value

    with pytest.raises(ValueError, match="observation sample"):
        _report([sample])


def test_huge_latency_is_normalized_to_generic_value_error() -> None:
    sample = _sample(0)
    sample["latency_seconds"] = 10**1000

    with pytest.raises(ValueError, match=r"^invalid observation sample$") as error:
        _report([sample])

    assert "1000" not in str(error.value)


def test_huge_worker_age_is_normalized_to_generic_value_error() -> None:
    with pytest.raises(ValueError, match=r"^invalid worker health$") as error:
        _report(worker_health={"available": True, "age_seconds": 10**1000})

    assert "1000" not in str(error.value)


def test_huge_policy_number_is_normalized_to_generic_value_error() -> None:
    with pytest.raises(ValueError, match=r"^invalid observation policy$") as error:
        ObservationPolicy(10**1000)

    assert "1000" not in str(error.value)


def test_report_redacts_unknown_nested_data_and_fingerprints_identifiers() -> None:
    sample = _sample(0)
    sample.update(
        {
            "prompt": {"nested": ["private prompt", {"dsn": "postgres://secret"}]},
            "smiles": "CCO",
            "path": "C:/private/ligand.sdf",
            "raw_exception": RuntimeError("private raw exception"),
            "unknown": {"user": {"email": "private@example.test"}},
            "error_code": "C:/private/raw-exception",
        }
    )
    worker = {
        "available": True,
        "age_seconds": 5.0,
        "worker_id": "private-worker",
        "dsn": "postgres://secret",
    }
    infrastructure = {
        "temporal": True,
        "namespace": True,
        "queue": True,
        "url": "http://private.example",
    }

    report = _report(
        [sample, *[_sample(index) for index in range(1, 20)]],
        worker_health=worker,
        infrastructure=infrastructure,
        blocking_alerts=[
            "../../private-alert",
            "TemporalUnknownPrivateAlert",
            BLOCKING_ALERT,
            BLOCKING_ALERT,
            {"name": "TemporalWorkerHeartbeatStale", "secret": "private"},
        ],
    )
    serialized = json.dumps(report, sort_keys=True)

    assert report["worker_health"] == {"available": True, "age_seconds": 5.0}
    assert report["infrastructure"] == {
        "temporal": True,
        "namespace": True,
        "queue": True,
    }
    assert report["blocking_alerts"] == [BLOCKING_ALERT]
    assert report["tasks"][0]["task_fingerprint"] == hashlib.sha256(
        b"private-task-00"
    ).hexdigest()
    assert report["tasks"][0]["workflow_fingerprint"] == hashlib.sha256(
        b"private-workflow-00"
    ).hexdigest()
    assert report["tasks"][0] == {
        "task_fingerprint": hashlib.sha256(b"private-task-00").hexdigest(),
        "workflow_fingerprint": hashlib.sha256(b"private-workflow-00").hexdigest(),
        "status": "succeeded",
        "latency_seconds": 30.0,
    }
    assert report["error_code_distribution"] == {"UNKNOWN": 1}
    for forbidden in (
        "private-task",
        "private-workflow",
        "private prompt",
        "postgres://",
        "CCO",
        "C:/private",
        "private raw exception",
        "private@example.test",
        "private-worker",
        "private.example",
        "private-alert",
        "TemporalUnknownPrivateAlert",
        "TemporalWorkerHeartbeatStale",
    ):
        assert forbidden not in serialized


class _ExplodingMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise RuntimeError("C:/private/raw-task-id")

    def __iter__(self) -> Iterator[str]:
        raise RuntimeError("C:/private/raw-task-id")

    def __len__(self) -> int:
        return 1


def test_sample_accepts_read_only_mapping_and_projects_immediately() -> None:
    samples = [MappingProxyType(_sample(index)) for index in range(20)]

    report = _report(samples)  # type: ignore[arg-type]

    assert report["status"] == "passed"
    assert report["sample_count"] == 20


def test_malicious_mapping_failure_is_generic_and_does_not_reflect_data() -> None:
    with pytest.raises(ValueError, match=r"^invalid observation sample$") as error:
        _report([_ExplodingMapping()])  # type: ignore[list-item]

    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("worker_health", "invalid worker health"),
        ("infrastructure", "invalid infrastructure"),
    ],
)
def test_malicious_summary_mapping_failure_is_generic(
    field: str, message: str
) -> None:
    with pytest.raises(ValueError, match=rf"^{message}$") as error:
        _report(**{field: _ExplodingMapping()})

    assert "private" not in str(error.value)


def test_report_has_exact_rollout_allowlist_stable_hash_and_evidence_compatibility() -> None:
    first = _report()
    second = _report()

    assert first == second
    assert set(first) == {
        "schema_version",
        "stage",
        "status",
        "current_level",
        "generated_at",
        "gates",
        "window_start",
        "window_end",
        "sample_count",
        "counts",
        "rates",
        "latency_seconds",
        "error_code_distribution",
        "worker_health",
        "infrastructure",
        "blocking_alerts",
        "backup_verified",
        "tasks",
        "sha256",
    }
    payload = dict(first)
    digest = payload.pop("sha256")
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    assert digest == hashlib.sha256(canonical).hexdigest()

    evidence = RolloutEvidence.from_payload(payload, digest)
    assert evidence.status == "passed"
    assert evidence.current_level == 5


def test_maximum_bounded_report_remains_rollout_evidence_compatible() -> None:
    report = _report([_sample(index) for index in range(10_000)])
    payload = dict(report)
    digest = payload.pop("sha256")

    evidence = RolloutEvidence.from_payload(payload, digest)

    assert evidence.status == "passed"
    assert len(report["tasks"]) == 10_000
