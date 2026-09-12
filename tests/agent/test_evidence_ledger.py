import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest

from src.agent.contracts import (
    ScientificClaim,
    ToolProvenance,
    ToolResult,
    WorkflowArtifact,
)
from src.agent.evidence import (
    EvidenceLedger,
    UnsupportedClaimError,
    render_claim_template,
)


def test_ledger_registers_tool_execution_with_provenance():
    ledger = EvidenceLedger(trace_id="trace-evidence")
    evidence_id = ledger.register_tool_result(
        step_id="properties",
        input_digest="sha-input",
        result=ToolResult.success_result(
            "property_calculator",
            data={"molecular_weight": 180.16},
            provenance=ToolProvenance(
                tool_name="property_calculator",
                tool_version="1",
            ),
        ),
    )

    record = ledger.get(evidence_id)
    assert record["step_id"] == "properties"
    assert record["provenance"]["tool_name"] == "property_calculator"
    assert record["scientific_usable"] is True


def test_demo_or_fallback_result_is_not_scientifically_usable():
    ledger = EvidenceLedger(trace_id="trace-demo")
    evidence_id = ledger.register_tool_result(
        step_id="activity",
        input_digest="sha-input",
        result=ToolResult.success_result(
            "activity_predictor",
            data={"pic50": 7.1},
            provenance=ToolProvenance(
                tool_name="activity_predictor",
                demo_mode=True,
            ),
        ),
    )
    assert ledger.get(evidence_id)["scientific_usable"] is False


def test_ledger_accepts_and_renders_only_evidence_backed_claims():
    ledger = EvidenceLedger(trace_id="trace-claim")
    evidence_id = ledger.register_tool_result(
        step_id="properties",
        input_digest="sha-input",
        result=ToolResult.success_result(
            "property_calculator",
            data={"molecular_weight": 180.16},
            provenance=ToolProvenance(tool_name="property_calculator"),
        ),
    )
    claim = ScientificClaim(
        claim_id="mw-aspirin",
        subject="aspirin",
        metric="molecular_weight",
        value=180.16,
        unit="g/mol",
        evidence_ids=(evidence_id,),
    )
    ledger.accept_claim(claim)
    rendered = render_claim_template(
        "Aspirin molecular weight is {{claim:mw-aspirin}}.",
        ledger.claims(),
    )
    assert rendered == "Aspirin molecular weight is 180.16 g/mol."


def test_ledger_rejects_claim_with_unknown_evidence():
    ledger = EvidenceLedger(trace_id="trace-unknown-evidence")
    claim = ScientificClaim(
        claim_id="unsupported-pic50",
        subject="CCO",
        metric="pic50",
        value=7.1,
        unit=None,
        evidence_ids=("missing-evidence",),
    )
    with pytest.raises(ValueError, match="Unknown evidence"):
        ledger.accept_claim(claim)


def test_renderer_rejects_unknown_claim_id():
    with pytest.raises(UnsupportedClaimError, match="unknown"):
        render_claim_template("Result {{claim:unknown}}", {})


def _nested_result():
    # Synthetic contract fixture: no scientific tool or artifact file is used.
    return ToolResult.success_result(
        "property_calculator",
        data={"rows": [{"value": 1.0}]},
        evidence=[{"source": "test-fixture", "rows": [{"value": 1.0}]}],
        artifacts=[WorkflowArtifact(
            artifact_type="test",
            path="fixture.json",
            label="contract fixture",
            metadata={"rows": [{"value": 1.0}]},
        )],
        provenance=ToolProvenance(tool_name="property_calculator"),
    )


def _record_id(record):
    payload = {key: value for key, value in record.items() if key != "evidence_id"}
    digest = hashlib.sha256(json.dumps(
        payload, sort_keys=True, ensure_ascii=False, default=str,
    ).encode("utf-8")).hexdigest()
    return f"evidence-{digest[:20]}"


@pytest.mark.parametrize("surface", ["original", "get", "to_list"])
@pytest.mark.parametrize("field", ["evidence", "artifacts"])
def test_registered_nested_payload_is_isolated_from_mutation(surface, field):
    ledger = EvidenceLedger("immutable-evidence")
    result = _nested_result()
    evidence_id = ledger.register_tool_result("properties", "input", result)
    expected = deepcopy(ledger.get(evidence_id))
    if surface == "original":
        target = (result.evidence[0] if field == "evidence"
                  else result.artifacts[0].metadata)
    else:
        record = (ledger.get(evidence_id) if surface == "get"
                  else ledger.to_list()[0])
        target = (record["evidence"][0] if field == "evidence"
                  else record["artifacts"][0]["metadata"])

    target["rows"][0]["value"] = 999.0
    target["rows"].append({"value": -1.0})

    assert ledger.get(evidence_id) == expected
    assert ledger.to_list() == [expected]
    assert _record_id(ledger.get(evidence_id)) == evidence_id
    assert ledger.register_tool_result("properties", "input", _nested_result()) == evidence_id
    assert ledger.to_list() == [expected]


