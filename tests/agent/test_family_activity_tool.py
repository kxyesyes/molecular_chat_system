"""Synthetic boundary tests: no datasets, checkpoints or external services."""
import copy

import pytest

from src.agent.contracts import ObservationStatus, ToolProvenance
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
from src.agent.tools.base_tool import execute_tool_compat


def family_row(**changes):
    row = dict(smiles="CCO", requested_target="PDE5A", family_id="pde-family",
               bundle_id="synthetic-bundle", success=True, status="passed",
               activity_class="无活性", activity_probability=0.0, predicted_pIC50=6.2,
               units="pIC50", label_threshold=5.0, probability_threshold=0.5,
               classification_regression_consistent=False,
               warnings=["分类与回归预测不一致，已保留两项原始结果。"], errors={},
               provenance={"bundle_id": "synthetic-bundle", "source_sha256": "a" * 64,
                           "models": {task: {"model_id": "synthetic-" + task,
                                             "weights_sha256": "b" * 64,
                                             "model_card_sha256": "c" * 64,
                                             "prepared_dataset_sha256": "d" * 64,
                                             "task_type": task, "target_id": "pde-family",
                                             "demo_mode": False, "fallback_used": False}
                                      for task in ("classification", "regression")}})
    row.update(changes)
    return row


@pytest.fixture
def boundary(monkeypatch):
    from src.activity import prediction_service
    from src.activity.family_contract import resolve_activity_family

    calls = []
    state = {"rows": None, "status": "passed"}

    def predict(smiles, *, target=None):
        calls.append((smiles, target))
        rows = copy.deepcopy(state["rows"])
        if rows is None:
            rows = [family_row(smiles=s, requested_target=target,
                               family_id=resolve_activity_family(target)) for s in smiles]
            for row in rows:
                for model in row["provenance"]["models"].values():
                    model["target_id"] = row["family_id"]
        return dict(success=state["status"] == "passed", status=state["status"],
                    results=rows, warnings=["服务边界提示"])

    monkeypatch.setattr(prediction_service, "predict_activity", predict)
    tool = ActivityPredictorTool()
    # No missing asset may accidentally invoke the global checkpoint finder.
    monkeypatch.setattr(tool, "_get_predictor", lambda: pytest.fail("legacy fallback"))
    return tool, calls, state


@pytest.mark.parametrize("warnings", [None, 7, "not a warning list", {"private": "not a warning list"}])
def test_malformed_optional_warning_container_preserves_observations(boundary, warnings):
    tool, _, state = boundary
    row = family_row(warnings=warnings)
    state["rows"] = [row]
    result = tool.execute({"smiles": "CCO", "target": "PDE5A"})
    assert result.success
    assert result.data == [row]
    assert result.warnings == ["服务边界提示"]


@pytest.mark.parametrize("changes", [
    {"units": "nM"}, {"units": None}, {"label_threshold": 7.0}, {"label_threshold": None},
])
@pytest.mark.parametrize("status", ["passed", "partial"])
def test_family_claims_require_fixed_endpoint_semantics(boundary, changes, status):
    tool, _, state = boundary
    row = family_row(**changes)
    if status == "partial":
        row.update(status="partial", success=False, predicted_pIC50=None,
                   errors={"regression": "unavailable"})
    state.update(rows=[row], status=status)
    result = tool.execute({"smiles": "CCO", "target": "PDE5A"})
    assert not result.success
    assert result.data is None


@pytest.mark.parametrize("payload,target", [
    ("预测CCO对PDE5A的活性", "PDE5A"),
    ("预测CCO对BuChE的活性", "BuChE"),
    ("预测CCO对丁酰胆碱酯酶的活性", "丁酰胆碱酯酶"),
    ({"query": "预测PDE5A活性", "smiles": ["CCO", "CCN"]}, "PDE5A"),
    ({"query": "预测活性", "smiles": "CCO", "target": "BChE"}, "BChE"),
])
def test_target_request_uses_shared_service(boundary, payload, target):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, payload)
    assert calls == [(["CCO", "CCN"] if isinstance(payload, dict) and isinstance(payload.get("smiles"), list) else ["CCO"], target)]
    assert result.success


@pytest.mark.parametrize("metric", ["pIC50", "IC50", "pic50", "PIC50"])
def test_metric_label_in_prose_is_not_a_second_smiles(boundary, metric):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, f"请预测 CCO 对 PDE5A 的活性和 {metric}。")
    assert result.success
    assert calls == [(["CCO"], "PDE5A")]


