"""Safe diagnostics over temporary synthetic sources, never host assets."""

from copy import deepcopy
import json

import pytest

from src.agent.tools import reverse_target_tool as module
from src.agent.tools.reverse_target_tool import ReverseTargetTool
from src.reverse_target import owned_source as owned
from src.reverse_target.receipt import CONTROLS
from tests.agent.test_reverse_receipt_consumption import strict_source_factory


PRIVATE = "private-test-only C:/private/source.tsv credential=not-a-real-key CCO"


def assert_safe_failure(result, details, code="invalid_output"):
    assert result["success"] is False
    assert result["data"] is None
    assert not result.get("evidence")
    assert not result.get("formatted")
    assert result["error"]["code"] == code
    assert result["error"].get("details") == details
    public = json.dumps(result["error"], ensure_ascii=False)
    for forbidden in (PRIVATE, "private-test-only", "C:/private", "not-a-real-key", "CCO"):
        assert forbidden not in public


@pytest.mark.parametrize("mutation,details", [
    ("missing_field", dict(stage="receipt", reason="schema_mismatch", field="receipt")),
    ("extra_field", dict(stage="receipt", reason="schema_mismatch", field="receipt")),
    ("schema_version", dict(stage="receipt", reason="invalid_field", field="schema_version")),
    ("invocation_id", dict(stage="receipt", reason="invalid_field", field="invocation_id")),
    ("source_name", dict(stage="receipt", reason="invalid_field", field="source.source_name")),
    ("record_count", dict(stage="receipt", reason="count_mismatch", field="record_count")),
    ("status", dict(stage="receipt", reason="invalid_field", field="status")),
    ("source_sha256", dict(stage="digest", reason="digest_mismatch", field="source_sha256")),
    ("input_sha256", dict(stage="digest", reason="digest_mismatch", field="input_sha256")),
    ("result_sha256", dict(stage="digest", reason="digest_mismatch", field="result_sha256")),
    ("provenance", dict(stage="records", reason="invalid_provenance", field="provenance")),
    ("score", dict(stage="records", reason="invalid_field", field="final_similarity")),
    ("records", dict(stage="records", reason="invalid_type", field="records")),
])
def test_corrupt_proof_reports_bounded_diagnostic(strict_source_factory, monkeypatch, mutation, details):
    predictor = strict_source_factory()
    envelope = predictor.predict_with_receipt("CCO", **CONTROLS)
    expected = predictor.capture_prediction_source()
    receipt = envelope["receipt"]
    if mutation == "missing_field":
        receipt.pop("invocation_id")
    elif mutation == "extra_field":
        receipt[PRIVATE] = PRIVATE
    elif mutation in ("schema_version", "invocation_id"):
        receipt[mutation] = PRIVATE
    elif mutation == "source_name":
        receipt["source"]["source_name"] = PRIVATE
    elif mutation == "record_count":
        receipt["record_count"] += 1
    elif mutation == "status":
        receipt["status"] = "verified_empty"
    elif mutation.endswith("sha256"):
        receipt[mutation] = "0" * 64
    elif mutation == "provenance":
        envelope["records"][0]["provenance"] = {"data_version": PRIVATE}
    elif mutation == "score":
        envelope["records"][0]["final_similarity"] = 2.0
    else:
        envelope["records"] = PRIVATE
    before = deepcopy(envelope)
    monkeypatch.setattr(predictor, "predict_with_receipt", lambda *a, **kw: envelope)
    monkeypatch.setattr(predictor, "validate_prediction_source", lambda *a, **kw: pytest.fail("invalid proof reached postflight"))
    assert_safe_failure(ReverseTargetTool(predictor).execute("CCO"), details)
    with pytest.raises(ValueError) as caught:
        owned.validate_envelope(envelope, smiles="CCO", controls=CONTROLS, expected=expected)
    assert str(caught.value) == owned.PROOF_ERROR
    assert caught.value.details == details
    assert envelope == before


def test_runtime_observation_mismatch_has_own_stage(strict_source_factory, monkeypatch):
    predictor = strict_source_factory()
    tool = ReverseTargetTool(predictor)
    normalize = tool._normalize_target_record

    def altered(row):
        value = normalize(row)
        value["assay"]["value"] += 1
        return value

    monkeypatch.setattr(tool, "_normalize_target_record", altered)
    monkeypatch.setattr(predictor, "validate_prediction_source", lambda *a, **kw: pytest.fail("invalid observation reached postflight"))
    assert_safe_failure(tool.execute("CCO"), dict(stage="runtime_observation", reason="validation_failed"))


def test_runtime_observation_must_be_verified(strict_source_factory, monkeypatch):
    monkeypatch.setattr(module, "validate_prediction_observation", lambda *a, **kw: None)
    assert_safe_failure(ReverseTargetTool(strict_source_factory()).execute("CCO"),
                        dict(stage="runtime_observation", reason="validation_failed"))


def test_postflight_source_and_configuration_failures_remain_distinct(strict_source_factory, monkeypatch):
    predictor = strict_source_factory()
    original = predictor.predict_with_receipt

    def corrupt(*a, **kw):
        envelope = original(*a, **kw)
        envelope["receipt"]["configuration_sha256"] = "0" * 64
        return envelope

    monkeypatch.setattr(predictor, "predict_with_receipt", corrupt)
    assert_safe_failure(ReverseTargetTool(predictor).execute("CCO"),
                        dict(stage="digest", reason="digest_mismatch", field="configuration_sha256"))
    monkeypatch.setattr(predictor, "predict_with_receipt", original)
    tool = ReverseTargetTool(predictor)
    normalize = tool._normalize_target_record

    def close_source(row):
        predictor.close_strict()
        return normalize(row)

    monkeypatch.setattr(tool, "_normalize_target_record", close_source)
    assert_safe_failure(tool.execute("CCO"), dict(stage="postflight", reason="source_unavailable"), "tool_unavailable")


@pytest.mark.parametrize("method,stage,code", [
    ("capture_prediction_source", "source", "tool_unavailable"),
    ("predict_with_receipt", "source", "tool_unavailable"),
    ("validate_prediction_source", "postflight", "invalid_output"),
])
def test_arbitrary_exception_details_are_not_trusted(strict_source_factory, monkeypatch, method, stage, code):
    predictor = strict_source_factory()

    class UntrustedError(ValueError):
        details = dict(stage=PRIVATE, reason=PRIVATE, field=PRIVATE)

    def fail(*a, **kw):
        raise UntrustedError(PRIVATE)

    monkeypatch.setattr(predictor, method, fail)
    assert_safe_failure(ReverseTargetTool(predictor).execute("CCO"),
                        dict(stage=stage, reason="validation_failed"), code)


@pytest.mark.parametrize("diagnostic,field,details", [
    (PRIVATE, PRIVATE, dict(stage="proof", reason="validation_failed")),
    ([PRIVATE], {PRIVATE: PRIVATE}, dict(stage="proof", reason="validation_failed")),
    ("digest", PRIVATE, dict(stage="digest", reason="digest_mismatch")),
])
def test_typed_diagnostic_export_is_allowlisted(strict_source_factory, monkeypatch, diagnostic, field, details):
    predictor = strict_source_factory()
    error = owned.ReverseProofError(diagnostic, field)
    exported = error.details
    exported.update(stage=PRIVATE, reason=PRIVATE, field=PRIVATE)

    def fail(*a, **kw):
        raise error

    monkeypatch.setattr(predictor, "validate_prediction_source", fail)
    assert_safe_failure(ReverseTargetTool(predictor).execute("CCO"), details)
