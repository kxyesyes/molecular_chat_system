"""Offline synthetic descriptor fixtures; real browser acceptance is separate."""
import pytest

from src.agent.tools.lead_optimization_verifier import LeadOptimizationVerifier
from tests.agent.test_lead_optimization_verifier import _payload


@pytest.mark.parametrize('query', [
    '这个分子的分子量是多少？另外预测活性',
    '这个分子的分子量和未知指标是多少？',
    '不要计算这个分子的分子量是多少？',
])
def test_contextual_property_request_does_not_discard_other_intent(query):
    from src.web.property_comparison import contextual_property_query
    evidence = {'session_id': 's', 'trace_id': 't', 'evidence_id': 'e', 'canonical_smiles': 'CCO'}
    assert contextual_property_query(query, [{'agent_used': True, 'property_evidence': evidence}], 's') == query


def test_contextual_property_request_rejects_foreign_session():
    from src.web.property_comparison import contextual_property_query
    query = '这个分子的分子量是多少？'
    evidence = {'session_id': 'other', 'trace_id': 't', 'evidence_id': 'e', 'canonical_smiles': 'CCO'}
    assert contextual_property_query(query, [{'agent_used': True, 'property_evidence': evidence}], 's') == query


@pytest.mark.parametrize('smiles', ['CCO', 'CC(=O)Oc1ccccc1C(=O)O'])
@pytest.mark.parametrize('query', [
    '根据刚才的结果，这个分子是否符合 Lipinski 五规则？如果还缺少指标，请先计算再判断。',
    '这个分子符合 Lipinski 五规则吗？',
    '它是否满足 Lipinski 规则？',
    '请判断该分子是否符合 Lipinski 五规则。',
])
def test_contextual_lipinski_followup_uses_verified_prior_molecule(smiles, query):
    from src.web.property_comparison import contextual_property_query
    from src.web.scientific_obligations import ScientificObligationCompiler
    evidence = {'session_id': 's', 'trace_id': 't', 'evidence_id': 'e', 'canonical_smiles': smiles}
    history = [{'agent_used': True, 'property_evidence': evidence}]
    resolved = contextual_property_query(query, history, 's')
    plan = ScientificObligationCompiler().compile(resolved)
    assert plan.status == 'ready'
    assert plan.smiles == smiles
    assert 'drug_likeness_assessment' in plan.required_tools
    assert contextual_property_query(query, history, 'other') == query
    assert contextual_property_query(query + '另外预测活性。', history, 's') == query + '另外预测活性。'


def test_drug_likeness_result_preserves_only_verified_single_molecule():
    from copy import deepcopy
    from src.web.property_comparison import accepted_property_evidence
    observation = {
        'tool_name': 'drug_likeness_assessment', 'status': 'succeeded', 'success': True,
        'error': None, 'quality': {'validated': True, 'evidence_id': 'e'},
        'provenance': {'tool_name': 'drug_likeness_assessment', 'input_digest': 'digest',
                       'demo_mode': False, 'fallback_used': False},
        'data': [{'smiles': 'CCO', 'assessment': {'molecular_properties':
                  {'molecular_weight': 46.07, 'logp': -0.001}}}],
    }
    result = {'trace_id': 't', 'tool_result_sequence': [observation]}
    query = '评估 SMILES: CCO 的 Lipinski 规则。'
    evidence = accepted_property_evidence(result, query, 's')
    assert evidence['canonical_smiles'] == 'CCO' and evidence['session_id'] == 's'
    assert accepted_property_evidence(result, '评估 SMILES: CCN 的 Lipinski 规则。', 's') is None
    bad = deepcopy(result)
    bad['tool_result_sequence'][0]['quality']['validated'] = False
    assert accepted_property_evidence(bad, query, 's') is None


