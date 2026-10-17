"""Synthetic offline identity contracts; fixtures are not scientific evidence."""

from copy import deepcopy

import pytest
import requests

from src.agent.tools.target_database_tool import TargetDatabaseTool


@pytest.fixture(autouse=True)
def no_real_service_or_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("identity tests must use only the synthetic service")

    monkeypatch.setattr(TargetDatabaseTool, "_get_service", lambda self: self._service)
    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", forbidden)


class SyntheticService:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.structure_calls = []

    def search_targets(self, query, **kwargs):
        self.calls.append((query, kwargs))
        rows = self.rows.get(query, []) if isinstance(self.rows, dict) else self.rows
        return {"status": "resolved", "results": deepcopy(rows),
                "lookup_path": ["synthetic-fixture"]}

    def get_target_structures(self, target_id):
        self.structure_calls.append(target_id)
        return {"structures": [{"structure_id": "6SAM", "source": "fixture"}]}


def execute(query, rows):
    service = SyntheticService(rows)
    tool = TargetDatabaseTool()
    tool._service = service
    return tool.execute(query), service


def bche_row():
    return {"target_id": 1, "gene_symbol": "BCHE", "uniprot_id": "P06276",
            "protein_name": "Synthetic fixture protein", "source": "fixture"}


def egfr_row():
    return {**bche_row(), "target_id": 2, "gene_symbol": "EGFR", "uniprot_id": "P00533"}


@pytest.mark.parametrize("query,accessions,target_ids", [
    ("Find EGFR (UniProt P00533) and BCHE (UniProt P06276) for structures.",
     ["P00533", "P06276"], [2, 1]),
    ("Find BuChE (UniProt P06276) and EGFR (UniProt P00533) for structures.",
     ["P06276", "P00533"], [1, 2]),
])
def test_explicit_name_accession_pairs_keep_distinct_corresponding_rows(query, accessions, target_ids):
    result, service = execute(query, {"P00533": [egfr_row()], "P06276": [bche_row()]})

    assert result["success"] is True
    assert result["status"] == "resolved"
    assert service.calls == [(accession, {}) for accession in accessions]
    assert service.structure_calls == target_ids
    assert [row["uniprot_id"] for row in result["data"]] == accessions
    assert [row["source_query"] for row in result["data"]] == accessions
    assert [row["target_id"] for row in result["data"]] == target_ids


@pytest.mark.parametrize("query", [
    "Compare UniProt P06276 and P00533 structures.",
    "比较 UniProt P06276 与 UniProt P00533 的结构。",
])
def test_name_free_accession_lists_still_query_each_identifier(query):
    result, service = execute(query, {"P00533": [egfr_row()], "P06276": [bche_row()]})

    assert result["success"] is True
    assert result["status"] == "resolved"
    assert service.calls == [("P06276", {}), ("P00533", {})]
    assert [row["uniprot_id"] for row in result["data"]] == ["P06276", "P00533"]


@pytest.mark.parametrize("query", [
    "UNLISTED42 (UniProt P06276)",
    "Find UNLISTED42; UniProt P06276 for experimental structures.",
])
def test_unlisted_explicit_name_is_bound_literally_not_added_to_alias_vocabulary(query):
    row = {**bche_row(), "gene_symbol": "unlisted42"}
    result, service = execute(query, [row])

    assert result["success"] is True
    assert result["status"] == "resolved"
    assert result["data"][0]["gene_symbol"] == "unlisted42"
    assert service.calls == [("P06276", {})]
    assert service.structure_calls == [1]


@pytest.mark.parametrize("query", [
    "TP53 (UniProt P06276)",
    "TP53 (UniProt P06276), PDB: 6SAM",
    "Find TP53; UniProt P06276 for experimental structures.",
    "Find TP53 and BCHE (UniProt P06276) for structures.",
    "Find BCHE (UniProt P06276) and TP53 for structures.",
    "Tumor protein p53 (UniProt P06276)",
])
def test_unknown_explicit_names_cannot_be_discarded_for_accession_lookup(query):
    result, service = execute(query, [bche_row()])

    assert result["success"] is False
    assert result["status"] == "invalid_input"
    assert result["error"]["code"] == "validation_error"
    assert not result.get("data")
    assert not result.get("formatted")
    assert service.structure_calls == []


@pytest.mark.parametrize("rows", [
    {"P00533": [bche_row()], "P06276": [bche_row()]},
    {"P00533": [egfr_row(), bche_row()], "P06276": [bche_row()]},
    {"P00533": [{**egfr_row(), "uniprot_id": "P06276"}], "P06276": [bche_row()]},
    {"P00533": [{**egfr_row(), "uniprot_id": None}], "P06276": [bche_row()]},
    {"P00533": [{**egfr_row(), "gene_symbol": None}], "P06276": [bche_row()]},
    {"P00533": [egfr_row()], "P06276": [egfr_row()]},
])
def test_explicit_pairs_reject_mixed_or_mismatched_service_identities(rows):
    query = "Find EGFR (UniProt P00533) and BCHE (UniProt P06276) for structures."
    result, service = execute(query, rows)

    assert result["success"] is False
    assert result["status"] == "invalid_input"
    assert result["error"]["code"] == "validation_error"
    assert not result.get("data")
    assert not result.get("formatted")