@pytest.mark.parametrize("query", [
    "请预测 CC(C)(( 对 PDE5A 的 pIC50。",
    "请预测 PDE5A 活性；SMILES: pIC50",
    "请预测 PDE5A 活性；SMILES: CCO pIC50",
    "请预测 CCO CC(C)(( 对 PDE5A 的 pIC50。",
])
def test_metric_filter_cannot_salvage_an_invalid_structure(boundary, query):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, query)
    assert not result.success
    assert not calls
    assert result.status == ObservationStatus.INVALID_INPUT


@pytest.mark.parametrize("payload", [
    "预测CCO对PDE5A和BuChE的活性",
    {"smiles": "CCO", "target": "PDE5A BuChE"},
    {"smiles": "CCO", "target": "AChE"},
    {"smiles": "CCO", "target": ""},
    "预测CCO对AChE的活性",
    "预测CCO对XPDE5A的活性",
    "预测 EGFR 活性，SMILES: CCO",
])
def test_unknown_or_ambiguous_target_fails_closed(boundary, payload):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, payload)
    assert not calls
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "靶点" in result.message


@pytest.mark.parametrize("payload", [
    {"smiles": "CCO)", "target": "PDE5A"},
    {"query": "CCO", "smiles": "CCO$bad", "target": "PDE5A"},
    {"smiles": "CCO invalid", "target": "PDE5A"},
    {"smiles": "", "target": "PDE5A"},
    "预测PDE5A活性，SMILES: CCO)；参考CCO",
    "预测活性，SMILES: CCO)；参考CCO",
])
def test_invalid_explicit_smiles_never_salvages(boundary, payload):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, payload)
    assert not calls
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "SMILES" in result.message


@pytest.mark.parametrize("status", ["passed", "partial", "failed"])
def test_compat_preserves_rows_status_provenance_and_honest_format(boundary, status):
    tool, _, state = boundary
    row = family_row()
    if status != "passed":
        row.update(success=False, status=status, predicted_pIC50=None,
                   errors={"regression" if status == "partial" else "bundle": "unavailable"})
    if status == "failed":
        row.update(activity_class=None, activity_probability=None, provenance={})
    state.update(rows=[row], status=status)
    result = execute_tool_compat(tool, {"smiles": "CCO", "target": "PDE5A"})
    assert result.data == [row]
    assert result.success is (status == "passed")
    assert result.status.value == ("succeeded" if status == "passed" else status)
    assert result.quality["prediction_status"] == status
    assert result.quality["model_provenance"] == [row["provenance"]]
    assert result.evidence[0]["prediction"] == row
    assert result.warnings == ["服务边界提示", *row["warnings"]]
    assert ToolProvenance.from_dict(result.provenance.to_dict()) == result.provenance
    assert not result.provenance.fallback_used
    assert "预测" in result.formatted
    if status != "failed":
        assert "无活性" in result.formatted and "0.0000" in result.formatted
    if status == "passed":
        assert "6.2000" in result.formatted
    else:
        assert "不可用" in result.formatted and "6.2000" not in result.formatted
        assert "unavailable" in result.formatted


def test_generated_candidates_keep_original_target_at_actual_invocation(boundary):
    from src.agent.react_agent import ReActMolecularAgent
    from src.agent.workflows import WorkflowCatalog
    from tests.agent.test_target_driven_design_workflow import FakeTool

    tool, calls, _ = boundary
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    names = ["target_database_search", "llm_molecular_generator", "property_calculator",
             "admet_predictor", "candidate_ranker"]
    agent.tools = {name: FakeTool(name) for name in names}
    agent.tools["activity_predictor"] = tool
    agent.execute("基于 PDE5A 设计类药候选分子", active_skill=WorkflowCatalog().require("target_driven_design"))
    assert agent.tools["llm_molecular_generator"].inputs
    assert calls == [(["CCO"], "PDE5A")]


