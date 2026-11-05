import pytest


def parse(query):
    from src.agent.planning.composite_request import (
        parse_target_structure_molecule_request,
    )
    return parse_target_structure_molecule_request(query)


def test_composite_request_binds_one_smiles_and_one_target():
    request = parse(
        "请分析 CCO 的分子性质和 ADMET，并检索 PDE4A 的可用 PDB 结构，"
        "推荐适合后续对接的结构。"
    )

    assert request is not None
    assert request.smiles == ("CCO",)
    assert request.target == "PDE4A"


@pytest.mark.parametrize("query", [
    "请分析这个分子的 ADMET，并检索 PDE4A 的可用 PDB 结构。",
    "请分析 CCO 和 CCN 的分子性质，并检索 PDE4A 的结构。",
    "请分析 CCO 的分子性质，并检索 PDE4A 和 EGFR 的结构。",
    "请分析 CCO 的性质，并运行 PDE4A 分子对接。",
    "请分析 CCO 的性质，并为 PDE4A 生成候选分子。",
])
def test_ambiguous_or_other_scientific_obligations_are_not_composite(query):
    assert parse(query) is None


def test_lookup_suitability_language_is_not_docking_execution():
    request = parse("请计算 CCO 的 LogP，并检索 PDE4A 结构是否适合后续对接。")

    assert request is not None
    assert request.target == "PDE4A"


@pytest.mark.parametrize('query', [
    '查询人 EGFR 的生物学功能和 UniProt 编号，并预测 CCO 的 ADMET。',
    '预测 CCO 的 ADMET，并查询人 EGFR 的生物学功能和 UniProt 编号。',
])
def test_target_function_and_admet_bind_separate_subjects(query):
    request = parse(query)
    assert request is not None
    assert request.target == 'EGFR' and request.smiles == ('CCO',)


def test_composite_binds_property_to_its_own_molecule():
    query = '预测 CCO 的 ADMET，并查询人 EGFR 的生物学功能和 UniProt 编号，再计算 CCN 的 LogP。'
    request = parse(query)
    assert request is not None
    assert request.target == 'EGFR' and request.smiles == ('CCO',)
    assert request.property_smiles == 'CCN' and request.property_metric == 'LogP'


def test_composite_does_not_drop_fourth_action():
    query = ('预测 CCO 的 ADMET，并查询人 EGFR 的生物学功能和 UniProt 编号，'
             '再计算 CCN 的 LogP，再预测 CCC 的活性。')
    assert parse(query) is None