def test_batch_lipinski_followup_uses_verified_previous_result():
    from src.web.property_comparison import (
        accepted_batch_property_evidence, batch_lipinski_followup, recorded_descriptor,
    )
    query = '比较 CCO 和 CCN 的分子量、LogP、HBA 和 HBD。'
    result = _generation_result()
    result['tool_result_sequence'] = [result['tool_result_sequence'][1]]
    rows = result['tool_result_sequence'][0]['data']
    rows[0]['properties'].update(hba=1, hbd=1)
    rows[1]['properties'].update(hba=1, hbd=1)
    evidence = accepted_batch_property_evidence(result, query, 's')
    history = [{'agent_used': True, 'batch_property_evidence': evidence}]
    followup = '根据刚才的结果，分别判断这两个分子是否符合 Lipinski 五规则。'
    view = batch_lipinski_followup(followup, history, 's')
    assert view['status'] == 'completed'
    assert 'CCO' in view['content'] and 'CCN' in view['content']
    assert '未超过' in view['content'] and '生物利用度' in view['content']
    assert batch_lipinski_followup(followup, history, 'other')['status'] == 'not_calculated'
    assert batch_lipinski_followup(followup + '另外预测活性。', history, 's') is None
    evidence['rows'].append(dict(evidence['rows'][0], smiles='CCC'))
    mismatch = batch_lipinski_followup(followup, history, 's')
    assert mismatch['status'] == 'not_calculated' and 'SMILES' in mismatch['content']
    assert mismatch['pending'] == evidence
    selected = recorded_descriptor('第二个的 logP 多少？', history, 's')['history_update']
    assert batch_lipinski_followup(followup, [selected], 's')['pending'] == evidence
    assert 'pending' not in batch_lipinski_followup(followup, [selected], 'foreign')


def test_descriptor_reference_matches_the_rendered_table_when_tool_rows_are_reordered():
    from types import SimpleNamespace
    from src.web.decision_chat import scientific_display_answer
    from src.web.property_comparison import accepted_batch_property_evidence, recorded_descriptor

    result = _generation_result()
    observation = result['tool_result_sequence'][1]
    result['tool_result_sequence'] = [observation]
    rows = observation['data']
    for row in rows:
        row['properties'].update(hba=1, hbd=1)
    rendered = scientific_display_answer([SimpleNamespace(tool_name='property_calculator',
        status=SimpleNamespace(value='succeeded'), data=rows)], concise=True, metrics=['mw'])
    assert rendered.index('CCN') < rendered.index('CCO')
    record = accepted_batch_property_evidence(result, '比较 CCO 和 CCN 的分子量。', 's')
    selected = recorded_descriptor('帮我看一下第一行。', [{'batch_property_evidence': record,
                                   'descriptor_metric': 'molecular_weight'}], 's')
    assert selected['history_update']['property_evidence']['canonical_smiles'] == 'CCN'


def test_batch_property_followup_reuses_evidence_after_lipinski():
    from src.web.web_business import WebTurnProjection
    evidence = {'session_id': 's', 'trace_id': 't', 'evidence_id': 'e',
                'rows': [{'smiles': 'CCO', 'molecular_weight': 46.07, 'logp': 1.31, 'hba': 1, 'hbd': 1},
                         {'smiles': 'CCN', 'molecular_weight': 45.08, 'logp': 3.073, 'hba': 1, 'hbd': 1}]}
    prior = [{'agent_used': True, 'batch_property_evidence': evidence}]
    turn = WebTurnProjection('根据刚才的结果，分别判断这两个分子是否符合 Lipinski 五规则。', 's', prior)
    assert turn.followup(enable_tools=True)['status'] == 'completed'
    query = '刚才比较的两个分子，哪个 LogP 更高？差值是多少？'
    followup = WebTurnProjection(query, 's', [turn.entry]).followup(enable_tools=True)
    assert followup['status'] == 'completed'
    assert all(value in followup['content'] for value in ('CCO', 'CCN', '1.763'))
    assert WebTurnProjection(query, 'other', [turn.entry]).followup(enable_tools=True)['status'] == 'not_calculated'
    assert WebTurnProjection(query + '再预测活性。', 's', [turn.entry]).followup(enable_tools=True) is None
    new_calculation = query + '再计算 CCC 的分子量。'
    assert WebTurnProjection(new_calculation, 's', [turn.entry]).followup(enable_tools=True) is None
    natural = '刚才这两个分子哪个 LogP 更高，差值是多少？仅根据刚才的计算回答。'
    answer = WebTurnProjection(natural, 's', [turn.entry]).followup(enable_tools=True)
    assert answer['status'] == 'completed'
    assert all(value in answer['content'] for value in ('CCO', 'CCN', '1.763'))
    assert WebTurnProjection(natural + '再预测活性。', 's', [turn.entry]).followup(enable_tools=True) is None


