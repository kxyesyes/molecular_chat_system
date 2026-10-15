"""End-to-end reverse diagnostics through the real compatibility boundaries."""

from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.agent.contracts import AgentErrorCode, ToolResult
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.reverse_target_tool import ReverseTargetTool
from src.agent.tooling.factory import build_tool_registry
from tests.agent.test_reverse_receipt_consumption import (
    adapter_result, strict_source_factory,
)


PRIVATE = "private-transport-fixture C:/private/data.tsv CCO credential=not-a-real-key"


def execute(boundary, tool):
    if boundary == "compat":
        return execute_tool_compat(tool, "CCO")
    registry = build_tool_registry([tool])
    try:
        return registry.resolve(tool.name).execute("CCO")
    finally:
        registry.close()


@pytest.mark.parametrize("boundary", ["compat", "registry"])
@pytest.mark.parametrize("extras", [False, True], ids=["clean", "provider-extras"])
@pytest.mark.parametrize("fault,details", [
    ("receipt", dict(stage="receipt", reason="invalid_field", field="schema_version")),
    ("digest", dict(stage="digest", reason="digest_mismatch", field="result_sha256")),
    ("observation", dict(stage="runtime_observation", reason="validation_failed")),
    ("postflight", dict(stage="digest", reason="digest_mismatch", field="configuration_sha256")),
])
def test_actual_reverse_diagnostics_are_direct_and_allowlisted(
        strict_source_factory, monkeypatch, boundary, extras, fault, details):
    predictor = strict_source_factory()
    tool = ReverseTargetTool(predictor)
    predict, normalize = predictor.predict_with_receipt, tool._normalize_target_record

    def corrupt(*args, **kwargs):
        envelope = predict(*args, **kwargs)
        if fault == "receipt":
            envelope["receipt"]["schema_version"] = PRIVATE
        elif fault == "digest":
            envelope["receipt"]["result_sha256"] = "0" * 64
        elif fault == "postflight":
            envelope["receipt"]["configuration_sha256"] = "0" * 64
        return envelope

    def corrupt_observation(row):
        value = normalize(row)
        value["assay"]["value"] += 1
        return value

    monkeypatch.setattr(predictor, "predict_with_receipt", corrupt)
    if fault == "observation":
        monkeypatch.setattr(tool, "_normalize_target_record", corrupt_observation)
    actual_execute = tool.execute
    originals = []

    def observed(query):
        raw = actual_execute(query)
        assert raw["success"] is False
        if extras:
            raw["error"]["details"].update(debug=PRIVATE, api_key=PRIVATE, smiles="CCO")
        originals.append(deepcopy(raw))
        return raw

    monkeypatch.setattr(tool, "execute", observed)
    result = execute(boundary, tool)
    assert not result.success and result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None and not result.evidence and not result.formatted
    assert result.error.details == details
    assert result.to_legacy_dict()["error"]["details"] == details
    assert "raw_result" not in result.error.details
    assert PRIVATE not in json.dumps(result.error.to_dict())
    assert len(originals) == 1


@pytest.mark.parametrize("value,expected", [
    (dict(stage=PRIVATE, reason="digest_mismatch", field="result_sha256"), {}),
    (dict(stage="digest", reason=PRIVATE, field="result_sha256"), {}),
    (dict(stage="receipt", reason="digest_mismatch", field="result_sha256"), {}),
    (dict(stage=["digest"], reason={"value": PRIVATE}), {}),
    (dict(stage="digest", reason="digest_mismatch", field=PRIVATE),
     dict(stage="digest", reason="digest_mismatch")),
    (dict(stage="digest", reason="digest_mismatch", field=[PRIVATE]),
     dict(stage="digest", reason="digest_mismatch")),
    (dict(provider_message=PRIVATE), {}),
    ([PRIVATE], {}),
])
def test_compat_does_not_copy_untrusted_reverse_detail_values(value, expected):
    class RawTool:
        name = "reverse_target_predictor"

        def execute(self, query):
            return dict(success=False, error=dict(code="invalid_output", message="safe", details=value))

    before = deepcopy(value)
    result = execute_tool_compat(RawTool(), "CCO")
    assert result.error.details == expected
    assert value == before


def test_non_reverse_structured_details_keep_their_existing_contract():
    details = dict(claim="binding_energy", provider_metadata={"fixture": True})

    class OtherTool:
        name = "molecular_docking"

        def execute(self, query):
            return dict(success=False, error=dict(code="invalid_output", message="safe", details=details))

    assert execute_tool_compat(OtherTool(), "CCO").error.details == details


@pytest.mark.parametrize("boundary", ["compat", "registry"])
@pytest.mark.parametrize("safe", [{}, dict(stage="digest", reason="digest_mismatch",
                                         field="configuration_sha256")])
def test_typed_reverse_error_details_without_chain_are_always_allowlisted(boundary, safe):
    raw = ToolResult.error_result("reverse_target_predictor", AgentErrorCode.INVALID_OUTPUT,
                                  "safe", details={**safe, "note": PRIVATE,
                                                   "path": PRIVATE, "api_key": PRIVATE})

    class RawTool:
        name = "reverse_target_predictor"

        def execute(self, query):
            return raw

    result = execute(boundary, RawTool())
    assert result is raw
    assert result.error.details == safe
    assert not hasattr(result.error, "_reverse_raw_result")
    assert PRIVATE not in json.dumps(result.error.to_dict())
    assert PRIVATE not in json.dumps(result.to_legacy_dict())


