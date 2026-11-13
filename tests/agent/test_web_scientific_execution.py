"""Task 3 Web execution contract: server-owned property obligations."""

import asyncio
from dataclasses import replace
import json

import pytest

from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.contracts.decision import FinishDecision, ToolDecision
from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.tools.property_calculator import PropertyCalculator
from test_admet_ai_integration import FakeBackend
from test_web_decision_runtime import ActualSocket, actual_app, cookie_for, result_of
from pi_web_support import PlannedWebModel, WebSocket, make_web_handler, run_web_turn
from src.web.decision_runtime import _Sender, _Turn, _resumable_scientific_clarification
from src.web.scientific_obligations import ScientificObligationCompiler


@pytest.fixture
def web_handler(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "agent.sqlite")
    handler = make_web_handler([PropertyCalculator()], store, model=PlannedWebModel())
    try:
        yield handler
    finally:
        assert not handler.decision_runtime.active_owners
        handler.agent_system.tool_registry.close()


@pytest.fixture
def admet_web_handler(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "agent.sqlite")
    handler = make_web_handler(
        [PropertyCalculator(), ADMETPredictor(backend=FakeBackend())],
        store,
        model=PlannedWebModel(),
    )
    try:
        yield handler
    finally:
        assert not handler.decision_runtime.active_owners
        handler.agent_system.tool_registry.close()


def test_property_request_with_result_only_clause_executes_rdkit(web_handler):
    _, frames = run_web_turn(web_handler, "请计算 CCO 的分子量和 QED，并只输出工具结果。")
    result = result_of(frames)
    assert result["status"] == "completed"
    step, = result["tool_result_sequence"]
    assert step["tool_name"] == "property_calculator"
    record, = step["data"]
    assert record["smiles"] == "CCO"
    assert record["properties"]["molecular_weight"] == pytest.approx(46.07, abs=0.01)
    assert record["properties"]["qed"] == pytest.approx(0.407, abs=0.001)


def test_ready_property_plan_executes_real_rdkit_tool_and_publishes_evidence(web_handler):
    _, frames = run_web_turn(
        web_handler,
        "请分析 CCO 的分子性质，返回 LogP 和 TPSA。",
    )
    result = result_of(frames)

    assert result["status"] == "completed", result
    assert result["metadata"]["required_tools"] == ["property_calculator"]
    assert result["metadata"]["workflow_version"] == "web-property-v1"
    assert result["tool_result_sequence"][0]["status"] == "succeeded"
    assert result["tool_result_sequence"][0]["provenance"]
    assert result["tool_result_sequence"][0]["quality"]["evidence_id"]
    assert "CCO" in json.dumps(result, ensure_ascii=False)
    assert "46.07" in json.dumps(result, ensure_ascii=False) or "46.069" in json.dumps(result, ensure_ascii=False)
    assert "gmm-llama:latest" not in json.dumps(result, ensure_ascii=False)
    assert not any(frame.get("type") == "agent_event" and
                   frame.get("event", {}).get("event") == "tool_failed"
                   for frame in frames)


@pytest.mark.parametrize('query', [
    ('我计划将 PDB 1M17 与配体 SMILES: CCO 进行分子对接，但还没有确定口袋。'
     '请先说明开始之前需要准备什么，不要执行对接。'),
    ('我想把 SMILES: CCO 与 EGFR 的 PDB 1M17 进行分子对接，但口袋尚未确定。'
     '请先告诉我还需要哪些输入和准备，不要执行对接。'),
    ('我准备使用 PDB 1M17 和配体 SMILES: CCO 做分子对接，现在还没有确定口袋。'
     '请说明开始之前需要准备什么，暂时不要运行对接。'),
    ('我计划使用 PDB 1M17 和配体 SMILES: CCN 做分子对接，目前尚未确定口袋。'
     '请说明开始之前需要准备什么，暂时不要执行对接。'),
    ('我有 EGFR 结构 1M17 和配体 SMILES: CCO，想做分子对接，但还不知道结合口袋。'
     '对接前需要准备什么？先别运行。'),
    ('配体 SMILES: CCN，受体 PDB 1M17。口袋尚未确定，计划做分子对接。'
     '对接前需要准备什么？先不要执行。'),
])
def test_docking_preparation_consultation_does_not_execute_docking(web_handler, query):
    from src.web.web_business import WebTurnProjection
    complete, frames = run_web_turn(web_handler, query)
    assert complete['status'] == 'partial'
    assert all(term in complete['content'] for term in ('口袋', '受体', '配体', '盒子', '未执行'))
    assert not result_of(frames)['tool_result_sequence']
    extended = query + '再计算 CCO 的 LogP。'
    assert WebTurnProjection(extended, 's', []).followup(enable_tools=True) is None
    assert WebTurnProjection(query + '然后执行对接。', 's', []).followup(enable_tools=True) is None
    assert WebTurnProjection(query + '请查询 EGFR 结构。', 's', []).followup(enable_tools=True) is None


def test_docking_preparation_with_property_calculation_fulfills_both(web_handler):
    query = ('我有 EGFR 结构 1M17 和配体 SMILES: CCO，想做分子对接，但还不知道结合口袋。'
             '对接前需要准备什么？先别运行。再计算 CCO 的 LogP。')
    complete, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert '口袋' in complete['content'] and '未执行对接' in complete['content']
    assert 'logp' in complete['content'].lower()
    assert [item['tool_name'] for item in result['tool_result_sequence']] == ['property_calculator']
    assert result['tool_result_sequence'][0]['status'] == 'succeeded'


def test_docking_preparation_with_invalid_property_input_reports_failure(web_handler):
    query = ('我有 EGFR 结构 1M17 和配体 SMILES: CCO，想做分子对接，但还不知道结合口袋。'
             '对接前需要准备什么？先别运行。再计算 C1CC 的 LogP。')
    complete, frames = run_web_turn(web_handler, query)
    assert '未执行对接' in complete['content']
    assert '无法验证' in complete['content'] or '无效' in complete['content']
    assert not result_of(frames)['tool_result_sequence']


def test_docking_preparation_with_extra_action_is_not_split():
    from src.web.web_business import WebTurnProjection
    query = ('我有 EGFR 结构 1M17 和配体 SMILES: CCO，想做分子对接，但还不知道结合口袋。'
             '对接前需要准备什么？先别运行。再计算 CCO 的 LogP。然后执行对接。')
    assert WebTurnProjection(query, 's', []).mixed_execution_query is None


@pytest.mark.parametrize('query', [
    '检索布洛芬与阿司匹林口服生物利用度的文献，列出来源。',
    '请给出支持刚才两药口服生物利用度比较的具体数据和文献来源。',
])
def test_literature_request_does_not_claim_molecular_index_is_literature(web_handler, query):
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'not_calculated'
    assert not result['tool_result_sequence']
    assert '文献' in result['display_answer'] and '未' in result['display_answer']


@pytest.mark.parametrize('query', [
    '布洛芬的口服生物利用度一定比阿司匹林更好吗？',
    '所以布洛芬的口服生物利用度一定比阿司匹林更好吗？',
    '阿司匹林和布洛芬，哪个口服生物利用度更高？',
    '阿司匹林和布洛芬哪个口服生物利用度更高？请给出大概数值。',
])
def test_drug_bioavailability_comparison_requires_verified_evidence(web_handler, query):
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'not_calculated'
    assert not result['tool_result_sequence']
    assert '无法' in result['display_answer'] and '药代' in result['display_answer']


def test_bioavailability_comparison_does_not_consume_additional_calculation():
    from src.web.web_business import WebTurnProjection
    query = '阿司匹林和布洛芬，哪个口服生物利用度更高？再计算 CCO 的分子量。'
    projection = WebTurnProjection(query, 'separate-operations', [])
    assert projection.followup(enable_tools=True) is None


def test_bioavailability_comparison_and_independent_property_are_partial(web_handler):
    query = '阿司匹林和布洛芬，哪个口服生物利用度更高？再计算 CCO 的分子量。'
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'partial'
    assert [item['tool_name'] for item in result['tool_result_sequence']] == ['property_calculator']
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert all(term in complete['content'] for term in ('药代', '无法', 'CCO', '46.07'))


def test_bioavailability_and_property_summary_shows_both_obligations(web_handler):
    query = '阿司匹林和布洛芬，哪个口服生物利用度更高？再计算 CCO 的分子量。'
    _, frames = run_web_turn(web_handler, query, result_format='summary_details_v1')
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert all(term in complete['content'] for term in ('药代', '无法', 'CCO', '46.07'))


def test_bioavailability_and_unknown_metric_do_not_publish_partial_calculation(web_handler):
    query = '阿司匹林和布洛芬，哪个口服生物利用度更高？再计算 CCO 的分子量和 FooMetric。'
    _, frames = run_web_turn(web_handler, query, result_format='summary_details_v1')
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'rejected'
    assert '药代' in complete['content'] and '未运行' in complete['content']
    assert '46.07' not in complete['content']


def test_correcting_mixed_invalid_structure_resumes_only_requested_calculation(web_handler):
    history = []
    session = 'mixed-invalid-correction'
    query = '阿司匹林和布洛芬，哪个口服生物利用度更高？再计算 C1CC 的分子量。'
    first, _ = run_web_turn(web_handler, query, history=history, session=session,
                            result_format='summary_details_v1')
    assert first['status'] == 'rejected'
    complete, frames = run_web_turn(web_handler, '更正为 CCO，请继续', history=history,
                                    session=session, result_format='summary_details_v1')
    assert complete['status'] == 'partial'
    assert all(value in complete['content'] for value in ('药代', '无法', 'CCO', '46.07'))
    assert 'logP' not in complete['content']


def test_herg_score_mismatch_is_explained_in_page_summary(admet_web_handler):
    from src.agent.candidate_evidence_followup import accepted_candidate_evidence
    from test_candidate_evidence_followup import observation
    session = 'herg-mismatch-summary'
    raw = {'trace_id': 'herg-trace', 'tool_result_sequence': [
        observation('admet_predictor', [{'smiles': 'CCO', 'admet': {
            'total_endpoints': 1, 'risk_count': 0, 'risk_threshold': .5,
            'risk_endpoint_ids': [], 'endpoints': {'hERG': {'value': .01059,
            'unit': '-', 'task_type': 'classification', 'category': 'Toxicity',
            'source': 'admet_ai_model'}}}}], 'herg-evidence')]}
    evidence = accepted_candidate_evidence(raw, '预测 CCO 的 ADMET，列出 hERG。', session)
    history = [{'candidate_evidence': evidence}]
    query = 'hERG 预测值 0.5 是否意味着 CCO 有 50% 的概率出现心脏毒性？'
    complete, _ = run_web_turn(admet_web_handler, query, history=history, session=session,
                               result_format='summary_details_v1')
    assert complete['status'] == 'not_calculated'
    assert '不一致' in complete['content'] and '核对' in complete['content']
    corrected = 'hERG 预测值 0.01059 是否意味着 CCO 有 1.059% 的概率出现心脏毒性？'
    complete, _ = run_web_turn(admet_web_handler, corrected, history=history, session=session,
                               result_format='summary_details_v1')
    assert complete['status'] == 'completed'
    assert all(term in complete['content'] for term in ('0.01059', '分类', '不能', '概率'))


@pytest.mark.parametrize('invalid_first', [False, True])
def test_named_admet_request_continues_after_user_supplies_smiles(tmp_path, invalid_first):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for row in rows:
                other = ['AMES', 'BBB_Martins', 'Bioavailability_Ma', 'CYP1A2_Veith',
                         'CYP2C19_Veith', 'CYP2C9_Veith', 'CYP2D6_Veith', 'CYP3A4_Veith']
                for endpoint, value in [(name, .2) for name in other] + [('hERG', .12), ('DILI', .34)]:
                    row['admet']['endpoints'][endpoint] = {'value': value, 'unit': '-',
                        'task_type': 'classification', 'source': 'admet_ai_model', 'category': 'Toxicity'}
                    row['admet']['units'][endpoint] = '-'
            return rows
    handler = make_web_handler([ADMETPredictor(backend=EndpointBackend())],
        SQLiteAgentStateStore(tmp_path / 'named-resume.sqlite'), model=PlannedWebModel())
    try:
        history = []
        session = 'named-admet-resume'
        first, _ = run_web_turn(handler, '请预测阿司匹林的 ADMET，重点查看 hERG 和肝毒性。',
                                history=history, session=session, result_format='summary_details_v1')
        assert first['status'] == 'rejected' and 'SMILES:' in first['content']
        assert '下一条' in first['content']
        if invalid_first:
            invalid, _ = run_web_turn(handler, 'SMILES: C1CC', history=history, session=session,
                                      result_format='summary_details_v1')
            assert invalid['status'] == 'rejected' and 'SMILES' in invalid['content']
        complete, _ = run_web_turn(handler, 'SMILES: CC(=O)Oc1ccccc1C(=O)O',
                                   history=history, session=session, result_format='summary_details_v1')
        assert complete['status'] in ('completed', 'partial')
        assert all(term in complete['content'] for term in ('hERG', 'DILI', '0.12', '0.34', '未核验'))
    finally:
        handler.agent_system.tool_registry.close()