def test_compare_entire_batch_after_courtesy_without_changing_row_order():
    from src.web.property_comparison import batch_property_followup
    rows = [{'smiles': s, 'logp': p} for s, p in [('CCC', 1.4163), ('CCN', -0.03499), ('CCO', -0.0014)]]
    batch = {'session_id': 'owner', 'trace_id': 'batch', 'evidence_id': 'e', 'rows': rows}
    previous = [{'descriptor_carry': {'batch_property_evidence': batch, 'batch_property_evidence_reused': True}}]
    query = '这批分子，哪个 logP 最低？'
    answer, update = batch_property_followup(query, previous, 'owner')
    assert answer['status'] == 'completed' and '`CCN` 的 LogP 最低' in answer['content']
    assert answer['content'].index('CCC') < answer['content'].index('CCN') < answer['content'].index('CCO')
    assert update['batch_property_evidence'] == batch
    foreign, _ = batch_property_followup(query, previous, 'other')
    assert foreign['status'] == 'not_calculated' and 'CCN' not in foreign['content']


def test_batch_ordinals_cover_all_supported_rows_and_reject_missing_row():
    from src.web.property_comparison import recorded_descriptor
    rows = [{'smiles': 'C' * i, 'logp': i / 10} for i in range(1, 17)]
    batch = {'session_id': 's', 'trace_id': 't', 'evidence_id': 'e', 'rows': rows}
    history = [{'agent_used': True, 'batch_property_evidence': batch, 'descriptor_metric': 'logp'}]
    chosen = recorded_descriptor('那第十六个呢？', history, 's')
    assert chosen['history_update']['property_evidence']['canonical_smiles'] == rows[-1]['smiles']
    corrected = recorded_descriptor('不好意思，其实我想看的是第一行。', [chosen['history_update']], 's')
    assert corrected['history_update']['property_evidence']['canonical_smiles'] == rows[0]['smiles']
    for reply, pos in [('下一行', 2), ('最后一行', 16), ('上一行', 15), ('看第8行', 8)]:
        corrected = recorded_descriptor(reply, [corrected['history_update']], 's')
        assert corrected['history_update']['property_evidence']['canonical_smiles'] == rows[pos - 1]['smiles']
    assert recorded_descriptor('看第8行，再算 CCC', history, 's') is None
    invalid = recorded_descriptor('那第17个呢？', [chosen['history_update']], 's')
    assert invalid['status'] == 'not_calculated'
    assert invalid['history_update']['property_evidence'] == chosen['history_update']['property_evidence']