@pytest.mark.parametrize("damage", ["missing", "one_model", "demo", "fallback", "digest", "wrong_family"])
def test_family_validator_rejects_unproven_numeric_claims(damage):
    from src.agent.contracts import ToolResult
    from src.agent.validators.domain_validators import ActivityResultValidator
    row = family_row()
    if damage == "missing":
        row["provenance"] = {}
    elif damage == "one_model":
        del row["provenance"]["models"]["regression"]
    else:
        model = row["provenance"]["models"]["classification"]
        model.update({"demo": {"demo_mode": True}, "fallback": {"fallback_used": True},
                      "digest": {"weights_sha256": None}, "wrong_family": {"target_id": "buche-family"}}[damage])
    result = ToolResult.success_result("activity_predictor", data=[row])
    assert ActivityResultValidator().validate(result) is not None


def test_family_validator_accepts_pinned_partial_and_full_rows():
    from src.agent.contracts import ToolResult
    from src.agent.validators.domain_validators import ActivityResultValidator
    for row in [family_row(), family_row(success=False, status="partial", predicted_pIC50=None)]:
        assert ActivityResultValidator().validate(ToolResult("activity_predictor", row["success"], "", data=[row])) is None


@pytest.mark.parametrize("payload", ["预测CCO对PDE5A的活性", {"query": "预测BuChE活性", "smiles": "CCO"}])
def test_should_use_accepts_natural_and_structured_requests(payload):
    assert ActivityPredictorTool().should_use(payload)


@pytest.mark.parametrize("status", ["passed", "partial"])
def test_tool_rejects_numeric_rows_without_real_provenance_even_when_partial(boundary, status):
    tool, _, state = boundary
    row = family_row(status=status, success=status == "passed", provenance={})
    state.update(rows=[row], status=status)
    result = execute_tool_compat(tool, {"smiles": "CCO", "target": "PDE5A"})
    assert not result.success
    assert result.error.code.value == "invalid_output"
    assert not result.formatted


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "6.2"])
def test_family_validator_rejects_non_numeric_regression(value):
    from src.agent.contracts import ToolResult
    from src.agent.validators.domain_validators import ActivityResultValidator
    assert ActivityResultValidator().validate(ToolResult.success_result(
        "activity_predictor", data=[family_row(predicted_pIC50=value)])) is not None


def test_real_shared_service_calls_family_predictor_without_missing_bundle_fallback(monkeypatch):
    from src.activity import prediction_service, predictor
    from src.activity.family_predictor import FamilyActivityPredictor

    class EmptyRegistry:
        def get_active_family_bundle(self, family):
            assert family == "pde-family"
            return None

    monkeypatch.setattr(prediction_service, "get_family_predictor", lambda: FamilyActivityPredictor(EmptyRegistry()))
    monkeypatch.setattr(predictor, "get_predictor", lambda: pytest.fail("global fallback"))
    result = execute_tool_compat(ActivityPredictorTool(), "预测CCO对PDE5A的活性")
    assert not result.success
    assert result.data[0]["errors"]["bundle"] == "family_model_bundle_unavailable_or_invalid"
    assert result.data[0]["predicted_pIC50"] is None
    assert result.data[0]["activity_probability"] is None
    assert "失败" in result.formatted


def test_workflow_candidate_binding_keeps_metadata_target(boundary):
    from src.agent.contracts import AgentContext
    from src.agent.orchestrators.base import WorkflowStep
    from src.agent.orchestrators.workflow import WorkflowOrchestrator
    from tests.agent.test_target_driven_design_workflow import FakeTool
    tool, calls, _ = boundary
    context = AgentContext(query="生成分子并预测活性", trace_id="synthetic-activity", mol_count=1,
                           metadata={"target": "PDE5A"})
    steps = [WorkflowStep("generate", "llm_molecular_generator", output_key="molecules"),
             WorkflowStep("activity", "activity_predictor", input_from="molecules", input_transform="smiles_text")]
    WorkflowOrchestrator().run(context, steps, {
        "llm_molecular_generator": FakeTool("llm_molecular_generator"), "activity_predictor": tool})
    assert calls == [(["CCO"], "PDE5A")]


def test_activity_request_query_binding_is_not_treated_as_candidate_smiles(boundary):
    from src.agent.contracts import AgentContext
    from src.agent.orchestrators.base import WorkflowStep
    from src.agent.orchestrators.workflow import WorkflowOrchestrator
    tool, calls, _ = boundary
    WorkflowOrchestrator().run(
        AgentContext(query="预测CCO对PDE5A的活性", trace_id="synthetic-request"),
        [WorkflowStep("activity", "activity_predictor", input_binding="$.request.query")],
        {"activity_predictor": tool})
    assert calls == [(["CCO"], "PDE5A")]


