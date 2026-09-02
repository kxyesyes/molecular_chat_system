import builtins
import importlib
import sys

import pytest

import src.agent.utils.validators as validators_module
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
    "term",
    [
        "BBB",
        "PPB",
        "CNS",
        "NO",
        "PK",
        "S9",
        "CNS/PK",
        "IC50/P450",
        "IC50-P450",
        "ADMET",
        "LogP",
        "QED",
        "TPSA",
    ],
)
def test_unlabeled_medicinal_chemistry_terms_are_not_molecular_input(term):
    validator = InputValidator()
    query = f"请评估 {term} 风险"
    analysis = validator.analyze_molecular_input(query)
    result = validator.validate_query(query)

    assert analysis.potential_smiles == ()
    assert analysis.blocks_execution is False
    assert result["has_potential_smiles"] is False
    assert result["valid"] is False


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("请分析分子 BBB", "BBB"),
        ("Evaluate molecule PPB", "PPB"),
        ("请分析结构 CNS", "CNS"),
        ("Evaluate compound NO", "NO"),
        ("请分析化合物 PK", "PK"),
    ],
)
def test_ambiguous_atom_token_requires_and_honors_molecular_cue(query, expected):
    analysis = InputValidator().analyze_molecular_input(query)

    assert analysis.potential_smiles == (expected,)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("SMILES: NO", "NO"),
        ("分子SMILES: CNS", "CNS"),
    ],
)
def test_explicitly_labeled_domain_token_is_validated_as_structure(query, expected):
    analysis = InputValidator().analyze_molecular_input(query)

    assert analysis.potential_smiles == (expected,)
    assert analysis.labeled_candidates[0].value == expected
    assert analysis.labeled_candidates[0].validation in {"valid", "invalid"}


@pytest.mark.parametrize(
    "smiles",
    [
        "CCO",
        "CCN",
        "CCCCCCCCCCCCCCCCCCCCC",
        "CC(C)O",
        "CC(=O)Oc1ccccc1C(=O)O",
        "c1ccccc1",
    ],
)
def test_unlabeled_explicit_structure_tokens_remain_supported(smiles):
    analysis = InputValidator().analyze_molecular_input(f"请分析 {smiles}")

    assert analysis.valid_smiles == (smiles,)
    assert analysis.blocks_execution is False


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


def test_contextual_invalid_smiles_is_not_masked_by_valid_candidate():
    analysis = InputValidator().analyze_molecular_input(
        "请全面分析。CCO 与 CC(C)(( 的成药性"
    )

    assert analysis.valid_smiles == ("CCO",)
    assert any(
        candidate.value == "CC(C)((" and candidate.validation == "invalid"
        for candidate in analysis.candidates
    )
    assert analysis.blocks_execution is True


@pytest.mark.parametrize("value", ["?", "X"])
def test_nonempty_labeled_non_smiles_value_fails_closed(value):
    analysis = InputValidator().analyze_molecular_input(f"SMILES: {value}")

    assert analysis.potential_smiles == (value,)
    assert analysis.valid_smiles == ()
    assert analysis.blocks_execution is True


@pytest.mark.parametrize("term", ["C++", "C#", "S9+"])
def test_programming_and_assay_notation_without_molecular_context_is_ignored(term):
    analysis = InputValidator().analyze_molecular_input(
        f"请检索关于 {term} 的普通说明"
    )

    assert analysis.potential_smiles == ()
    assert analysis.blocks_execution is False


@pytest.mark.parametrize(
    ("query", "term"),
    [
        ("请解释用 C++ 处理分子结构的方法", "C++"),
        ("请解释 C# 在分子数据处理中的用途", "C#"),
        ("请解释 S9+ 对药物分子稳定性评估的影响", "S9+"),
    ],
)
def test_non_molecular_notation_is_ignored_even_in_molecular_prose(query, term):
    analysis = InputValidator().analyze_molecular_input(query)

    assert term not in analysis.potential_smiles
    assert analysis.blocks_execution is False


def test_bare_structurally_explicit_invalid_smiles_fails_closed():
    analysis = InputValidator().analyze_molecular_input("CC(C)((")

    assert analysis.potential_smiles == ("CC(C)((",)
    assert analysis.valid_smiles == ()
    assert analysis.blocks_execution is True


def test_bare_simple_smiles_fails_closed_when_rdkit_is_unavailable(monkeypatch):
    monkeypatch.setattr(validators_module, "_load_rdkit_chem", lambda: None)

    analysis = InputValidator().analyze_molecular_input("CCO")

    assert analysis.potential_smiles == ("CCO",)
    assert analysis.validation_available is False
    assert analysis.blocks_execution is True