def test_other_descriptor_row_requires_a_unique_alternative_in_this_session():
    from src.web.property_comparison import recorded_descriptor

    rows = [{'smiles': 'CCO', 'logp': -0.001}, {'smiles': 'CCC', 'logp': 1.416}]
    batch = {'session_id': 's', 'trace_id': 't', 'evidence_id': 'e', 'rows': rows}
    history = [{'batch_property_evidence': batch, 'descriptor_metric': 'logp'}]
    query = '那另一个呢？'
    assert recorded_descriptor(query, history, 's')['status'] == 'not_calculated'
    selected = recorded_descriptor('第二行', history, 's')['history_update']
    for phrase in (query, '刚才那两个里，另一个呢？', '另一个的情况呢？', '剩下那个呢？',
                   '另一个分子的 logP 多少？'):
        answer = recorded_descriptor(phrase, [selected], 's')
        assert answer['status'] == 'completed' and 'CCO' in answer['content']
        assert 'CCC' not in answer['content']
    assert recorded_descriptor(query, [selected], 'foreign') is None
    assert recorded_descriptor('另一个呢？另外预测活性。', [selected], 's') is None
    for size in (1, 3):
        available = (rows + [{'smiles': 'CCN', 'logp': -0.035}])[:size]
        history = [{'batch_property_evidence': {**batch, 'rows': available}, 'descriptor_metric': 'logp'}]
        selected = recorded_descriptor('第一行', history, 's')['history_update']
        unclear = recorded_descriptor(query, [selected], 's')
        assert unclear['status'] == 'not_calculated' and '序号' in unclear['content']
        assert unclear['history_update']['property_evidence'] == selected['property_evidence']


def test_batch_lipinski_does_not_trust_incomplete_or_rounded_evidence():
    from copy import deepcopy
    from src.web.property_comparison import accepted_batch_property_evidence, batch_lipinski_followup
    query = '比较 CCO 和 CCN 的分子量、LogP、HBA 和 HBD。'
    result = _generation_result()
    result['tool_result_sequence'] = [result['tool_result_sequence'][1]]
    rows = result['tool_result_sequence'][0]['data']
    rows[0]['properties'].update(molecular_weight=500.0, hba=1, hbd=1)
    rows[1]['properties'].update(hba=1, hbd=1)
    evidence = accepted_batch_property_evidence(result, query, 's')
    view = batch_lipinski_followup('根据刚才的结果，分别判断这两个分子是否符合 Lipinski 五规则。',
                                   [{'agent_used': True, 'batch_property_evidence': evidence}], 's')
    assert view['status'] == 'partial' and '舍入' in view['content']
    bad = deepcopy(result)
    bad['tool_result_sequence'][0]['data'].pop()
    assert accepted_batch_property_evidence(bad, query, 's') is None
    bad = deepcopy(result)
    bad['tool_result_sequence'][0]['quality']['validated'] = False
    assert accepted_batch_property_evidence(bad, query, 's') is None


def test_lipinski_clarification_does_not_use_foreign_or_out_of_range_selection():
    from src.web.property_comparison import batch_lipinski_followup
    pending = {'session_id': 's', 'trace_id': 't', 'evidence_id': 'e',
               'rows': [{'smiles': value, 'molecular_weight': 46.0, 'logp': 1.0, 'hba': 1, 'hbd': 1}
                        for value in ('CCO', 'CCN', 'CCC')]}
    history = [{'batch_lipinski_pending': pending}]
    assert batch_lipinski_followup('前两个。', history, 'other')['status'] == 'not_calculated'
    invalid = batch_lipinski_followup('第一个和第四个。', history, 's')
    assert invalid['status'] == 'not_calculated' and invalid['pending'] == pending
    assert batch_lipinski_followup('前两个，另外预测活性。', history, 's') is None
    unknown = batch_lipinski_followup('CCO 和 CCF。', history, 's')
    assert unknown['status'] == 'not_calculated' and unknown['pending'] == pending
    assert batch_lipinski_followup('CCO 和 CCC，另外预测活性。', history, 's') is None


@pytest.mark.parametrize("mass,status", [(449.9, "supported"), (450.0, "unverified"),
                                         (500.0, "evaluated_not_improved"), (None, "unverified")])
def test_optimization_cannot_ignore_explicit_molecular_weight_bound(mass, status):
    payload = _payload("降低 logP，仍保留分子量小于 450 的约束。")
    payload["outputs"]["candidate_properties"][0]["properties"]["molecular_weight"] = mass
    result = LeadOptimizationVerifier().execute(payload)
    candidate = result["data"]["candidates"][0]
    assert candidate["status"] == status
    assert candidate["constraint_checks"][0]["value"] == mass
    assert "molecular_weight" in result["formatted"]
    assert "2.0" in result["formatted"] and "1.5" in result["formatted"]


