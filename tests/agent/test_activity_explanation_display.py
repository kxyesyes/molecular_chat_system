"""Activity prose displays known statuses without changing unrelated language."""
import pytest

from src.web.web_business import activity_explanation_text


@pytest.mark.parametrize('text,expected', [
    ('classification_regression_consistent = false', '分类与回归结果：不一致'),
    ('分类与回归结果一致 = false', '分类与回归结果：不一致'),
    ('记录中分类与回归一致性为 false', '记录中分类与回归结果：不一致'),
    ('（一致性为 false）', '（一致性：不一致）'),
    ('分类与回归结果一致 = true', '分类与回归结果：一致'),
    ('species = mixed', '物种：混合物种'),
    ('物种 = mixed', '物种：混合物种'),
    ('混合物种（mixed）', '混合物种'),
])
def test_observed_status_assignments_use_public_language(text, expected):
    assert activity_explanation_text(text, '解释刚才的结果。') == expected


@pytest.mark.parametrize('text,query', [
    ('classification_regression_consistent = false', '解释 classification_regression_consistent=false。'),
    ('species = mixed', '解释 species=mixed 的原始字段。'),
    ('false positives and mixed effects models', '解释刚才的结果。'),
    ('某测试结果为 false，mixed 是示例字符串。', '解释刚才的结果。'),
])
def test_unrelated_boolean_text_and_explicit_field_questions_stay_unchanged(text, query):
    assert activity_explanation_text(text, query) == text