@pytest.mark.parametrize("invalid", ["X", "CCX"])
def test_invalid_item_in_labeled_smiles_list_fails_closed(invalid):
    analysis = InputValidator().analyze_molecular_input(f"SMILES: CCO, {invalid}")

    assert analysis.valid_smiles == ("CCO",)
    assert invalid in analysis.potential_smiles
    assert analysis.blocks_execution is True


@pytest.mark.parametrize(
    "term",
    ["ADMET", "QED", "TPSA", "IC50", "P450", "BBB", "CNS"],
)
def test_task_term_after_labeled_smiles_is_not_treated_as_list_item(term):
    analysis = InputValidator().analyze_molecular_input(f"SMILES: CCO, {term}")

    assert analysis.potential_smiles == ("CCO",)
    assert analysis.valid_smiles == ("CCO",)
    assert analysis.blocks_execution is False


@pytest.mark.parametrize("term", ["C++17", "C#12", "C/C++", "C/C++17"])
def test_versioned_programming_notation_is_not_molecular_input(term):
    analysis = InputValidator().analyze_molecular_input(
        f"请检索关于 {term} 的开发说明"
    )

    assert analysis.potential_smiles == ()
    assert analysis.blocks_execution is False


def test_plain_chat_without_candidates_does_not_load_rdkit(monkeypatch):
    def fail_if_loaded():
        raise AssertionError("RDKit must not be loaded for plain chat")

    monkeypatch.setattr(validators_module, "_load_rdkit_chem", fail_if_loaded)

    analysis = InputValidator().analyze_molecular_input("你好，请介绍系统能力")

    assert analysis.potential_smiles == ()
    assert analysis.blocks_execution is False


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_rdkit_initialization_failure_is_reported_as_unavailable(
    monkeypatch,
    error_type,
):
    real_import = builtins.__import__

    def reject_rdkit(name, *args, **kwargs):
        if name == "rdkit" or name.startswith("rdkit."):
            raise error_type("broken RDKit runtime")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_rdkit)

    analysis = InputValidator().analyze_molecular_input("SMILES: CCO")

    assert analysis.validation_available is False
    assert analysis.valid_smiles == ()
    assert analysis.blocks_execution is True


def test_chinese_adjacent_smiles_label_preserves_invalid_precedence():
    analysis = InputValidator().analyze_molecular_input(
        "分子SMILES: CC(C)((；并与 CCO 比较"
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


def test_validate_query_rejects_labeled_invalid_even_with_incidental_valid_smiles():
    result = InputValidator().validate_query(
        "请计算。SMILES: CC(C)((；并与 CCO 比较"
    )

    assert result["valid"] is False
    assert result["has_potential_smiles"] is True
    assert result["smiles_candidates"] == ["CCO"]


@pytest.mark.parametrize(
    "query",
    [
        "No SMILES was provided; calculate LogP.",
        "没有提供 SMILES，请计算 LogP。",
    ],
)
def test_prose_without_molecular_input_is_classified_as_missing_smiles(query):
    validator = InputValidator()
    analysis = validator.analyze_molecular_input(query)
    result = validator.validate_query(query)

    assert analysis.potential_smiles == ()
    assert analysis.blocks_execution is False
    assert result["valid"] is False
    assert result["has_potential_smiles"] is False


def test_structurally_explicit_malformed_smiles_remains_blocking():
    analysis = InputValidator().analyze_molecular_input("请分析分子 CC(C)((")

    assert analysis.potential_smiles == ("CC(C)((",)
    assert analysis.valid_smiles == ()
    assert analysis.blocks_execution is True


def test_labeled_square_wrapped_smiles_falls_back_to_valid_inner_structure():
    analysis = InputValidator().analyze_molecular_input("SMILES: [CCO]")

    assert analysis.valid_smiles == ("CCO",)
    assert analysis.labeled_candidates[0].value == "CCO"
    assert analysis.labeled_candidates[0].validation == "valid"
    assert analysis.blocks_execution is False


def test_genuine_bracket_atom_is_not_unwrapped_after_raw_validation():
    analysis = InputValidator().analyze_molecular_input("SMILES: [Na+]")

    assert analysis.valid_smiles == ("[Na+]",)
    assert analysis.labeled_candidates[0].value == "[Na+]"
    assert analysis.blocks_execution is False


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
