"""Offline regressions: never rescue a valid subset of a target request."""

import json
from copy import deepcopy

import pytest

from src.agent.tools.target_database_tool import TargetDatabaseTool
from src.agent.planning.request_parsing import target_lookup_text


def test_target_function_flag_reaches_service(monkeypatch):
    calls = []
    class Service:
        def search_targets(self, query, **kwargs):
            calls.append((query, kwargs))
            return {'status': 'not_found', 'results': [], 'warnings': [], 'lookup_path': []}
    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda: Service())
    tool.execute({'query': 'EGFR', 'include_function': True})
    assert calls == [('EGFR', {'include_function': True})]


def test_target_adapter_preserves_function_flag():
    from types import SimpleNamespace
    from src.agent.tooling.target_contract import TargetSearchInput, TargetToolAdapter
    adapter = SimpleNamespace(spec=SimpleNamespace(name='target_database_search', input_schema=TargetSearchInput))
    payload = {'query': 'EGFR', 'include_function': True}
    assert TargetToolAdapter._validate_input(adapter, payload) == payload


def test_target_adapter_preserves_requested_species():
    from types import SimpleNamespace
    from src.agent.tooling.target_contract import TargetSearchInput, TargetToolAdapter
    adapter = SimpleNamespace(spec=SimpleNamespace(name='target_database_search', input_schema=TargetSearchInput))
    payload = {'query': 'EGFR', 'organism': 'Mus musculus'}
    assert TargetToolAdapter._validate_input(adapter, payload) == payload


@pytest.mark.parametrize('query,expected', [
    ('列出三个分辨率最高的实验结构', 3), ('列出分辨率最高的四个 PDB 条目', 4),
    ('找出分辨率最高的结构', 1), ('Find the best-resolution structure', 1),
    ('List the three highest resolution structures', 3),
    ('按分辨率从高到低给出前 5 项', 5), ('列出分辨率最高的25个 PDB', 25),
    ('列出可用结构，不按分辨率最高排序', None),
    ('不用按最高分辨率排序', None), ('查找人源 EGFR 的实验结构和分辨率', None),
])
def test_structure_ranking_request_selects_only_positive_scope(query, expected):
    from src.agent.tools.target_observation import target_resolution_limit
    assert target_resolution_limit(query) == expected


def test_ranked_lookup_cannot_rank_fallback_preview_after_enumeration_failure(monkeypatch):
    class Service:
        def search_targets(self, query, **kwargs):
            return {'status': 'resolved', 'results': [{'target_id': 1, 'gene_symbol': 'EGFR',
                'uniprot_id': 'P00533', 'recommended_structures': [{'structure_id': '1M17',
                    'source': 'RCSB_PDB', 'structure_type': 'experimental', 'resolution': 2.6}]}]}
        def get_target_structures(self, target_id):
            raise OSError('test only')
    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda: Service())
    result = tool.execute({'query': 'EGFR', 'resolution_limit': 1})
    assert not result['success'] and not result['data'][0]['recommended_structures']
    assert '无法核对' in ' '.join(result['warnings'])


def test_resolution_preview_reports_display_limit_with_a_larger_request():
    from src.web.decision_chat import _target_display_answer
    structures = [{'structure_id': f'1A{i:02}', 'source': 'RCSB_PDB', 'structure_type': 'experimental',
                   'resolution': 2.5} for i in range(20)]
    data = [{'gene_symbol': 'EGFR', 'searched_structure_count': 27, 'comparable_structure_count': 27,
             'recommended_structures': structures}]
    assert '单次最多展示 20 条' in _target_display_answer(data, resolution_limit=25)


@pytest.mark.parametrize('value', [True, 0, -1, 21, '3'])
def test_target_adapter_rejects_invalid_resolution_limit(value):
    from src.agent.tooling.target_contract import TargetSearchInput
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TargetSearchInput.model_validate({'query': 'EGFR', 'resolution_limit': value})


def test_target_species_cannot_accept_a_mismatched_service_result(monkeypatch):
    class Service:
        def search_targets(self, query, **kwargs):
            return {'status': 'resolved', 'results': [{'gene_symbol': 'EGFR',
                    'uniprot_id': 'P00533', 'organism': 'Homo sapiens'}]}
    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda organism=None: Service())
    result = tool.execute({'query': 'EGFR', 'organism': 'Mus musculus'})
    assert not result['success'] and not result.get('data')


def test_mouse_target_lookup_uses_explicit_accession_without_gene_fallback(monkeypatch):
    calls = []
    class Service:
        def search_targets(self, query, **kwargs):
            calls.append(query)
            return {'status': 'not_found', 'results': [], 'warnings': [], 'lookup_path': []}
    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda organism=None: Service())
    result = tool.execute({'query': '请查询小鼠 EGFR（UniProt P00533）的蛋白信息和实验结构。',
                           'organism': 'Mus musculus'})
    assert calls == ['P00533']
    assert result['status'] == 'not_found' and not result.get('data')