def test_diagnostics_do_not_strip_nested_proof_checks(strict_source_factory):
    raw = ReverseTargetTool(strict_source_factory()).execute("CCO")
    nested = dict(success=False, error=dict(code="tool_unavailable", message="safe", details={
        "stage": "postflight", "reason": "source_unavailable", "raw_result": raw,
    }))
    result = adapter_result(deepcopy(nested))
    assert not result.success and result.data is None and not result.evidence
    assert result.error.details["stage"] == "postflight"
    assert "raw_result" not in result.error.details
    assert result.error._reverse_raw_result["evidence"] == raw["evidence"]
    raw["evidence"][0]["prediction_receipt"]["result_sha256"] = "0" * 64
    result = adapter_result(nested)
    assert not result.success and result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert result.data is None and not result.evidence and not result.error.details


@pytest.mark.parametrize("boundary", ["compat", "registry"])
@pytest.mark.parametrize("canonical", [False, True], ids=["raw", "canonical"])
@pytest.mark.parametrize("depth", [1, 3])
def test_private_snapshot_never_serializes_but_is_still_revalidated(
        strict_source_factory, boundary, canonical, depth):
    leaf = ReverseTargetTool(strict_source_factory()).execute("CCO")
    leaf["message"] = PRIVATE
    safe = dict(stage="postflight", reason="source_unavailable")
    raw = leaf
    for _ in range(depth):
        raw = dict(success=False, error=dict(code="tool_unavailable", message="safe", details={
            **safe, "raw_result": raw, "provider_debug": PRIVATE,
        }))
    if canonical:
        raw = ToolResult.error_result("reverse_target_predictor", AgentErrorCode.TOOL_UNAVAILABLE,
                                      "safe", details=raw["error"]["details"])

    class RawTool:
        name = "reverse_target_predictor"

        def execute(self, query):
            return raw

    result = execute(boundary, RawTool())
    assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
    assert result.error.details == safe
    assert not result.success and result.data is None and not result.evidence
    assert result.error.to_dict()["details"] == safe
    assert result.to_legacy_dict()["error"]["details"] == safe
    from src.agent.harness.decision_bounds import observation_value

    for public in (result.error.to_dict(), result.to_legacy_dict(),
                   vars(result.error), asdict(result), observation_value(result)):
        assert PRIVATE not in json.dumps(public)
        assert "raw_result" not in json.dumps(public)
    if canonical:
        assert result is raw
    # A normalized value may cross the adapter again. The private proof is not
    # an authorization marker: it must be traversed again, including after copy.
    copied = deepcopy(result)
    assert adapter_result(copied).error.code is AgentErrorCode.TOOL_UNAVAILABLE
    snapshot = copied.error._reverse_raw_result
    while "raw_result" in (snapshot.get("error") or {}).get("details", {}):
        snapshot = snapshot["error"]["details"]["raw_result"]
    snapshot["evidence"][0]["prediction_receipt"]["result_sha256"] = "0" * 64
    rejected = adapter_result(copied)
    assert rejected.error.code is AgentErrorCode.INVALID_OUTPUT
    assert rejected.data is None and not rejected.evidence


@pytest.mark.parametrize("details", [None, {}])
def test_reverse_fallback_snapshot_is_not_public(details):
    class RawTool:
        name = "reverse_target_predictor"

        def execute(self, query):
            return dict(success=False, provider_debug=PRIVATE,
                        error=dict(code="tool_unavailable", message="safe", details=details))

    result = execute_tool_compat(RawTool(), "CCO")
    assert result.error.details == {}
    assert PRIVATE not in json.dumps(result.to_legacy_dict())


@pytest.mark.parametrize("mutation", ["null", "malformed", "cycle", "depth", "conflict"])
def test_private_snapshot_keeps_fail_closed_shape_guards(strict_source_factory, mutation):
    leaf = ReverseTargetTool(strict_source_factory()).execute("CCO")
    raw = dict(success=False, error=dict(code="tool_unavailable", message="safe",
                                        details={"raw_result": leaf}))
    result = adapter_result(raw)
    assert result.error.code is AgentErrorCode.TOOL_UNAVAILABLE
    if mutation == "null":
        result.error._reverse_raw_result = None
    elif mutation == "malformed":
        result.error._reverse_raw_result = {"success": "false"}
    elif mutation == "cycle":
        result.error._reverse_raw_result = result
    elif mutation == "depth":
        for _ in range(17):
            leaf = dict(success=False, error=dict(code="tool_unavailable", message="safe",
                                                 details={"raw_result": leaf}))
        result.error._reverse_raw_result = leaf
    else:
        result.error.details = {"raw_result": leaf}
    rejected = adapter_result(result)
    assert rejected.error.code is AgentErrorCode.INVALID_OUTPUT
    assert rejected.data is None and not rejected.evidence


def test_shared_presentation_sanitizer_is_lightweight_and_never_follows_raw_results():
    code = """
import sys
before = set(sys.modules)
from src.system.scientific_diagnostics import sanitize_reverse_target_diagnostics
assert not any(name.split('.')[0] in {'numpy', 'pandas', 'rdkit', 'torch'}
               for name in set(sys.modules) - before)
safe = {'stage': 'digest', 'reason': 'digest_mismatch', 'field': 'result_sha256'}
assert sanitize_reverse_target_diagnostics(safe) == safe
assert sanitize_reverse_target_diagnostics({'raw_result': {'error': {'details': safe}}}) == {}
output = sanitize_reverse_target_diagnostics(safe)
output['field'] = 'changed'
assert safe['field'] == 'result_sha256'
"""
    result = subprocess.run([sys.executable, "-B", "-c", code],
                            cwd=Path(__file__).resolve().parents[2],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
