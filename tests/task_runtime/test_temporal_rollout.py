from __future__ import annotations

from copy import copy
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import pickle
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


def _observation_payload() -> dict[str, object]:
    payload = _payload(5, "observation")
    payload.update(
        {
            "window_start": "2026-08-21T08:00:00+00:00",
            "window_end": "2026-08-21T10:00:00+00:00",
            "sample_count": 20,
            "counts": {"succeeded": 20, "failed": 0},
            "rates": {"failure": 0.0},
            "latency_seconds": {"p50": 30.0, "p95": 40.0},
            "error_code_distribution": {},
            "worker_health": {"available": True, "age_seconds": 5.0},
            "infrastructure": {
                "temporal": True,
                "namespace": True,
                "queue": True,
            },
            "blocking_alerts": [],
            "backup_verified": True,
            "tasks": [
                {
                    "task_fingerprint": "task-01",
                    "workflow_fingerprint": "workflow-01",
                    "status": "succeeded",
                    "latency_seconds": 30.0,
                }
            ],
        }
    )
    return payload


def _preflight_payload() -> dict[str, object]:
    payload = _payload(0, "preflight")
    payload.update(
        {
            "deployment": {"status": "passed"},
            "contract": {"status": "passed", "run_count": 3},
            "real": {
                "status": "passed",
                "run_count": 3,
                "scientific_execution": True,
            },
            "infrastructure": {
                "temporal": True,
                "namespace": True,
                "queue": True,
                "worker": True,
            },
            "blocking_alerts": [],
            "backup": {"status": "passed"},
        }
    )
    return payload


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


def test_full_observation_report_is_hashed_in_full_and_authorizes_observation():
    payload = _observation_payload()

    evidence = RolloutEvidence.from_payload(payload, _digest(payload))
    decision = validate_transition(5, 10, evidence, now=GENERATED_AT)

    assert decision.allowed is True


def test_observation_extra_fields_are_integrity_protected():
    payload = _observation_payload()
    expected_sha256 = _digest(payload)
    rates = payload["rates"]
    assert isinstance(rates, dict)
    rates["failure"] = 0.5

    _assert_rejected(payload, expected_sha256)


def test_full_preflight_report_authorizes_only_zero_to_five():
    payload = _preflight_payload()
    evidence = RolloutEvidence.from_payload(payload, _digest(payload))

    decision = validate_transition(0, 5, evidence, now=GENERATED_AT)

    assert decision.allowed is True
    with pytest.raises(EvidenceError):
        validate_transition(5, 10, evidence, now=GENERATED_AT)


def test_preflight_extra_fields_are_integrity_protected():
    payload = _preflight_payload()
    expected_sha256 = _digest(payload)
    deployment = payload["deployment"]
    assert isinstance(deployment, dict)
    deployment["status"] = "failed"

    _assert_rejected(payload, expected_sha256)


@pytest.mark.parametrize(
    "payload_factory",
    [_observation_payload, _preflight_payload],
)
def test_each_stage_rejects_unknown_top_level_report_fields(payload_factory):
    payload = payload_factory()
    payload["future_unplanned_section"] = {"status": "passed"}

    _assert_rejected(payload, _digest(payload))


def _assert_generic_payload_error(payload: dict[str, object]) -> None:
    with pytest.raises(EvidenceError) as failure:
        RolloutEvidence.from_payload(payload, "0" * 64)
    assert str(failure.value) == "invalid evidence payload"


def test_deeply_nested_report_is_normalized_to_generic_evidence_error():
    payload = _observation_payload()
    nested: dict[str, object] = {}
    payload["infrastructure"] = nested
    for _ in range(1000):
        child: dict[str, object] = {}
        nested["child"] = child
        nested = child

    _assert_generic_payload_error(payload)


def test_deeply_nested_list_is_normalized_to_generic_evidence_error():
    payload = _observation_payload()
    nested: list[object] = []
    payload["blocking_alerts"] = nested
    for _ in range(1000):
        child: list[object] = []
        nested.append(child)
        nested = child

    _assert_generic_payload_error(payload)


def test_report_with_excessive_nodes_is_rejected_before_serialization():
    payload = _observation_payload()
    payload["blocking_alerts"] = [None] * 200_001

    _assert_generic_payload_error(payload)


@pytest.mark.parametrize(
    "container,node_budget",
    [
        ([{}, {}], 2),
        ({"first": {}, "second": {}}, 4),
    ],
    ids=["list-values", "dict-keys-and-values"],
)
def test_container_child_budget_is_checked_before_child_traversal(
    monkeypatch,
    container,
    node_budget,
):
    child_ids = {
        id(value)
        for value in (
            container if isinstance(container, list) else container.values()
        )
    }
    real_id = id
    real_len = len
    checked_container_length = False
    visited_children: list[int] = []

    def recording_id(value):
        identity = real_id(value)
        if identity in child_ids:
            visited_children.append(identity)
        return identity

    def recording_len(value):
        nonlocal checked_container_length
        if value is container:
            checked_container_length = True
        return real_len(value)

    monkeypatch.setattr(rollout, "MAX_EVIDENCE_NODES", node_budget)
    monkeypatch.setattr(rollout, "id", recording_id, raising=False)
    monkeypatch.setattr(rollout, "len", recording_len, raising=False)

    with pytest.raises(EvidenceError) as failure:
        rollout._validate_json_structure(container)

    assert str(failure.value) == "invalid evidence payload"
    assert checked_container_length is True
    assert visited_children == []


def test_oversized_canonical_report_is_rejected_generically():
    payload = _observation_payload()
    payload["infrastructure"] = {"summary": "x" * (5 * 1024 * 1024)}

    _assert_generic_payload_error(payload)