@pytest.mark.parametrize("query,value,passed", [
    ("logP 小于 3", 3.0, False), ("logP 不超过 3", 3.0, True),
    ("MW <= 450 Da", 450.0, True), ("分子量低于450", 449.0, True),
    ("logP > -1.5", -1.0, True), ("logP >= 0", 0.0, True),
])
def test_supported_descriptor_bound_operators(query, value, passed):
    from src.agent.property_constraints import requested_property_bounds, check_property_bounds
    bounds, unknown = requested_property_bounds(query)
    assert not unknown and len(bounds) == 1
    checks = check_property_bounds(bounds, {bounds[0]["metric"]: value})
    assert checks[0]["satisfied"] is passed


@pytest.mark.parametrize('query,properties', [
    ('MW <= 45.08 Da', {'molecular_weight': 45.08}),
    ('logP < -0.001', {'logp': -0.001}),
    ('TPSA >= 20.23', {'tpsa': 20.23}),
    ('QED <= 0.407', {'qed': 0.407}),
])
def test_rounded_tool_values_at_a_bound_are_unverified(query, properties):
    from src.agent.property_constraints import requested_property_bounds, check_property_bounds
    bounds, unknown = requested_property_bounds(query)
    assert not unknown
    assert check_property_bounds(bounds, properties, rounded=True)[0]['satisfied'] is None


def test_generated_candidates_do_not_claim_rounded_boundary_is_satisfied():
    from src.web.property_constraints import generation_constraint_report
    report = generation_constraint_report(_generation_result(), '设计两个候选，分子量不大于45.08 Da')
    assert '候选 2 `CCN` | 分子量 <= 45.08 Da | 45.08 | 未验证' in report


def test_optimization_does_not_claim_rounded_boundary_is_satisfied():
    payload = _payload('降低 logP，仍保留分子量不大于 450 的约束。')
    payload['outputs']['candidate_properties'][0]['properties']['molecular_weight'] = 450.0
    result = LeadOptimizationVerifier().execute(payload)
    assert result['data']['candidates'][0]['status'] == 'unverified'
    assert result['data']['candidates'][0]['constraint_checks'][0]['satisfied'] is None


@pytest.mark.parametrize("query", ["MW < NaN", "MW < 450kDa", "MW < 450.2.3", "logP < 1e999",
                                    "不要限制 MW < 450", "0 < logP < 3", "MW < -450"])
def test_ambiguous_or_invalid_bounds_are_unverified(query):
    from src.agent.property_constraints import requested_property_bounds
    _, unknown = requested_property_bounds(query)
    assert unknown


@pytest.mark.parametrize("value", [True, "450", float("inf"), float("nan"), None, 10**1000])
def test_non_numeric_descriptor_does_not_pass(value):
    from src.agent.property_constraints import requested_property_bounds, check_property_bounds
    bounds, _ = requested_property_bounds("MW < 450")
    assert check_property_bounds(bounds, {"molecular_weight": value})[0]["satisfied"] is None


def _generation_result():
    def observation(name, data):
        return {"tool_name": name, "success": True, "status": "succeeded", "error": None,
                "provenance": {"tool_name": name, "demo_mode": False, "fallback_used": False,
                               "input_digest": "fixture-digest"},
                "quality": {"validated": True, "evidence_id": "fixture-" + name}, "data": data}
    return {"trace_id": "fixture-run", "tool_result_sequence": [
        observation("llm_molecular_generator", {"candidates": [
            {"candidate_id": "candidate-1", "canonical_smiles": "CCO"},
            {"candidate_id": "candidate-2", "canonical_smiles": "CCN"}]}),
        observation("property_calculator", [
            {"candidate_id": "candidate-2", "smiles": "CCN", "properties": {"molecular_weight": 45.08, "logp": 3.1}},
            {"candidate_id": "candidate-1", "smiles": "CCO", "properties": {"molecular_weight": 46.07, "logp": -0.001}}]),
    ]}


