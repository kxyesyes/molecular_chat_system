from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from types import MappingProxyType

import pytest

import src.task_runtime.rollout as rollout
from src.task_runtime.rollout import (
    ALLOWED_LEVELS,
    MAX_EVIDENCE_AGE,
    EvidenceError,
    RolloutDecision,
    RolloutEvidence,
    validate_transition,
)


GENERATED_AT = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)


def _payload(
    current_level: int = 5,
    stage: str = "observation",
    *,
    generated_at: object = GENERATED_AT.isoformat(),
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "stage": stage,
        "status": "passed",
        "current_level": current_level,
        "generated_at": generated_at,
        "gates": {"all_required_gates": "passed"},
    }


def _digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _evidence(
    current_level: int,
    stage: str,
    *,
    generated_at: datetime = GENERATED_AT,
) -> RolloutEvidence:
    payload = _payload(
        current_level,
        stage,
        generated_at=generated_at.isoformat(),
    )
    return RolloutEvidence.from_payload(payload, _digest(payload))


def _assert_rejected(payload: object, expected_sha256: object) -> None:
    with pytest.raises(EvidenceError):
        RolloutEvidence.from_payload(payload, expected_sha256)  # type: ignore[arg-type]


def test_rollout_contract_constants_and_error_type_are_stable():
    assert ALLOWED_LEVELS == (0, 5, 10, 25)
    assert MAX_EVIDENCE_AGE == timedelta(hours=1)
    assert issubclass(EvidenceError, ValueError)


def test_evidence_parses_canonical_json_and_uses_constant_time_hash_comparison(
    monkeypatch,
):
    payload = _payload()
    expected_sha256 = _digest(payload)
    calls: list[tuple[str, str]] = []
    real_compare_digest = hmac.compare_digest

    def recording_compare_digest(actual: str, expected: str) -> bool:
        calls.append((actual, expected))
        return real_compare_digest(actual, expected)

    monkeypatch.setattr(rollout.hmac, "compare_digest", recording_compare_digest)

    evidence = RolloutEvidence.from_payload(payload, expected_sha256)

    assert calls == [(expected_sha256, expected_sha256)]
    assert evidence == RolloutEvidence(
        schema_version=1,
        stage="observation",
        status="passed",
        current_level=5,
        generated_at=GENERATED_AT,
        gates={"all_required_gates": "passed"},
        sha256=expected_sha256,
    )


@pytest.mark.parametrize(
    "expected_sha256",
    [None, b"0" * 64, "", "0" * 63, "0" * 65, "g" * 64, "A" * 64],
)
def test_evidence_rejects_malformed_hashes(expected_sha256):
    payload = _payload()
    _assert_rejected(payload, expected_sha256)


def test_evidence_rejects_hash_mismatch():
    payload = _payload()
    _assert_rejected(payload, "0" * 64)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        (),
        "payload",
        MappingProxyType(_payload()),
    ],
)
def test_evidence_requires_a_plain_dict_payload(payload):
    _assert_rejected(payload, "0" * 64)


@pytest.mark.parametrize(
    "field",
    ["schema_version", "stage", "status", "current_level", "generated_at", "gates"],
)
def test_evidence_rejects_missing_required_fields(field):
    payload = _payload()
    del payload[field]
    _assert_rejected(payload, _digest(payload))


def test_evidence_rejects_unknown_top_level_fields():
    payload = _payload()
    payload["debug_path"] = "C:/private/evidence.json"
    _assert_rejected(payload, _digest(payload))


@pytest.mark.parametrize("schema_version", [True, False, 1.0, "1", 0, 2, None])
def test_evidence_requires_schema_version_exact_integer_one(schema_version):
    payload = _payload()
    payload["schema_version"] = schema_version
    _assert_rejected(payload, _digest(payload))


@pytest.mark.parametrize("status", ["failed", "PASSED", "", True, None, 1])
def test_evidence_requires_passed_status(status):
    payload = _payload()
    payload["status"] = status
    _assert_rejected(payload, _digest(payload))


@pytest.mark.parametrize(
    "stage",
    ["", "promotion", "Preflight", True, None, 1, ["observation"]],
)
def test_evidence_rejects_invalid_stages(stage):
    payload = _payload()
    payload["stage"] = stage
    _assert_rejected(payload, _digest(payload))