@pytest.mark.parametrize("surface", ["get", "to_list"])
def test_exported_provenance_cannot_make_demo_evidence_usable(surface):
    ledger = EvidenceLedger("immutable-provenance")
    result = _nested_result()
    result.provenance = replace(result.provenance, demo_mode=True)
    evidence_id = ledger.register_tool_result("properties", "input", result)
    expected = deepcopy(ledger.get(evidence_id))
    record = ledger.get(evidence_id) if surface == "get" else ledger.to_list()[0]

    record["provenance"]["demo_mode"] = False
    record["scientific_usable"] = True
    record["evidence_id"] = "forged"

    assert ledger.get(evidence_id) == expected
    with pytest.raises(ValueError, match="Scientifically unusable"):
        ledger.accept_claim(ScientificClaim(
            "fixture", "fixture", "fixture", 1.0, None, (evidence_id,),
        ))


def test_output_digest_uses_historical_canonical_json_bytes():
    data = {"z": [3, {"b": False, "a": None}], "a": "分子"}
    canonical = '{"a": "分子", "z": [3, {"a": null, "b": false}]}'
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert EvidenceLedger.output_digest(data) == expected
    assert EvidenceLedger("digest").output_digest(
        {"a": "分子", "z": [3, {"a": None, "b": False}]},
    ) == expected
    assert EvidenceLedger.output_digest({"a": "分子", "z": [3, None]}) != expected


