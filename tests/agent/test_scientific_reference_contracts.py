"""Offline structural contracts; fixtures are not scientific predictions."""

import importlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
from uuid import UUID

import pytest

from src.agent.contracts.candidates import CandidateRecord


def _module():
    name = "src.agent.contracts.scientific_references"
    module = importlib.import_module(name) if importlib.util.find_spec(name) else None
    assert hasattr(module, "ScientificPresentation"), "ScientificPresentation is missing"
    return module


def _candidate(index=1, smiles="CCO"):
    return CandidateRecord.from_smiles(
        index, index, smiles, smiles,
        generation_provenance={"tool_name": "offline-contract-fixture"},
        metadata={"nested": ["preserved"]},
    ).to_dict()


def _inputs():
    return dict(
        source_trace_id="trace-1", source_version="a" * 64,
        source_status="partial",
        ordered_candidates=[
            {"observation_id": "observation-2", "candidate": _candidate(2, "CCN")},
            {"observation_id": "observation-1", "candidate": _candidate()},
        ],
        target="confirmed target", evidence=[{"source": "offline fixture", "ids": [1]}],
        warnings=["One source candidate failed validation"], created_at=1700000000.25,
    )


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _resign(value):
    value["revision"] = sha256(_canonical({k: v for k, v in value.items() if k != "revision"}).encode("utf-8")).hexdigest()
    return value


def test_public_contract_exists():
    assert callable(_module().reference_json)


def test_create_roundtrip_uuid_fixed_ttl_order_and_complete_candidate():
    cls = _module().ScientificPresentation
    inputs = _inputs()
    view = cls.create(**inputs)
    payload = view.to_dict()
    assert UUID(payload["presentation_id"]).version == 4
    assert payload["schema_version"] == 1
    assert payload["expires_at"] == inputs["created_at"] + 86400
    assert payload["source_status"] == "partial"
    for key, value in inputs.items():
        assert payload[key] == value
    assert payload["revision"] == _resign(dict(payload))["revision"]
    assert cls.from_dict(payload).to_dict() == payload
    assert view.ordered_keys == [(row["observation_id"], row["candidate"]["candidate_id"]) for row in inputs["ordered_candidates"]]
    assert cls.create(**inputs).presentation_id != view.presentation_id


def test_frozen_value_and_all_returned_containers_are_detached():
    cls = _module().ScientificPresentation
    inputs = _inputs()
    view = cls.create(**inputs)
    baseline = view.to_dict()
    inputs["evidence"][0]["ids"].append(2)
    inputs["ordered_candidates"][0]["candidate"]["metadata"]["nested"].append("changed")
    output = view.to_dict()
    output["evidence"][0]["ids"].append(3)
    output["ordered_candidates"].reverse()
    view.ordered_keys.clear()
    assert view.to_dict() == baseline
    with pytest.raises((FrozenInstanceError, AttributeError)):
        view.revision = "b" * 64
    with pytest.raises(TypeError):
        view.ordered_candidates[0]["candidate"]["metadata"]["nested"][0] = "changed"
    restored = cls.from_dict(baseline)
    baseline["warnings"].append("changed")
    assert restored.to_dict() == view.to_dict()


@pytest.mark.parametrize("field", list(_inputs()) + ["presentation_id", "schema_version", "expires_at"])
def test_from_dict_rejects_unverified_revision_on_any_field_change(field):
    cls = _module().ScientificPresentation
    payload = cls.create(**_inputs()).to_dict()
    payload[field] = None if payload[field] is not None else "changed"
    with pytest.raises(ValueError):
        cls.from_dict(payload)


@pytest.mark.parametrize("field,value", [
    ("schema_version", True), ("schema_version", 1.0), ("schema_version", "1"),
    ("presentation_id", "not-a-uuid"), ("source_version", "A" * 64),
    ("source_version", "a" * 63), ("source_trace_id", " "),
    ("source_status", "failed"), ("source_status", "rejected"),
    ("source_status", "cancelled"), ("target", " "), ("target", {}),
    ("evidence", {}), ("evidence", ["not-an-object"]),
    ("warnings", "warning"), ("warnings", [1]),
    ("created_at", True), ("created_at", "1700000000"),
    ("expires_at", True), ("expires_at", 1700000000.25 + 86401),
])
def test_from_dict_checks_schema_even_with_recomputed_revision(field, value):
    cls = _module().ScientificPresentation
    payload = cls.create(**_inputs()).to_dict()
    payload[field] = value
    with pytest.raises(ValueError):
        cls.from_dict(_resign(payload))


