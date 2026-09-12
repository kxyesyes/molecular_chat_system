"""Evidence-backed clarification, real RDKit and explicit offline model doubles."""
import asyncio

import pytest

from test_decision_loop import setup_loop, clarify, tool, finish_last
from src.agent.contracts import AgentContext
from src.agent.tools.property_calculator import PropertyCalculator


def invoke(bundle, query, **options):
    return asyncio.run(bundle.loop.run(
        AgentContext(query, 'clarity-trace', user_id='owner', session_id='session'),
        request_kind=options.pop('request_kind', 'scientific'),
        allowed_tools={'property_calculator'}, required_tools={'property_calculator'},
        event_bus=bundle.bus, **options))


@pytest.mark.parametrize('query', [
    '请分析这个 SMILES 的成药性：CC(C)((。',
    'SMILES: CC(C)((',
    'SMILES: CCO; SMILES: CC(C)((',
])
def test_invalid_complete_input_explained_without_fake_results(setup_loop, query):
    decision = clarify().model_copy(update={'question': 'pIC50=9; binding energy=-8 kcal/mol',
                                           'missing_fields': ['pIC50=9']})
    b = setup_loop([decision], [PropertyCalculator()])
    result = invoke(b, query)
    assert 'SMILES 无效' in result.final_answer
    assert not result.success and result.metadata['waiting_for_input']
    assert result.metadata['continuation_id']
    assert not result.tool_results
    assert all(s not in result.final_answer for s in ('pIC50', 'kcal/mol', 'CC(C)(('))


def test_missing_input_requests_smiles_not_invalid_diagnosis(setup_loop):
    b = setup_loop([clarify()], [PropertyCalculator()])
    result = invoke(b, '请计算分子性质，我还未提供结构。')
    assert '请提供完整的 SMILES' in result.final_answer
    assert 'SMILES 无效' not in result.final_answer


def test_valid_input_does_not_invent_invalid_diagnosis(setup_loop):
    b = setup_loop([clarify()], [PropertyCalculator()])
    result = invoke(b, 'SMILES: CCO')
    assert '需要补充或确认输入' in result.final_answer
    assert 'SMILES 无效' not in result.final_answer


def test_corrected_input_resume_uses_current_validation(setup_loop):
    b = setup_loop([clarify(), clarify(), tool(), finish_last], [PropertyCalculator()])
    original = 'SMILES: CC(C)(('
    waiting = invoke(b, original)
    assert 'SMILES 无效' in waiting.final_answer
    again = invoke(b, original, continuation_id=waiting.metadata['continuation_id'],
                   clarified_query='SMILES: CCN')
    assert again.metadata['waiting_for_input']
    assert 'SMILES 无效' not in again.final_answer
    result = invoke(b, original, continuation_id=again.metadata['continuation_id'],
                    clarified_query='SMILES: CCN')
    assert result.success
    assert result.trace_id == waiting.trace_id
    assert result.tool_results[0].data[0]['smiles'] == 'CCN'


def test_missing_rdkit_reports_unavailable_not_invalid(monkeypatch):
    from src.agent.harness.decision_clarification import scientific_clarification
    monkeypatch.setattr('src.agent.utils.validators._load_rdkit_chem', lambda: None)
    text = scientific_clarification('SMILES: CC(C)((', {'property_calculator'})
    assert '校验暂不可用' in text
    assert 'SMILES 无效' not in text


def test_validator_failure_does_not_echo_exception(monkeypatch):
    from src.agent.harness.decision_clarification import scientific_clarification
    def fail(*args):
        raise RuntimeError('private diagnostic marker')
    monkeypatch.setattr('src.agent.utils.validators.InputValidator.analyze_molecular_input', fail)
    text = scientific_clarification('SMILES: CCO', {'property_calculator'})
    assert '校验暂不可用' in text
    assert 'private' not in text


