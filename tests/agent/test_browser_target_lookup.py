"""Offline regressions: never rescue a valid subset of a target request."""

import json
from copy import deepcopy

import pytest

from src.agent.tools.target_database_tool import TargetDatabaseTool
from src.agent.planning.request_parsing import target_lookup_text


@pytest.mark.parametrize('query', [
    'Lookup BuChE and UNKNOWN target information',
    'Lookup BuChE target information except EGFR',
    'Lookup UNKNOWN target information',
    'Lookup BuChE target information and docking',
])
def test_short_target_information_normalization_does_not_drop_qualifications(query):
    assert target_lookup_text(query) == query


@pytest.mark.parametrize('status', ['resolved', 'partial'])
def test_exact_lookup_retains_structure_evidence_beyond_model_view_budget(monkeypatch, status):
    structures = [{
        'structure_id': f'S{i:03}', 'source': 'RCSB_PDB',
        'title': 'Synthetic structure title ' + 'x' * 900,
        'ligand_ids': ['SYN'], 'resolution': 2.0, 'stale': False,
    } for i in range(10)]
    row = {'gene_symbol': 'BCHE', 'uniprot_id': 'P06276',
           'organism': 'Homo sapiens', 'source': 'synthetic_fixture',
           'source_record_id': 'fixture-only', 'recommended_structures': structures,
           'structure_count': 10}

    class Service:
        def search_targets(self, query, **kwargs):
            return {'status': status, 'results': [deepcopy(row)],
                    'warnings': ['synthetic warning'], 'lookup_path': ['fixture']}

    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda: Service())
    result = tool.execute('BCHE')
    assert len(json.dumps(result).encode()) > 12 * 1024
    assert result['status'] == status
    assert result['success'] is (status == 'resolved')
    assert result['data'][0]['recommended_structures'] == structures
    assert len(result['evidence']) == 11
    assert 'S009' in result['formatted']
    assert 'synthetic warning' in result['warnings']
    assert 'target_search_output_bounded' not in result['warnings']


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


def test_broad_family_lookup_is_bounded_without_silent_success(monkeypatch):
    class Service:
        def search_targets(self, query, **kwargs):
            structures = [
                {
                    "structure_id": f"PDB{j}",
                    "source": "RCSB_PDB",
                    "title": "A representative experimental structure " + "x" * 1200,
                    "organism": "Homo sapiens",
                }
                for j in range(3)
            ]
            return {
                "status": "resolved",
                "results": [
                    {
                        "gene_symbol": f"PDE{i}",
                        "protein_name": "Phosphodiesterase family member " + "x" * 600,
                        "uniprot_id": f"P{i:05d}",
                        "organism": "Homo sapiens",
                        "source": "local_target_db",
                        "source_record_id": f"LOCAL_PDE{i}",
                        "match_reason": "family match",
                        "recommended_structures": structures,
                    }
                    for i in range(24)
                ],
                "evidence": [],
                "lookup_path": ["local"],
            }

    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, "_get_service", lambda: Service())

    result = tool.execute("PDE")

    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) < 12 * 1024
    assert result["success"] is False
    assert result["status"] == "partial"
    assert "target_search_output_bounded" in result["warnings"]
    assert result["quality"]["status"] == "partial"
    assert len(result["data"]) <= 5
    assert len(result["evidence"]) <= 5
    assert result["quality"]["matched_target_count"] == 24
    assert result["quality"]["requires_exact_subtype"] is True
