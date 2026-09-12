"""Activity-specific context must not weaken complete-structure validation."""
import pytest

from src.agent.contracts import ObservationStatus
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
from src.agent.tools.base_tool import execute_tool_compat
from tests.agent.test_family_activity_tool import boundary  # shared synthetic service


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
    assert execute_tool_compat(tool, f"Predict {metric} activity of CCO for PDE5A").success
    assert calls == [(["CCO"], "PDE5A")]


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


def test_targetless_legacy_predictor_interface_remains_in_use(monkeypatch):
    calls = []

    class Legacy:
        demo_mode = False
        current_model_metadata = {"model_id": "synthetic-legacy"}

        def predict(self, smiles):
            calls.append(smiles)
            return [{"smiles": s, "success": True, "task_type": "regression",
                     "value": 1.0, "endpoint": "synthetic", "units": "synthetic"} for s in smiles]

    tool = ActivityPredictorTool()
    monkeypatch.setattr(tool, "_get_predictor", lambda: Legacy())
    result = tool.execute("预测活性；SMILES: CCO")
    assert isinstance(result, dict) and result["success"]
    assert calls == [["CCO"]]


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
