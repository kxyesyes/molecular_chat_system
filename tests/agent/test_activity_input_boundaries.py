"""Activity-specific context must not weaken complete-structure validation."""
import pytest

from src.agent.contracts import ToolResult
from src.agent.contracts import ObservationStatus
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
from src.agent.tools.base_tool import execute_tool_compat
from tests.agent.test_family_activity_tool import boundary  # shared synthetic service


@pytest.mark.parametrize("query", [
    "预测 CCO 的 pKi",
    "Predict Ki activity of CCO",
    "请评估 CCO 的 pKd",
])
def test_activity_tool_recognizes_supported_activity_endpoint_language(query):
    assert ActivityPredictorTool().should_use(query)


@pytest.mark.parametrize("query", [
    "不要预测 CCO 的活性",
    "请不要给出 CCO 的 pIC50",
    "Do not predict pKi for CCO",
])
def test_activity_tool_does_not_trigger_for_explicit_activity_negation(query):
    assert not ActivityPredictorTool().should_use(query)


@pytest.mark.parametrize("field", [
    '"CCO"junk', "'CCO'junk", '`CCO`junk', '"CCO" invalid',
    '"CCO" pIC50', '"CCO" CCN', '"CCO"垃圾',
    'CCO ethanol', 'CCO\tethanol', 'CCO pIC50', 'CCO$bad',
    'CCO 请预测', 'CCO，请预测', 'CCO)；CCN', 'CCO\nnot_a_smiles',
    'CCO\nCCN$bad', 'CCO |atomProp:0.foo.bar|',
])
def test_explicit_field_rejects_full_invalid_value(boundary, field):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, f"target: PDE5A; SMILES: {field}")
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "SMILES" in result.message
    assert not calls


@pytest.mark.parametrize("smiles", [
    ["CCO", "not_a_smiles"], ["CCO", "CCN$bad"], ["CCO", "CCN name"],
    ["CCO", ""], ["CCO", None], ["CCO", 7], [], None, {},
    'CCO\nCCN', '"CCO"', 'CCO; CCN', "SMILES: CCO", "CCO；参考CCN",
])
def test_structured_smiles_are_authoritative_not_reparsed_as_prose(boundary, smiles):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, {"query": "预测CCO活性", "target": "BuChE", "smiles": smiles})
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


@pytest.mark.parametrize("payload,target", [
    ("target: PDE5A; SMILES: CCO", "PDE5A"),
    ("靶点：丁酰胆碱酯酶；SMILES: CCO", "丁酰胆碱酯酶"),
    ("Predict activity of CCO for BuChE", "BuChE"),
    ("Predict PDE5A activity; SMILES: CCO", "PDE5A"),
    ("预测 PDE5A 活性；SMILES: CCO", "PDE5A"),
    ({"smiles": ["CCO", "CCN"], "target": "BuChE"}, "BuChE"),
])
def test_target_labels_do_not_become_molecules(boundary, payload, target):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, payload)
    assert result.success
    assert calls == [(["CCO", "CCN"] if isinstance(payload, dict) else ["CCO"], target)]


@pytest.mark.parametrize("metric", ["IC50", "pIC50", "PIC50", "EC50", "pEC50", "Ki", "pKi", "Kd", "pKd"])
def test_metric_words_are_context_only_outside_explicit_fields(boundary, metric):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, f"Predict {metric} activity of CCO for PDE5A")
    if metric.casefold() == "pic50":
        assert result.success
        assert calls == [(["CCO"], "PDE5A")]
    else:
        assert result.status == ObservationStatus.UNAVAILABLE
        assert not calls


@pytest.mark.parametrize("payload", [
    "target: EGFR; SMILES: CCO", "target: XPDE5A; SMILES: CCO",
    "target: ; SMILES: CCO", "靶点：；SMILES: CCO",
    "靶点：PDE5A EGFR；SMILES: CCO", "target: PDE5A EGFR; SMILES: CCO",
    "Predict activity of CCO for EGFR", "Predict activity against EGFR; SMILES: CCO",
    "target: PDE5A; target: BuChE; SMILES: CCO",
    "预测CCO对PDE5A和EGFR的活性",
    "Predict activity of CCO for PDE5A and EGFR",
    "Predict activity of CCO against PDE5A and BuChE",
    "target PDE5A EGFR; SMILES: CCO",
    "靶点 PDE5A EGFR；SMILES: CCO",
    {"target": "PDE5A EGFR", "smiles": "CCO"},
    {"target": None, "smiles": "CCO"},
    {"target": "PDE5A", "query": "target: EGFR", "smiles": "CCO"},
])
def test_all_explicit_target_tokens_must_be_known_and_nonconflicting(boundary, payload):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, payload)
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "靶点" in result.message
    assert not calls


@pytest.mark.parametrize("payload", ["CCO$bad", "CCO ethanol", "CCO\nCCN$bad"])
def test_unlabelled_invalid_full_structures_cannot_fall_back_global(boundary, payload):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, payload)
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


@pytest.mark.parametrize("query", [
    "预测活性；SMILES: CCO", "预测分子活性；SMILES: CCO",
    "预测这个分子的活性；SMILES: CCO", "评估该分子的活性；SMILES: CCO",
])
def test_targetless_activity_request_requires_explicit_target(query):
    tool = ActivityPredictorTool()
    result = tool.execute(query)
    assert isinstance(result, ToolResult)
    assert result.success is False
    assert result.status == ObservationStatus.INVALID_INPUT
    assert "靶点" in result.message