@pytest.mark.parametrize("change", ["extra", "missing", "missing-revision", "uppercase-revision"])
def test_exact_fields_and_revision_are_required(change):
    cls = _module().ScientificPresentation
    payload = cls.create(**_inputs()).to_dict()
    if change == "extra":
        payload["owner"] = "untrusted"
        _resign(payload)
    elif change == "missing":
        del payload["target"]
        _resign(payload)
    elif change == "missing-revision":
        del payload["revision"]
    else:
        payload["revision"] = payload["revision"].upper()
    with pytest.raises(ValueError):
        cls.from_dict(payload)


@pytest.mark.parametrize("bad_time", [True, None, "now", float("nan"), float("inf"), -float("inf"), 1e300])
def test_create_rejects_invalid_or_unrepresentable_fixed_ttl(bad_time):
    inputs = _inputs()
    inputs["created_at"] = bad_time
    with pytest.raises(ValueError):
        _module().ScientificPresentation.create(**inputs)


@pytest.mark.parametrize("problem", ["empty", "too-many", "duplicate-key", "duplicate-smiles", "extra-row-field", "missing-row-field", "bad-candidate", "empty-observation", "tuple"])
def test_candidate_projection_is_strict(problem):
    inputs = _inputs()
    rows = inputs["ordered_candidates"]
    if problem == "empty":
        inputs["ordered_candidates"] = []
    elif problem == "too-many":
        inputs["ordered_candidates"] = [{"observation_id": str(i), "candidate": _candidate(i + 1, "C" * (i + 1))} for i in range(33)]
    elif problem == "duplicate-key":
        rows.append(rows[0])
    elif problem == "duplicate-smiles":
        rows.append({"observation_id": "different", "candidate": _candidate(3, "CCO")})
    elif problem == "extra-row-field":
        rows[0]["owner"] = "untrusted"
    elif problem == "missing-row-field":
        del rows[0]["observation_id"]
    elif problem == "bad-candidate":
        rows[0]["candidate"]["validation"]["valid"] = False
    elif problem == "empty-observation":
        rows[0]["observation_id"] = " "
    else:
        inputs["ordered_candidates"] = tuple(rows)
    with pytest.raises(ValueError):
        _module().ScientificPresentation.create(**inputs)


def test_32_candidates_succeeded_and_no_target_are_supported():
    inputs = _inputs()
    inputs.update(source_status="succeeded", target=None, warnings=[], evidence=[])
    inputs["ordered_candidates"] = [{"observation_id": str(i), "candidate": _candidate(i + 1, "C" * (i + 1))} for i in range(32)]
    assert len(_module().ScientificPresentation.create(**inputs).ordered_keys) == 32


def test_reference_json_is_canonical_for_arbitrary_namespace_objects_and_lists():
    encode = _module().reference_json
    value = {"views": [{"confirmed": False, "presentation": {"z": "分子", "a": None}}]}
    assert encode(value) == _canonical(value)
    assert encode(value["views"]) == _canonical(value["views"])
    shared = [1]
    assert encode([shared, shared]) == "[[1],[1]]"


@pytest.mark.parametrize("kind", ["text", "utf8", "escaped", "width", "nodes", "depth", "cycle", "huge-int"])
def test_bounds_reject_before_json_serializers_or_secret_scans(monkeypatch, kind):
    module = _module()
    redaction = importlib.import_module("src.agent.persistence.redaction")
    if kind == "text":
        value = ["x" * (512 * 1024)]
    elif kind == "utf8":
        value = ["分" * (180 * 1024)]
    elif kind == "escaped":
        value = ["\x00" * (100 * 1024)]
    elif kind == "width":
        value = [None] * 65536
    elif kind == "nodes":
        shared = [None] * 100
        value = [shared] * 700
    elif kind == "depth":
        value = []
        for _ in range(100):
            value = [value]
    elif kind == "cycle":
        value = []
        value.append(value)
    else:
        value = [1 << (512 * 1024 * 4)]

    def forbidden(*args, **kwargs):
        raise AssertionError("unbounded data reached serializer or secret traversal")

    with monkeypatch.context() as patch:
        patch.setattr(json, "dumps", forbidden)
        patch.setattr(json.JSONEncoder, "iterencode", forbidden)
        patch.setattr(redaction, "contains_secret_material", forbidden)
        patch.setattr(redaction, "redact_sensitive", forbidden)
        with pytest.raises(ValueError):
            module.reference_json(value)


def test_json_byte_and_node_limits_are_inclusive():
    encode = _module().reference_json
    assert len(encode("x" * (512 * 1024 - 2)).encode("utf-8")) == 512 * 1024
    assert json.loads(encode([None] * 65535)) == [None] * 65535


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "key"}, (1,), {1}, object(), "\ud800"])
def test_json_rejects_non_json_or_invalid_unicode(value):
    with pytest.raises(ValueError):
        _module().reference_json(value)