@pytest.mark.parametrize("level", [True, False, -1, 1, 6, 100, 5.0, "5", None])
def test_evidence_requires_an_exact_allowed_integer_level(level):
    payload = _payload()
    payload["current_level"] = level
    _assert_rejected(payload, _digest(payload))


@pytest.mark.parametrize(
    "generated_at",
    [
        None,
        0,
        True,
        "",
        "not-a-timestamp",
        "2026-08-21T10:00:00",
        ["2026-08-21T10:00:00+00:00"],
    ],
)
def test_evidence_rejects_malformed_or_offset_naive_timestamps(generated_at):
    payload = _payload(generated_at=generated_at)
    _assert_rejected(payload, _digest(payload))


def test_evidence_accepts_a_valid_non_utc_offset_timestamp():
    payload = _payload(generated_at="2026-08-21T18:00:00+08:00")

    evidence = RolloutEvidence.from_payload(payload, _digest(payload))

    assert evidence.generated_at.utcoffset() == timedelta(hours=8)


@pytest.mark.parametrize(
    "gates",
    [
        None,
        [],
        MappingProxyType({"all_required_gates": "passed"}),
        {},
        {"all_required_gates": "failed"},
        {"all_required_gates": True},
        {1: "passed", "all_required_gates": "passed"},
        {"all_required_gates": "passed", "nested": {"secret": "value"}},
        {"all_required_gates": "passed", "items": ["passed"]},
    ],
)
def test_evidence_requires_a_flat_string_gate_dict_with_required_gate_passed(gates):
    payload = _payload()
    payload["gates"] = gates
    try:
        expected_sha256 = _digest(payload)
    except (TypeError, ValueError):
        expected_sha256 = "0" * 64
    _assert_rejected(payload, expected_sha256)


def test_evidence_accepts_additional_flat_string_gates():
    payload = _payload()
    payload["gates"] = {
        "all_required_gates": "passed",
        "worker_health": "passed",
    }

    evidence = RolloutEvidence.from_payload(payload, _digest(payload))

    assert dict(evidence.gates) == payload["gates"]


def test_evidence_defensively_copies_gates_and_does_not_expose_a_mutable_dict():
    payload = _payload()
    gates = payload["gates"]
    evidence = RolloutEvidence.from_payload(payload, _digest(payload))

    assert isinstance(gates, dict)
    gates["all_required_gates"] = "failed"
    payload["gates"] = {"all_required_gates": "failed"}

    assert evidence.gates["all_required_gates"] == "passed"
    with pytest.raises(TypeError):
        evidence.gates["all_required_gates"] = "failed"  # type: ignore[index]


def test_direct_evidence_construction_also_defensively_copies_gates():
    gates = {"all_required_gates": "passed"}
    evidence = RolloutEvidence(
        schema_version=1,
        stage="observation",
        status="passed",
        current_level=5,
        generated_at=GENERATED_AT,
        gates=gates,
        sha256="a" * 64,
    )

    gates["all_required_gates"] = "failed"

    assert evidence.gates["all_required_gates"] == "passed"


def test_public_value_contracts_are_frozen():
    evidence = _evidence(5, "observation")
    decision = RolloutDecision(True, "evidence_passed", 5, 10)

    with pytest.raises(FrozenInstanceError):
        evidence.current_level = 10  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.allowed = False  # type: ignore[misc]


@pytest.mark.parametrize(
    "current,target,stage",
    [(0, 5, "preflight"), (5, 10, "observation"), (10, 25, "observation")],
)
def test_all_legal_promotions_require_current_stage_matched_evidence(
    current,
    target,
    stage,
):
    decision = validate_transition(
        current,
        target,
        _evidence(current, stage),
        now=GENERATED_AT + timedelta(minutes=5),
    )

    assert decision == RolloutDecision(True, "evidence_passed", current, target)


@pytest.mark.parametrize("current", [5, 10, 25])
def test_any_nonzero_level_can_rollback_to_zero_without_evidence(current):
    decision = validate_transition(current, 0, None)

    assert decision == RolloutDecision(True, "rollback_to_zero", current, 0)