def test_parser_runtime_failure_keeps_unavailable_provenance(monkeypatch):
    from src.agent.harness.decision_clarification import scientific_clarification
    from src.agent.utils.validators import InputValidator
    class BrokenChem:
        @staticmethod
        def MolFromSmiles(value):
            raise RuntimeError('synthetic parser failure')
    monkeypatch.setattr('src.agent.utils.validators._load_rdkit_chem', lambda: BrokenChem)
    analysis = InputValidator().analyze_molecular_input('SMILES: CCO')
    assert analysis.candidates[0].validation == 'unavailable'
    assert analysis.blocks_execution
    text = scientific_clarification('SMILES: CCO', {'property_calculator'})
    assert '校验暂不可用' in text
    assert 'SMILES 无效' not in text


def test_markdown_formatting_ambiguity_is_not_a_structure_diagnosis():
    from src.agent.harness.decision_clarification import scientific_clarification
    from src.agent.utils.validators import InputValidator
    query = 'SMILES: `CCO`'
    assert InputValidator().analyze_molecular_input(query).blocks_execution
    text = scientific_clarification(query, {'property_calculator'})
    assert '格式' in text
    assert 'SMILES 无效' not in text


def test_formatting_or_valid_fragment_does_not_override_invalid_structure():
    from src.agent.harness.decision_clarification import scientific_clarification
    text = scientific_clarification('SMILES: `CCO`; SMILES: CC(C)((', {'property_calculator'})
    assert 'SMILES 无效' in text


def test_nonmolecular_context_does_not_acquire_smiles_requirement(monkeypatch):
    from src.agent.harness.decision_clarification import scientific_clarification
    def fail(*args):
        pytest.fail('unrelated context must not run molecular validation')
    monkeypatch.setattr('src.agent.utils.validators.InputValidator.analyze_molecular_input', fail)
    text = scientific_clarification('请搜索 EGFR', {'target_database_search'})
    assert '需要补充或确认输入' in text
    assert 'SMILES' not in text


def test_oversized_input_does_not_run_unbounded_validation(monkeypatch):
    from src.agent.harness.decision_clarification import scientific_clarification
    def fail(*args):
        pytest.fail('oversized input should not be parsed')
    monkeypatch.setattr('src.agent.utils.validators.InputValidator.analyze_molecular_input', fail)
    text = scientific_clarification('C' * 16385, {'property_calculator'})
    assert '需要补充或确认输入' in text


@pytest.mark.parametrize('query', ['SMILES: ' + 'C' * 512, 'SMILES: ' + 'C' * 8000,
                                  'SMILES: ' + 'C' * 16000, '请' * 90],
                         ids=['512_atoms', '8000_atoms', '16000_atoms', 'utf8_bytes'])
def test_long_clarification_avoids_rdkit_on_async_control_path(monkeypatch, query):
    from src.agent.harness.decision_clarification import scientific_clarification
    def fail(*args):
        pytest.fail('long clarification must not invoke synchronous RDKit parsing')
    monkeypatch.setattr('src.agent.utils.validators.InputValidator.analyze_molecular_input', fail)
    text = scientific_clarification(query, {'property_calculator'})
    assert '需要补充或确认输入' in text


def test_invalid_input_not_written_to_rdkit_diagnostic_stream(capfd):
    from src.agent.harness.decision_clarification import scientific_clarification
    scientific_clarification('SMILES: CC(C)((', {'property_calculator'})
    captured = capfd.readouterr()
    assert 'CC(C)((' not in captured.out + captured.err


def test_chat_clarification_behavior_unchanged(setup_loop):
    b = setup_loop([clarify().model_copy(update={'question': '请问想了解哪些功能？'})], [PropertyCalculator()])
    result = asyncio.run(b.loop.run(AgentContext('你好', 'chat-clarity'),
        request_kind='chat', allowed_tools=set(), required_tools=set()))
    assert result.final_answer == '请问想了解哪些功能？'