def test_web_mouse_accession_not_found_is_reported(tmp_path, monkeypatch):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn

    class Service:
        def search_targets(self, query, **kwargs):
            return {'status': 'not_found', 'results': [], 'warnings': [], 'lookup_path': ['UniProt']}

    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda organism=None: Service())
    handler = make_web_handler([tool], SQLiteAgentStateStore(tmp_path / 'target.sqlite'))
    complete, frames = run_web_turn(handler, '请查询小鼠 EGFR（UniProt P00533）的蛋白信息和实验结构。',
                                    result_format='summary_details_v1')
    assert '未找到匹配靶点' in complete['content'], frames
    assert 'Q01279' not in complete['content']


def test_unsupported_target_species_is_explained_without_using_human_data():
    from src.web.web_business import WebTurnProjection
    query = '请查询大鼠 EGFR 的蛋白信息和实验结构。'
    view = WebTurnProjection(query, 'species-session', []).followup(enable_tools=True)
    assert view['status'] == 'rejected'
    assert '大鼠' in view['content'] and '不支持' in view['content'] and '人源' in view['content']
    assert WebTurnProjection(query + '再运行对接。', 'species-session', []).followup(enable_tools=True) is None


def test_unknown_target_species_is_explained_without_using_human_data():
    from src.web.web_business import WebTurnProjection
    query = '请查询鸡源 EGFR 的蛋白信息和实验结构，列出物种、UniProt 和 PDB 编号。'
    view = WebTurnProjection(query, 'species-session', []).followup(enable_tools=True)
    assert view['status'] == 'rejected'
    assert '鸡源' in view['content'] and '不支持' in view['content']
    assert WebTurnProjection(query + '再运行对接。', 'species-session', []).followup(enable_tools=True) is None


@pytest.mark.parametrize('species', ['鸡', 'chicken', '斑马鱼', 'zebrafish'])
def test_unsupported_species_without_source_suffix_is_explained(species):
    from src.web.web_business import WebTurnProjection
    query = f'请查询{species} EGFR 的蛋白信息和实验结构，列出物种、UniProt 和 PDB 编号。'
    view = WebTurnProjection(query, 'species-session', []).followup(enable_tools=True)
    assert view['status'] == 'rejected'
    assert species in view['content'] and '不支持' in view['content']


@pytest.mark.parametrize('qualifier', ['雪貂', 'ferret', 'Mustela putorius'])
def test_unrecognized_target_qualifier_requires_clarification(qualifier):
    from src.web.web_business import WebTurnProjection
    query = f'请查询{qualifier} EGFR 的蛋白信息和实验结构，列出物种、UniProt 和 PDB 编号。'
    view = WebTurnProjection(query, 'species-session', []).followup(enable_tools=True)
    assert view['status'] == 'rejected'
    assert qualifier in view['content'] and '物种' in view['content'] and '未检索' in view['content']
    assert '人源' in view['content'] and '小鼠' in view['content']
    assert WebTurnProjection(query + '再运行对接。', 'species-session', []).followup(enable_tools=True) is None


@pytest.mark.parametrize('species', ['雪貂', '大鼠', '鸡', 'ferret'])
def test_explicit_unsupported_species_declaration_is_explained(species):
    from src.web.web_business import WebTurnProjection
    query = f'请查询 EGFR 的蛋白信息和实验结构，物种为{species}。'
    view = WebTurnProjection(query, 'species-session', []).followup(enable_tools=True)
    assert view['status'] == 'rejected'
    assert species in view['content'] and '人源' in view['content'] and '小鼠' in view['content']
    assert '未检索' in view['content']
    assert WebTurnProjection(query + '再运行对接。', 'species-session', []).followup(enable_tools=True) is None


@pytest.mark.parametrize('species', ['人源', '小鼠'])
def test_supported_species_declaration_is_not_refused(species):
    from src.web.web_business import WebTurnProjection
    query = f'请查询 EGFR 的蛋白信息和实验结构，物种为{species}。'
    assert WebTurnProjection(query, 'species-session', []).followup(enable_tools=True) is None


def test_web_species_declaration_rejection_reaches_summary(tmp_path):
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from pi_web_support import make_web_handler, run_web_turn
    handler = make_web_handler([], SQLiteAgentStateStore(tmp_path / 'species.sqlite'))
    complete, _ = run_web_turn(handler, '请查询 EGFR 的蛋白信息和实验结构，物种为雪貂。',
                               result_format='summary_details_v1')
    assert complete['status'] == 'rejected'
    assert all(word in complete['content'] for word in ('雪貂', '人源', '小鼠', '未检索'))