@pytest.mark.parametrize("location", ["target", "warning", "evidence", "candidate", "key", "redaction-change"])
def test_credentials_and_redaction_changes_rejected_without_echo(location):
    module = _module()
    inputs = _inputs()
    synthetic = "sk" + "-" + "SYNTHETIC" * 4
    if location == "target":
        inputs["target"] = synthetic
    elif location == "warning":
        inputs["warnings"] = ["password" + "=synthetic-value"]
    elif location == "evidence":
        inputs["evidence"] = [{"api-key": "synthetic-value"}]
    elif location == "candidate":
        inputs["ordered_candidates"][0]["candidate"]["metadata"]["note"] = synthetic
    elif location == "key":
        inputs["evidence"] = [{synthetic: "value"}]
    else:
        inputs["evidence"] = [{"token_count": 2}]
    rejected = False
    try:
        module.ScientificPresentation.create(**inputs)
    except ValueError as exc:
        rejected = True
        assert synthetic not in str(exc)
        assert "synthetic-value" not in str(exc)
    assert rejected, "credential-bearing reference must be rejected"


def test_molecular_slashes_and_benign_evidence_urls_are_preserved():
    inputs = _inputs()
    inputs["ordered_candidates"] = [{"observation_id": "obs", "candidate": _candidate(1, "F/C=C/F")}]
    inputs["evidence"] = [{"url": "https://example.org/papers/1", "path": "/data/structures/1"}]
    assert _module().ScientificPresentation.create(**inputs).to_dict()["evidence"] == inputs["evidence"]


@pytest.mark.parametrize("entrypoint", ["create", "from_dict"])
@pytest.mark.parametrize("kind", ["oversized", "cycle", "deep"])
def test_presentation_bounds_precede_candidate_copy_and_secret_checks(monkeypatch, entrypoint, kind):
    module = _module()
    inputs = _inputs()
    payload = inputs if entrypoint == "create" else module.ScientificPresentation.create(**inputs).to_dict()
    metadata = payload["ordered_candidates"][0]["candidate"]["metadata"]
    if kind == "oversized":
        metadata["value"] = "x" * (512 * 1024)
    elif kind == "cycle":
        metadata["value"] = metadata
    else:
        value = []
        for _ in range(100):
            value = [value]
        metadata["value"] = value
    redaction = importlib.import_module("src.agent.persistence.redaction")

    def forbidden(*args, **kwargs):
        raise AssertionError("unbounded input reached downstream processing")

    with monkeypatch.context() as patch:
        patch.setattr(json, "dumps", forbidden)
        patch.setattr(CandidateRecord, "from_dict", forbidden)
        patch.setattr(redaction, "contains_secret_material", forbidden)
        patch.setattr(redaction, "redact_sensitive", forbidden)
        with pytest.raises(ValueError):
            if entrypoint == "create":
                module.ScientificPresentation.create(**payload)
            else:
                module.ScientificPresentation.from_dict(payload)


def test_depth_boundary_and_scalar_types_are_preserved():
    value = [True, False, None, -12, -0.0, 0.125]
    for _ in range(31):
        value = [value]
    assert _module().reference_json(value) == _canonical(value)
    with pytest.raises(ValueError):
        _module().reference_json([value])


def test_arbitrary_namespace_total_budget_not_only_individual_views():
    module = _module()
    piece = {"text": "x" * (270 * 1024)}
    assert module.reference_json(piece)
    with pytest.raises(ValueError):
        module.reference_json([piece, piece])


def test_plain_json_guard_does_not_invoke_custom_hooks():
    class HostileDict(dict):
        def items(self):
            raise AssertionError("custom mapping hook called")

    class HostileValue:
        def __str__(self):
            raise AssertionError("custom string hook called")

        def __deepcopy__(self, memo):
            raise AssertionError("custom copy hook called")

    for value in (HostileDict(), HostileValue()):
        with pytest.raises(ValueError):
            _module().reference_json(value)


@pytest.mark.parametrize("first", ["contracts.scientific_references", "persistence.sqlite_store"])
def test_fresh_process_imports_contract_and_store_in_either_order(first):
    repo = Path(__file__).resolve().parents[2]
    code = """
import importlib, socket, sys
def blocked(*args, **kwargs):
    raise AssertionError('offline import must not connect to services')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
sys.path.insert(0, sys.argv[1])
importlib.import_module('src.agent.' + sys.argv[2])
contract = importlib.import_module('src.agent.contracts.scientific_references')
store = importlib.import_module('src.agent.persistence.sqlite_store')
assert hasattr(store, 'SQLiteAgentStateStore')
assert contract.reference_json({'namespace': []}) == '{"namespace":[]}'
assert 'src.web.app' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(repo), first],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