@pytest.mark.parametrize("field", ["current", "target"])
@pytest.mark.parametrize("value", [True, False, -1, 1, 6, 100, 5.0, "5", None])
def test_transition_rejects_boolean_noninteger_and_unknown_levels(field, value):
    current = value if field == "current" else 5
    target = value if field == "target" else 10

    with pytest.raises(EvidenceError):
        validate_transition(current, target, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("level", ALLOWED_LEVELS)
def test_transition_rejects_same_level_writes(level):
    with pytest.raises(EvidenceError):
        validate_transition(level, level, None)


@pytest.mark.parametrize(
    "current,target",
    [
        (0, 10),
        (0, 25),
        (5, 25),
        (10, 5),
        (25, 5),
        (25, 10),
    ],
)
def test_transition_rejects_skips_and_nonzero_downgrades(current, target):
    with pytest.raises(EvidenceError):
        validate_transition(current, target, _evidence(current, "observation"))


@pytest.mark.parametrize("current,target", [(0, 5), (5, 10), (10, 25)])
def test_promotions_reject_missing_or_wrongly_typed_evidence(current, target):
    for evidence in (None, object(), {"status": "passed"}):
        with pytest.raises(EvidenceError):
            validate_transition(current, target, evidence)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "current,target,wrong_level,stage",
    [
        (0, 5, 5, "preflight"),
        (5, 10, 0, "observation"),
        (10, 25, 5, "observation"),
    ],
)
def test_promotions_reject_evidence_for_a_different_current_level(
    current,
    target,
    wrong_level,
    stage,
):
    with pytest.raises(EvidenceError):
        validate_transition(
            current,
            target,
            _evidence(wrong_level, stage),
            now=GENERATED_AT,
        )


@pytest.mark.parametrize(
    "current,target,wrong_stage",
    [(0, 5, "observation"), (5, 10, "preflight"), (10, 25, "preflight")],
)
def test_promotions_reject_evidence_from_the_wrong_stage(
    current,
    target,
    wrong_stage,
):
    with pytest.raises(EvidenceError):
        validate_transition(
            current,
            target,
            _evidence(current, wrong_stage),
            now=GENERATED_AT,
        )


def test_evidence_exactly_at_maximum_age_is_valid():
    evidence = _evidence(5, "observation")

    decision = validate_transition(
        5,
        10,
        evidence,
        now=GENERATED_AT + MAX_EVIDENCE_AGE,
    )

    assert decision.allowed is True


def test_evidence_older_than_maximum_age_is_stale():
    evidence = _evidence(5, "observation")

    with pytest.raises(EvidenceError):
        validate_transition(
            5,
            10,
            evidence,
            now=GENERATED_AT + MAX_EVIDENCE_AGE + timedelta(microseconds=1),
        )


def test_evidence_generated_exactly_at_now_is_valid():
    decision = validate_transition(
        5,
        10,
        _evidence(5, "observation"),
        now=GENERATED_AT,
    )

    assert decision.allowed is True


def test_any_future_evidence_is_rejected_without_clock_skew_tolerance():
    with pytest.raises(EvidenceError):
        validate_transition(
            5,
            10,
            _evidence(
                5,
                "observation",
                generated_at=GENERATED_AT + timedelta(microseconds=1),
            ),
            now=GENERATED_AT,
        )


@pytest.mark.parametrize(
    "now",
    ["2026-08-21T10:00:00+00:00", GENERATED_AT.replace(tzinfo=None)],
)
def test_explicit_now_must_be_a_timezone_aware_datetime(now):
    with pytest.raises(EvidenceError):
        validate_transition(5, 10, _evidence(5, "observation"), now=now)  # type: ignore[arg-type]


def test_evidence_errors_do_not_echo_secrets_or_machine_paths():
    secret = "sk-private-secret-value"
    private_path = "C:/Users/operator/private/evidence.json"
    payload = _payload(stage=f"{secret}:{private_path}")

    with pytest.raises(EvidenceError) as failure:
        RolloutEvidence.from_payload(payload, _digest(payload))

    message = str(failure.value)
    assert secret not in message
    assert private_path not in message