def test_named_admet_resume_rejects_ambiguous_or_extended_inputs():
    from src.web.web_business import WebTurnProjection
    previous = '请预测阿司匹林的 ADMET，重点查看 hERG 和肝毒性。'
    for supplied, history in [
        ('SMILES: C1CC', [{'user': previous, 'agent_used': False}]),
        ('SMILES: CCO，再预测活性', [{'user': previous, 'agent_used': False}]),
        ('SMILES: CCO', [{'user': previous + '再查询靶点。', 'agent_used': False}]),
        ('SMILES: CCO', [{'user': previous, 'agent_used': True}]),
        ('SMILES: CCO', [{'user': previous, 'agent_used': False}, {'user': '你好', 'agent_used': False}]),
    ]:
        projection = WebTurnProjection(supplied, 'named-boundary', history)
        assert projection.resumed_named_admet is None

    pending = [{'user': 'SMILES: C1CC', 'agent_used': False, 'pending_named_admet': previous}]
    unrelated = WebTurnProjection('你好', 'named-boundary', pending)
    assert 'pending_named_admet' not in unrelated.entry
    later = WebTurnProjection('SMILES: CCO', 'named-boundary', [unrelated.entry])
    assert later.resumed_named_admet is None


def test_mixed_correction_does_not_rewrite_valid_or_extended_request():
    from src.web.web_business import WebTurnProjection
    old = '阿司匹林和布洛芬，哪个口服生物利用度更高？再计算 C1CC 的分子量。'
    for correction, previous in [
        ('更正为 CCO，请继续，再预测活性。', old),
        ('更正为 CCO，请继续', old.replace('C1CC', 'CCN')),
    ]:
        projection = WebTurnProjection(correction, 'correction-boundary',
                                       [{'user': previous, 'agent_used': False}])
        assert projection.mixed_execution_query is None


def test_oral_absorption_and_bioavailability_concept_distinguishes_systemic_exposure(web_handler):
    _, frames = run_web_turn(web_handler, '口服吸收和口服生物利用度有什么区别？')
    result = result_of(frames)
    assert result['status'] == 'completed'
    assert not result['tool_result_sequence']
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert all(term in complete['content'] for term in ('肠壁', '体循环', '原形', '首过', '作用部位'))


def test_target_resolution_ranking_does_not_claim_complete_corpus(tmp_path):
    from src.agent.tools.target_database_tool import TargetDatabaseTool
    class Service:
        def search_targets(self, query, **kwargs):
            return {'status': 'resolved', 'results': [{'target_id': 1, 'gene_symbol': 'EGFR',
                'uniprot_id': 'P00533', 'organism': 'Homo sapiens', 'structure_count': 2}],
                'warnings': [], 'lookup_path': ['local']}
        def get_target_structures(self, target_id):
            return {'structures': [
                {'structure_id': '1M17', 'source': 'RCSB_PDB', 'structure_type': 'experimental',
                 'resolution': 2.6, 'ligand_ids': ['AQ4']},
                {'structure_id': '1MOX', 'source': 'RCSB_PDB', 'structure_type': 'experimental',
                 'resolution': 2.5, 'ligand_ids': ['NAG']}]}
        def close(self):
            pass
    tool = TargetDatabaseTool()
    tool._service = Service()
    handler = make_web_handler([tool], SQLiteAgentStateStore(tmp_path / 'target.sqlite'), model=PlannedWebModel())
    try:
        query = '请查询人源 EGFR 的 UniProt 编号和实验解析结构，列出分辨率最高的三个 PDB 条目并说明是否有配体。'
        _, frames = run_web_turn(handler, query)
        result = result_of(frames)
        assert result['status'] == 'partial', result
        assert '无法确认全库分辨率最高' in json.dumps(result, ensure_ascii=False)
        assert '1M17' in json.dumps(result, ensure_ascii=False)
    finally:
        handler.agent_system.tool_registry.close()


def test_property_comparison_explains_oral_absorption_limits(web_handler):
    query = ('比较阿司匹林（CC(=O)Oc1ccccc1C(=O)O）和布洛芬（CC(C)Cc1ccc(C(C)C(=O)O)cc1）'
             '的 logP 与 TPSA，并解释这些差异对口服吸收的意义。')
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'completed', result
    assert [item['tool_name'] for item in result['tool_result_sequence']] == ['property_calculator']
    answer = result['display_answer']
    assert all(value in answer for value in ('1.31', '3.073', '63.6', '37.3', 'LogP', 'TPSA'))
    assert all(value in answer for value in ('不能', '口服吸收', '溶解', '离子'))


def test_optimization_interpretation_is_reported_incomplete_after_calculation(web_handler, monkeypatch):
    async def unavailable(*args, **kwargs):
        raise RuntimeError('model unavailable')
    monkeypatch.setattr(web_handler.decision_runtime.application.model, 'generate', unavailable)
    query = '请计算 CCO 的理化性质，并指出结果对后续优化的意义。'
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'partial', result
    assert result['tool_result_sequence'][0]['status'] == 'succeeded'
    assert '模型说明未生成' in json.dumps(result, ensure_ascii=False)


def test_generic_descriptor_explanation_does_not_complete_optimization_request(web_handler, monkeypatch):
    async def explain(*args, **kwargs):
        return 'LogP 描述分子亲脂性，可作为研究参考。'
    monkeypatch.setattr(web_handler.decision_runtime.application.model, 'generate', explain)
    _, frames = run_web_turn(web_handler, '请计算 CCO 的理化性质，并指出结果对后续优化的意义。')
    result = result_of(frames)
    assert result['status'] == 'partial', result
    assert '优化建议尚未生成' in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize('subject, expected_warning', [
    ('阿司匹林（SMILES: CCO）', True),
    ('分子（SMILES: CCO）', False),
])
def test_property_and_drug_likeness_preserve_missing_optimization_warning(tmp_path, subject, expected_warning):
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    store = SQLiteAgentStateStore(tmp_path / 'mixed.sqlite')
    handler = make_web_handler([PropertyCalculator(), DrugLikenessAssessment()], store, model=PlannedWebModel())
    try:
        query = (f'请评估{subject}的理化性质和类药性，'
                 '并指出结果对后续优化的意义。')
        _, frames = run_web_turn(handler, query)
        result = result_of(frames)
        assert result['status'] == 'partial', (result.get('metadata'), [x.get('tool_name') for x in result.get('tool_result_sequence', [])])
        assert '优化建议尚未生成' in json.dumps(result, ensure_ascii=False)
        assert ('名称与所给结构的对应关系未核验' in json.dumps(result, ensure_ascii=False)) is expected_warning
    finally:
        handler.agent_system.tool_registry.close()


def test_oral_explanation_does_not_discard_a_later_operation():
    from src.web.web_business import WebTurnProjection
    query = '比较 CCO 和 CCN 的 logP 与 TPSA，并解释这些差异对口服吸收的意义。再预测 CCO 的活性。'
    assert WebTurnProjection(query, 'session', []).execution_query() == query


def test_oral_explanation_keeps_an_explicit_property_bound(web_handler):
    query = ('比较 CCO 和 CCN 的 logP 与 TPSA，并判断 logP < 1，'
             '并解释这些差异对口服吸收的意义。')
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'completed', result
    assert [item['tool_name'] for item in result['tool_result_sequence']] == ['property_calculator']
    assert '约束检查' in result['display_answer'] and result['display_answer'].count('满足') == 2
    assert '口服吸收' in result['display_answer'] and '不能' in result['display_answer']


def test_oral_explanation_with_ambiguous_bound_is_not_reported_complete(web_handler):
    query = ('比较 CCO 和 CCN 的 logP 与 TPSA，并判断 logP = 1，'
             '并解释这些差异对口服吸收的意义。')
    _, frames = run_web_turn(web_handler, query)
    assert result_of(frames)['status'] != 'completed'


def test_oral_explanation_reports_rounding_uncertainty_for_bound(web_handler):
    query = ('比较 CCO 和 CCN 的 logP 与 TPSA，并判断 logP < -0.001，'
             '并解释这些差异对口服吸收的意义。')
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'partial', result
    assert '舍入精度不足' in result['display_answer']
    assert '口服吸收' in result['display_answer']


def test_clogp_request_reaches_property_calculator(web_handler):
    _, frames = run_web_turn(web_handler, '计算 CCO 的 cLogP。')
    result = result_of(frames)
    assert result['status'] == 'completed'
    assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'


