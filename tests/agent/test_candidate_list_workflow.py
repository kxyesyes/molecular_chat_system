"""User-supplied multiline candidates retain supported property/ADMET work."""
import pytest

from src.web.scientific_obligations import ScientificObligationCompiler
from src.web.decision_chat import _requested_admet_endpoints


SMILES = ('Cn1c(=O)c2c(ncn2C)n(C)c1=O', 'Cn1c(=O)c2[nH]cnc2n(C)c1=O')
STRUCTURES = '\n' + '\n'.join('SMILES: ' + value for value in SMILES)
QUERY = ('请比较以下两个分子的分子量、LogP、TPSA，以及 hERG 和 BBB 预测。'
         '只列出这些指标，不要给综合评分。' + STRUCTURES)
ADMET_QUERY = ('请比较以下两个分子的 hERG 和 BBB 预测，'
               '只列出这两个指标，不要给综合评分。' + STRUCTURES)


@pytest.mark.parametrize('reference', ['这些', '这两个', '这两项', '这2个', '这三项'])
def test_display_metric_reference_does_not_add_scientific_obligations(reference):
    query = ADMET_QUERY.replace('这两个', reference)
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready', plan
    assert plan.required_tools == ('admet_predictor',)
    assert plan.metrics == ()
    assert plan.smiles.split('\n') == list(SMILES)
    assert plan.workflow_version == 'web-admet-batch-v1'
    assert _requested_admet_endpoints(query) == ('hERG', 'BBB_Martins')


@pytest.mark.parametrize('extra', ['和 FooMetric', '，计算 FooMetric'])
def test_display_metric_reference_does_not_hide_unknown_obligations(extra):
    plan = ScientificObligationCompiler().compile(
        ADMET_QUERY.replace('这两个指标', '这两个指标' + extra))
    assert plan.status == 'rejected'
    assert 'metric:unreviewed' in plan.missing_capabilities


@pytest.mark.parametrize('separator', ['\n', '；'])
def test_original_comparison_preserves_both_structures_and_requested_tools(separator):
    query = QUERY.replace('\n', separator)
    assert ScientificObligationCompiler.is_profile_candidate(query)
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready', plan
    assert plan.required_tools == ('property_calculator', 'admet_predictor')
    assert set(plan.metrics) == {'mw', 'logp', 'tpsa'}
    assert plan.smiles.split('\n') == list(SMILES)
    assert plan.workflow_version == 'web-property-admet-batch-v1'
    assert _requested_admet_endpoints(query) == ('hERG', 'BBB_Martins')


@pytest.mark.parametrize('subject', ['以下两个分子', '下列分子'])
def test_generic_list_subject_is_not_an_unknown_property(subject):
    plan = ScientificObligationCompiler().compile(f'计算{subject}的分子量、LogP、TPSA。' + STRUCTURES)
    assert plan.status == 'ready', plan
    assert plan.required_tools == ('property_calculator',)


@pytest.mark.parametrize('metric', ['LogD', 'FooMetric', '量子轨道能量'])
def test_candidate_list_does_not_hide_unimplemented_obligations(metric):
    query = QUERY.replace('TPSA', 'TPSA、' + metric)
    assert ScientificObligationCompiler().compile(query).status == 'rejected'


def test_explicit_invalid_structure_blocks_all_candidate_calculations():
    plan = ScientificObligationCompiler().compile(QUERY.replace(SMILES[1], 'CCO$bad'))
    assert plan.status == 'rejected'
    assert plan.missing_capabilities == ('invalid_smiles',)


def test_negative_endpoint_request_does_not_authorize_admet():
    query = '计算以下两个分子的分子量、LogP、TPSA。不要做 hERG 和 BBB 预测。' + STRUCTURES
    plan = ScientificObligationCompiler().compile(query)
    assert plan.status == 'ready', plan
    assert plan.required_tools == ('property_calculator',)
    assert _requested_admet_endpoints(query) == ()


def test_explicit_boron_structure_is_never_removed_as_endpoint_prose():
    plan = ScientificObligationCompiler().compile('计算分子量。SMILES: BBB')
    assert plan.status == 'ready' and plan.smiles == 'BBB'
    assert ScientificObligationCompiler().compile('计算分子量。SMILES: BBB预测').status == 'rejected'


@pytest.mark.parametrize('missing', [False, True])
@pytest.mark.parametrize('result_format', [None, 'summary_details_v1'])
@pytest.mark.parametrize('query', [QUERY, ADMET_QUERY])
def test_web_executes_exact_candidates_and_reports_requested_endpoints(tmp_path, missing, result_format, query):
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.tools.admet_predictor import ADMETPredictor
    from src.agent.tools.property_calculator import PropertyCalculator
    from tests.agent.test_admet_ai_integration import FakeBackend
    from tests.agent.pi_web_support import make_web_handler, PlannedWebModel, run_web_turn

    class EndpointBackend(FakeBackend):
        def predict_batch(self, smiles, molecule_ids):
            rows = super().predict_batch(smiles, molecule_ids)
            for index, row in enumerate(rows):
                for endpoint in ('hERG', 'BBB_Martins'):
                    if missing and index == 1 and endpoint == 'BBB_Martins':
                        continue
                    row['admet']['endpoints'][endpoint] = {
                        'value': (index + 1) / 10, 'unit': '-', 'task_type': 'classification',
                        'source': 'admet_ai_model', 'category': 'Distribution' if endpoint == 'BBB_Martins' else 'Toxicity'}
                    row['admet']['units'][endpoint] = '-'
            return rows

    backend = EndpointBackend()
    handler = make_web_handler([PropertyCalculator(), ADMETPredictor(backend=backend)],
                              SQLiteAgentStateStore(tmp_path / 'candidates.sqlite'), model=PlannedWebModel())
    try:
        complete, frames = run_web_turn(handler, query, result_format=result_format)
        assert complete['status'] == ('partial' if missing else 'completed'), complete
        assert backend.calls[0][0] == list(SMILES)
        result = next(frame for frame in frames if frame['type'] == 'agent_result')
        sequence = ([dict(item['output'], tool_name=item['tool_name'])
                     for item in handler.agent_system.state_store.get_tool_executions(complete['trace_id'])]
                    if result_format else result['tool_result_sequence'])
        expected_tools = ['property_calculator', 'admet_predictor'] if query == QUERY else ['admet_predictor']
        assert [item['tool_name'] for item in sequence] == expected_tools
        assert all([row['smiles'] for row in item['data']] == list(SMILES) for item in sequence)
        assert 'hERG' in complete['content']
        for term in ('分子量', 'logP', 'TPSA'):
            assert (term in complete['content']) is (query == QUERY)
        assert '血脑屏障通透性' in complete['content']
        assert '| BBB_Martins |' not in complete['content']
        assert 'BBB_Martins' in sequence[-1]['data'][0]['admet']['endpoints']
        assert 'QED' not in complete['content']
        assert ('未返回' in complete['content']) is missing
    finally:
        handler.agent_system.tool_registry.close()
