"""Activity-specific context must not weaken complete-structure validation."""
import pytest

from src.agent.contracts import ToolResult
from src.agent.contracts import ObservationStatus
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
from src.agent.tools.base_tool import execute_tool_compat
from tests.agent.test_family_activity_tool import boundary  # shared synthetic service


@pytest.mark.parametrize('smiles', ['CCO', 'CCO.Cl'])
def test_labeled_activity_field_followed_by_separated_target(smiles):
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.base_tool import BaseMolecularTool
    _, values, target = parse_activity_input(
        f'请预测 SMILES: {smiles} 对人丁酰胆碱酯酶（BuChE）的活性。', BaseMolecularTool('test', ''))
    assert values == [smiles] and target == '丁酰胆碱酯酶'


def test_activity_target_reference_does_not_hide_new_explicit_target():
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.base_tool import BaseMolecularTool
    parser = BaseMolecularTool('test', '')
    query = '请预测 SMILES: CCO 对人丁酰胆碱酯酶（BuChE）的抑制活性，并说明模型是否支持该靶点以及该分子是否在模型适用域内。'
    assert parse_activity_input(query, parser)[1:] == (['CCO'], '丁酰胆碱酯酶')
    with pytest.raises(ValueError):
        parse_activity_input(query.replace('该靶点以及', '该靶点 EGFR 以及'), parser)


@pytest.mark.parametrize('suffix', ['', '，并给出模型信息与适用范围'])
def test_spaced_human_buche_request_keeps_the_full_smiles(suffix):
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.base_tool import BaseMolecularTool
    parser = BaseMolecularTool('test', '')
    query = f'请预测 SMILES：CCO 对人源 BuChE 的活性{suffix}。'
    assert parse_activity_input(query, parser)[1:] == (['CCO'], 'BuChE')
    with pytest.raises(ValueError):
        parse_activity_input(query.replace('CCO', 'CCO$bad'), parser)


@pytest.mark.parametrize('suffix', ['$bad', ')', '.'])
def test_labeled_activity_field_does_not_discard_invalid_structure_suffix(suffix):
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.base_tool import BaseMolecularTool
    with pytest.raises(ValueError):
        parse_activity_input(f'请预测 SMILES: CCO{suffix} 对人丁酰胆碱酯酶（BuChE）的活性。',
                             BaseMolecularTool('test', ''))


def test_explicit_labeled_two_molecule_activity_comparison(boundary):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, '比较 SMILES: CCO 和 CCN 对 BuChE 的活性。')
    assert result.success
    assert calls == [(['CCO', 'CCN'], 'BuChE')]


def test_labeled_comparison_retains_complete_disconnected_structure():
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.base_tool import BaseMolecularTool
    _, structures, target = parse_activity_input(
        '比较 SMILES: CCO.Cl 和 C1CC1 对 BuChE 的活性。', BaseMolecularTool('test', ''))
    assert structures == ['CCO.Cl', 'C1CC1'] and target == 'BuChE'


def test_labeled_activity_comparison_routes_only_to_activity():
    from src.agent.contracts import AgentContext
    from src.agent.contracts.web_requirements import WEB_TOOLS
    from src.agent.scientific_session.web_requirements import compile_web_requirements
    query = '比较 SMILES: CCO 和 CCN 对 BuChE 的活性。'
    plan = compile_web_requirements(AgentContext(query, 'comparison'), allowed_tools=WEB_TOOLS)
    assert [step.tool_name for step in plan.steps] == ['activity_predictor']
    assert plan.request['query'] == query


def test_web_labeled_activity_comparison_uses_both_exact_structures(tmp_path):
    from tests.agent.test_browser_evidence_followup import handler_for, prediction, turn
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3), prediction('CCN', 4.7)])
    result, _ = turn(handler, '比较 SMILES: CCO 和 CCN 对 BuChE 的活性。', [])
    assert result['status'] == 'completed', result
    assert len(activity.calls) == 1
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.base_tool import BaseMolecularTool
    _, structures, target = parse_activity_input(activity.calls[0], BaseMolecularTool('test', ''))
    assert structures == ['CCO', 'CCN'] and target == 'BuChE'
    assert 'CCO' in result['content'] and 'CCN' in result['content']