def test_target_lookup_retains_structures_beyond_preview_and_resolution(monkeypatch):
    from src.web.decision_chat import _target_display_answer
    structures = [{'structure_id': f'{i:04d}', 'source': 'RCSB_PDB',
                   'structure_type': 'experimental', 'resolution': 1.5 + i / 10,
                   'ligand_ids': [], 'recommendation_level': 'High'} for i in range(5)]
    structures[-1]['resolution'] = None
    class Service:
        def search_targets(self, query, **kwargs):
            return {'status': 'resolved', 'results': [{'target_id': 1, 'gene_symbol': 'PDE4A',
                'organism': 'Homo sapiens', 'structure_count': 5}], 'warnings': [], 'lookup_path': ['local']}
        def get_target_structures(self, target_id):
            return {'structures': deepcopy(structures)}
    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda: Service())
    result = tool.execute('查找人源 PDE4A 的实验结构和分辨率')
    assert len(result['data'][0]['recommended_structures']) == 5
    answer = _target_display_answer(result['data'], concise=True)
    assert all(row['structure_id'] in answer for row in structures)
    assert '1.5' in answer and '分辨率：未记录' in answer
    assert '推荐级别' not in answer and 'High' not in answer
    assert result['data'][0]['recommended_structures'][0]['recommendation_level'] == 'High'
    assert result['status'] == 'partial' and 'structure_resolution_missing' in result['warnings']


def test_long_structure_list_reports_truncation(monkeypatch):
    from src.web.decision_chat import _target_display_answer
    class Service:
        def search_targets(self, query, **kwargs):
            return {'status': 'resolved', 'results': [{'target_id': 1, 'gene_symbol': 'PDE4A',
                'structure_count': 21}], 'warnings': [], 'lookup_path': ['local']}
        def get_target_structures(self, target_id):
            return {'structures': [{'structure_id': f'{i:04d}', 'source': 'RCSB_PDB',
                'structure_type': 'experimental', 'resolution': 2.0} for i in range(21)]}
    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda: Service())
    result = tool.execute('查找 PDE4A 的实验结构和分辨率')
    assert result['status'] == 'partial' and 'structure_list_truncated' in result['warnings']
    assert len(result['data'][0]['recommended_structures']) == 20
    assert '当前展示 20 条，记录总数为 21' in _target_display_answer(result['data'], concise=True)
    identity = tool.execute('PDE4A')
    assert identity['status'] == 'resolved'
    assert 'structure_list_truncated' in identity['warnings']


def test_named_target_comparison_keeps_structure_examples_for_each_identity(monkeypatch):
    class Service:
        def search_targets(self, query, **kwargs):
            target_id = 1 if query in ('PDE5A', 'PDE5') else 2
            return {'status': 'resolved', 'results': [{'target_id': target_id,
                'gene_symbol': ('PDE5A', 'EGFR')[target_id - 1], 'structure_count': 30,
                'has_experimental_structure': True, 'has_alphafold_structure': True}]}

        def get_target_structures(self, target_id):
            return {'structures': [{'structure_id': f'{target_id}{i:03d}',
                'title': 'x' * 1200, 'source': 'AlphaFold' if i == 29 else 'RCSB_PDB',
                'structure_type': 'predicted' if i == 29 else 'experimental'} for i in range(30)]}

    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda: Service())
    for query, expected_count in [(['PDE5A', 'EGFR'], 2), (['PDE5A', 'PDE5'], 1)]:
        result = tool.execute(query)
        assert result['status'] == 'resolved'
        assert len(result['data']) == expected_count
        for target in result['data']:
            assert target['structure_count'] == 30
            assert {row['source'] for row in target['recommended_structures']} == {'RCSB_PDB', 'AlphaFold'}
            assert all(row['structure_id'] in result['formatted'] for row in target['recommended_structures'])
        assert len(json.dumps(result).encode()) < 48 * 1024


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


def test_human_chinese_alias_and_accession_reaches_target_lookup(monkeypatch):
    calls = []
    class Service:
        def search_targets(self, query, **kwargs):
            calls.append(query)
            return {'status': 'not_found', 'results': [], 'warnings': [], 'lookup_path': []}
    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda: Service())
    result = tool.execute('请查询人丁酰胆碱酯酶 BCHE（UniProt P06276）的蛋白信息和实验结构。')
    assert calls == ['P06276']
    assert result['status'] == 'not_found'


def test_target_function_and_structures_with_named_accession_reaches_authoritative_service(monkeypatch):
    calls = []
    class Service:
        def search_targets(self, query, **kwargs):
            calls.append((query, kwargs))
            return {'status': 'not_found', 'results': [], 'warnings': [], 'lookup_path': []}
    tool = TargetDatabaseTool()
    monkeypatch.setattr(tool, '_get_service', lambda: Service())
    result = tool.execute({'query': '请查询人 EGFR（UniProt P00533）的蛋白功能和实验结构。', 'include_function': True})
    assert calls == [('P00533', {'include_function': True})]
    assert result['status'] == 'not_found'