@pytest.mark.parametrize("payload", [
    {"query": "SMILES: CCO, target: EGFR", "smiles": "CCN"},
    {"query": "SMILES: CCO, target\tEGFR", "smiles": "CCN"},
    {"query": "SMILES: CCO, target: BuChE", "smiles": "CCN", "target": "PDE5A"},
    {"query": "SMILES: CCO，靶点：BuChE", "smiles": "CCN", "target": "PDE5A"},
    {"query": "target: PDE5A, EGFR", "smiles": "CCN"},
    {"query": "target: PDE5A，EGFR", "smiles": "CCN"},
    {"query": "Predict activity of CCO for PDE5A / EGFR", "smiles": "CCN"},
    {"query": "Predict activity of CCO for PDE5A, BuChE", "smiles": "CCN"},
])
def test_all_target_positions_and_list_delimiters_fail_closed(boundary, payload):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, payload)
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


@pytest.mark.parametrize("query", [
    "SMILES: CCO, target: PDE5A", "target: PDE5A, PDE4D",
    "Predict activity of CCO for PDE5A / PDE4D",
])
def test_known_same_family_positions_remain_usable(boundary, query):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, {"query": query, "smiles": "CCN"})
    assert result.success
    assert calls == [(["CCN"], "PDE5A")]


@pytest.mark.parametrize("phrase", [
    "Predict {target} activity", "Assess {target} potency", "Evaluate {target} activity",
    "预测 {target} 活性", "评估 {target} 的活性",
])
@pytest.mark.parametrize("target,structured_target", [("EGFR", None), ("BuChE", "PDE5A")])
def test_late_labelled_target_cannot_be_discarded(boundary, phrase, target, structured_target):
    tool, calls, _ = boundary
    payload = {"query": "SMILES: CCO, " + phrase.format(target=target), "smiles": "CCN"}
    if structured_target is not None:
        payload["target"] = structured_target
    result = execute_tool_compat(tool, payload)
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


@pytest.mark.parametrize("phrase", [
    "Predict PDE5A activity", "Assess PDE5A potency", "Evaluate PDE5A activity",
    "预测 PDE5A 活性", "评估 PDE5A 的活性",
])
def test_late_known_labelled_target_uses_generated_structures(boundary, phrase):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, {"query": "SMILES: CCO, " + phrase, "smiles": "CCN"})
    assert result.success
    assert calls == [(["CCN"], "PDE5A")]


@pytest.mark.parametrize("label", ["for", "against"])
@pytest.mark.parametrize("separator", [":", "=", "：", "\t", " "])
@pytest.mark.parametrize("target,structured_target", [("EGFR", None), ("BuChE", "PDE5A")])
def test_late_positioned_target_uses_full_label_grammar(
        boundary, label, separator, target, structured_target):
    tool, calls, _ = boundary
    payload = {"query": f"SMILES: CCO, {label}{separator}{target}", "smiles": "CCN"}
    if structured_target is not None:
        payload["target"] = structured_target
    result = execute_tool_compat(tool, payload)
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


@pytest.mark.parametrize("payload", [
    "预测CCO对乙酰胆碱酯酶的活性",
    'Predict activity of CCO for "EGFR"',
    "预测CCO对PDE5A和乙酰胆碱酯酶的活性",
    'Predict activity of CCO for PDE5A and "EGFR"',
    {"query": "SMILES: CCO, against 乙酰胆碱酯酶", "smiles": "CCN"},
    {"query": 'SMILES: CCO, for "EGFR"', "smiles": "CCN", "target": "PDE5A"},
    "Predict activity of CCO for ;",
])
def test_unknown_or_missing_positioned_values_never_disappear(boundary, payload):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, payload)
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


@pytest.mark.parametrize("prefix", ["background: PDE5A, ", "研究背景：PDE5A，"])
@pytest.mark.parametrize("declaration", ["target: EGFR", "Predict EGFR activity", "对乙酰胆碱酯酶的活性"])
@pytest.mark.parametrize("structured", [False, True])
def test_background_cannot_hide_explicit_target_declarations(boundary, prefix, declaration, structured):
    tool, calls, _ = boundary
    query = prefix + declaration
    payload = {"query": query, "target": "BuChE", "smiles": "CCO"} if structured else query + "; SMILES: CCO"
    result = execute_tool_compat(tool, payload)
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


def test_explicit_known_target_after_background_overrides_background_alias(boundary):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, "background: BuChE, target: PDE5A; SMILES: CCO")
    assert result.success
    assert calls == [(["CCO"], "PDE5A")]


@pytest.mark.parametrize("target", ["PDE5A$EGFR", "PDE5A.EGFR", "PDE5A:EGFR", "PDE5A乙酰胆碱酯酶"])
def test_positioned_target_cannot_be_truncated_to_known_prefix(boundary, target):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, {"query": f"Predict activity for {target}", "smiles": "CCO"})
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


@pytest.mark.parametrize("phrase", ['Predict "EGFR" activity', "预测乙酰胆碱酯酶活性", "评估乙酰胆碱酯酶的活性"])
def test_labelled_unknown_target_values_fail_closed(boundary, phrase):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, phrase + "; SMILES: CCO")
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


@pytest.mark.parametrize("query,target", [
    ("预测CCO对PDE5A的pIC50", "PDE5A"),
    ("预测CCO对BuChE的抑制活性", "BuChE"),
])
def test_known_targets_keep_supported_metric_suffixes(boundary, query, target):
    tool, calls, _ = boundary
    assert execute_tool_compat(tool, query).success
    assert calls == [(["CCO"], target)]