def test_generation_constraint_table_matches_identity_not_position():
    from src.web.property_constraints import generation_constraint_report
    report = generation_constraint_report(_generation_result(), "设计两个候选，要求分子量小于450、logP小于3")
    assert "候选 1 `CCO` | 分子量 < 450.0 Da | 46.07 | 满足" in report
    assert "候选 2 `CCN` | logP < 3.0 | 3.1 | 不满足" in report
    assert "fixture-property_calculator" not in report and "fixture-run" not in report


@pytest.mark.parametrize("defect", ["unvalidated", "fallback", "identity", "duplicate", "missing"])
def test_generation_constraint_table_never_rescues_invalid_property_evidence(defect):
    from src.web.property_constraints import generation_constraint_report
    result = _generation_result()
    item = result["tool_result_sequence"][1]
    if defect == "unvalidated":
        item["quality"]["validated"] = False
    elif defect == "fallback":
        item["provenance"]["fallback_used"] = True
    elif defect == "identity":
        item["data"][0]["smiles"] = "CCO"
    elif defect == "duplicate":
        item["data"].append(item["data"][0])
    else:
        item["data"] = []
    report = generation_constraint_report(result, "设计两个候选，分子量小于450")
    assert "未验证" in report and "| 满足 |" not in report


def test_drug_likeness_tpsa_report_uses_square_angstrom_not_corrupt_unit():
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    report = DrugLikenessAssessment().execute("CCO")["formatted"]
    assert "Å²" in report and "Ų" not in report


@pytest.mark.parametrize("tail", [" (kDa)", "千道尔顿", "±5", "+5", " DaEGFR"])
def test_unrecognized_threshold_suffix_cannot_claim_support(tail):
    payload = _payload("降低 logP，MW > 450" + tail)
    payload["outputs"]["candidate_properties"][0]["properties"]["molecular_weight"] = 500
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"


@pytest.mark.parametrize("defect", ["foreign_id", "failed", "error", "status"])
def test_numeric_verifier_rejects_foreign_or_failed_property_rows(defect):
    payload = _payload("降低 logP，MW < 450")
    row = payload["outputs"]["candidate_properties"][0]
    row["properties"]["molecular_weight"] = 400
    row.update({"foreign_id": {"candidate_id": "foreign"}, "failed": {"success": False},
                "error": {"error": {"code": "failed"}}, "status": {"status": "failed"}}[defect])
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"
    assert result["data"]["candidates"][0]["constraint_checks"][0]["satisfied"] is None


@pytest.mark.parametrize("defect", ["trace", "digest", "failed", "error", "status"])
def test_generation_constraints_require_accepted_source_and_rows(defect):
    from src.web.property_constraints import generation_constraint_report
    result = _generation_result()
    item = result["tool_result_sequence"][1]
    if defect == "trace":
        result.pop("trace_id")
    elif defect == "digest":
        item["provenance"]["input_digest"] = {"bad": "type"}
    else:
        item["data"][0].update({"failed": {"success": False}, "error": {"error": {"code": "failed"}},
                                "status": {"status": "failed"}}[defect])
    report = generation_constraint_report(result, "MW < 450")
    assert "未验证" in report and "| 满足 |" not in report


@pytest.mark.parametrize("failure", [{"success": False}, {"status": "failed"}, {"error": {"code": "failed"}}])
def test_failed_baseline_does_not_support_relative_improvement(failure):
    payload = _payload("降低 logP，MW < 450")
    payload["outputs"]["candidate_properties"][0]["properties"]["molecular_weight"] = 400
    payload["outputs"]["baseline"][0].update(failure)
    result = LeadOptimizationVerifier().execute(payload)
    assert result["data"]["status"] == "unverified"
