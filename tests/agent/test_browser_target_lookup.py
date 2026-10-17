"""Offline regressions: never rescue a valid subset of a target request."""

import pytest

from src.agent.tools.target_database_tool import TargetDatabaseTool


@pytest.mark.parametrize("query", [
    "比较 P00533-extra 和 UniProt P06276 的结构",
    "比较 XP00533 和 UniProt P06276 的结构",
    "比较 P06276-extra 和 UniProt P06276 的结构",
    "比较 XP06276 和 UniProt P06276 的结构",
    "比较 P06276$bad 和 UniProt P06276 的结构",
    "比较 UniProt P06276 和 乙酰胆碱酯酶 的结构",
    "将 EGFR 改用 UniProt P06276 查询",
    "比较 EGFR 和 UniProt P06276 的结构",
    "比较 UNKNOWN 和 UniProt P06276 的结构",
    "比较 乙酰胆碱酯酶 与 UniProt P06276 的结构",
    "Compare EGFR and UniProt P06276 structures",
])
def test_partial_or_modified_lookup_preserves_whole_query(query):
    assert TargetDatabaseTool()._search_queries(query) == [query]


def test_exact_s03_still_extracts_explicit_accession():
    query = (
        "查找人源丁酰胆碱酯酶 BuChE（UniProt P06276），"
        "列出可用于对接的实验结构，说明物种、结构编号和共晶配体信息。"
    )
    assert TargetDatabaseTool()._search_queries(query) == ["P06276"]