@pytest.mark.parametrize(
    "nested_value",
    [
        {1: "not-a-string-key"},
        ("tuple-is-not-a-json-list",),
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": float("-inf")},
    ],
)
def test_invalid_nested_json_shapes_raise_generic_evidence_error(nested_value):
    payload = _observation_payload()
    payload["infrastructure"] = nested_value

    _assert_generic_payload_error(payload)


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
def test_direct_forged_evidence_cannot_authorize_any_promotion(
    current,
    target,
    stage,
):
    forged = RolloutEvidence(
        schema_version=999,
        stage=stage,
        status="failed",
        current_level=current,
        generated_at=GENERATED_AT,
        gates={"all_required_gates": "failed"},
        sha256="invalid",
    )

    with pytest.raises(EvidenceError):
        validate_transition(current, target, forged, now=GENERATED_AT)


def test_direct_valid_looking_evidence_is_not_trusted_without_payload_validation():
    payload = _payload(5, "observation")
    direct = RolloutEvidence(
        schema_version=1,
        stage="observation",
        status="passed",
        current_level=5,
        generated_at=GENERATED_AT,
        gates={"all_required_gates": "passed"},
        sha256=_digest(payload),
    )

    with pytest.raises(EvidenceError):
        validate_transition(5, 10, direct, now=GENERATED_AT)


@pytest.mark.parametrize(
    "field,value,current,target",
    [
        ("schema_version", 999, 5, 10),
        ("stage", type("ObservationStage", (str,), {})("observation"), 5, 10),
        ("status", "failed", 5, 10),
        ("current_level", 10, 10, 25),
        ("generated_at", GENERATED_AT + timedelta(minutes=1), 5, 10),
        ("gates", {"all_required_gates": "passed"}, 5, 10),
        ("sha256", "0" * 64, 5, 10),
    ],
)
def test_validated_evidence_rejects_security_critical_field_tampering(
    field,
    value,
    current,
    target,
):
    evidence = _evidence(5, "observation")
    object.__setattr__(evidence, field, value)

    with pytest.raises(EvidenceError):
        validate_transition(
            current,
            target,
            evidence,
            now=GENERATED_AT + timedelta(minutes=5),
        )


def test_copying_private_provenance_marker_to_direct_forgery_is_rejected():
    trusted = _evidence(5, "observation")
    forged = RolloutEvidence(
        schema_version=999,
        stage="observation",
        status="failed",
        current_level=5,
        generated_at=GENERATED_AT,
        gates={"all_required_gates": "failed"},
        sha256="invalid",
    )
    private_state = {
        name: value for name, value in vars(trusted).items() if name.startswith("_")
    }
    assert private_state
    for name, value in private_state.items():
        object.__setattr__(forged, name, value)

    with pytest.raises(EvidenceError):
        validate_transition(5, 10, forged, now=GENERATED_AT)


def test_dataclass_replacement_cannot_transfer_trust_to_modified_evidence():
    trusted = _evidence(5, "observation")
    forged = replace(
        trusted,
        schema_version=999,
        status="failed",
        gates={"all_required_gates": "failed"},
        sha256="invalid",
    )

    with pytest.raises(EvidenceError):
        validate_transition(5, 10, forged, now=GENERATED_AT)


def test_copying_untrusted_evidence_cannot_fabricate_trust():
    forged = RolloutEvidence(
        schema_version=999,
        stage="observation",
        status="failed",
        current_level=5,
        generated_at=GENERATED_AT,
        gates={"all_required_gates": "failed"},
        sha256="invalid",
    )

    with pytest.raises(EvidenceError):
        validate_transition(5, 10, copy(forged), now=GENERATED_AT)


def test_tampering_a_copy_of_validated_evidence_invalidates_provenance():
    copied = copy(_evidence(5, "observation"))
    object.__setattr__(copied, "status", "failed")

    with pytest.raises(EvidenceError):
        validate_transition(5, 10, copied, now=GENERATED_AT)


def test_transition_constant_time_verifies_public_digest_and_internal_content_seal(
    monkeypatch,
):
    evidence = _evidence(5, "observation")
    calls: list[tuple[str, str]] = []
    real_compare_digest = hmac.compare_digest

    def recording_compare_digest(actual: str, expected: str) -> bool:
        calls.append((actual, expected))
        return real_compare_digest(actual, expected)

    monkeypatch.setattr(rollout.hmac, "compare_digest", recording_compare_digest)

    decision = validate_transition(5, 10, evidence, now=GENERATED_AT)

    assert decision.allowed is True
    assert len(calls) == 2
    assert calls[0] == (evidence.sha256, evidence.sha256)
    assert calls[1][0] == calls[1][1]


def test_copying_validated_immutable_evidence_preserves_safe_use_and_value_semantics():
    trusted = _evidence(5, "observation")
    copied = copy(trusted)

    decision = validate_transition(5, 10, copied, now=GENERATED_AT)

    assert decision.allowed is True
    assert copied == trusted
    assert repr(copied) == repr(trusted)
    with pytest.raises(TypeError):
        copied.gates["all_required_gates"] = "failed"  # type: ignore[index]


def test_pickling_evidence_cannot_create_a_trusted_promotion_credential():
    trusted = _evidence(5, "observation")

    try:
        serialized = pickle.dumps(trusted)
    except (EvidenceError, pickle.PicklingError, TypeError):
        return

    restored = pickle.loads(serialized)
    with pytest.raises(EvidenceError):
        validate_transition(5, 10, restored, now=GENERATED_AT)


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