@pytest.mark.parametrize("data", [None, True, 0, 1.5, "CCO", ["tool", "1", {}]])
def test_output_digest_accepts_json_values_used_by_decisions(data):
    expected = hashlib.sha256(json.dumps(
        data, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    assert EvidenceLedger.output_digest(data) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("nested", [False, True])
def test_output_digest_rejects_nonfinite_numbers(value, nested):
    with pytest.raises(ValueError):
        EvidenceLedger.output_digest({"rows": [value]} if nested else value)


@pytest.mark.parametrize("value", [{"not-json"}, object()])
def test_output_digest_does_not_stringify_non_json_values(value):
    with pytest.raises(TypeError):
        EvidenceLedger.output_digest(value)


@pytest.mark.parametrize("value", [(1, 2), b"CCO", bytearray(b"CCO"), {1, 2}, object()])
@pytest.mark.parametrize("nested", [False, True])
def test_output_digest_requires_json_types_recursively(value, nested):
    with pytest.raises(TypeError):
        EvidenceLedger.output_digest({"rows": [value]} if nested else value)


@pytest.mark.parametrize("key", [1, 1.5, None, False, (1, 2)])
@pytest.mark.parametrize("nested", [False, True])
def test_output_digest_rejects_non_string_keys_without_coercion(key, nested):
    value = {key: "value"}
    with pytest.raises(TypeError):
        EvidenceLedger.output_digest([{"rows": value}] if nested else value)


@pytest.mark.parametrize("kind,value", [
    (str, "CCO"), (int, 1), (float, 1.5), (list, [1]), (dict, {"a": 1}),
])
@pytest.mark.parametrize("nested", [False, True])
def test_output_digest_rejects_builtin_subclasses(kind, value, nested):
    value = type("JSONSubclass", (kind,), {})(value)
    with pytest.raises(TypeError):
        EvidenceLedger.output_digest({"rows": [value]} if nested else value)


def test_output_digest_rejects_string_subclass_keys():
    key = type("StringSubclass", (str,), {})("key")
    with pytest.raises(TypeError):
        EvidenceLedger.output_digest({"rows": [{key: 1}]})


@pytest.mark.parametrize("kind", ["list", "dict", "indirect"])
def test_output_digest_rejects_cycles_with_value_error(kind):
    value = [] if kind == "list" else {}
    if kind == "list":
        value.append(value)
    elif kind == "dict":
        value["cycle"] = value
    else:
        value["cycle"] = [value]
    with pytest.raises(ValueError, match="[Cc]ircular|[Cc]ycle"):
        EvidenceLedger.output_digest(value)


def test_output_digest_validates_before_json_encoding(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("non-JSON input reached the encoder")
    monkeypatch.setattr("src.agent.evidence.ledger.json.dumps", forbidden)
    with pytest.raises(TypeError):
        EvidenceLedger.output_digest({"rows": [(1, 2)]})


def test_output_digest_preserves_shared_acyclic_json_and_float_bytes():
    shared = {"unicode": "分子", "values": [True, False, None, -0.0, 1e-12]}
    value = [shared, shared, {"nested": shared}]
    expected = hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")).hexdigest()
    assert EvidenceLedger.output_digest(value) == expected


def _input_binding():
    return {
        "request_input_digest": "request-1",
        "input_evidence_ids": ["upstream-1", "upstream-2"],
        "operation_key": "operation-1",
    }


@pytest.mark.parametrize("field,value", [
    ("request_input_digest", "request-2"),
    ("input_evidence_ids", ["upstream-2", "upstream-1"]),
    ("operation_key", "operation-2"),
])
def test_each_input_binding_field_participates_in_evidence_identity(field, value):
    ledger = EvidenceLedger("binding-identity")
    result = _nested_result()
    result.quality = _input_binding()
    first_id = ledger.register_tool_result("properties", "input", result)
    result.quality[field] = value
    second_id = ledger.register_tool_result("properties", "input", result)

    assert first_id != second_id
    assert ledger.get(first_id)["input_binding"] == _input_binding()
    assert ledger.get(second_id)["input_binding"][field] == value
    assert _record_id(ledger.get(first_id)) == first_id
    assert _record_id(ledger.get(second_id)) == second_id
    assert ledger.register_tool_result("properties", "input", result) == second_id
    assert [row["evidence_id"] for row in ledger.to_list()] == sorted([first_id, second_id])


@pytest.mark.parametrize("surface", ["original", "get", "to_list"])
def test_input_binding_snapshot_is_isolated_from_mutation(surface):
    ledger = EvidenceLedger("binding-snapshot")
    result = _nested_result()
    result.quality = {**_input_binding(), "unrelated": {"debug": True}}
    evidence_id = ledger.register_tool_result("properties", "input", result)
    expected = deepcopy(ledger.get(evidence_id))
    assert expected["input_binding"] == _input_binding()
    if surface == "original":
        binding = result.quality
    else:
        record = ledger.get(evidence_id) if surface == "get" else ledger.to_list()[0]
        binding = record["input_binding"]

    binding["input_evidence_ids"].append("forged-upstream")
    binding["request_input_digest"] = "forged-request"
    binding["operation_key"] = "forged-operation"

    assert ledger.get(evidence_id) == expected
    assert ledger.to_list() == [expected]
    assert _record_id(ledger.get(evidence_id)) == evidence_id


@pytest.mark.parametrize("request_digest", ["request-1", ""])
def test_input_binding_preserves_missing_optional_fields_as_none(request_digest):
    ledger = EvidenceLedger("binding-optional")
    result = _nested_result()
    result.quality = {"request_input_digest": request_digest}
    evidence_id = ledger.register_tool_result("properties", "input", result)
    assert ledger.get(evidence_id)["input_binding"] == {
        "request_input_digest": request_digest,
        "input_evidence_ids": None,
        "operation_key": None,
    }


@pytest.mark.parametrize("quality", [{}, {
    "request_input_digest": None,
    "input_evidence_ids": ["ignored"],
    "operation_key": "ignored",
}])
def test_legacy_unbound_registration_keeps_payload_and_identity(quality):
    ledger = EvidenceLedger("legacy-identity")
    result = _nested_result()
    result.quality = quality
    evidence_id = ledger.register_tool_result("properties", "input", result)
    expected = {
        "trace_id": "legacy-identity",
        "step_id": "properties",
        "tool_name": result.tool_name,
        "status": "succeeded",
        "input_digest": "input",
        "provenance": result.provenance.to_dict(),
        "evidence": result.evidence,
        "artifacts": [artifact.to_dict() for artifact in result.artifacts],
        "scientific_usable": True,
    }
    assert ledger.get(evidence_id) == {"evidence_id": evidence_id, **expected}
    assert evidence_id == _record_id(expected)


@pytest.mark.parametrize("mode", ["demo", "fallback", "failed"])
def test_unusable_observations_cannot_support_claims(mode):
    ledger = EvidenceLedger("unusable")
    result = _nested_result()
    if mode == "failed":
        result = ToolResult(tool_name=result.tool_name, success=False, message="failed")
    else:
        result.provenance = replace(result.provenance, **{
            "demo_mode" if mode == "demo" else "fallback_used": True,
        })
    evidence_id = ledger.register_tool_result("properties", "input", result)
    assert ledger.get(evidence_id)["scientific_usable"] is False
    with pytest.raises(ValueError, match="Scientifically unusable"):
        ledger.accept_claim(ScientificClaim(
            "fixture", "fixture", "fixture", 1.0, None, (evidence_id,),
        ))
    assert ledger.claims() == {}


@pytest.mark.parametrize("surface", ["original", "claims", "serialized_claim"])
def test_accepted_claim_nested_value_is_isolated_from_mutation(surface):
    ledger = EvidenceLedger("claim-snapshot")
    evidence_id = ledger.register_tool_result("properties", "input", _nested_result())
    # Isolation only: the ledger does not prove this value against the evidence.
    claim = ScientificClaim(
        "fixture", "fixture", "fixture", {"rows": [{"value": 1.0}]},
        None, (evidence_id,),
    )
    expected = deepcopy(claim)
    ledger.accept_claim(claim)
    if surface == "original":
        value = claim.value
    elif surface == "claims":
        value = ledger.claims()[claim.claim_id].value
    else:
        value = ledger.claims()[claim.claim_id].to_dict()["value"]

    value["rows"][0]["value"] = 999.0

    assert ledger.claims() == {claim.claim_id: expected}
