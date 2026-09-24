"""Real input chemistry at generator execution; model outputs are test doubles."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.agent.tools import base_tool
from src.agent.tools import llm_molecular_generator as generator_module
from src.agent.tools import molecular_input
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator


@pytest.fixture
def boundary(monkeypatch):
    from rdkit import Chem

    assert Chem.MolFromSmiles('CCO') is not None
    llm = SimpleNamespace(model_name='test-only', generate=Mock(return_value=''))
    tool = LLMMolecularGenerator(llm_model=llm)
    dispatch = Mock(return_value=[])
    monkeypatch.setattr(tool, '_generate_with_retry', dispatch)
    return tool, dispatch, llm


INVALID = [
    'optimize SMILES: CCO)((',
    'optimize SMILES: CCO]',
    'optimize SMILES: CCO.',
    'optimize SMILES: cco',
    'optimize SMILES: CC(C)((',
    'optimize SMILES: [NH4+].',
    'optimize SMILES: CCO molecule-name',
    'optimize SMILES: "CCO',
    'optimize SMILES: CCO |bad-extension|',
    'optimize CCO |bad-extension|',
    'optimize SMILES: CCO |$C1;C2;O3$|',
    '优化 CCO 和 CCN)INVALID 的溶解性',
    'optimize CCO and CCOfoo',
    'optimize SMILES: CCO; CC(C)((',
    'optimize SMILES: CCO\nCCN)INVALID',
    'optimize SMILES: CC(C)((\nSMILES: CCO',
    'optimize SMILES: CCO\nSMILES:',
    'optimize SMILES:',
    'optimize SMILES: optimize',
]


@pytest.mark.parametrize('query', INVALID)
@pytest.mark.parametrize('structured', [False, True])
def test_invalid_input_never_dispatches(boundary, query, structured):
    tool, dispatch, llm = boundary
    request = ({'query': query, 'metadata': {'requested_count': 2}}
               if structured else query)
    result = tool.execute(request)
    dispatch.assert_not_called()
    llm.generate.assert_not_called()
    assert result['success'] is False
    assert result['data'] is None and result['formatted'] == ''
    assert result['error']['code'] == 'invalid_input'
    assert result['error']['details'] == {'reason': 'invalid_optimization_input'}
    assert 'quality' not in result


@pytest.mark.parametrize('seed', [
    'CCO', 'OCC', 'C', 'CO', '[Na+].[Cl-]',
    '[13CH3][C@@H](O)C(=O)[O-]', 'C%12CCCCC%12',
    'N#Cc1ccc(C(=O)O)cc1',
])
@pytest.mark.parametrize('prefix', [
    'optimize SMILES: ', 'improve SMILES: ', 'modify SMILES: ', '优化 ',
])
def test_seed_reaches_dispatch_unchanged(boundary, seed, prefix):
    tool, dispatch, _ = boundary
    tool.execute(prefix + seed)
    dispatch.assert_called_once()
    intent = dispatch.call_args.args[0]
    assert intent['type'] == 'optimization'
    assert intent['base_smiles'] == seed


@pytest.mark.parametrize('batch', [
    'SMILES: CCO\nSMILES: CCN', 'CCO\nCCN', 'SMILES: CCO; CCN',
    'SMILES: CCO\nSMILES: CCO', 'SMILES: CCO; N#Cc1ccccc1',
])
def test_all_valid_batch_uses_only_first_seed(boundary, batch):
    tool, dispatch, _ = boundary
    tool.execute('optimize ' + batch)
    dispatch.assert_called_once()
    assert dispatch.call_args.args[0]['base_smiles'] == 'CCO'


def test_supported_legacy_prose(boundary):
    tool, dispatch, _ = boundary
    tool.execute('optimize CCO into 2 molecules')
    assert dispatch.call_args.args[0]['base_smiles'] == 'CCO'
    # Optimization prose is not an actionable generation-count clause today.
    assert dispatch.call_args.args[0]['count'] == 1


def test_seed_spelling_reaches_real_prompt():
    llm = SimpleNamespace(model_name='test-only', generate=Mock(return_value='CCN'))
    tool = LLMMolecularGenerator(llm_model=llm)
    result = tool.execute('optimize SMILES: [13CH3][C@@H](O)C(=O)[O-]', mol_count=1)
    assert result['success'] is True  # Pipeline contract, not scientific evidence.
    llm.generate.assert_called_once()
    assert 'Original SMILES: [13CH3][C@@H](O)C(=O)[O-]\n' in llm.generate.call_args.args[0]


@pytest.mark.parametrize('structured', [False, True])
def test_seedless_optimization_word_keeps_description_dispatch(monkeypatch, structured):
    tool = LLMMolecularGenerator(llm_model=object())
    description = Mock(return_value=['CCO'])
    optimization = Mock(side_effect=AssertionError('No optimization seed'))
    monkeypatch.setattr(tool, '_generate_with_llm', description)
    monkeypatch.setattr(tool, '_optimize_with_llm', optimization)
    query = 'Generate 2 molecules with improved solubility'
    request = ({'query': query, 'metadata': {'requested_count': 1},
                'outputs': {'target': {'gene_symbol': 'PDE5A',
                                       'source_record_id': 'O76074', 'source': 'UniProt'}}}
               if structured else query)
    result = tool.execute(request, mol_count=1)
    assert result['success'] is True
    description.assert_called_once()
    assert description.call_args.args[0]['base_smiles'] is None
    if structured:
        assert 'PDE5A' in description.call_args.args[0]['target_evidence']
    optimization.assert_not_called()


def test_de_novo_and_target_generation_do_not_parse_seeds(boundary, monkeypatch):
    tool, dispatch, _ = boundary
    parser = Mock(side_effect=AssertionError('Not a seeded optimization'))
    monkeypatch.setattr(generator_module, 'parse_molecular_smiles', parser, raising=False)
    requests = [
        'Generate 2 molecules',
        {'query': 'Generate 7.5 molecules',
         'metadata': {'requested_count': 2},
         'outputs': {'target': {'gene_symbol': 'PDE5A',
                                'source_record_id': 'O76074', 'source': 'UniProt'}}},
    ]
    for request in requests:
        dispatch.reset_mock()
        tool.execute(request)
        dispatch.assert_called_once()
        intent = dispatch.call_args.args[0]
        assert intent['count'] == 2 and intent['base_smiles'] is None
        if isinstance(request, dict):
            assert 'PDE5A' in intent['target_evidence']
    parser.assert_not_called()


def test_parser_unavailable_is_not_invalid_structure(boundary, monkeypatch):
    tool, dispatch, llm = boundary
    parser = Mock(side_effect=molecular_input.MolecularInputUnavailable('private diagnostic'))
    monkeypatch.setattr(generator_module, 'parse_molecular_smiles', parser, raising=False)
    result = tool.execute('optimize SMILES: CCO')
    dispatch.assert_not_called()
    llm.generate.assert_not_called()
    assert result['success'] is False
    assert result['data'] is None and result['formatted'] == ''
    assert result['error']['code'] == 'tool_unavailable'
    assert result['error']['details']['reason'] == 'optimization_input_validation_unavailable'
    assert 'private diagnostic' not in str(result)


def test_missing_rdkit_preserves_existing_preflight(boundary, monkeypatch):
    tool, dispatch, llm = boundary
    monkeypatch.setattr(base_tool, 'RDKIT_AVAILABLE', False)
    result = tool.execute('optimize SMILES: CCO')
    assert result['success'] is False and result['data'] is None
    assert 'RDKit' in result['message']
    assert result.get('error', {}).get('code') != 'invalid_input'
    dispatch.assert_not_called()
    llm.generate.assert_not_called()


def test_missing_llm_preserves_existing_preflight(boundary):
    tool, dispatch, _ = boundary
    tool.llm = None
    result = tool.execute('optimize SMILES: CCO')
    assert result['success'] is False and 'LLM' in result['message']
    dispatch.assert_not_called()


def test_should_use_does_not_call_new_parser_or_generator(boundary, monkeypatch):
    tool, dispatch, llm = boundary
    parser = Mock(side_effect=AssertionError('Selection is separate'))
    monkeypatch.setattr(generator_module, 'parse_molecular_smiles', parser, raising=False)
    assert tool.should_use('Generate 2 molecules')
    assert not tool.should_use('Please analyze SMILES: CCO')
    parser.assert_not_called()
    dispatch.assert_not_called()
    llm.generate.assert_not_called()


def test_parser_missing_is_distinct_from_explicit_empty(boundary):
    tool, _, _ = boundary
    with pytest.raises(molecular_input.MolecularInputMissing):
        molecular_input.parse_molecular_smiles('Generate molecules with improved solubility', tool)
    with pytest.raises(ValueError) as exc:
        molecular_input.parse_molecular_smiles('optimize SMILES:', tool)
    assert not isinstance(exc.value, molecular_input.MolecularInputMissing)


@pytest.mark.parametrize('value', [None, '', ' ', 'x' * 65537, 'SMILES: CCO\n' * 101],
                         ids=['non_string', 'empty', 'blank', 'too_long', 'too_many'])
def test_non_candidate_limits_are_not_missing(boundary, value):
    tool, _, _ = boundary
    with pytest.raises(ValueError) as exc:
        molecular_input.parse_molecular_smiles(value, tool)
    assert not isinstance(exc.value, molecular_input.MolecularInputMissing)


def test_parser_import_failure_is_unavailable(boundary, monkeypatch):
    import builtins

    tool, dispatch, llm = boundary
    original_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == 'rdkit':
            raise ImportError('private dependency diagnostic')
        return original_import(name, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(builtins, '__import__', blocked_import)
        with pytest.raises(molecular_input.MolecularInputUnavailable):
            molecular_input.parse_molecular_smiles('SMILES: CCO', tool)
        result = tool.execute('optimize SMILES: CCO')
    assert result['error']['code'] == 'tool_unavailable'
    assert 'private dependency diagnostic' not in str(result)
    dispatch.assert_not_called()
    llm.generate.assert_not_called()
