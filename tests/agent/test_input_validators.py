from src.agent.utils.validators import InputValidator, sanitize_input


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
