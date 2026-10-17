"""Offline parser regressions from real browser target lookup failures."""

import pytest

from src.agent.tools.target_database_tool import TargetDatabaseTool


@pytest.mark.parametrize("query", [
    "查找人源丁酰胆碱酯酶 BuChE（UniProt P06276），列出可用于对接的实验结构，说明物种、结构编号和共晶配体信息。",
    "搜索靶点数据库：人源 BuChE，UniProt P06276。列出实验结构编号与配体信息。",
    "搜索 P06276",
    "Search UniProt accession: P06276 for experimental structures.",
])
def test_explicit_accession_is_provider_query_not_surrounding_instructions(query):
    tool = TargetDatabaseTool()
    assert tool._search_queries(query) == ["P06276"]


@pytest.mark.parametrize("query", [
    "UniProt P06276-extra", "UniProt XP06276", "UniProt P06276A",
    "UniProt P06276_2", "protein kinase C", "unrecognized protein name",
    "比较 UniProt P06276 与 UniProt invalid 的结构",
    "不要查 UniProt P06276，而是 EGFR",
    "Search EGFR, not UniProt P06276",
    "查找 UniProt P06276$bad 的结构", "查找 UniProt P06276/invalid 的结构",
    "比较 UniProt P06276 和 P00533-extra 的实验结构",
    "请勿查询 UniProt P06276", "别查 UniProt P06276，查 EGFR",
    "比较 P00533 和 UniProt P06276 的结构",
])
def test_malformed_or_free_text_target_is_not_silently_shortened(query):
    assert TargetDatabaseTool()._search_queries(query) == [query]


def test_structured_reverse_target_records_keep_existing_gene_priority():
    assert TargetDatabaseTool()._search_queries([
        {"target_gene": "BCHE", "uniprot_id": "P06276"},
        {"uniprot_id": "P00533"},
    ]) == ["BCHE", "P00533"]


def test_multiple_explicit_accessions_are_not_collapsed_to_one():
    assert TargetDatabaseTool()._search_queries(
        "比较 UniProt P06276 与 UniProt P00533 的结构。"
    ) == ["P06276", "P00533"]


@pytest.mark.parametrize("query", [
    "比较 UniProt P06276 和 P00533 的实验结构",
    "Compare UniProt P06276 and P00533 structures.",
    "比较 UniProt P06276、P00533 的实验结构",
])
def test_multiple_accessions_can_share_one_label(query):
    assert TargetDatabaseTool()._search_queries(query) == ["P06276", "P00533"]


def test_wrapper_dispatches_only_the_explicit_accession_to_provider():
    class RecordingService:
        def __init__(self):
            self.calls = []

        def search_targets(self, query):
            self.calls.append(query)
            return {"status": "not_found", "results": [], "lookup_path": ["fixture"]}

    tool = TargetDatabaseTool()
    tool._service = RecordingService()
    result = tool.execute("搜索靶点数据库：人源 BuChE，UniProt P06276。列出实验结构编号与配体信息。")
    assert tool._service.calls == ["P06276"]
    assert result["status"] == "not_found"
    assert result["data"] is None


@pytest.mark.parametrize("ligands,expected", [
    (["BUA", "NAG"], "BUA, NAG"),
    (None, "unavailable"),
    ([], "none recorded"),
])
def test_structure_report_preserves_ligand_records_without_claiming_a_pocket(ligands, expected):
    class FixtureService:
        def search_targets(self, query):
            structure = {"structure_id": "1P0I", "source": "fixture",
                         "method": "X-RAY DIFFRACTION", "resolution": 2.0}
            if ligands is not None:
                structure["ligand_ids"] = ligands
            return {"status": "resolved", "results": [{
                "gene_symbol": "BCHE", "uniprot_id": "P06276",
                "organism": "Homo sapiens", "recommended_structures": [structure],
            }]}

    tool = TargetDatabaseTool()
    tool._service = FixtureService()
    result = tool.execute("P06276")
    assert expected in result["formatted"]
    assert "Ligand IDs" in result["formatted"]
    assert "pocket role not verified" in result["formatted"]