@pytest.mark.parametrize("damage", ["probability", "digest", "bundle"])
def test_family_validator_rejects_invalid_probability_or_provenance_identity(damage):
    from src.agent.contracts import ToolResult
    from src.agent.validators.domain_validators import ActivityResultValidator
    row = family_row()
    if damage == "probability":
        row["activity_probability"] = 2.0
    elif damage == "digest":
        row["provenance"]["models"]["regression"]["weights_sha256"] = "not-a-digest"
    else:
        row["provenance"]["bundle_id"] = "another-bundle"
    assert ActivityResultValidator().validate(ToolResult.success_result("activity_predictor", data=[row])) is not None


@pytest.mark.parametrize("quoted", ['"CCO invalid"', "'CCO invalid'", '`CCO invalid`',
                                    '"CCO invalid', '"CCO；invalid"'])
def test_explicit_quoted_smiles_is_validated_as_a_whole(boundary, quoted):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, f"预测PDE5A活性，SMILES: {quoted}")
    assert not calls
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "SMILES" in result.message


@pytest.mark.parametrize("quoted", ['"CCO"', "'CCO'", '`CCO`'])
def test_valid_quoted_smiles_remains_supported(boundary, quoted):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, f"预测PDE5A活性，SMILES: {quoted}")
    assert result.success
    assert calls == [(["CCO"], "PDE5A")]


@pytest.mark.parametrize("payload", [
    "背景为PDE研究；靶点：EGFR；SMILES: CCO",
    "靶点：PDE5A；靶点：EGFR；SMILES: CCO",
    "靶点：PDE5A；预测BuChE活性；SMILES: CCO",
    {"query": "靶点：EGFR", "target": "PDE5A", "smiles": "CCO"},
    {"query": "靶点：BuChE", "target": "PDE5A", "smiles": "CCO"},
])
def test_explicit_target_labels_take_priority_and_conflicts_fail_closed(boundary, payload):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, payload)
    assert not calls
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "靶点" in result.message


def test_explicit_target_ignores_clearly_labelled_background(boundary):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, "背景为PDE研究；靶点：BuChE；SMILES: CCO")
    assert result.success
    assert calls == [(["CCO"], "BuChE")]


@pytest.mark.parametrize("changes", [
    dict(activity_class="有活性", activity_probability=None, predicted_pIC50=None, provenance={}),
    dict(activity_class="有活性", activity_probability=None, predicted_pIC50=None),
    dict(activity_class="maybe"),
    dict(activity_class=None),
    dict(activity_class="有活性", activity_probability=0.0),
    dict(activity_class="无活性", activity_probability=0.5),
    dict(status="failed", success=False),
    dict(status="partial", success=False),  # A partial row cannot claim completed regression.
    dict(status="passed", predicted_pIC50=None),
    dict(status="partial", predicted_pIC50=None, success=True),
    dict(errors={"classification": "failed"}),
    dict(probability_threshold=0.8),
])
def test_family_claim_requires_valid_class_probability_and_stage_contract(boundary, changes):
    from src.agent.contracts import ToolResult
    from src.agent.validators.domain_validators import ActivityResultValidator
    tool, _, state = boundary
    row = family_row(**changes)
    assert ActivityResultValidator().validate(ToolResult("activity_predictor", False, "", data=[row])) is not None
    state.update(rows=[row], status=row["status"])
    result = execute_tool_compat(tool, {"smiles": "CCO", "target": "PDE5A"})
    assert not result.success
    assert result.error.code.value == "invalid_output"
    assert not result.formatted


def test_genuine_failed_null_row_keeps_errors_without_model_provenance(boundary):
    tool, _, state = boundary
    row = family_row(success=False, status="failed", activity_class=None,
                     activity_probability=None, predicted_pIC50=None, provenance={},
                     errors={"classification": "classification_failed_or_invalid_output"})
    state.update(rows=[row], status="failed")
    result = execute_tool_compat(tool, {"smiles": "CCO", "target": "PDE5A"})
    assert result.status == ObservationStatus.FAILED
    assert result.data == [row]
    assert "classification_failed_or_invalid_output" in result.formatted


@pytest.mark.parametrize("field", ["CCO invalid", "CCO please predict activity", "CCO\tinvalid",
                                    "  CCO invalid  ；请预测", "CCO invalid\n请预测"])