@pytest.mark.parametrize("query", [
    "Find EGFR (UniProt P06276) and BCHE (UniProt P00533) for structures.",
    "Find EGFR (UniProt P06276) and BCHE (UniProt P06276) for structures.",
    "Find EGFR (UniProt P00533) and BCHE for structures.",
    "Find EGFR (UniProt P00533) and UniProt P06276 for structures.",
    "Find TP53 and EGFR (UniProt P00533) and BCHE (UniProt P06276) for structures.",
])
def test_explicit_pairs_do_not_merge_wrong_or_incomplete_name_bindings(query):
    result, service = execute(query, {"P00533": [egfr_row()], "P06276": [bche_row()]})

    assert result["success"] is False
    assert result["status"] == "invalid_input"
    assert result["error"]["code"] == "validation_error"
    assert not result.get("data")
    assert not result.get("formatted")


@pytest.mark.parametrize("query", [
    "查找 EGFR（UniProt P06276），列出实验结构。",
    "Find EGFR; UniProt P06276 for experimental structures.",
    "Find EGFR; UniProt P06276, PDB: 6SAM",
])
def test_labelled_accession_cannot_discard_conflicting_name(query):
    result, service = execute(query, [bche_row()])

    assert result["success"] is False
    assert result["status"] == "invalid_input"
    assert result["error"]["code"] == "validation_error"
    assert "Target name and accession" in result["message"]
    assert not result.get("data")
    assert not result.get("formatted")
    assert service.calls == [("P06276", {"pdb_id": "6SAM"} if "PDB:" in query else {})]
    assert service.structure_calls == []


@pytest.mark.parametrize("gene", [None, "EGFR", "BuChE", "BCHE"])
def test_named_accession_requires_matching_gene_evidence(gene):
    row = bche_row()
    row["gene_symbol"] = gene
    result, service = execute("查找 BuChE（UniProt P06276），列出实验结构。", [row])

    assert result["success"] is (gene in {"BuChE", "BCHE"})
    assert service.calls == [("P06276", {})]
    if gene in {"BuChE", "BCHE"}:
        assert result["status"] == "resolved"
        assert result["data"][0]["gene_symbol"] == gene
        assert service.structure_calls == [1]
    else:
        assert result["status"] == "invalid_input"
        assert not result.get("data")
        assert service.structure_calls == []


@pytest.mark.parametrize("query", [
    "查找 BuChE 和 EGFR（UniProt P06276），列出实验结构。",
    "Find EGFR and BuChE (UniProt P00533) for experimental structures.",
])
def test_extracted_accession_does_not_select_first_of_multiple_named_targets(query):
    row = bche_row()
    if "P00533" in query:
        row.update(gene_symbol="EGFR", uniprot_id="P00533")
    result, service = execute(query, [row])

    assert result["success"] is False
    assert result["status"] == "invalid_input"
    assert result["error"]["code"] == "validation_error"
    assert not result.get("data")
    assert service.structure_calls == []


@pytest.mark.parametrize("query,lookup,kwargs", [
    ("查找BuChE（UniProt P06276），列出实验结构。", "P06276", {}),
    ("查询BuChE（UniProt P06276）", "P06276", {}),
    ("请搜索人源BuChE（UniProt P06276）", "P06276", {}),
    ("查找 BuChE（UniProt P06276），列出实验结构。", "P06276", {}),
    ("BuChE", "BCHE", {}),
    ("查找 BuChE 的实验结构", "查找 BuChE 的实验结构", {}),
    ("P06276", "P06276", {}),
    ("查找 UniProt P06276 的实验结构", "P06276", {}),
    ("Find BuChE; UniProt P06276, PDB: 6SAM", "P06276", {"pdb_id": "6SAM"}),
    ("查找 BCHE 的实验结构，PDB: 6SAM", "BCHE", {"pdb_id": "6SAM"}),
])
def test_matching_and_single_identifier_controls(query, lookup, kwargs):
    result, service = execute(query, [bche_row()])

    assert result["success"] is True
    assert result["status"] == "resolved"
    assert service.calls == [(lookup, kwargs)]
    assert service.structure_calls == [1]
    assert result["data"][0]["uniprot_id"] == "P06276"
    assert result["data"][0]["recommended_structures"][0]["structure_id"] == "6SAM"


@pytest.mark.parametrize("query,rows,expected_queries", [
    ("PDE", [{"gene_symbol": "PDE5A"}], ["PDE"]),
    ("比较 BuChE 和 EGFR 的结构", [{"gene_symbol": "BCHE"}, {"gene_symbol": "EGFR"}],
     ["比较 BuChE 和 EGFR 的结构"]),
    (["BCHE", "EGFR"], [{"gene_symbol": "BCHE"}, {"gene_symbol": "EGFR"}], ["BCHE", "EGFR"]),
])
def test_name_only_and_multi_target_search_do_not_gain_single_identity_guard(query, rows, expected_queries):
    result, service = execute(query, rows)

    assert result["success"] is True
    assert result["status"] == "resolved"
    assert [call[0] for call in service.calls] == expected_queries
