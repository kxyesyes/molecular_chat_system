import builtins
import importlib
import sys

import pytest

from src.agent.utils.validators import (
    InputValidator,
    sanitize_input,
    validate_smiles_with_rdkit,
)


def test_input_validator_imports_and_recognizes_chinese_calculation_query():
    result = InputValidator().validate_query("请计算 CCO 的 LogP 和 QED")

    assert result["valid"] is True
    assert "CCO" in result["smiles_candidates"]


def test_input_validator_reports_missing_smiles():
    result = InputValidator().validate_query("请计算这个分子的 LogP")

    assert result["valid"] is False
    assert result["has_calculation_request"] is True
    assert result["has_potential_smiles"] is False


def test_sanitize_input_removes_markup_and_limits_length():
    sanitized = sanitize_input("<script>`" + "C" * 1200)

    assert "<" not in sanitized
    assert "`" not in sanitized
    assert len(sanitized) == 1000


@pytest.mark.parametrize(
    "term",
    ["LogP", "TPSA", "Calculate", "Compute", "Protein", "Carbon"],
)
def test_scientific_prose_is_not_a_potential_smiles_candidate(term):
    candidates = InputValidator().extract_potential_smiles_candidates(
        f"Please {term} molecular properties"
    )

    assert term not in candidates


@pytest.mark.parametrize(
    "text",
    ["请计算 (CCO) 的性质", '请计算 "CCO" 的性质', "请计算 'CCO' 的性质"],
)
def test_prose_wrappers_preserve_short_valid_smiles(text):
    assert InputValidator().extract_smiles_candidates(text) == ["CCO"]


def test_bracket_atom_is_preserved_as_valid_smiles():
    assert InputValidator().extract_smiles_candidates("请分析 [Na+]") == ["[Na+]"]


def test_labeled_invalid_smiles_is_not_masked_by_incidental_valid_candidate():
    analysis = InputValidator().analyze_molecular_input(
        "SMILES: CC(C)((；并与 CCO 比较"
    )

    assert analysis.valid_smiles == ("CCO",)
    assert analysis.labeled_candidates[0].value == "CC(C)(("
    assert analysis.labeled_candidates[0].validation == "invalid"
    assert analysis.blocks_execution is True


def test_validate_query_rejects_invalid_labeled_smiles():
    result = InputValidator().validate_query("请计算 SMILES: CC(C)(( 的 LogP")

    assert result["valid"] is False
    assert result["has_potential_smiles"] is True
    assert result["smiles_candidates"] == []


def test_rdkit_import_failure_fails_closed(monkeypatch):
    real_import = builtins.__import__

    def reject_rdkit(name, *args, **kwargs):
        if name == "rdkit" or name.startswith("rdkit."):
            raise ImportError("RDKit unavailable for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_rdkit)

    assert validate_smiles_with_rdkit("CCO") is False
    analysis = InputValidator().analyze_molecular_input("SMILES: CCO")
    assert analysis.validation_available is False
    assert analysis.blocks_execution is True
    assert analysis.valid_smiles == ()


def test_agent_utils_package_import_is_safe_without_rdkit(monkeypatch):
    real_import = builtins.__import__
    previous_utils = sys.modules.pop("src.agent.utils", None)
    previous_extractor = sys.modules.pop("src.agent.utils.smiles_extractor", None)

    def reject_rdkit(name, *args, **kwargs):
        if name == "rdkit" or name.startswith("rdkit."):
            raise ImportError("RDKit unavailable for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_rdkit)
    try:
        module = importlib.import_module("src.agent.utils")
        assert module.__all__ == ["SMILESExtractor"]
        assert "src.agent.utils.smiles_extractor" not in sys.modules
    finally:
        sys.modules.pop("src.agent.utils", None)
        if previous_utils is not None:
            sys.modules["src.agent.utils"] = previous_utils
        if previous_extractor is not None:
            sys.modules["src.agent.utils.smiles_extractor"] = previous_extractor
