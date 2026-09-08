"""Real RDKit input regressions, shared by the two migrated analysis tools."""
import pytest

from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment


@pytest.fixture(params=[PropertyCalculator, DrugLikenessAssessment])
def calculator(request):
    return request.param()


@pytest.mark.parametrize('query, expected', [
    ('CCN', ['CCN']),
    ('Please analyze QED for SMILES: CCO', ['CCO']),
    ('Calculate SMILES: CCO', ['CCO']),
    ('Please analyze QED for CCO', ['CCO']),
    ('SMILES: CCO\nSMILES: CCN', ['CCO', 'CCN']),
    ('CCO\nCCN', ['CCO', 'CCN']),
    ('SMILES: CCO\nCCN', ['CCO', 'CCN']),
    ('SMILES: CCO; CCN', ['CCO', 'CCN']),
    ('CCO\nSMILES: CCN', ['CCO', 'CCN']),
    ('CCO; SMILES: CCN', ['CCO', 'CCN']),
    ('计算 CCO 和 CCN 的性质', ['CCO', 'CCN']),
    ('SMILES: CC(=O)Oc1ccccc1C(=O)O\nSMILES: CCN', ['CC(=O)Oc1ccccc1C(=O)O', 'CCN']),
    ('SMILES：`[Na+].[Cl-]`；SMILES: "N[C@@H](C)C(=O)O"', ['[Na+].[Cl-]', 'N[C@@H](C)C(=O)O']),
    ('SMILES: [13CH3]CO', ['[13CH3]CO']),
    ('SMILES: C；SMILES: CO', ['C', 'CO']),
    ('SMILES: CCN。输出 LogP、QED。', ['CCN']),
])
def test_full_batch_is_computed(calculator, query, expected):
    result = calculator.execute(query)
    assert result['success'], result['message']
    assert [r['smiles'] for r in result['data']] == expected


@pytest.mark.parametrize('query', [
    'SMILES: cco',
    '分析 CCN 和 CCN)INVALID 的性质',
    '分析 CCN 和 CCOfoo 的性质',
    'Please analyze QED for CCN and CCN)INVALID',
    'SMILES: CCO.',
    'SMILES: CCO)INVALID',
    'SMILES: CCO molecule-name',
    'SMILES: CC(C)((',
    'SMILES: CCO\nSMILES: CC(C)((',
    'CCO\nCC(C)((',
    'SMILES: CCO\nCC(C)((',
    'SMILES: CCO; CCN)INVALID',
    'CCO\nCCN)INVALID',
    'CCO; CCOfoo',
    'SMILES: CCO\nCCN molecule-name',
    'CC(C)((\nSMILES: CCN',
    'SMILES: CCO\nCCN)INVALID molecule-name',
    'SMILES: CCO\nCCOfoo molecule-name',
    'SMILES: CCO\nSMILES:',
    'SMILES: "CCO',
    'SMILES: CCO |invalid-extension|',
])
def test_invalid_input_cannot_be_silently_repaired_or_dropped(calculator, query):
    result = calculator.execute(query)
    assert not result['success']
    assert not result['data'] and not result['formatted']
    assert 'SMILES' in result['message']
    assert not calculator.should_use('分析 ' + query)


def test_plain_chat_does_not_select_analysis_tool(calculator):
    assert not calculator.should_use('你好，请介绍系统功能')


def test_english_instruction_selects_analysis_tool(calculator):
    assert calculator.should_use('Please analyze QED for SMILES: CCO')


def test_missing_rdkit_is_unavailable_not_heuristic_success(calculator, monkeypatch):
    import src.agent.tools.base_tool as base
    monkeypatch.setattr(base, 'RDKIT_AVAILABLE', False)
    result = calculator.execute('SMILES: CCN')
    assert not result['success'] and not result['data']
    assert 'RDKit' in result['message']
