"""Count-only regressions for real S05 optimization requests; no model calls."""

import pytest

from src.agent.contracts.generation_request import (
    GenerationRequestError,
    has_generation_intent,
    parse_generation_count,
    preflight_generation_request,
)


S05 = "对第 2 个候选做两种优化，优先降低 logP，仍保留分子量小于 450 的约束。"


@pytest.mark.parametrize("query", [
    S05,
    "对第3个候选做两种优化",
    "对第3个候选做2种优化",
    "对第三个候选进行两种结构优化",
    "将第3个候选优化成2个候选",
    "将第 3 个候选优化成两个新分子",
    "优化成2个候选",
    "对第3个候选做两种优化；返回2个新SMILES",
    "对第３个候选做２种优化",
    "对第3个候选分子做两种优化",
    "对这个分子做两种优化",
    "对第3个候选分子进行两种结构优化",
    "将第3个候选分子优化成2个候选",
])
def test_optimization_count_is_two_not_the_selected_candidate_ordinal(query):
    assert parse_generation_count(query) == 2
    assert has_generation_intent(query)
    assert preflight_generation_request(query) == 2


@pytest.mark.parametrize("query", ["2种优化", "两种优化", "进行2种优化"])
def test_short_optimization_count_in_active_molecular_context(query):
    assert parse_generation_count(query) == 2
    assert preflight_generation_request(query, active_molecular_skill=True) == 2


@pytest.mark.parametrize("query", [
    "优化第2个候选，优先降低logP，分子量小于450",
    "对第3个候选做优化",
    "对第3个候选做第2种优化",
    "返回第2个候选的SMILES",
    "选择第2种优化",
    "对第3个候选不要做两种优化",
    "不要对第 3 个候选做两种优化",
    "不要把第3个候选优化成2个候选",
    "不要优化成2个候选",
    "无需对第3个候选进行两种结构优化",
    "对第3个候选不做2种优化",
    "例如对第3个候选做两种优化",
    "对第3个候选做两种优化作为一个示例",
    '解释“对第3个候选做两种优化”',
    "`对第3个候选做两种优化`",
    "不要对第3个候选做11种优化",
    "不要把第3个候选优化成2.5个候选",
])
def test_ordinal_only_and_non_actionable_counts_keep_default(query):
    assert parse_generation_count(query, default=7) == 7


@pytest.mark.parametrize("query", [
    "不要对第3个候选分子做两种优化",
    "不要对这个分子做两种优化",
    "无需对第3个候选分子进行两种结构优化",
    "不要将第3个候选分子优化成2个候选",
])
@pytest.mark.parametrize("active_molecular_skill", [False, True])
def test_negated_molecule_optimization_has_no_actionable_count(query, active_molecular_skill):
    assert (
        parse_generation_count(query, default=7),
        has_generation_intent(query, active_molecular_skill=active_molecular_skill),
        preflight_generation_request(query, active_molecular_skill=active_molecular_skill),
    ) == (7, False, None)


@pytest.mark.parametrize("template", [
    "对第3个候选做{count}种优化",
    "将第3个候选优化成{count}个候选",
])
@pytest.mark.parametrize("count", [
    "0", "11", "-2", "2.5", "两点五", "1,000", "2 3", "一2", "2e1", "NaN",
])
def test_optimization_count_rejects_invalid_values(template, count):
    with pytest.raises(GenerationRequestError):
        preflight_generation_request(template.format(count=count))


def test_conflicting_output_counts_are_not_resolved_using_the_ordinal():
    with pytest.raises(GenerationRequestError, match="conflicting generation counts"):
        parse_generation_count("对第3个候选做两种优化；返回3个新SMILES")


@pytest.mark.parametrize("query", [
    "不要对第3个候选做两种优化；而是将第3个候选优化成3个候选",
    "不要对第3个候选分子做两种优化；而是将第3个候选分子优化成3个候选",
    "不要对这个分子做两种优化；而是将这个分子优化成3个候选",
])
def test_active_count_is_not_masked_by_prior_negated_optimization(query):
    assert parse_generation_count(query) == 3
    assert has_generation_intent(query)
    assert preflight_generation_request(query) == 3


def test_explicit_authoritative_count_remains_authoritative():
    assert preflight_generation_request(S05, 4, count_supplied=True) == 4


def test_non_molecular_optimization_does_not_acquire_molecular_intent():
    query = "对算法做两种优化"
    assert not has_generation_intent(query)
    assert preflight_generation_request(query) is None
def test_long_whitespace_non_generation_request_finishes_with_bounded_work():
    """A failed count match must not partition the same whitespace cubically."""
    import subprocess
    import sys

    script = (
        "from src.agent.contracts.generation_request import has_generation_intent; "
        "assert not has_generation_intent(' ' * 10000 + 'Explain logP'); "
        "assert not has_generation_intent('做2' + ' ' * 10000 + 'Explain logP')"
    )
    completed = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, timeout=8)
    assert completed.returncode == 0, completed.stderr.decode('utf-8', errors='replace')
