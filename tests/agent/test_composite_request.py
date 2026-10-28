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