def test_unquoted_explicit_smiles_never_truncates_internal_whitespace(boundary, field):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, f"预测PDE5A活性；SMILES: {field}")
    assert not calls
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "SMILES" in result.message


@pytest.mark.parametrize("field", ["CCO。请预测", "  CCO  ；请预测", "CCO; please predict",
                                    "CCO\n请预测"])
def test_unquoted_explicit_smiles_accepts_only_clear_field_boundaries(boundary, field):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, f"预测PDE5A活性；SMILES: {field}")
    assert result.success
    assert calls == [(["CCO"], "PDE5A")]


@pytest.mark.parametrize("status,success", [("passed", True), ("partial", False), ("partial", True)])
@pytest.mark.parametrize("boundary_kind", ["validator", "tool"])
def test_all_null_family_rows_cannot_claim_observations(boundary, status, success, boundary_kind):
    from src.agent.contracts import ToolResult
    from src.agent.validators.domain_validators import ActivityResultValidator
    tool, _, state = boundary
    row = family_row(status=status, success=success, activity_class=None,
                     activity_probability=None, predicted_pIC50=None, provenance={},
                     errors={"classification": "classification_failed_or_invalid_output"})
    if boundary_kind == "validator":
        assert ActivityResultValidator().validate(ToolResult("activity_predictor", success, "", data=[row])) is not None
        return
    state.update(rows=[row], status=status)
    result = execute_tool_compat(tool, {"smiles": "CCO", "target": "PDE5A"})
    assert not result.success
    assert result.error.code.value == "invalid_output"
    assert not result.formatted


@pytest.mark.parametrize("row", [
    {"smiles": "CCO", "success": True, "task_type": "classification", "probability": 0.5},
    {"smiles": "CCO", "success": True, "task_type": "regression", "value": 6.2},
    {"smiles": "CCO", "success": False, "error": "model unavailable"},
])
def test_family_presence_validation_does_not_change_legacy_task_rows(row):
    from src.agent.contracts import ToolResult
    from src.agent.validators.domain_validators import ActivityResultValidator
    assert ActivityResultValidator().validate(ToolResult("activity_predictor", row["success"], "", data=[row])) is None


@pytest.mark.parametrize("rows", [
    [], [None], [{"smiles": "CCO", "success": True, "value": 6.2}],
    [family_row(smiles="CCN")], [family_row(requested_target="BuChE")],
    [family_row(family_id="buche-family")], [family_row(), family_row()],
])
def test_family_service_rows_must_match_actual_request(boundary, rows):
    tool, _, state = boundary
    state["rows"] = rows
    result = execute_tool_compat(tool, {"target": "PDE5A", "smiles": "CCO"})
    assert not result.success
    assert result.error.code.value == "invalid_output"
    assert not result.formatted


def test_family_summary_cannot_promote_partial_rows_to_success(boundary):
    tool, _, state = boundary
    state["rows"] = [family_row(success=False, status="partial", predicted_pIC50=None,
                                errors={"regression": "synthetic_unavailable"})]
    result = execute_tool_compat(tool, {"target": "PDE5A", "smiles": "CCO"})
    assert not result.success
    assert result.error.code.value == "invalid_output"


def test_mixed_batch_keeps_failed_null_row_and_partial_status(boundary):
    tool, _, state = boundary
    failed = family_row(smiles="CCN", success=False, status="failed", activity_class=None,
                        activity_probability=None, predicted_pIC50=None, provenance={},
                        errors={"bundle": "synthetic_unavailable"})
    state.update(rows=[family_row(), failed], status="partial")
    result = execute_tool_compat(tool, {"target": "PDE5A", "smiles": ["CCO", "CCN"]})
    assert result.status == ObservationStatus.PARTIAL
    assert not result.success
    assert result.data == state["rows"]
    assert result.evidence[1]["prediction"] == failed


def test_family_service_exception_is_sanitized_and_never_uses_global(boundary, monkeypatch):
    from src.activity import prediction_service
    tool, calls, _ = boundary

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic private diagnostic must not be echoed")

    monkeypatch.setattr(prediction_service, "predict_activity", unavailable)
    result = execute_tool_compat(tool, {"target": "PDE5A", "smiles": "CCO"})
    assert result.status == ObservationStatus.UNAVAILABLE
    assert result.error.code.value == "model_unavailable"
    assert "private diagnostic" not in str(result.to_legacy_dict())
    assert not calls
