import pytest

from src.agent.tools.base_tool import BaseMolecularTool
from src.agent.utils.validators import InputValidator
from src.system.molecular_input_parser import parse_molecular_smiles


QUERY = ('请使用 RCSB 的人丁酰胆碱酯酶结构 1P0I，为阿司匹林'
         '（SMILES: CC(=O)Oc1ccccc1C(=O)O）准备分子对接。'
         '请先检查结构和结合位点，准备完成后再让我确认是否运行。')
SMILES = 'CC(=O)Oc1ccccc1C(=O)O'


def test_rcsb_structure_identifier_is_not_a_molecule():
    analysis = InputValidator().analyze_molecular_input(QUERY)
    assert not analysis.blocks_execution
    assert analysis.valid_smiles == (SMILES,)
    assert parse_molecular_smiles(QUERY, BaseMolecularTool('test', '')) == [SMILES]


@pytest.mark.parametrize('bad', ['CCO$bad', '1P0I'])
def test_explicit_invalid_smiles_remains_invalid_with_pdb_reference(bad):
    query = f'使用 PDB 1P0I，SMILES: {bad}，准备对接。'
    assert InputValidator().analyze_molecular_input(query).blocks_execution
    with pytest.raises(ValueError):
        parse_molecular_smiles(query, BaseMolecularTool('test', ''))
