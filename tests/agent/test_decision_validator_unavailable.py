"""Parser exceptions are availability failures, not molecular diagnoses."""
import pytest
from src.agent.utils.validators import InputValidator


@pytest.mark.parametrize('query,fail_on', [('SMILES: CCO', 'CCO'),
                                         ('SMILES: [CCO]', 'CCO')])
def test_parser_exception_never_classified_as_invalid(monkeypatch, query, fail_on):
    class Chem:
        @staticmethod
        def MolFromSmiles(value):
            if value == fail_on:
                raise RuntimeError('synthetic unavailable parser')
            return None
    monkeypatch.setattr('src.agent.utils.validators._load_rdkit_chem', lambda: Chem)
    result = InputValidator().analyze_molecular_input(query)
    assert result.candidates[0].validation == 'unavailable'
    assert result.blocks_execution