@pytest.mark.parametrize('query', [
    '计算阿司匹林（SMILES: CC(=O)Oc1ccccc1C(=O)O）的分子量、logP、氢键供体和受体数，并判断是否符合 Lipinski 五规则。',
    '计算 CC(=O)Oc1ccccc1C(=O)O 的分子量、logP、HBD、HBA，并判断是否符合 Lipinski 规则。',
])
def test_lipinski_request_runs_both_requested_assessments(tmp_path, query):
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    handler = make_web_handler([PropertyCalculator(), DrugLikenessAssessment()],
        SQLiteAgentStateStore(tmp_path / 'lipinski.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, query)
        result = result_of(frames)
        assert result['status'] == 'completed', result
        assert {item['tool_name'] for item in result['tool_result_sequence']} == {
            'property_calculator', 'drug_likeness_assessment'}
        assert set(result['metadata']['metrics']) >= {'mw', 'logp', 'hbd', 'hba'}
        complete = next(frame for frame in frames if frame['type'] == 'complete')
        assert 'Lipinski' in complete['content'] and '180' in complete['content']
    finally:
        handler.agent_system.tool_registry.close()


@pytest.mark.parametrize('query', [
    '请分别判断 CCO 和 CCN 是否符合 Lipinski 五规则。',
    '请分别评估 SMILES: CC(=O)Oc1ccccc1C(=O)O 和 SMILES: CC(C)Cc1ccc(cc1)C(C)C(=O)O 是否符合 Lipinski 五规则。',
])
def test_explicit_batch_lipinski_runs_real_assessment(tmp_path, query):
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    handler = make_web_handler([DrugLikenessAssessment()],
        SQLiteAgentStateStore(tmp_path / 'batch-lipinski.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, query)
        result = result_of(frames)
        complete = next(frame for frame in frames if frame['type'] == 'complete')
        assert result['status'] == 'completed', result
        assert [item['tool_name'] for item in result['tool_result_sequence']] == ['drug_likeness_assessment']
        assert len(result['tool_result_sequence'][0]['data']) == 2
        assert 'Lipinski' in complete['content'] and '生物利用度' in complete['content']
        assert 'QED' not in complete['content'] and '综合评分' not in complete['content']
    finally:
        handler.agent_system.tool_registry.close()


def test_batch_lipinski_plan_rejects_invalid_structure():
    plan = ScientificObligationCompiler().compile('请分别判断 CCO 和 C1CC 是否符合 Lipinski 五规则。')
    assert plan.status == 'rejected' and not plan.steps


def test_batch_lipinski_keeps_explicit_property_request(tmp_path):
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    handler = make_web_handler([PropertyCalculator(), DrugLikenessAssessment()],
        SQLiteAgentStateStore(tmp_path / 'combined-batch-lipinski.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '计算 CCO 和 CCN 的分子量、logP，并判断是否符合 Lipinski 五规则。')
        result = result_of(frames)
        complete = next(frame for frame in frames if frame['type'] == 'complete')
        assert result['status'] == 'completed', result
        assert {item['tool_name'] for item in result['tool_result_sequence']} == {'property_calculator', 'drug_likeness_assessment'}
        assert all(value in complete['content'] for value in ('CCO', 'CCN', 'Lipinski', '46.07', '45.08'))
    finally:
        handler.agent_system.tool_registry.close()


def test_batch_lipinski_does_not_accept_missing_result_row(tmp_path):
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    class IncompleteAssessment(DrugLikenessAssessment):
        def execute(self, query):
            result = super().execute(query)
            result['data'] = result['data'][:1]
            return result
    handler = make_web_handler([IncompleteAssessment()],
        SQLiteAgentStateStore(tmp_path / 'missing-batch-lipinski.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '请分别判断 CCO 和 CCN 是否符合 Lipinski 五规则。')
        assert result_of(frames)['status'] != 'completed'
    finally:
        handler.agent_system.tool_registry.close()


@pytest.mark.parametrize(('subject', 'warning'), [
    ('阿司匹林（SMILES: CCO）', True),
    ('阿司匹林（SMILES: CC(=O)Oc1ccccc1C(=O)O）', True),
    ('CC(=O)Oc1ccccc1C(=O)O', False),
])
def test_combined_lipinski_reports_unverified_molecule_names(tmp_path, subject, warning):
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    handler = make_web_handler([PropertyCalculator(), DrugLikenessAssessment()],
        SQLiteAgentStateStore(tmp_path / 'identity.sqlite'), model=PlannedWebModel())
    try:
        query = f'计算{subject}的分子量、logP、HBD、HBA，并判断是否符合 Lipinski 规则。'
        _, frames = run_web_turn(handler, query)
        result = result_of(frames)
        assert result['status'] == 'completed'
        assert any('名称与所给结构的对应关系未核验' in item for item in result['warnings']) is warning
    finally:
        handler.agent_system.tool_registry.close()


@pytest.mark.parametrize(('query', 'warning'), [
    ('计算 Cc1cc(O)cc(F)c1 和 CCc1cc(N)cc(OC)c1 的分子量和 logP。', False),
    ('计算 CC(=O)Oc1ccccc1C(=O)O 的分子量。', False),
    ('请计算乙醇（CCO）的分子量和 logP。', True),
    ('请计算乙醇（CCN）的分子量和 logP。', True),
    ('请计算乙醇 CCN 的分子量和 logP。', True),
    ('请计算乙醇 CCO 的分子量和 logP。', True),
    ('请计算 CCO 的分子量和 logP。', False),
    ('比较阿司匹林 CCO 和 对乙酰氨基酚 CCN 的分子量和 logP。', True),
    ('比较 CCO 和 阿司匹林 CCN 的分子量和 logP。', True),
    ('比较 CCO 和 CCN 的分子量和 logP。', False),
    ('比较乙醇（CCO）和 CCc1cc(N)cc(OC)c1 的分子量。', True),
])
def test_name_structure_warning_ignores_smiles_branches(web_handler, query, warning):
    _, frames = run_web_turn(web_handler, query)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'completed', complete
    assert any('名称与所给结构的对应关系未核验' in item for item in result_of(frames)['warnings']) is warning


def test_property_threshold_checks_report_rounding_uncertainty(web_handler):
    _, frames = run_web_turn(web_handler,
        '比较 CCO 和 CCN 的分子量与 logP，判断它们是否满足分子量不大于 45.08 Da。')
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert 'CCO' in complete['content'] and '不满足' in complete['content']
    assert 'CCN' in complete['content'] and '未验证' in complete['content']
    assert '舍入' in complete['content'], result_of(frames)


def test_two_molecule_natural_threshold_question_keeps_boundary_uncertainty(web_handler):
    query = '比较 CCO 和 CCN 的分子量和 logP，判断两者是否都不超过分子量 45.08 Da。'
    _, frames = run_web_turn(web_handler, query)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert 'CCO' in complete['content'] and '不满足' in complete['content']
    assert 'CCN' in complete['content'] and '舍入精度不足' in complete['content']


def test_unrecognized_constraint_never_displays_an_empty_markdown_table(web_handler):
    query = '比较 CCO 和 CCN 的分子量和 logP，判断两者是否都不超过分子量 45.08 kDa。'
    _, frames = run_web_turn(web_handler, query)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert '部分约束无法识别或存在歧义，未验证' in complete['content']
    assert '| SMILES | 约束 | 结果 |' not in complete['content']


def test_mixed_supported_and_unrecognized_constraints_are_not_dropped(web_handler):
    query = ('比较 CCO 和 CCN 的分子量和 logP，判断它们是否满足'
             '分子量不大于 50 Da 和 logP 不大于 2 kDa。')
    _, frames = run_web_turn(web_handler, query)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert '分子量 <= 50.0 Da' in complete['content'] and '满足' in complete['content']
    assert '部分约束无法识别或存在歧义，未验证' in complete['content']
    assert '未验证：logP' in complete['content']


def test_unknown_constraints_name_each_unverified_metric():
    from src.web.property_constraints import property_constraint_report
    report, partial = property_constraint_report(
        '分子量不大于 50 kDa，logP 不大于 2 kDa。',
        [{'smiles': 'CCO', 'properties': {'molecular_weight': 46.07, 'logp': -0.001}}])
    assert partial
    assert '未验证：分子量、logP' in report
    assert '| SMILES | 约束 | 结果 |' not in report


def test_property_threshold_question_is_answered_without_hiding_extra_metrics(web_handler):
    query = '请计算 CCO 的分子量，并判断其分子量是否小于 50 Da。'
    _, frames = run_web_turn(web_handler, query)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'completed', complete
    assert 'CCO' in complete['content'] and '分子量 < 50.0 Da' in complete['content']
    assert '满足' in complete['content']
    _, frames = run_web_turn(web_handler, query[:-1] + '，并计算 FooMetric。')
    assert result_of(frames)['status'] == 'rejected'


@pytest.mark.parametrize('query,properties,state,incomplete', [
    ('分子量不大于 50 Da', {'molecular_weight': 46.07}, '满足', False),
    ('分子量小于 46.07 Da', {'molecular_weight': 46.07}, '未验证', True),
    ('HBA 不大于 1', {'hba': 1}, '满足', False),
    ('logP 小于 2', {}, '未验证', True),
    ('分子量约为 50', {'molecular_weight': 46.07}, '未验证', True),
])
def test_property_threshold_report_respects_precision_and_missing_values(query, properties, state, incomplete):
    from src.web.property_constraints import property_constraint_report
    report, partial = property_constraint_report(query, [{'smiles': 'CCO', 'properties': properties}])
    assert state in report
    assert partial is incomplete


def test_human_buche_full_name_activity_reaches_the_tool(tmp_path):
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    handler = make_web_handler([ActivityPredictorTool()], SQLiteAgentStateStore(tmp_path / 'activity.sqlite'),
                               model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '请预测 SMILES: CCO 对人丁酰胆碱酯酶（BuChE）的活性。')
        result = result_of(frames)
        assert result['tool_result_sequence'], result
        assert result['tool_result_sequence'][0]['tool_name'] == 'activity_predictor'
    finally:
        handler.agent_system.tool_registry.close()


@pytest.mark.parametrize('query', [
    '请预测 SMILES: CCO 对人丁酰胆碱酯酶（BuChE）的抑制活性。',
    '请预测 SMILES: CCO 对人丁酰胆碱酯酶（BuChE）的抑制活性，并说明模型是否支持该靶点以及该分子是否在模型适用域内。',
])
def test_human_buche_inhibitory_activity_reaches_the_tool(tmp_path, query):
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    handler = make_web_handler([ActivityPredictorTool()], SQLiteAgentStateStore(tmp_path / 'human-inhibition.sqlite'),
                               model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, query)
        result = result_of(frames)
        assert result['tool_result_sequence'], result
        assert result['tool_result_sequence'][0]['tool_name'] == 'activity_predictor'
    finally:
        handler.agent_system.tool_registry.close()


def test_explicit_generation_only_respects_prediction_exclusions():
    from src.agent.contracts import AgentContext
    from src.agent.contracts.web_requirements import WEB_TOOLS
    from src.agent.scientific_session.web_requirements import compile_web_requirements
    query = '请仅生成两个潜在 BuChE 抑制剂候选分子并给出 SMILES，不要预测活性或 ADMET。'
    plan = compile_web_requirements(AgentContext(query, 'gen-only', session_id='owner'), allowed_tools=WEB_TOOLS)
    assert [step.tool_name for step in plan.steps] == ['llm_molecular_generator']
    assert {'activity_predictor', 'admet_predictor'} <= set(plan.forbidden_tools)
    assert plan.metadata['requested_count'] == 2
    with pytest.raises(ValueError):
        compile_web_requirements(AgentContext(query + ' 然后对 CCO 执行分子对接。',
                                             'gen-and-dock', session_id='owner'), allowed_tools=WEB_TOOLS)


@pytest.mark.parametrize('first,expected', [
    ('计算 CCO 的 LogP。', 'completed'),
    ('计算 CCO 和 CCN 的 LogP。', 'rejected'),
])
def test_property_followup_resolves_only_one_prior_molecule(actual_app, first, expected):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as app:
            cookie = await cookie_for(app.app)
            async with ActualSocket(app.app, cookie) as socket:
                await socket.ready()
                initial = result_of(await socket.turn({'message': first, 'enable_tools': True}))
                assert initial['status'] == 'completed'
                return result_of(await socket.turn({'message': '这个分子的分子量是多少？',
                                                    'enable_tools': True}))

    result = asyncio.run(run())
    assert result['status'] == expected
    if expected == 'completed':
        assert result['metadata']['workflow_version'] == 'web-property-v1'
        assert len(result['tool_result_sequence']) == 1
        assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
        assert 'CCO' in json.dumps(result, ensure_ascii=False)
        assert '46.07' in json.dumps(result, ensure_ascii=False)


def test_property_followup_uses_most_recent_single_molecule(actual_app):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as app:
            cookie = await cookie_for(app.app)
            async with ActualSocket(app.app, cookie) as socket:
                await socket.ready()
                for message in ('计算 CCO 的分子量和 LogP。', '再计算 CCN 的分子量和 LogP。'):
                    assert result_of(await socket.turn({'message': message, 'enable_tools': True}))['status'] == 'completed'
                return result_of(await socket.turn({'message': '刚才这个分子的 LogP 是多少？',
                                                    'enable_tools': True}))

    result = asyncio.run(run())
    assert result['status'] == 'completed'
    assert 'CCN' in json.dumps(result, ensure_ascii=False)
    assert 'CCO' not in json.dumps(result, ensure_ascii=False)


def test_ambiguous_property_followup_asks_for_a_structure(actual_app):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as app:
            cookie = await cookie_for(app.app)
            async with ActualSocket(app.app, cookie) as socket:
                await socket.ready()
                assert result_of(await socket.turn({'message': '计算 CCO 和 CCN 的分子量和 LogP。',
                                                    'enable_tools': True}))['status'] == 'completed'
                clarification = result_of(await socket.turn({'message': '刚才这个分子的 LogP 是多少？',
                                                             'enable_tools': True}))
                resumed = result_of(await socket.turn({'message': 'CCN', 'enable_tools': True}))
                return clarification, resumed

    result, resumed = asyncio.run(run())
    rendered = json.dumps(result, ensure_ascii=False)
    assert '多个分子' in rendered and 'SMILES' in rendered
    assert 'LogP' in rendered
    assert '候选集合' not in rendered
    assert resumed['status'] == 'completed'
    assert 'CCN' in json.dumps(resumed, ensure_ascii=False)
    assert 'CCO' not in json.dumps(resumed, ensure_ascii=False)


def test_property_clarification_does_not_inherit_across_boundaries():
    from src.web.web_business import WebTurnProjection
    history = [{'property_clarification': {'session_id': 'owner', 'metrics': 'LogP',
                                           'candidates': ['CCO', 'CCN']}}]
    assert WebTurnProjection('CCN', 'owner', history).execution_query() == '计算 CCN 的 LogP。'
    assert WebTurnProjection('CCN', 'other', history).execution_query() == 'CCN'
    assert WebTurnProjection('CCC', 'owner', history).execution_query() == 'CCC'
    assert WebTurnProjection('CCN，再预测活性', 'owner', history).execution_query() == 'CCN，再预测活性'
    assert WebTurnProjection('CCN', 'owner', history + [{'user': '其他问题'}]).execution_query() == 'CCN'


@pytest.mark.parametrize('query,expected,excluded', [
    ('请计算 CCO 的 LogP。', ('logP',), ('TPSA', '分子量', 'QED')),
    ('计算 CCO 的分子量和 TPSA。', ('分子量', 'TPSA'), ('logP', 'QED')),
    ('计算 CCO 的分子性质。', ('分子量', 'logP', 'TPSA', 'QED'), ()),
    ('计算 CCO 和 CCN 的 LogP。', ('CCO', 'CCN', 'logP'), ('TPSA', '分子量', 'QED')),
    ('计算 CCO 和 CCN 的分子量和 TPSA。', ('CCO', 'CCN', '分子量', 'TPSA'), ('logP', 'QED')),
])
def test_compiled_property_summary_shows_requested_metrics(web_handler, query, expected, excluded):
    from src.agent.contracts import AgentResult, RunOutcome
    from src.agent.orchestrators import WorkflowOrchestrator
    from src.web.result_summary import prepare_summary
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'completed'
    raw = result['tool_result_sequence'][0]
    observation = WorkflowOrchestrator._result_from_checkpoint(raw['tool_name'],
        {'output': raw, 'status': raw['status']})
    published = AgentResult(result['trace_id'], True, '', tool_results=[observation],
        outcome=RunOutcome.COMPLETED, metadata={**result['metadata'], 'binding_profile': 'medchat-web-v1'})
    frame, details = prepare_summary(web_handler, published)
    assert all(label in frame['display_answer'] for label in expected)
    assert all(label not in frame['display_answer'] for label in excluded)
    assert 'molecular_weight' in json.dumps(details, ensure_ascii=False)


def test_compiled_drug_likeness_displays_validated_result_in_summary(tmp_path):
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    handler = make_web_handler([DrugLikenessAssessment()], SQLiteAgentStateStore(tmp_path / 'agent.sqlite'),
                               model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '请评估阿司匹林（SMILES: CC(=O)Oc1ccccc1C(=O)O）的成药性。')
        result = result_of(frames)
        assert result['status'] == 'completed'
        from src.agent.contracts import AgentResult, RunOutcome
        from src.agent.orchestrators import WorkflowOrchestrator
        from src.web.result_summary import prepare_summary
        raw = result['tool_result_sequence'][0]
        observation = WorkflowOrchestrator._result_from_checkpoint(raw['tool_name'],
            {'output': raw, 'status': raw['status']})
        published = AgentResult(result['trace_id'], True, '', tool_results=[observation],
            outcome=RunOutcome.COMPLETED, metadata={**result['metadata'], 'binding_profile': 'medchat-web-v1'})
        frame, _ = prepare_summary(handler, published)
        assert '类药性评估结果' in frame['display_answer']
        assert 'QED' in frame['display_answer'] and 'CC(=O)Oc1ccccc1C(=O)O' in frame['display_answer']
        assert '规则综合评分' in frame['display_answer']
        assert 'Lipinski' in frame['display_answer'] and '违反 0 项' in frame['display_answer']
        observation.quality['validated'] = False
        frame, _ = prepare_summary(handler, published)
        assert '类药性评估结果' not in frame['display_answer']
    finally:
        handler.agent_system.tool_registry.close()


def test_named_structure_discloses_that_name_was_not_verified(web_handler):
    _, frames = run_web_turn(web_handler, '计算阿司匹林（CCO）的分子量和 logP。')
    result = result_of(frames)
    assert result['status'] == 'completed'
    assert any('名称' in warning and '未核验' in warning for warning in result['warnings'])
    assert 'CCO' in result['display_answer']


def test_name_warning_does_not_mark_completed_explanation_as_partial(web_handler, monkeypatch):
    async def explanation(*args, **kwargs):
        return '分子量和 logP 可用于初步描述分子的理化特征。'

    monkeypatch.setattr('src.web.models.generate_for_chat', explanation)
    _, frames = run_web_turn(web_handler,
        '比较阿司匹林（CCO）和布洛芬（CCN）的分子量和 logP，并说明结果对口服成药性的意义。')
    result = result_of(frames)
    assert result['status'] == 'completed'
    assert any('未核验' in warning for warning in result['warnings'])
    assert '结果解释' in result['display_answer']


def test_descriptor_significance_keeps_calculation_and_explanation(web_handler, monkeypatch):
    async def explanation(*args, **kwargs):
        return 'logP 描述分子的亲脂性，可用于药物设计中的初步筛选。'

    monkeypatch.setattr('src.web.models.generate_for_chat', explanation)
    _, frames = run_web_turn(web_handler, '计算 CCO 的 logP，并解释这个数值对药物设计的意义。')
    result = result_of(frames)
    assert result['status'] == 'completed'
    assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
    assert 'CCO' in result['display_answer'] and '结果解释' in result['display_answer']


def test_descriptor_significance_does_not_hide_unknown_metric(web_handler):
    _, frames = run_web_turn(web_handler, '计算 CCO 的 logP 和 FooMetric，并解释这个数值对药物设计的意义。')
    result = result_of(frames)
    assert result['status'] == 'rejected' and result['tool_result_sequence'] == []


def test_property_comparison_identifies_computed_values(web_handler):
    _, frames = run_web_turn(web_handler,
        '比较 CCO 和 CCN 的理化性质，并指出哪些指标是计算值。')
    result = result_of(frames)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert result['status'] == complete['status'] == 'completed'
    assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
    assert 'CCO' in complete['content'] and 'CCN' in complete['content']
    assert '计算' in complete['content'] and '非实测' in complete['content']


def test_property_experiment_origin_and_followup_reuse_actual_evidence(web_handler):
    history = []
    session = 'property-origin-followup'
    first, _ = run_web_turn(web_handler,
        '请计算 SMILES: CC(=O)Oc1ccccc1C(=O)O 的分子量和 LogP，并说明这些数值是否来自实验测量。',
        history=history, session=session)
    assert '计算' in first['content'] and '非实测' in first['content']
    followup, frames = run_web_turn(web_handler,
        '刚才的 LogP 是实验测得的吗？这两个数值是怎么得到的？', history=history, session=session)
    assert all(term in followup['content'] for term in ('1.31', '180.16', 'RDKit', '实验'))
    assert not result_of(frames)['tool_result_sequence']


def test_salt_property_origin_and_followup_use_whole_input(web_handler):
    history = []
    session = 'salt-structure-evidence'
    first, _ = run_web_turn(web_handler,
        '请计算 SMILES: C[NH3+].[Cl-] 的分子量和 LogP，并说明这些值对应完整盐还是游离碱。',
        history=history, session=session)
    assert '67.52' in first['content'] and '-4.138' in first['content']
    assert '完整' in first['content'] and '游离碱' in first['content']
    followup, frames = run_web_turn(web_handler,
        '刚才的分子量包含氯离子吗？这个 LogP 能否直接作为游离碱的实验分配系数？',
        history=history, session=session)
    assert all(term in followup['content'] for term in ('氯离子', '67.52', '-4.138', '不能', '实验'))
    assert not result_of(frames)['tool_result_sequence']


def test_property_calculation_reports_unavailable_pka_without_inventing_value(web_handler):
    query = ('请计算 SMILES: CC(=O)O 的分子量，并预测它的 pKa 和 pH 7.4 下的电离比例。'
             '若无法预测，请明确说明。')
    complete, frames = run_web_turn(web_handler, query)
    assert complete['status'] == 'partial'
    assert '60.05' in complete['content'] and 'pKa' in complete['content']
    assert '电离比例' in complete['content'] and '未' in complete['content']
    assert [item['tool_name'] for item in result_of(frames)['tool_result_sequence']] == ['property_calculator']
    invalid, _ = run_web_turn(web_handler, query.replace('CC(=O)O', 'CC(=O)O$bad'))
    assert invalid['status'] == 'rejected' and '60.05' not in invalid['content']
    from src.web.web_business import WebTurnProjection
    extra = query + '再预测活性。'
    assert WebTurnProjection(extra, 'session', []).execution_query() == extra


def test_single_molecule_natural_threshold_question_checks_each_bound(web_handler):
    query = ('请计算 SMILES: CC(=O)O 的分子量和 logP，并判断其是否符合'
             '分子量小于 500、logP 小于 5 的要求。')
    complete, frames = run_web_turn(web_handler, query)
    assert complete['status'] == 'completed'
    assert all(value in complete['content'] for value in ('60.05', '0.091', '约束检查', '500', '5'))
    assert complete['content'].count('满足') >= 2
    assert [item['tool_name'] for item in result_of(frames)['tool_result_sequence']] == ['property_calculator']


def test_threshold_request_reports_unavailable_ionization_as_partial(web_handler):
    query = ('请计算 SMILES: CC(=O)O 的分子量和 logP，并判断其是否符合'
             '分子量小于 500、logP 小于 5 的要求；同时告诉我 pH 7.4 下的电离比例，'
             '无法预测的项目请明确说明。')
    complete, frames = run_web_turn(web_handler, query)
    assert complete['status'] == 'partial'
    assert all(term in complete['content'] for term in ('60.05', '0.091', '约束检查', '电离比例', '未'))
    assert complete['content'].count('满足') >= 2
    assert [item['tool_name'] for item in result_of(frames)['tool_result_sequence']] == ['property_calculator']
    from src.web.web_business import WebTurnProjection
    extra = query + '再预测活性。'
    assert WebTurnProjection(extra, 'session', []).execution_query() == extra
    invalid, _ = run_web_turn(web_handler, query.replace('CC(=O)O', 'CC(=O)O$bad'))
    assert invalid['status'] == 'rejected' and '60.05' not in invalid['content']
    unknown, _ = run_web_turn(web_handler, query.replace('logP，并', 'logP 和 FooMetric，并'))
    assert unknown['status'] != 'completed' and '60.05' not in unknown['content']


def test_user_assay_selectivity_comparison_uses_each_compound_and_unit(web_handler):
    query = ('我有一组假设的同条件酶实验数据：化合物 A 对靶点 X 的 IC50 为 80 nM，'
             '对靶点 Y 为 0.8 μM；化合物 B 对 X 为 0.2 μM，对 Y 为 400 nM。'
             '请比较各自对 X 相对于 Y 的选择性倍数，并说明谁对 X 更有选择性。')
    complete, frames = run_web_turn(web_handler, query)
    assert complete['status'] == 'completed'
    assert all(term in complete['content'] for term in ('A', 'B', '10', '2', 'X', 'Y'))
    assert not result_of(frames)['tool_result_sequence']


def test_assay_comparison_rejects_invalid_or_ambiguous_input():
    from src.web.assay_comparison import compare_supplied_ic50
    query = ('我有一组假设的同条件酶实验数据：化合物 A 对靶点 X 的 IC50 为 80 nM，'
             '对靶点 Y 为 0.8 μM；化合物 B 对 X 为 0.2 μM，对 Y 为 400 nM。'
             '请比较各自对 X 相对于 Y 的选择性倍数，并说明谁对 X 更有选择性。')
    result = compare_supplied_ic50(query)
    assert '化合物 A：10' in result['content'] and '化合物 B：2' in result['content']
    assert '化合物 A 对 X' in result['content'] and '亲和力' in result['content']
    assert compare_supplied_ic50(query.replace('80 nM', '0 nM'))['status'] == 'partial'
    assert compare_supplied_ic50(query.replace('对 Y 为 400 nM', '对 Z 为 400 nM')) is None
    assert compare_supplied_ic50(query.replace('化合物 B', '化合物 A')) is None
    assert compare_supplied_ic50(query + '再预测 CCO 的活性。') is None
    reversed_query = query.replace('对 X 相对于 Y', '对 Y 相对于 X').replace('谁对 X', '谁对 Y')
    reversed_result = compare_supplied_ic50(reversed_query)
    assert '化合物 A：0.1' in reversed_result['content']
    assert '化合物 B：0.5' in reversed_result['content']
    assert '化合物 B 对 Y' in reversed_result['content']


def test_censored_assay_selectivity_keeps_bounds_and_only_ranks_when_proven(web_handler):
    query = ('我有一组假设的同条件酶实验数据：化合物 A 对靶点 X 的 IC50 为 80 nM，'
             '对靶点 Y 为 >0.8 μM；化合物 B 对 X 为 0.2 μM，对 Y 为 400 nM。'
             '请比较各自对 X 相对于 Y 的选择性倍数，并说明谁对 X 更有选择性。')
    complete, _ = run_web_turn(web_handler, query)
    assert complete['status'] == 'completed'
    assert '> 10' in complete['content'] and 'B：2' in complete['content']
    assert '化合物 A 对 X' in complete['content']
    from src.web.assay_comparison import compare_supplied_ic50
    uncertain = compare_supplied_ic50(query.replace('>0.8 μM', '>0.08 μM'))
    assert '无法确定' in uncertain['content']
    reverse = query.replace('对 X 相对于 Y', '对 Y 相对于 X').replace('谁对 X', '谁对 Y')
    assert '< 0.1' in compare_supplied_ic50(reverse)['content']
    assert '化合物 B 对 Y' in compare_supplied_ic50(reverse)['content']
    touching = query.replace('>0.8 μM', '≥0.16 μM')
    assert '无法确定' in compare_supplied_ic50(touching)['content']
    assert '化合物 A 对 X' in compare_supplied_ic50(touching.replace('≥', '>'))['content']
    both = query.replace('80 nM', '<80 nM')
    assert '> 10' in compare_supplied_ic50(both)['content']
    unresolved = query.replace('80 nM', '>80 nM')
    assert '无法确定' in compare_supplied_ic50(unresolved)['content']
    rounded = query.replace('80 nM', '81 nM')
    assert '> 9.87' in compare_supplied_ic50(rounded)['content']


def test_named_batch_compares_lipophilicity_from_computed_logp(web_handler):
    query = ('比较阿司匹林（CC(=O)Oc1ccccc1C(=O)O）和'
             '对乙酰氨基酚（CC(=O)Nc1ccc(O)cc1）的分子量和 logP，指出哪个更亲脂。')
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert result['status'] == complete['status'] == 'completed'
    assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
    assert 'CC(=O)Oc1ccccc1C(=O)O' in complete['content']
    assert 'CC(=O)Nc1ccc(O)cc1' in complete['content']
    assert 'logP：第二个结构较高' in complete['content']
    assert '第二个结构更亲脂' in complete['content']
    assert 'TPSA' not in complete['content'] and 'QED' not in complete['content']


def test_batch_property_followup_reuses_verified_values(web_handler):
    history = []
    session = 'property-followup'
    _, first_frames = run_web_turn(web_handler, '比较 CCO 和 CCN 的分子量和 logP。',
                                   history=history, session=session)
    assert result_of(first_frames)['status'] == 'completed'
    _, frames = run_web_turn(web_handler, '刚才这两个分子中哪个的分子量更高？各是多少？',
                             history=history, session=session)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'completed'
    assert all(value in complete['content'] for value in ('CCO', 'CCN', '46.07', '45.08'))
    assert 'CCO` 的计算分子量更高' in complete['content']


def test_oral_interpretation_preserves_actual_descriptor_evidence(web_handler):
    history = []
    session = 'oral-evidence-chain'
    run_web_turn(web_handler, '请计算 SMILES: CC(=O)Oc1ccccc1C(=O)O 的 LogP 和 TPSA。',
                 history=history, session=session)
    explanation, _ = run_web_turn(web_handler,
        '根据刚才的结果，这个分子的口服生物利用度一定很高吗？还需要哪些数据才能判断？',
        history=history, session=session)
    assert all(term in explanation['content'] for term in ('1.31', '63.60', '不能'))
    values, frames = run_web_turn(web_handler,
        '刚才该分子的 LogP 和 TPSA 分别是多少？请引用实际计算值。', history=history, session=session)
    assert all(term in values['content'] for term in ('1.31', '63.60'))
    assert not result_of(frames)['tool_result_sequence']


def test_oral_interpretation_does_not_guess_ambiguous_evidence(web_handler):
    history = []
    session = 'oral-ambiguous'
    run_web_turn(web_handler, '比较 CCO 和 CCN 的 logP 和 TPSA。', history=history, session=session)
    complete, _ = run_web_turn(web_handler,
        '根据刚才的结果，这个分子的口服生物利用度一定很高吗？还需要哪些数据才能判断？',
        history=history, session=session)
    assert '无法' in complete['content'] or '多个' in complete['content']


def test_property_recall_does_not_invent_unrequested_tpsa(web_handler):
    history = []
    session = 'oral-missing-tpsa'
    run_web_turn(web_handler, '计算 CCO 的 LogP。', history=history, session=session)
    complete, frames = run_web_turn(web_handler,
        '刚才该分子的 LogP 和 TPSA 分别是多少？请引用实际计算值。', history=history, session=session)
    assert '无法确认' in complete['content']
    assert not result_of(frames)['tool_result_sequence']


@pytest.mark.parametrize(('query', 'expected', 'excluded'), [
    ('刚才这个分子的 TPSA 具体是多少？', ('TPSA', '63.60'), 'logP'),
    ('刚才该分子的 LogP 是多少？', ('logP', '1.310'), 'TPSA'),
    ('上一轮这个分子的 TPSA 和 LogP 分别是多少？', ('TPSA', '63.60', 'logP', '1.310'), None),
])
def test_property_recall_selects_only_requested_verified_metrics(web_handler, query, expected, excluded):
    history = []
    session = 'property-recall-selection'
    run_web_turn(web_handler, '计算 SMILES: CC(=O)Oc1ccccc1C(=O)O 的 LogP 和 TPSA。',
                 history=history, session=session)
    complete, frames = run_web_turn(web_handler, query, history=history, session=session)
    assert all(value in complete['content'] for value in expected)
    assert excluded is None or excluded not in complete['content']
    assert not result_of(frames)['tool_result_sequence']


def test_property_recall_does_not_hide_new_computation():
    from src.web.web_business import WebTurnProjection
    query = '刚才这个分子的 TPSA 是多少？再计算 CCN 的 LogP。'
    assert WebTurnProjection(query, 's', []).followup(enable_tools=True) is None


@pytest.mark.parametrize(('selection', 'included', 'excluded'), [
    ('前两个。', ('CCO', 'CCN'), 'CCC'),
    ('第一个和第三个。', ('CCO', 'CCC'), 'CCN'),
    ('CCO 和 CCC。', ('CCO', 'CCC'), 'CCN'),
    ('OCC 和 CCC。', ('CCO', 'CCC'), 'CCN'),
])
def test_batch_lipinski_clarification_continues_with_selected_rows(web_handler, selection, included, excluded):
    history = []
    session = 'lipinski-clarification'
    _, first = run_web_turn(web_handler, '请比较 CCO、CCN 和 CCC 的分子量、logP、HBA 和 HBD。', history=history, session=session)
    assert result_of(first)['status'] == 'completed'
    _, clarification = run_web_turn(web_handler, '根据刚才的结果，分别判断这两个分子是否符合 Lipinski 五规则。', history=history, session=session)
    assert result_of(clarification)['status'] == 'not_calculated'
    _, frames = run_web_turn(web_handler, selection, history=history, session=session)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'completed'
    assert all(value in complete['content'] for value in included)
    assert excluded not in complete['content']
    assert '生物利用度' in complete['content']


def test_natural_candidate_lipophilicity_followup_uses_previous_turn(web_handler):
    history = []
    session = 'candidate-lipophilicity'
    _, first = run_web_turn(web_handler, '比较 CCO 和 CCN 的分子量和 logP。',
                            history=history, session=session)
    assert result_of(first)['status'] == 'completed'
    _, frames = run_web_turn(web_handler, '这两个候选哪个更亲脂？各是多少？',
                             history=history, session=session)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'completed'
    assert all(value in complete['content'] for value in ('CCO', 'CCN', '-0.001', '-0.035'))
    assert '`CCO` 的计算LogP更高' in complete['content']


def test_lipophilicity_followup_can_reference_previous_logp(web_handler):
    history = []
    session = 'lipophilicity-previous-logp'
    _, first = run_web_turn(web_handler, '比较 CCO 和 CCN 的分子量和 logP。',
                            history=history, session=session)
    assert result_of(first)['status'] == 'completed'
    _, frames = run_web_turn(web_handler, '这两个分子哪个更亲脂？请根据刚才的 LogP 解释。',
                             history=history, session=session)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'completed'
    assert all(value in complete['content'] for value in ('CCO', 'CCN', '-0.001', '-0.035', '更高'))


def test_equal_rounded_logp_does_not_claim_equal_exact_values(web_handler):
    structures = 'Cc1cc(O)cc(F)c1 和 CCc1cc(N)cc(OC)c1'
    history = []
    _, first = run_web_turn(web_handler, f'计算 {structures} 的分子量和 logP。',
                            history=history, session='rounding-followup')
    assert result_of(first)['status'] == 'completed'
    _, frames = run_web_turn(web_handler, '刚才两个分子中哪个 LogP 更高？',
                             history=history, session='rounding-followup')
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert '1.840' in complete['content'] and '舍入' in complete['content']
    assert '两者的计算LogP相同' not in complete['content']


def test_equal_rounded_logp_does_not_claim_equal_lipophilicity(web_handler):
    structures = 'Cc1cc(O)cc(F)c1 和 CCc1cc(N)cc(OC)c1'
    _, frames = run_web_turn(web_handler, f'比较 {structures} 的 logP，指出哪个更亲脂。')
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert '舍入' in complete['content']
    assert '亲脂性相同' not in complete['content']


def test_natural_lipophilicity_question_uses_calculated_values(web_handler):
    _, frames = run_web_turn(web_handler, '请计算 CCO 和 CCN 的 logP，哪个更亲脂？')
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'completed'
    assert all(value in complete['content'] for value in ('CCO', 'CCN', '-0.001', '-0.035', '第一个结构更亲脂'))


def test_hydrophobicity_question_compares_the_calculated_logp(web_handler):
    _, frames = run_web_turn(web_handler, '请计算 CCO 和 CCN 的 logP，并说明哪个更疏水。')
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'completed'
    assert all(value in complete['content'] for value in ('CCO', 'CCN', '-0.001', '-0.035', '第一个结构更亲脂'))


def test_property_first_structure_list_compares_calculated_lipophilicity(web_handler):
    query = ('比较这两个分子的理化性质：CCO 和 c1ccccc1O。'
             '请列出分子量、LogP、TPSA、氢键供体和受体，并说明哪个更亲脂。')
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert result['status'] == 'completed', complete
    assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
    assert all(value in complete['content'] for value in ('CCO', 'c1ccccc1O', '-0.001', '1.392', '第二个结构更亲脂'))


def test_property_first_structure_list_keeps_unknown_metric_rejected(web_handler):
    query = ('比较这两个分子的理化性质：CCO 和 c1ccccc1O。'
             '请列出分子量、LogP 和 FooMetric，并说明哪个更亲脂。')
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert result['tool_result_sequence'] == []


def test_natural_lipophilicity_question_keeps_unknown_metric_rejected(web_handler):
    _, frames = run_web_turn(web_handler, '请计算 CCO 和 CCN 的 logP 和 FooMetric，哪个更亲脂？')
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert result['tool_result_sequence'] == []


def test_batch_logd_followup_does_not_invent_values_or_formula(web_handler):
    history = []
    session = 'batch-logd-followup'
    _, first = run_web_turn(web_handler, '请计算 CCO 和 CCN 的 logP，哪个更亲脂？',
                            history=history, session=session)
    assert result_of(first)['status'] == 'completed'
    _, frames = run_web_turn(web_handler, '这些结果能直接推断 pH 7.4 时的 logD 吗？',
                             history=history, session=session)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'completed'
    assert all(value in complete['content'] for value in ('不能', 'logP', 'logD', '未计算'))
    assert '10^' not in complete['content']


def test_batch_logd_value_followup_recognizes_previous_molecules(web_handler):
    history = []
    session = 'batch-logd-value-followup'
    _, first = run_web_turn(web_handler, '请计算 CCO 和 CCN 的 logP，哪个更亲脂？',
                            history=history, session=session)
    assert result_of(first)['status'] == 'completed'
    _, frames = run_web_turn(web_handler, '那在 pH 7.4 下它们的 logD 呢？',
                             history=history, session=session)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'not_calculated'
    assert all(value in complete['content'] for value in ('CCO', 'CCN', 'logD', '未计算'))
    assert '请提供分子' not in complete['content']


def test_optimization_choice_followup_reuses_calculated_descriptors(web_handler):
    history = []
    session = 'optimization-choice'
    _, first = run_web_turn(web_handler, '请比较 CCO 和 CCN 的分子量、logP 和 TPSA。',
                            history=history, session=session)
    assert result_of(first)['status'] == 'completed'
    _, frames = run_web_turn(web_handler, '根据刚才的性质，哪个分子更适合继续优化？还需要哪些证据？',
                             history=history, session=session)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert all(value in complete['content'] for value in ('CCO', 'CCN', '20.23', '26.02', '不能', '靶点'))


def test_lipophilicity_comparison_does_not_hide_unknown_metric(web_handler):
    _, frames = run_web_turn(web_handler,
        '比较 CCO 和 CCN 的 logP 和 FooMetric，指出哪个更亲脂。')
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert result['tool_result_sequence'] == []


def test_two_molecule_property_comparison_can_request_each_value(web_handler):
    query = ('请比较 CC(=O)Oc1ccccc1C(=O)O 和 CC(=O)Nc1ccc(O)cc1 '
             '的分子量、LogP 和 TPSA，并列出各自数值。')
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'completed'
    assert any(item['tool_name'] == 'property_calculator' for item in result['tool_result_sequence'])
    bad = query.replace('各自数值', '各自数值和 FooMetric')
    _, rejected = run_web_turn(web_handler, bad)
    assert result_of(rejected)['status'] == 'rejected'


def test_computed_value_explanation_does_not_hide_unknown_metric(web_handler):
    _, frames = run_web_turn(web_handler,
        '比较 CCO 和 CCN 的分子量和 FooMetric，并指出哪些指标是计算值。')
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert result['tool_result_sequence'] == []


@pytest.mark.parametrize(('structures', 'position'), [('CCO 和 C1CC', 2), ('C1CC 和 CCO', 1)])
def test_invalid_batch_structure_reports_position_without_partial_calculation(web_handler, structures, position):
    _, frames = run_web_turn(web_handler, f'请计算 {structures} 的分子量与 logP，并比较结果。')
    result = result_of(frames)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert result['status'] == 'rejected' and result['tool_result_sequence'] == []
    assert f'第 {position} 个' in complete['content'] and 'SMILES' in complete['content']
    assert 'C1CC' not in complete['content']


def test_correcting_invalid_second_batch_structure_resumes_original_properties(web_handler):
    history = []
    session = 'batch-invalid-correction'
    _, first = run_web_turn(web_handler, '请比较 CCO 和 C1CC 的分子量、logP 和 TPSA。',
                            history=history, session=session)
    assert result_of(first)['status'] == 'rejected'
    _, frames = run_web_turn(web_handler, '第二个改成 CCN，再算一次。', history=history, session=session)
    result = result_of(frames)
    assert result['status'] == 'completed'
    rows = result['tool_result_sequence'][0]['data']
    assert [row['smiles'] for row in rows] == ['CCO', 'CCN']
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert all(term in complete['content'] for term in ('CCO', 'CCN', 'logP', 'TPSA'))


def test_natural_batch_correction_does_not_treat_correction_words_as_name(web_handler):
    history = []
    session = 'batch-correction-no-name'
    run_web_turn(web_handler, '请比较 CCO 和 C1CC 的分子量和 LogP。', history=history, session=session)
    _, frames = run_web_turn(web_handler, '第二个写错了，应该是 C1CC1，请继续比较。',
                             history=history, session=session)
    result = result_of(frames)
    assert result['status'] == 'completed'
    assert not any('名称与所给结构' in warning for warning in result['warnings'])


@pytest.mark.parametrize('correction', ['更正为 CCO，请继续', '更正为 C1CC1，请继续原来的计算。'])
def test_correcting_unclosed_ring_single_structure_resumes_properties(web_handler, correction):
    history = []
    session = 'single-invalid-correction'
    _, first = run_web_turn(web_handler, '计算 C1CC 的分子量和 logP。', history=history, session=session)
    assert result_of(first)['status'] == 'rejected'
    _, frames = run_web_turn(web_handler, correction, history=history, session=session)
    result = result_of(frames)
    assert result['status'] == 'completed'
    assert result['tool_result_sequence']
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert all(term in complete['content'] for term in (('C1CC1' if 'C1CC1' in correction else 'CCO'), 'logP'))


def test_single_structure_correction_does_not_hide_additional_requests():
    from src.web.property_comparison import corrected_property_query
    previous = [{'user': '请计算 C1CC 的分子量和 LogP。', 'agent_used': False}]
    correction = '更正为 C1CC1，请继续原来的计算。'
    assert corrected_property_query(correction, previous) == '请计算 C1CC1 的分子量和 LogP。'
    extended = correction + '再预测活性。'
    assert corrected_property_query(extended, previous) == extended
    assert corrected_property_query(correction, [{'user': '请计算 CCO 的 LogP。', 'agent_used': True}]) == correction


def test_batch_correction_does_not_override_valid_or_additional_requests():
    from src.web.property_comparison import corrected_property_query
    correction = '第二个改成 CCN，再算一次。'
    rejected = {'user': '请比较 CCO 和 C1CC 的分子量、logP 和 TPSA。', 'agent_used': False}
    assert corrected_property_query(correction, [rejected]) == '请比较 CCO 和 CCN 的分子量、logP 和 TPSA。'
    assert corrected_property_query(correction, [{**rejected, 'agent_used': True}]) == correction
    assert corrected_property_query(correction, [{'user': '请比较 CCO 和 CCC 的 logP。', 'agent_used': False}]) == correction
    assert corrected_property_query('第一个改成 CCN，再算一次。', [rejected]) == '第一个改成 CCN，再算一次。'
    extended = correction + '再预测 CCN 的 ADMET。'
    assert corrected_property_query(extended, [rejected]) == extended
    assert corrected_property_query(correction, [{**rejected, 'user': rejected['user'] + '然后预测活性。'}]) == correction


def test_natural_batch_correction_resumes_only_the_invalid_molecule():
    from src.web.property_comparison import corrected_property_query
    rejected = {'user': '请比较 CCO 和 C1CC 的分子量和 LogP。', 'agent_used': False}
    correction = '第二个写错了，应该是 C1CC1，请继续比较。'
    assert corrected_property_query(correction, [rejected]) == '请比较 CCO 和 C1CC1 的分子量和 LogP。'
    assert corrected_property_query(correction, []) == correction
    assert corrected_property_query(correction, [{**rejected, 'agent_used': True}]) == correction
    extra = correction + '再预测活性。'
    assert corrected_property_query(extra, [rejected]) == extra


def test_corrected_batch_can_calculate_and_compare_requested_results(web_handler):
    _, frames = run_web_turn(web_handler, '请计算 CCO 和 C1CC1 的分子量与 logP，并比较结果。')
    result = result_of(frames)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert result['status'] == complete['status'] == 'completed'
    assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
    assert 'CCO' in complete['content'] and 'C1CC1' in complete['content']
    assert '分子量' in complete['content'] and 'logP' in complete['content'] and '较高' in complete['content']


def test_comparison_clause_cannot_hide_unknown_metrics(web_handler):
    _, frames = run_web_turn(web_handler, '请计算 CCO 和 C1CC1 的分子量、FooMetric，并比较结果。')
    result = result_of(frames)
    assert result['status'] == 'rejected' and result['tool_result_sequence'] == []


def test_labeled_oral_lead_comparison_has_no_drug_name_warning(web_handler, monkeypatch):
    async def explanation(*args, **kwargs):
        return '分子量和 logP 可用于描述分子的理化特征，不能单独决定口服表现。'

    monkeypatch.setattr('src.web.models.generate_for_chat', explanation)
    query = ('比较两个候选分子：A 的 SMILES 是 CC(=O)Oc1ccccc1C(=O)O，'
             'B 的 SMILES 是 CC(C)Cc1ccc(cc1)[C@@H](C)C(=O)O。'
             '请计算它们的分子量和 LogP，说明哪个更适合作为口服药物先导物，并说明判断的局限。')
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert len(result['tool_result_sequence'][0]['data']) == 2
    assert not any('名称与所给结构' in warning for warning in result['warnings'])


def test_oral_screening_keeps_calculated_results_and_partial_status(web_handler, monkeypatch):
    async def explanation(*args, **kwargs):
        return '分子量、logP 和 TPSA 可用于初步描述分子的理化特征。'

    monkeypatch.setattr('src.web.models.generate_for_chat', explanation)
    _, frames = run_web_turn(web_handler,
        '比较 CCO 和 CCN 的分子量、logP 和 TPSA，并说明哪一个更适合口服筛选。')
    result = result_of(frames)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert result['status'] == complete['status'] == 'partial'
    assert result['tool_result_sequence'][0]['tool_name'] == 'property_calculator'
    assert 'CCO' in complete['content'] and 'CCN' in complete['content']
    assert '无法确定哪个分子更适合口服筛选' in complete['content']


@pytest.mark.parametrize('query', ['请计算这个分子的 LogP', '计算分子的分子量和 logP。'])
def test_missing_smiles_waits_for_input_without_scientific_tool(web_handler, query):
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)

    assert result["status"] == "waiting_for_input", result
    assert result["metadata"]["stop_reason"] == "clarification_required"
    assert result["tool_result_sequence"] == []
    assert not any(frame.get("type") == "agent_event" and
                   frame.get("event", {}).get("event") == "tool_started"
                   for frame in frames)


@pytest.mark.parametrize('reconnect', [False, True])
@pytest.mark.parametrize('query', ['请计算这个分子的 LogP', '计算分子的分子量和 logP。'])
def test_missing_smiles_continuation_resumes_same_scientific_obligation(web_handler, reconnect, query):
    async def drive_two_turns():
        session = "missing-smiles-continuation"
        sender = _Sender(WebSocket(session))
        sender._runtime = web_handler.decision_runtime

        first = _Turn(dict(message=query,
                           enable_tools=True, enable_rag=False))
        web_handler.decision_runtime.active_owners.add(first)
        await web_handler.decision_runtime._execute(web_handler, sender, first)
        waiting_frames = sender.websocket.messages[:]
        waiting = result_of(waiting_frames)
        continuation_id = waiting["metadata"].get("continuation_id")
        assert waiting["status"] == "waiting_for_input"
        assert isinstance(continuation_id, str) and len(continuation_id) == 32
        assert sender.waiting is not None
        assert sender.waiting.context.query == query
        assert sender.waiting.prepared.required_tools == {'property_calculator'}
        assert waiting['tool_result_sequence'] == []
        runtime = web_handler.decision_runtime
        assert runtime._restore_durable_waiting(
            _Sender(WebSocket('another-owner')), first.trace_id, continuation_id) is None
        if reconnect:
            sender = _Sender(WebSocket(session))
            sender._runtime = runtime
            sender.waiting = runtime._restore_durable_waiting(sender, first.trace_id, continuation_id)
            assert sender.waiting is not None

        second = _Turn(dict(message="CCO", enable_tools=True, enable_rag=False),
                       trace_id=sender.waiting.context.trace_id,
                       waiting=sender.waiting)
        web_handler.decision_runtime.active_owners.add(second)
        start = len(sender.websocket.messages)
        await web_handler.decision_runtime._execute(web_handler, sender, second)
        assert runtime._restore_durable_waiting(sender, first.trace_id, continuation_id) is None
        return waiting, continuation_id, sender.websocket.messages[start:]

    waiting, continuation_id, resumed_frames = asyncio.run(drive_two_turns())

    assert waiting["status"] == "waiting_for_input"
    assert isinstance(continuation_id, str) and len(continuation_id) == 32
    resumed = result_of(resumed_frames)

    assert resumed["status"] == "completed", resumed
    assert len(resumed["tool_result_sequence"]) == 1
    assert resumed["tool_result_sequence"][0]["tool_name"] == "property_calculator"
    assert "46.07" in json.dumps(resumed, ensure_ascii=False)
    assert resumed['metadata']['task_requirements']['request']['query'] == query
    from src.agent.contracts import AgentResult, RunOutcome
    from src.agent.orchestrators import WorkflowOrchestrator
    from src.web.decision_chat import web_scientific_display
    from src.web.result_summary import prepare_summary
    raw = resumed['tool_result_sequence'][0]
    observation = WorkflowOrchestrator._result_from_checkpoint(raw['tool_name'],
        {'output': raw, 'status': raw['status']})
    published = AgentResult(resumed['trace_id'], True, '', final_answer=resumed['final_answer'],
        tool_results=[observation], outcome=RunOutcome.COMPLETED, metadata=resumed['metadata'])
    answer = web_scientific_display(published, concise=True)
    summary, _ = prepare_summary(web_handler, published)
    assert summary['display_answer'] == answer
    assert 'logP' in answer and '-0.001' in answer
    if query == '请计算这个分子的 LogP':
        assert '分子量' not in answer and 'TPSA' not in answer and 'QED' not in answer
    else:
        assert '分子量' in answer and '46.07' in answer and 'TPSA' not in answer


@pytest.mark.parametrize('query', ['计算阿司匹林的分子性质', '计算乙醇的分子性质',
                                  '请计算分子阿司匹林的分子性质'])
def test_browser_cannot_enable_missing_structure_admission(web_handler, query):
    _, frames = run_web_turn(web_handler, query,
                            allow_molecular_clarification=True)
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert not result['tool_result_sequence']
    assert not result['metadata'].get('continuation_id')


@pytest.mark.parametrize('query', [
    '计算分子的分子量和 logP。分子为阿司匹林。',
    '计算分子的分子量和 logP。对象为患者。',
    '计算分子的分子量和 logP；分子为阿司匹林。',
    '计算分子的分子量和 logP\n对象为患者。',
    '计算分子的分子量和 logP。这个分子为阿司匹林。',
])
def test_bare_subject_clarification_requires_the_entire_generic_request(query):
    plan = ScientificObligationCompiler().compile(query)
    assert not _resumable_scientific_clarification(plan)


@pytest.mark.parametrize('query', [
    '计算分子的分子量和 logP。分子为阿司匹林。',
    '计算分子的分子量和 logP。对象为患者。',
])
@pytest.mark.parametrize('reconnect', [False, True])
def test_later_subject_has_no_admission_or_authenticated_cco_continuation(actual_app, query, reconnect):
    async def run():
        async with actual_app(mode='pi', ordinary_policy='medchat-web-v1') as b:
            cookie = await cookie_for(b.app)

            async def reject_resume(socket, result):
                await socket.send({'type': 'resume', 'trace_id': result['trace_id'],
                    'continuation_id': result['metadata'].get('continuation_id', 'a' * 32),
                    'message': 'CCO'})
                assert await socket.receive() == {'type': 'error', 'code': 'continuation_unavailable'}

            async with ActualSocket(b.app, cookie) as socket:
                await socket.ready()
                result = result_of(await socket.turn({'message': query,
                    'enable_tools': True, 'enable_rag': False}))
                if not reconnect:
                    await reject_resume(socket, result)
            if reconnect:
                async with ActualSocket(b.app, cookie) as socket:
                    await socket.ready()
                    await reject_resume(socket, result)

            assert result['status'] == 'rejected'
            assert not result['metadata'].get('continuation_id')
            assert result['tool_result_sequence'] == []
            assert b.app.agent_state_store.get_run(result['trace_id']) is None
            assert b.app.agent_state_store.get_tool_executions(result['trace_id']) == []
            assert not b.calls and not b.claims

    asyncio.run(run())


@pytest.mark.parametrize('query,capability', [
    ('请分析这个分子的分子性质，返回LogP和LogD', 'metric:logd'),
    ('计算分子的分子量和 LogD。', 'metric:logd'),
    ('计算分子的 FooMetric 和 logP。', 'metric:unreviewed'),
    ('计算分子的分子量和 FooMetric。', 'metric:unreviewed'),
    ('计算分子的量子轨道能量和 logP。', 'metric:unreviewed'),
])
def test_missing_structure_cannot_drop_unsupported_metric_on_continuation(web_handler, query, capability):
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert result['metadata']['missing_capabilities'] == [capability]
    assert not result['tool_result_sequence']
    assert not result['metadata'].get('continuation_id')


def test_exclusion_cannot_drop_a_positive_unsupported_metric(web_handler):
    _, frames = run_web_turn(web_handler, '只计算 CCO 的 LogP 和 LogD，不要 ADMET')
    result = result_of(frames)
    assert result['status'] == 'rejected'
    assert 'metric:logd' in result['metadata']['missing_capabilities']
    assert not result['tool_result_sequence']


@pytest.mark.parametrize('query', [
    '请计算 CCO 的 logD（pH 7.4）。',
    '请计算 CCO 的 logP 和 pH 7.4 下的 logD，分别给出结果。',
])
def test_logd_rejection_identifies_unavailable_metric_without_substitution(web_handler, query):
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert result['status'] == 'rejected' and not result['tool_result_sequence']
    assert 'logD' in complete['content'] and '未' in complete['content']
    assert 'logP' in complete['content'] and '不能' in complete['content']
    assert '未执行' in complete['content'] or '未计算' in complete['content']


@pytest.mark.parametrize('fail_admet', [False, True])
def test_resumed_compound_obligation_cannot_finish_after_only_one_tool(admet_web_handler, fail_admet):
    class EarlyFinishModel(PlannedWebModel):
        async def decide(self, messages, **kwargs):
            response = await super().decide(messages, **kwargs)
            if isinstance(response.decision, ToolDecision):
                ids = [identity for m in messages if m.get('role') == 'tool'
                       if (identity := json.loads(m['content']).get('quality', {}).get('evidence_id'))]
                if ids:
                    return replace(response, decision=FinishDecision(version='1', action='finish',
                        response_kind='scientific', text='Protocol-only early finish', evidence_ids=ids))
            return response

    handler = admet_web_handler
    if fail_admet:
        handler.agent_system.tools['admet_predictor'].backend.fail_ids.add('molecule-001')
    runtime = handler.decision_runtime
    handler.model = runtime.application.model = EarlyFinishModel()

    async def run():
        sender = _Sender(WebSocket('compound-clarification'))
        sender._runtime = runtime
        first = _Turn(dict(message='请计算这个分子的 LogP 并预测 ADMET',
                           enable_tools=True, enable_rag=False))
        runtime.active_owners.add(first)
        await runtime._execute(handler, sender, first)
        assert sender.waiting is not None, sender.websocket.messages
        assert sender.waiting.prepared.required_tools == {'property_calculator', 'admet_predictor'}
        start = len(sender.websocket.messages)
        second = _Turn(dict(message='CCO'), trace_id=first.trace_id, waiting=sender.waiting)
        runtime.active_owners.add(second)
        await runtime._execute(handler, sender, second)
        return result_of(sender.websocket.messages[start:])

    result = asyncio.run(run())
    assert result['status'] == ('partial' if fail_admet else 'completed'), result
    assert [item['tool_name'] for item in result['tool_result_sequence']] == [
        'property_calculator', 'admet_predictor']


def test_other_mixed_unsupported_request_is_rejected_before_execution(web_handler):
    _, frames = run_web_turn(web_handler, "请分析 CCO 的 docking 和 LogP")
    result = result_of(frames)

    assert result["status"] == "rejected"
    assert result["metadata"]["missing_capabilities"] == ["docking"]
    assert result["tool_result_sequence"] == []
    assert not any(frame.get("type") == "agent_event" and
                   frame.get("event", {}).get("event") == "tool_started"
                   for frame in frames)


def test_property_and_docking_rejection_explains_what_was_not_run(web_handler):
    query = '先计算 SMILES: CCO 的 LogP，再与人源 EGFR 对接并给出结合能。'
    _, frames = run_web_turn(web_handler, query)
    result = result_of(frames)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert result['status'] == 'rejected' and result['tool_result_sequence'] == []
    assert all(text in complete['content'] for text in ('对接', '未执行', '受体', '对接盒'))


def test_unavailable_property_tool_is_not_reported_as_success(web_handler):
    web_handler.decision_runtime.registry.resolve(
        "property_calculator", require_available=False
    ).set_available(False, "test tool unavailable")

    _, frames = run_web_turn(
        web_handler,
        "请分析 CCO 的分子性质，返回 LogP 和 TPSA。",
    )
    result = result_of(frames)

    assert result["status"] in {"unavailable", "failed", "partial"}
    assert result["success"] is False
    observation = result["tool_result_sequence"][0]
    assert observation["status"] != "succeeded"
    assert not result.get("evidence")


def test_ready_admet_plan_executes_real_tool_and_publishes_model_provenance(
    admet_web_handler,
):
    _, frames = run_web_turn(admet_web_handler, "请预测 CCO 的 ADMET 特征")
    result = result_of(frames)

    assert result["status"] == "completed", result
    assert result["metadata"]["workflow_version"] == "web-admet-v1"
    assert result["metadata"]["required_tools"] == ["admet_predictor"]
    observation = result["tool_result_sequence"][0]
    assert observation["tool_name"] == "admet_predictor"
    assert observation["status"] == "succeeded"
    assert observation["provenance"]["tool_name"] == "admet_predictor"
    assert observation["provenance"]["model_version"] == "1.4.0"
    assert "gmm-llama:latest" not in json.dumps(result, ensure_ascii=False)


def test_batch_admet_displays_requested_endpoint_for_each_input(tmp_path):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for index, row in enumerate(rows):
                row['admet']['endpoints']['hERG'] = {
                    'value': (index + 1) / 10, 'unit': '-', 'task_type': 'classification',
                    'source': 'admet_ai_model', 'category': 'Toxicity'}
                row['admet']['units']['hERG'] = '-'
            return rows
    backend = EndpointBackend()
    handler = make_web_handler([ADMETPredictor(backend=backend)],
        SQLiteAgentStateStore(tmp_path / 'batch-admet.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '比较 CCO 和 CCN 的 ADMET，并列出各自的 hERG 预测值。')
        result = result_of(frames)
        assert result['status'] == 'completed', result
        assert backend.calls and backend.calls[0][0] == ['CCO', 'CCN']
        rows = result['tool_result_sequence'][0]['data']
        assert [row['smiles'] for row in rows] == ['CCO', 'CCN']
        complete = next(frame for frame in frames if frame['type'] == 'complete')
        assert 'CCO' in complete['content'] and 'CCN' in complete['content']
        assert 'hERG' in complete['content'] and '0.1' in complete['content'] and '0.2' in complete['content']
        assert '| 端点 | `CCO` | `CCN` |' in complete['content']
        assert '| hERG | 0.1 (-, 分类) | 0.2 (-, 分类) |' in complete['content']
    finally:
        handler.agent_system.tool_registry.close()


@pytest.mark.parametrize('missing', [False, True])
def test_batch_admet_risk_comparison_displays_requested_endpoints(tmp_path, missing):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for index, row in enumerate(rows):
                for endpoint in ('hERG', 'DILI'):
                    if missing and index == 1 and endpoint == 'hERG':
                        continue
                    row['admet']['endpoints'][endpoint] = {
                        'value': (index + 1) / 10, 'unit': '-', 'task_type': 'classification',
                        'source': 'admet_ai_model', 'category': 'Toxicity'}
                    row['admet']['units'][endpoint] = '-'
            return rows
    handler = make_web_handler([ADMETPredictor(backend=EndpointBackend())],
        SQLiteAgentStateStore(tmp_path / 'batch-risk.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '请预测 CCO 和 CCN 的 ADMET，比较它们的 hERG 和 DILI 风险。')
        complete = next(frame for frame in frames if frame['type'] == 'complete')
        assert complete['status'] == ('partial' if missing else 'completed')
        assert all(value in complete['content'] for value in ('CCO', 'CCN', 'hERG', 'DILI'))
        assert ('未返回' in complete['content']) is missing
    finally:
        handler.agent_system.tool_registry.close()


def test_batch_property_and_admet_fulfills_both_obligations(tmp_path):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for index, row in enumerate(rows):
                row['admet']['endpoints']['hERG'] = {
                    'value': (index + 1) / 10, 'unit': '-', 'task_type': 'classification',
                    'source': 'admet_ai_model', 'category': 'Toxicity'}
                row['admet']['units']['hERG'] = '-'
            return rows
    backend = EndpointBackend()
    handler = make_web_handler([PropertyCalculator(), ADMETPredictor(backend=backend)],
        SQLiteAgentStateStore(tmp_path / 'batch-combined.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '请比较 CCO 和 CCN 的分子量、logP 和 ADMET，并列出各自的 hERG 预测值。')
        result = result_of(frames)
        assert result['status'] == 'completed', result
        assert [item['tool_name'] for item in result['tool_result_sequence']] == ['property_calculator', 'admet_predictor']
        assert all([row['smiles'] for row in item['data']] == ['CCO', 'CCN'] for item in result['tool_result_sequence'])
        complete = next(frame for frame in frames if frame['type'] == 'complete')
        assert all(value in complete['content'] for value in ('CCO', 'CCN', '分子量', 'logP', 'hERG', '0.1', '0.2'))
        assert 'TPSA' not in complete['content'] and 'QED' not in complete['content']
    finally:
        handler.agent_system.tool_registry.close()


def test_batch_property_and_admet_missing_requested_endpoint_is_partial(admet_web_handler):
    _, frames = run_web_turn(admet_web_handler, '请比较 CCO 和 CCN 的分子量、logP 和 ADMET，并列出各自的 hERG 预测值。')
    result = result_of(frames)
    assert result['status'] == 'partial'
    assert 'hERG' in result['display_answer'] and '未返回' in result['display_answer']


def test_focus_list_without_value_suffix_reports_missing_endpoints(admet_web_handler, monkeypatch):
    async def explanation(*args, **kwargs):
        return '模型预测不是实验结果，需要通过实验验证。'
    monkeypatch.setattr('src.web.models.generate_for_chat', explanation)
    query = '请计算 SMILES: CCO 的分子量和 LogP，并预测 ADMET，重点列出 hERG 和 DILI，说明结果的局限。'
    complete, _ = run_web_turn(admet_web_handler, query, result_format='summary_details_v1')
    assert complete['status'] == 'partial'
    assert all(term in complete['content'] for term in ('hERG', 'DILI', '未返回'))


@pytest.mark.parametrize('query', [
    '请预测 SMILES: CCO 的 ADMET，重点列出 hERG 和肝毒性预测结果。',
    '请预测 SMILES: CCO 的 ADMET，重点查看 hERG 和 DILI。',
])
def test_mixed_chinese_focus_reports_missing_requested_admet_endpoints(admet_web_handler, query):
    complete, _ = run_web_turn(admet_web_handler, query, result_format='summary_details_v1')
    assert complete['status'] == 'partial'
    assert all(term in complete['content'] for term in ('hERG', 'DILI', '未返回'))


def test_admet_out_of_range_endpoint_does_not_hide_valid_results(admet_web_handler, monkeypatch):
    backend = admet_web_handler.agent_system.tools['admet_predictor'].backend
    original = backend.predict_batch
    def predict(smiles, ids):
        rows = original(smiles, ids)
        for row in rows:
            row['admet']['endpoints']['Half_Life_Obach'] = {
                'value': -6.2, 'unit': 'hr', 'task_type': 'regression',
                'category': 'Excretion', 'source': 'admet_ai_model', 'validity': 'out_of_range'}
            row['admet']['units']['Half_Life_Obach'] = 'hr'
        return rows
    monkeypatch.setattr(backend, 'predict_batch', predict)
    complete, _ = run_web_turn(admet_web_handler, '预测 CCO 的 ADMET，并列出 HIA_Hou 的预测值。',
                               result_format='summary_details_v1')
    assert all(term in complete['content'] for term in ('HIA_Hou', '0.91', 'Half_Life_Obach', '超出', '不可用'))


def test_batch_property_and_admet_tool_failure_is_not_complete(tmp_path):
    backend = FakeBackend(fail_ids={'molecule-002'})
    handler = make_web_handler([PropertyCalculator(), ADMETPredictor(backend=backend)],
        SQLiteAgentStateStore(tmp_path / 'batch-combined-failure.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '比较 CCO 和 CCN 的分子量、logP 和 ADMET。')
        result = result_of(frames)
        assert result['status'] != 'completed' and not result['success']
    finally:
        handler.agent_system.tool_registry.close()


@pytest.mark.parametrize(('threshold', 'status', 'expected'), [
    ('0', 'completed', '满足'),
    ('-0.001', 'partial', '舍入精度不足'),
])
def test_batch_property_admet_checks_requested_bounds(admet_web_handler, threshold, status, expected):
    query = f'比较 CCO 和 CCN 的分子量、logP 和 ADMET，并判断哪些分子满足 logP < {threshold}。'
    _, frames = run_web_turn(admet_web_handler, query)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == status
    assert all(value in complete['content'] for value in ('CCO', 'CCN', 'ADMET', '约束检查', expected))


def test_batch_property_admet_reports_both_missing_endpoint_and_bound(admet_web_handler):
    query = '比较 CCO 和 CCN 的 logP 和 ADMET，判断 logP < 0，并列出各自的 hERG 预测值。'
    _, frames = run_web_turn(admet_web_handler, query)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert all(value in complete['content'] for value in ('约束检查', '满足', 'hERG', '未返回'))


@pytest.mark.parametrize(('threshold', 'status', 'expected'), [
    ('0', 'completed', '满足'),
    ('-0.001', 'partial', '舍入精度不足'),
])
def test_single_property_admet_checks_requested_bound(admet_web_handler, threshold, status, expected):
    query = f'请计算 CCO 的 logP 并预测 ADMET，判断 logP 是否小于 {threshold}。'
    _, frames = run_web_turn(admet_web_handler, query)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == status
    assert all(value in complete['content'] for value in ('CCO', 'logP', 'ADMET', '约束检查', expected))
    assert 'TPSA' not in complete['content'] and 'QED' not in complete['content']


def test_batch_admet_missing_endpoint_is_partial_for_each_molecule(tmp_path):
    backend = FakeBackend()
    handler = make_web_handler([ADMETPredictor(backend=backend)],
        SQLiteAgentStateStore(tmp_path / 'missing-endpoint.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '比较 CCO 和 CCN 的 ADMET，并列出各自的 hERG 预测值。')
        result = result_of(frames)
        assert result['status'] == 'partial'
        assert 'hERG' in result['display_answer'] and '未返回' in result['display_answer']
        assert 'CCO' in result['display_answer'] and 'CCN' in result['display_answer']
    finally:
        handler.agent_system.tool_registry.close()


def test_batch_admet_failed_row_cannot_be_reported_as_complete(tmp_path):
    backend = FakeBackend(fail_ids={'molecule-002'})
    handler = make_web_handler([ADMETPredictor(backend=backend)],
        SQLiteAgentStateStore(tmp_path / 'partial-batch.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, '比较 CCO 和 CCN 的 ADMET，并列出各自的 hERG 预测值。')
        result = result_of(frames)
        assert result['status'] != 'completed'
        assert not result['success']
        assert backend.calls and backend.calls[0][0] == ['CCO', 'CCN']
        complete = next(frame for frame in frames if frame['type'] == 'complete')
        assert 'CCN' in complete['content'] and '失败' in complete['content'], json.dumps(result, ensure_ascii=False)
    finally:
        handler.agent_system.tool_registry.close()


@pytest.mark.parametrize(('query', 'warning', 'status'), [
    ('预测阿司匹林（SMILES: CCO）的 ADMET。', True, 'completed'),
    ('预测阿司匹林（SMILES: CCO）的 ADMET，重点给出 hERG 端点的预测值。', True, 'partial'),
    ('预测 CCO 的 ADMET。', False, 'completed'),
])
def test_admet_preserves_name_warning_and_endpoint_status(admet_web_handler, query, warning, status):
    _, frames = run_web_turn(admet_web_handler, query)
    result = result_of(frames)
    assert result['status'] == status
    assert any('名称与所给结构的对应关系未核验' in item for item in result['warnings']) is warning
    if status == 'partial':
        assert 'hERG' in result['display_answer'] and '未返回' in result['display_answer']


@pytest.mark.parametrize('query', [
    '请预测 CCO 的 ADMET，重点给出 hERG 端点的预测值。',
    '请预测 CCO 的 hERG 毒性风险并给出预测值。',
])
def test_requested_admet_endpoint_missing_from_model_is_partial(admet_web_handler, query):
    _, frames = run_web_turn(admet_web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'partial'
    assert 'hERG' in result['display_answer'] and '未返回' in result['display_answer']
    assert result['tool_result_sequence'][0]['tool_name'] == 'admet_predictor'


def test_multiple_admet_endpoints_missing_one_is_partial(admet_web_handler):
    _, frames = run_web_turn(admet_web_handler, '预测 CCO 的 ADMET，并列出 HIA_Hou 和 hERG 的预测值。')
    result = result_of(frames)
    assert result['status'] == 'partial'
    assert 'HIA_Hou' in result['display_answer'] and 'hERG' in result['display_answer']
    assert '未返回' in result['display_answer']


def test_multiple_endpoint_explanation_does_not_invent_missing_endpoint(admet_web_handler, monkeypatch):
    async def explain(*args, **kwargs):
        return '模型预测并非实验结果，需要通过实验验证。'
    monkeypatch.setattr('src.web.models.generate_for_chat', explain)
    _, frames = run_web_turn(admet_web_handler,
        '预测 CCO 的 ADMET，给出 HIA_Hou 和 hERG 的预测结果并说明它们的含义及局限。')
    result = result_of(frames)
    assert result['status'] == 'partial'
    assert 'hERG' in result['display_answer'] and '未返回' in result['display_answer']
    assert '端点数值解释未完成' in json.dumps(result, ensure_ascii=False)


def test_multiple_requested_admet_endpoints_are_visible_and_explained(tmp_path, monkeypatch):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for row in rows:
                for endpoint, value in [('hERG', .12), ('DILI', .34)]:
                    row['admet']['endpoints'][endpoint] = {'value': value, 'unit': '-',
                        'task_type': 'classification', 'source': 'admet_ai_model', 'category': 'Toxicity'}
                    row['admet']['units'][endpoint] = '-'
            return rows
    async def explain(*args, **kwargs):
        return '模型预测并非实验结果，需结合适用域和实验验证。'
    monkeypatch.setattr('src.web.models.generate_for_chat', explain)
    handler = make_web_handler([ADMETPredictor(backend=EndpointBackend())],
        SQLiteAgentStateStore(tmp_path / 'multi-explain.sqlite'), model=PlannedWebModel())
    try:
        query = '预测 CCO 的 ADMET，给出 hERG 和 DILI 的预测结果并说明它们的含义及局限。'
        _, frames = run_web_turn(handler, query)
        result = result_of(frames)
        answer = result['display_answer']
        assert result['status'] == 'completed', result
        assert all(value in answer for value in ('hERG', '0.12', 'DILI', '0.34', '阻断', '肝损伤', '不能', '实验'))
    finally:
        handler.agent_system.tool_registry.close()


def test_direct_cyp3a4_inhibition_displays_model_endpoint_and_clinical_limit(tmp_path):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for row in rows:
                row['admet']['endpoints']['CYP3A4_Veith'] = {'value': .12, 'unit': '-',
                    'task_type': 'classification', 'source': 'admet_ai_model', 'category': 'Metabolism'}
                row['admet']['units']['CYP3A4_Veith'] = '-'
            return rows
    handler = make_web_handler([ADMETPredictor(backend=EndpointBackend())],
        SQLiteAgentStateStore(tmp_path / 'cyp-inhibition.sqlite'), model=PlannedWebModel())
    try:
        query = '请预测分子 SMILES: CCO 的 CYP3A4 抑制风险，给出预测结果并说明能否直接当作人体不良反应概率。'
        _, frames = run_web_turn(handler, query)
        result = result_of(frames)
        assert result['status'] == 'completed'
        assert all(value in result['display_answer'] for value in ('CYP3A4_Veith', '0.12', '不能', '人体'))
    finally:
        handler.agent_system.tool_registry.close()


def test_direct_cyp3a4_inhibition_missing_endpoint_is_partial(admet_web_handler):
    query = '请预测分子 SMILES: CCO 的 CYP3A4 抑制风险，给出预测结果并说明能否直接当作人体不良反应概率。'
    _, frames = run_web_turn(admet_web_handler, query)
    result = result_of(frames)
    assert result['status'] == 'partial'
    assert 'CYP3A4_Veith' in result['display_answer'] and '未返回' in result['display_answer']


def test_herg_prediction_explains_classification_and_human_safety(tmp_path):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for row in rows:
                row['admet']['endpoints']['hERG'] = {'value': .12, 'unit': '-',
                    'task_type': 'classification', 'source': 'admet_ai_model', 'category': 'Toxicity'}
                row['admet']['units']['hERG'] = '-'
            return rows
    handler = make_web_handler([ADMETPredictor(backend=EndpointBackend())],
        SQLiteAgentStateStore(tmp_path / 'herg-explain.sqlite'), model=PlannedWebModel())
    try:
        query = '预测 CCO 的 ADMET，给出 hERG 的预测值，并解释这个数值代表什么，能否直接当作人体心脏毒性的概率？'
        _, frames = run_web_turn(handler, query)
        complete = next(frame for frame in frames if frame['type'] == 'complete')
        assert complete['status'] == 'completed'
        assert all(value in complete['content'] for value in ('hERG', '0.12', '分类', '阻断', '不能', '人体'))
        _, other = run_web_turn(handler, '预测 CCO 的 ADMET，给出 hERG 的预测值，并解释 DILI 的数值代表什么。')
        other_complete = next(frame for frame in other if frame['type'] == 'complete')
        assert other_complete['status'] == 'partial'
        assert '解释未完成' in other_complete['content']
    finally:
        handler.agent_system.tool_registry.close()


def test_herg_explanation_missing_prediction_is_not_complete(admet_web_handler):
    query = '预测 CCO 的 ADMET，给出 hERG 的预测值，并解释这个数值代表什么，能否直接当作人体心脏毒性的概率？'
    _, frames = run_web_turn(admet_web_handler, query)
    complete = next(frame for frame in frames if frame['type'] == 'complete')
    assert complete['status'] == 'partial'
    assert 'hERG' in complete['content'] and '未返回' in complete['content']


@pytest.mark.parametrize('query', [
    '预测 CCO 的 ADMET，给出 DILI 的预测值，并解释这个数值代表什么，能否直接当作人体肝损伤的概率？',
    '预测 CCO 的 ADMET，给出 hERG 的预测值，并解释 DILI 的数值代表什么。',
])
def test_dili_explanation_uses_the_matching_validated_endpoint(tmp_path, query):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for row in rows:
                for endpoint, value in [('hERG', .12), ('DILI', .34)]:
                    row['admet']['endpoints'][endpoint] = {'value': value, 'unit': '-',
                        'task_type': 'classification', 'source': 'admet_ai_model', 'category': 'Toxicity'}
                    row['admet']['units'][endpoint] = '-'
            return rows
    handler = make_web_handler([ADMETPredictor(backend=EndpointBackend())],
        SQLiteAgentStateStore(tmp_path / 'dili-explain.sqlite'), model=PlannedWebModel())
    try:
        _, frames = run_web_turn(handler, query)
        complete = next(frame for frame in frames if frame['type'] == 'complete')
        assert complete['status'] == 'completed'
        assert all(value in complete['content'] for value in ('DILI', '0.34', '分类', '肝损伤', '不能', '人体'))
        if 'hERG' in query:
            assert 'hERG' in complete['content'] and '0.12' in complete['content']
    finally:
        handler.agent_system.tool_registry.close()


def test_requested_endpoint_results_are_both_explained(tmp_path):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for row in rows:
                for endpoint, value in [('hERG', .12), ('DILI', .34)]:
                    row['admet']['endpoints'][endpoint] = {'value': value, 'unit': '-',
                        'task_type': 'classification', 'source': 'admet_ai_model', 'category': 'Toxicity'}
                    row['admet']['units'][endpoint] = '-'
            return rows
    handler = make_web_handler([ADMETPredictor(backend=EndpointBackend())],
        SQLiteAgentStateStore(tmp_path / 'endpoint-explain.sqlite'), model=PlannedWebModel())
    query = '请预测 SMILES: CCO 的 ADMET 特征，重点给出 hERG 和 DILI 结果并说明这些结果的含义。'
    complete, _ = run_web_turn(handler, query, result_format='summary_details_v1')
    assert complete['status'] == 'completed'
    assert all(word in complete['content'] for word in ('hERG', 'DILI', '0.12', '0.34', '阻断', '肝损伤', '不能'))


def test_focused_endpoints_are_visible_even_without_screening_hits(tmp_path):
    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for row in rows:
                for endpoint, value in [(f'other_{i}', .1) for i in range(9)] + [('hERG', .12), ('DILI', .34)]:
                    row['admet']['endpoints'][endpoint] = {'value': value, 'unit': '-',
                        'task_type': 'classification', 'source': 'admet_ai_model', 'category': 'Toxicity'}
                    row['admet']['units'][endpoint] = '-'
            return rows
    handler = make_web_handler([ADMETPredictor(backend=EndpointBackend())],
        SQLiteAgentStateStore(tmp_path / 'focused-preview.sqlite'), model=PlannedWebModel())
    try:
        complete, _ = run_web_turn(handler, '请预测 SMILES: CCO 的 ADMET，重点查看 hERG 和 DILI。',
                                   result_format='summary_details_v1')
        assert complete['status'] == 'completed'
        assert '| hERG | 0.12 |' in complete['content'] and '| DILI | 0.34 |' in complete['content']
        assert complete['content'].index('| hERG |') < complete['content'].index('| other_0 |')
    finally:
        handler.agent_system.tool_registry.close()


@pytest.mark.parametrize('fails', [False, True])
def test_admet_risk_and_limitations_request_is_not_silently_dropped(admet_web_handler, monkeypatch, fails):
    calls = []
    async def explain(*args, **kwargs):
        calls.append(args)
        if fails:
            raise RuntimeError('offline explanation failure')
        return '这是模型预测而非实验结果；可靠性取决于训练数据覆盖和适用域，应通过实验验证。'
    monkeypatch.setattr('src.web.models.generate_for_chat', explain)
    _, frames = run_web_turn(admet_web_handler,
        '请预测 CCO 的 ADMET 特征，并说明结果中的主要风险和模型预测的局限。')
    result = result_of(frames)
    assert len(calls) == 1
    assert result['tool_result_sequence'][0]['tool_name'] == 'admet_predictor'
    assert result['status'] == ('partial' if fails else 'completed')
    if fails:
        assert any('说明未生成' in warning for warning in result['warnings'])
    else:
        assert '模型预测而非实验结果' in result['display_answer']


@pytest.mark.parametrize('failure', ['digits', 'exception'])
def test_admet_explanation_diagnostics_preserve_partial_without_logging_content(
        admet_web_handler, monkeypatch, caplog, failure):
    async def explain(*args, **kwargs):
        if failure == 'exception':
            raise RuntimeError('PRIVATE_DIAGNOSTIC_SENTINEL')
        return 'PRIVATE_DIAGNOSTIC_SENTINEL 42'
    monkeypatch.setattr('src.web.models.generate_for_chat', explain)
    caplog.set_level('INFO', logger='src.web.decision_runtime')
    _, frames = run_web_turn(admet_web_handler,
        '请预测 CCO 的 ADMET 特征，并说明结果中的主要风险和模型预测的局限。')
    result = result_of(frames)
    assert result['status'] == 'partial'
    assert any('说明未生成' in warning for warning in result['warnings'])
    assert 'Result interpretation unavailable:' in caplog.text
    assert ('digits=True' in caplog.text) is (failure == 'digits')
    assert 'PRIVATE_DIAGNOSTIC_SENTINEL' not in caplog.text


def test_mixed_property_admet_requires_both_observations(admet_web_handler):
    _, frames = run_web_turn(
        admet_web_handler,
        "请分析 CCO 的分子性质和 ADMET 特征",
    )
    result = result_of(frames)

    assert result["metadata"]["required_tools"] == [
        "property_calculator",
        "admet_predictor",
    ]
    assert [item["tool_name"] for item in result["tool_result_sequence"]] == [
        "property_calculator",
        "admet_predictor",
    ]
    assert all(
        item["status"] == "succeeded"
        for item in result["tool_result_sequence"]
    )


def test_admet_failure_is_not_reported_as_a_completed_scientific_result(
    admet_web_handler,
):
    admet_web_handler.agent_system.tools["admet_predictor"].backend.fail_ids.add(
        "molecule-001"
    )

    _, frames = run_web_turn(admet_web_handler, "请预测 CCO 的 ADMET 特征")
    result = result_of(frames)

    assert result["status"] in {"partial", "failed", "unavailable"}
    assert result["success"] is False
    assert result["tool_result_sequence"][0]["status"] != "succeeded"


@pytest.mark.parametrize('fails', [False, True])
@pytest.mark.parametrize('query', [
    '请预测 CCO 的 ADMET，不需要说明模型局限。',
    '请预测 CCO 的 ADMET 性质，不需要说明模型局限。',
])
def test_admet_only_excluding_explanation_runs_no_extra_tools(admet_web_handler, fails, query):
    if fails:
        admet_web_handler.agent_system.tools['admet_predictor'].backend.fail_ids.add('molecule-001')
    _, frames = run_web_turn(admet_web_handler, query)
    result = result_of(frames)
    assert [item['tool_name'] for item in result['tool_result_sequence']] == ['admet_predictor']
    if fails:
        assert result['status'] != 'completed'
    else:
        assert result['status'] == 'completed'
