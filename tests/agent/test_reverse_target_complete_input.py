import pytest

from src.agent.tools import base_tool, reverse_target_tool
from src.agent.tools.molecular_input import MolecularInputUnavailable
from src.agent.tools.reverse_target_tool import ReverseTargetTool
from tests.agent.test_reverse_receipt_consumption import strict_source_factory


@pytest.fixture
def tool_and_calls(strict_source_factory, monkeypatch):
    calls = []
    predictor = strict_source_factory(())
    tool = ReverseTargetTool(predictor)
    strict = predictor.predict_with_receipt

    def predict(smiles, **kwargs):
        assert 0 < kwargs['timeout_seconds'] <= 180
        assert kwargs['cancelled']() is False
        calls.append((smiles, {k: v for k, v in kwargs.items() if k not in ('timeout_seconds', 'cancelled')}))
        return strict(smiles, **kwargs)

    monkeypatch.setattr(predictor, 'predict_with_receipt', predict)
    monkeypatch.setattr(predictor, 'predict', lambda *a, **kw: pytest.fail('legacy predict'))
    monkeypatch.setattr(predictor, 'load', lambda *a, **kw: pytest.fail('legacy load'))
    return tool, calls


@pytest.mark.parametrize('value', [
    'CCO)((', 'CC(C)((', 'CCO]', '[NH4+].', 'CCO name', '"CCO',
    'CCO |bad-extension|', 'CCO; CC(C)((', 'CCO\nCCO)((',
])
def test_invalid_whole_input_never_reaches_predictor(tool_and_calls, value):
    tool, calls = tool_and_calls
    query = 'Reverse target prediction. SMILES: ' + value
    result = tool.execute(query)
    assert calls == []
    assert result['success'] is False
    assert result.get('data') is None
    assert result['error']['code'] == 'validation_error'
    assert not tool.should_use(query)


@pytest.mark.parametrize('smiles', [
    'CCO', '[NH4+].[Cl-]', '[13CH3][C@@H](O)C(=O)[O-]',
    'C%12CCCCC%12', 'N#Cc1ccc(C(=O)O)cc1', 'CC(C)NCC(O)COc1cccc2ccccc12',
])
@pytest.mark.parametrize('prefix', ['', '请反向寻靶：', 'Reverse target prediction. SMILES: '])
def test_valid_structure_reaches_predictor_unchanged(tool_and_calls, smiles, prefix):
    tool, calls = tool_and_calls
    result = tool.execute(prefix + smiles)
    assert calls == [(smiles, {'threshold': 0.6, 'top_k': 10, 'combine_by_target': True, 'organism_filter': ''})]
    assert result['success'] is True
    assert result['data'] == []  # Verified empty, not a missing/unavailable result.
    assert result['evidence'][0]['prediction_receipt']['status'] == 'verified_empty'
    assert result['evidence'][0]['input_smiles'] == smiles
    assert smiles in result['formatted']
    if prefix:
        assert tool.should_use(prefix + smiles)


def test_valid_batch_keeps_historical_first_structure(tool_and_calls):
    tool, calls = tool_and_calls
    assert tool.execute('CCO\nOCC')['success'] is True
    assert calls[0][0] == 'CCO'
    assert len(calls) == 1


def test_parser_unavailable_is_not_reported_as_invalid_structure(tool_and_calls, monkeypatch):
    tool, calls = tool_and_calls

    def unavailable(*args, **kwargs):
        raise MolecularInputUnavailable('private backend diagnostic')

    monkeypatch.setattr(reverse_target_tool, 'parse_molecular_smiles', unavailable, raising=False)
    result = tool.execute('Reverse target SMILES: CCO')
    assert calls == []
    assert result['success'] is False
    assert result['error']['code'] == 'tool_unavailable'
    assert 'private backend diagnostic' not in str(result)
    assert not tool.should_use('Reverse target SMILES: CCO')


def test_missing_rdkit_keeps_existing_preflight(tool_and_calls, monkeypatch):
    tool, calls = tool_and_calls
    monkeypatch.setattr(base_tool, 'RDKIT_AVAILABLE', False)
    result = tool.execute('CCO')
    assert result['success'] is False
    assert calls == []
    assert 'RDKit' in result['message']


def test_invalid_input_does_not_initialize_predictor(monkeypatch):
    tool = ReverseTargetTool()

    def forbidden():
        pytest.fail('Invalid structures must not initialize the database predictor')

    monkeypatch.setattr(tool, '_get_predictor', forbidden)
    result = tool.execute('SMILES: CCO)((')
    assert result['success'] is False
    assert result.get('data') is None