@pytest.mark.parametrize('bad_structure', ['CCN$bad', 'CCN)', 'C1CC'])
def test_web_labeled_comparison_reports_complete_invalid_structure(tmp_path, bad_structure):
    from src.agent.utils.validators import InputValidator
    from tests.agent.test_browser_evidence_followup import handler_for, prediction, turn
    query = f'比较 SMILES: CCO 和 {bad_structure} 对 BuChE 的活性。'
    analysis = InputValidator().analyze_molecular_input(query)
    assert analysis.blocks_execution
    assert any(candidate.value == bad_structure and candidate.validation == 'invalid'
               for candidate in analysis.candidates)
    handler, activity, _ = handler_for(tmp_path, [prediction('CCO', 4.3), prediction('CCN', 4.7)])
    result, _ = turn(handler, query, [])
    assert result['status'] == 'rejected' and 'SMILES' in result['content']
    assert '无效' in result['content'] and not activity.calls


def test_invalid_comparison_does_not_log_structure(capfd, caplog):
    from src.agent.utils.validators import InputValidator
    query = '比较 SMILES: CCO 和 CCN$private_marker 对 BuChE 的活性。'
    assert InputValidator().analyze_molecular_input(query).blocks_execution
    assert 'private_marker' not in capfd.readouterr().err
    assert 'private_marker' not in caplog.text


@pytest.mark.parametrize('query', [
    '比较 SMILES: CCO 和 CCN$bad 对 BuChE 的活性。',
    '比较 SMILES: CCO 和 CCN 对 BuChE 的活性和 FooMetric。',
    '比较 SMILES: CCO 和 CCN 对 BuChE 的活性并发送邮件。',
])
def test_labeled_activity_comparison_never_drops_invalid_suffix_or_extra_obligations(query):
    from src.agent.contracts import AgentContext
    from src.agent.contracts.web_requirements import WEB_TOOLS
    from src.agent.scientific_session.web_requirements import compile_web_requirements
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query, 'invalid-comparison'), allowed_tools=WEB_TOOLS)


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


def test_negated_docking_clause_does_not_conflict_with_activity_target(boundary):
    tool, calls, _ = boundary
    query = "请使用分子生成工具，针对 PDE4A 生成 2 个候选小分子；不要做对接。"

    result = execute_tool_compat(tool, {
        "query": query,
        "target": "PDE4A",
        "smiles": ["CCO"],
    })

    assert result.success
    assert calls == [(["CCO"], "PDE4A")]


def test_generated_candidates_pronoun_does_not_override_explicit_target(boundary):
    from src.agent.tools.activity_input import activity_target
    from rdkit import Chem
    query = '为 BuChE 设计两个候选小分子，给出 SMILES 并预测它们的活性。'
    assert activity_target(query, lambda value: Chem.MolFromSmiles(value) is not None).casefold() == 'buche'
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, {'query': query, 'target': 'BuChE', 'smiles': ['CCO', 'CCN']})
    assert result.success and calls == [(['CCO', 'CCN'], 'BuChE')]


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


def test_equivalent_parenthesized_target_alias_is_one_target(boundary):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, '预测 CCO 对丁酰胆碱酯酶（BuChE）的活性。')
    assert result.success
    assert calls == [(['CCO'], '丁酰胆碱酯酶')]


@pytest.mark.parametrize('alias', ['PDE5A', 'EGFR'])
def test_parenthesized_target_alias_must_not_hide_conflict_or_unknown(boundary, alias):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, f'预测 CCO 对丁酰胆碱酯酶（{alias}）的活性。')
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


def test_parenthesized_alias_does_not_hide_an_attached_unknown_target(boundary):
    tool, calls, _ = boundary
    result = execute_tool_compat(tool, '预测 CCO 对丁酰胆碱酯酶（BuChE）EGFR 的活性。')
    assert result.status == ObservationStatus.INVALID_INPUT
    assert not calls


def test_activity_target_rejection_mentions_species_scope():
    from src.agent.scientific_session.controller import _decision_failure_message
    assert '物种' in _decision_failure_message('activity_target_conflict_or_unknown')


def test_explicit_new_activity_prediction_accepts_reprediction_verb():
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.base_tool import BaseMolecularTool
    query = '请重新预测 SMILES: CCO 对 BuChE 的活性。'
    _, smiles, target = parse_activity_input(query, BaseMolecularTool('test', ''))
    assert smiles == ['CCO'] and target == 'BuChE'
    with pytest.raises(ValueError):
        parse_activity_input(query.replace('CCO', 'CCO$bad'), BaseMolecularTool('test', ''))
