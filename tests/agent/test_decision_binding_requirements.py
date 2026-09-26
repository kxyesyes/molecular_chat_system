"""B1 preparation/closed views only; no execution or source-authenticity claim."""
import importlib
import json
from copy import deepcopy
from enum import Enum

import pytest


B1 = 'ordinary-semantic-b1-v1'
ERROR = 'invalid_binding_requirements'
TOOLS = frozenset({'property_calculator', 'drug_likeness_assessment',
    'admet_predictor', 'activity_predictor', 'reverse_target_predictor',
    'rag_search', 'target_database_search'})


def api():
    return importlib.import_module('src.agent.contracts.binding_requirements')


def payload(**changes):
    return {'version': '2', 'profile': B1, **changes}


def parse(value):
    return api().parse_binding_requirements(value)


def reject(value):
    with pytest.raises(ValueError) as error:
        parse(value)
    assert type(error.value) is ValueError
    assert error.value.args == (ERROR,)
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def prepare(value, query='SMILES: CCO', *, capabilities=None, metadata=None,
            allowed=TOOLS, required=(), kind='scientific'):
    from src.agent.contracts import AgentContext
    from src.agent.harness.decision_bindings import prepare_binding_requirements
    data = dict(metadata or {})
    if capabilities is not None:
        data['capabilities'] = capabilities
    context = AgentContext(query=query, trace_id='preparation-only', metadata=data)
    before = deepcopy(context)
    result = prepare_binding_requirements(value, context=context, request_kind=kind,
        allowed_tools=allowed, required_tools=required)
    assert context == before
    return result


def test_explicit_v2_and_detached_frozen_roundtrip():
    raw = payload(retrieval_result={'query': ' synthetic query ', 'k': 3, 'require_hits': False})
    result = parse(raw)
    assert result.model_dump(mode='json')['retrieval_result'] == raw['retrieval_result']
    raw['retrieval_result']['query'] = 'changed'
    assert result.retrieval_result.query == ' synthetic query '
    for model in (result, result.retrieval_result):
        assert type(model).model_config['strict'] is True
        assert type(model).model_config['frozen'] is True
        assert type(model).model_config['extra'] == 'forbid'
    with pytest.raises(ValueError):
        result.retrieval_result.query = 'changed'
    assert parse(result) == result and parse(result) is not result
    assert parse(result.model_dump(mode='json')) == result


@pytest.mark.parametrize('raw', [None, {}, {'version': '2'}, {'profile': B1},
    payload(version='1'), payload(profile='ordinary-semantic-b2-v1'),
    payload(unknown=True), payload(forbidden_tools=('rag_search',)),
    payload(forbidden_tools=['rag_search', 'rag_search']),
    payload(forbidden_tools=['a'] * 33), payload(forbidden_tools=['rag_search\n']),
    payload(retrieval_result={'query': b'query'}),
    payload(retrieval_result={'query': ' '}),
    payload(retrieval_result={'query': 'q', 'k': True}),
    payload(retrieval_result={'query': 'q', 'k': 3.0}),
    payload(retrieval_result={'query': 'q', 'k': '3'}),
    payload(retrieval_result={'query': 'q', 'require_hits': 1}),
    payload(retrieval_result={'query': '界' * 5462}),
    payload(target_result={'input': 'user'}),
    payload(target_result={'input': 'user', 'query': '\t'}),
    payload(target_result={'input': 'user', 'query': 'q' * 8193}),
    payload(target_result={'input': 'reverse', 'query': 'EGFR'}),
    payload(target_result={'input': 'reverse', 'require_resolved': 1}),
    payload(reverse_result={'expected_smiles': ['CCO']}),
    payload(reverse_result={'expected_smiles': ''}),
])
def test_closed_native_requirements(raw):
    reject(raw)


@pytest.mark.parametrize('tool', ['admet_predictor', 'activity_predictor'])
@pytest.mark.parametrize('change', [
    {'exact_molecule_count': True}, {'exact_molecule_count': 0},
    {'exact_molecule_count': 101}, {'exact_molecule_count': 1.0},
    {'expected_smiles': ['CCO', 'CCO']}, {'expected_smiles': ['C'] * 101},
    {'expected_smiles': ['CCO'], 'exact_molecule_count': 2},
    {'expected_smiles': ('CCO',)}, {'unknown': 'x'},
    {'target': ''}, {'target': 'x' * 129},
])
def test_analysis_bounds(tool, change):
    reject(payload(analysis_results=[{'tool_name': tool, **change}]))


def test_analysis_scope_and_tool_uniqueness():
    raw = payload(analysis_results=[{'tool_name': 'admet_predictor'},
        {'tool_name': 'activity_predictor', 'target': 'PDE'}])
    parsed = parse(raw)
    assert parsed.analysis_results[0].scope == 'available_methods'
    assert parsed.analysis_results[1].target == 'PDE'
    reject(payload(analysis_results=[{'tool_name': 'admet_predictor', 'target': 'PDE'}]))
    reject(payload(analysis_results=[{'tool_name': 'admet_predictor', 'scope': 'all_endpoints'}]))
    reject(payload(analysis_results=[{'tool_name': 'activity_predictor', 'scope': 'available_methods'}]))
    reject(payload(analysis_results=[{'tool_name': 'other'}]))
    reject(payload(analysis_results=[{'tool_name': 'admet_predictor'}] * 2))
    reject(payload(molecular_results=[{'tool_name': 'property_calculator'}] * 2))


@pytest.mark.parametrize('group,entry,tool', [
    ('molecular_results', [{'tool_name': 'property_calculator'}], 'property_calculator'),
    ('analysis_results', [{'tool_name': 'admet_predictor'}], 'admet_predictor'),
    ('reverse_result', {'expected_smiles': 'CCO'}, 'reverse_target_predictor'),
    ('retrieval_result', {'query': 'q'}, 'rag_search'),
    ('target_result', {'input': 'reverse'}, 'target_database_search'),
])
def test_required_groups_conflict_with_forbidden(group, entry, tool):
    reject(payload(**{group: entry}, forbidden_tools=[tool]))


def test_native_validation_precedes_serialization(monkeypatch):
    module = api()
    class Text(str, Enum):
        QUERY = 'query'
    class Dict(dict):
        pass
    cycle = payload()
    cycle['cycle'] = cycle
    deep = []
    for _ in range(40):
        deep = [deep]
    def forbidden(*args, **kwargs):
        pytest.fail('serialization reached for invalid/unbounded native input')
    monkeypatch.setattr(module.json, 'dumps', forbidden)
    for raw in (payload(retrieval_result={'query': Text.QUERY}), Dict(payload()),
                payload(forbidden_tools=('rag_search',)), cycle, payload(deep=deep),
                payload(retrieval_result={'query': 'x' * 32769}),
                payload(huge=10 ** 5000), payload(bad=float('nan'))):
        reject(raw)


def test_default_expansion_must_fit_same_bound():
    # Each field is individually bounded and raw <32KiB; defaults push it over.
    raw = payload(molecular_results=[{'tool_name': 'property_calculator',
        'expected_smiles': ['C' * 8100, 'N' * 8100]}],
        retrieval_result={'query': ''})
    raw['retrieval_result']['query'] = 'q' * (32760 - len(json.dumps(raw, ensure_ascii=False).encode()))
    assert len(raw['retrieval_result']['query']) <= 16384
    assert len(json.dumps(raw, ensure_ascii=False).encode()) < 32768
    reject(raw)


def test_constructed_models_revalidated_without_serializer_coercion():
    module = api()
    from src.agent.contracts.task_requirements import MolecularRequirement
    valid = parse(payload(molecular_results=[{'tool_name': 'property_calculator',
        'expected_smiles': ['CCO']}]))
    assert type(valid.molecular_results[0]) is MolecularRequirement
    for bad in (b'CCO', ('CCO',), 1):
        nested = MolecularRequirement.model_construct(tool_name='property_calculator', expected_smiles=(bad,))
        reject(module.BindingRequirements.model_construct(version='2', profile=B1, molecular_results=(nested,)))
    invalid = valid.model_copy(update={'forbidden_tools': (b'rag_search',)})
    reject(invalid)
    invalid = valid.model_copy(update={'surprise': True})
    reject(invalid)
    class Subclass(module.BindingRequirements):
        pass
    reject(Subclass.model_construct(version='2', profile=B1))
    # Raw dict callers do not gain the trusted-model tuple projection path.
    reject(payload(molecular_results=[valid.molecular_results[0]]))


@pytest.mark.parametrize('capabilities,required,allowed,kind', [
    ({'scientific_tools': False}, ('property_calculator',), TOOLS, 'scientific'),
    ({'rag': False}, ('rag_search',), TOOLS, 'scientific'),
    ({'scientific_tools': 1}, (), TOOLS, 'scientific'),
    ({'rag': 'true'}, (), TOOLS, 'scientific'),
    ([], (), TOOLS, 'scientific'),
    ({}, ('rag_search',), (), 'scientific'),
    ({}, ('rag_search',), TOOLS, 'chat'),
    ({}, ('candidate_ranker',), TOOLS | {'candidate_ranker'}, 'scientific'),
    ({}, (), TOOLS, 'unknown'),
])
def test_preparation_permissions(capabilities, required, allowed, kind):
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(payload(), capabilities=capabilities, required=required, allowed=allowed, kind=kind)


def test_required_union_independent_rag_and_chat():
    raw = payload(retrieval_result={'query': 'exact query'})
    result = prepare(raw, query='exact query', capabilities={'scientific_tools': False, 'rag': True})
    from src.agent.contracts.binding_requirements import required_binding_tools
    assert required_binding_tools(result, ('target_database_search',)) == frozenset({'rag_search', 'target_database_search'})
    assert prepare(payload(), kind='chat').version == '2'
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(raw, query='exact query', allowed=())
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(payload(forbidden_tools=['rag_search']), required=('rag_search',))


@pytest.mark.parametrize('smiles', ['CC(C)((', 'CCO name', 'CCO\nCCN', 'CCO;CCN', 'CCO任意', 'SMILES: CCO'])
def test_whole_subject_not_fragment(smiles):
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(payload(reverse_result={'expected_smiles': smiles}))


@pytest.mark.parametrize('subjects', [['CCO', 'OCC'], ['CCO', 'CC(C)((']])
@pytest.mark.parametrize('group,tool', [('molecular_results', 'property_calculator'), ('analysis_results', 'admet_predictor')])
def test_canonical_subjects(group, tool, subjects):
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(payload(**{group: [{'tool_name': tool, 'expected_smiles': subjects}]}))


def test_valid_whole_batch_and_missing_input_preserve_obligations():
    raw = payload(analysis_results=[{'tool_name': 'admet_predictor',
        'expected_smiles': ['OCC', 'CCN'], 'exact_molecule_count': 2}])
    assert prepare(raw, query='SMILES: CCO; SMILES: CCN').analysis_results[0].expected_smiles == ('OCC', 'CCN')
    assert prepare(raw, query='请计算分子性质').analysis_results[0].exact_molecule_count == 2
    for query in ('SMILES: CCN', 'SMILES: CCO; SMILES: OCC', 'SMILES: CCO broken'):
        with pytest.raises(ValueError, match='^' + ERROR + '$'):
            prepare(raw, query=query)
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(payload(molecular_results=[{'tool_name': 'property_calculator', 'exact_molecule_count': 2}],
            reverse_result={'expected_smiles': 'CCO'}), query='请计算分子性质')


def test_original_queries_not_model_arguments_or_rewrites():
    query = ' 查询 EGFR '
    assert prepare(payload(retrieval_result={'query': query}), query=query).retrieval_result.query == query
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(payload(retrieval_result={'query': query.strip()}), query=query)
    for target in (query,):
        assert prepare(payload(target_result={'input': 'user', 'query': target}), query=query).target_result.query == target
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(payload(target_result={'input': 'user', 'query': 'KRAS'}), query=query,
            metadata={'arguments': {'query': 'KRAS'}})
    assert prepare(payload(target_result={'input': 'reverse'})).target_result.query is None


def test_activity_target_only_from_original_user():
    raw = payload(analysis_results=[{'tool_name': 'activity_predictor', 'target': 'PDE', 'expected_smiles': ['CCO']}])
    assert prepare(raw, query='预测 PDE 活性；SMILES: CCO').analysis_results[0].target == 'PDE'
    for query in ('SMILES: CCO', '预测 BuChE 活性；SMILES: CCO'):
        with pytest.raises(ValueError, match='^' + ERROR + '$'):
            prepare(raw, query=query)
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(raw, query='预测 PDE 活性；SMILES: CCO', metadata={'target': 'BuChE'})


@pytest.mark.parametrize('target', [
    'BuChE KRAS', 'KRAS BuChE', 'BuChE,KRAS', 'BuChE/KRAS',
    'BuChE extra', 'BuChE BCHE', 'BuChE!', 'BuChE PDE',
])
def test_activity_obligation_rejects_nonwhole_target_identifier(target):
    raw = payload(analysis_results=[{'tool_name': 'activity_predictor',
        'target': target, 'expected_smiles': ['CCO']}])
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(raw, query='预测 BuChE 活性；SMILES: CCO')


@pytest.mark.parametrize('alias', ['BuChE', 'BCHE', 'buche-family', '丁酰胆碱酯酶', 'bChE'])
@pytest.mark.parametrize('alias_in_original', [False, True])
def test_activity_obligation_preserves_exact_buche_aliases(alias, alias_in_original):
    original, target = (alias, 'BuChE') if alias_in_original else ('BuChE', alias)
    raw = payload(analysis_results=[{'tool_name': 'activity_predictor',
        'target': target, 'expected_smiles': ['CCO']}])
    result = prepare(raw, query=f'预测 {original} 活性；SMILES: CCO')
    assert result.analysis_results[0].target == target


def test_activity_obligation_does_not_collapse_distinct_pde_subtypes():
    raw = payload(analysis_results=[{'tool_name': 'activity_predictor',
        'target': 'PDE4D', 'expected_smiles': ['CCO']}])
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare(raw, query='预测 PDE4B 活性；SMILES: CCO')


def proof(**changes):
    return dict(version='1', profile=B1, requirements_sha256='a' * 64,
        action_sha256='b' * 64, input_sha256='c' * 64, roles=[], own_source=None,
        selection_sha256=None, policy='b1-v1', **changes)


def parse_proof(value):
    from src.agent.contracts.decision_bindings import parse_binding_proof
    return parse_binding_proof(value)


def test_closed_proof_sources_roles_and_detachment():
    raw = proof()
    raw['own_source'] = {'kind': 'rag', 'generation_id': '1' * 32, 'epoch': 0,
        'configuration_sha256': '2' * 64, 'source_identity_sha256': '3' * 64}
    parsed = parse_proof(raw)
    assert parsed.model_dump(mode='json') == raw
    raw['own_source']['epoch'] = 8
    assert parsed.own_source.epoch == 0
    assert parse_proof(parsed) == parsed and parse_proof(parsed) is not parsed
    with pytest.raises(ValueError):
        parsed.own_source.epoch = 9
    raw = proof()
    raw.update(own_source={'kind': 'reverse', 'generation_id': '1' * 32,
        'source_sha256': '2' * 64, 'configuration_sha256': '3' * 64},
        roles=[{'role': 'reverse_record', 'evidence_id': 'evidence-1', 'output_sha256': '4' * 64}],
        selection_sha256='5' * 64)
    assert parse_proof(raw).model_dump(mode='json') == raw
    raw['roles'][0]['role'] = 'molecules'
    raw['selection_sha256'] = None
    assert parse_proof(raw).roles[0].role == 'molecules'


@pytest.mark.parametrize('change', [
    {'version': 1}, {'profile': 'ordinary-semantic-b2-v1'}, {'policy': 'b2-v1'},
    {'input_sha256': 'C' * 64}, {'action_sha256': 'b' * 64 + '\n'},
    {'extra': True}, {'selection_sha256': 'a' * 64}, {'roles': ()},
    {'own_source': {'kind': 'rag', 'generation_id': 'a' * 32, 'epoch': True,
        'configuration_sha256': 'a' * 64, 'source_identity_sha256': 'b' * 64}},
    {'own_source': {'kind': 'reverse', 'generation_id': 'a' * 32, 'epoch': 0,
        'configuration_sha256': 'a' * 64, 'source_sha256': 'b' * 64}},
    {'roles': [{'role': 'molecules', 'evidence_id': 'evidence-1\n', 'output_sha256': 'a' * 64}]},
    {'roles': [{'role': 'properties', 'evidence_id': 'evidence-1', 'output_sha256': 'a' * 64}]},
    {'roles': [{'role': 'reverse_record', 'evidence_id': 'evidence-1', 'output_sha256': 'a' * 64}]},
    {'roles': [{'role': 'molecules', 'evidence_id': 'evidence-1', 'output_sha256': 'a' * 64}] * 2},
    {'roles': [{'role': 'molecules', 'evidence_id': 'evidence-1', 'output_sha256': 'a' * 64}] * 6},
])
def test_invalid_proof(change):
    raw = proof()
    raw.update(change)
    with pytest.raises(ValueError, match='^invalid_binding_proof$'):
        parse_proof(raw)


def test_proof_bounds_before_copy_or_dump(monkeypatch):
    from src.agent.contracts import decision_bindings as module
    parser = module.parse_binding_proof
    def forbidden(*args, **kwargs):
        pytest.fail('unbounded proof serialized')
    monkeypatch.setattr(json, 'dumps', forbidden)
    raw = proof()
    raw['oversize'] = 'x' * 8193
    with pytest.raises(ValueError, match='^invalid_binding_proof$'):
        parser(raw)
    raw['oversize'] = raw
    with pytest.raises(ValueError, match='^invalid_binding_proof$'):
        parser(raw)


def test_constructed_proof_revalidated():
    good = parse_proof(proof())
    for changed in ({'roles': (b'bad',)}, {'input_sha256': b'a' * 64}, {'extra': True}):
        with pytest.raises(ValueError, match='^invalid_binding_proof$'):
            parse_proof(good.model_copy(update=changed))


def test_old_parsers_stay_closed():
    from src.agent.contracts.task_requirements import parse_task_requirements
    from src.agent.contracts.decision_bindings import parse_binding_arguments
    with pytest.raises(ValueError, match='^invalid_task_requirements$'):
        parse_task_requirements(payload())
    with pytest.raises(ValueError, match='^invalid_binding_arguments$'):
        parse_binding_arguments('rag_search', {'input_ref': 'user'})


def test_positive_limits_and_all_groups_roundtrip():
    from src.agent.contracts.binding_requirements import required_binding_tools
    raw = payload(forbidden_tools=['forbidden_' + str(i) for i in range(32)],
        molecular_results=[{'tool_name': 'property_calculator', 'exact_molecule_count': 100},
                           {'tool_name': 'drug_likeness_assessment', 'required_metrics': ['lipinski_compliant']}],
        analysis_results=[{'tool_name': 'admet_predictor', 'exact_molecule_count': 100},
                          {'tool_name': 'activity_predictor', 'target': 'PDE'}],
        retrieval_result={'query': '界' * 5461 + 'q', 'k': 3},
        target_result={'input': 'user', 'query': 'q' * 8192},
        reverse_result={'expected_smiles': 'CCO'})
    result = parse(raw)  # Schema does not perform preparation/scientific checks.
    assert required_binding_tools(result) == TOOLS
    assert parse(result) == result
    assert result.target_result.require_resolved is False
    assert result.retrieval_result.require_hits is False
    assert result.analysis_results[0].scope == 'available_methods'
    assert type(result.forbidden_tools) is type(result.analysis_results) is tuple
    for group in (*result.molecular_results, *result.analysis_results,
                  result.target_result, result.retrieval_result, result.reverse_result):
        assert type(group).model_config['frozen'] is True


def test_derived_target_requires_unambiguous_existing_user_parser():
    assert prepare(payload(target_result={'input': 'user', 'query': 'EGFR'}),
        query='查询 EGFR 靶点').target_result.query == 'EGFR'
    for query in ('查询 EGFR 和 KRAS 靶点', '不要针对 EGFR', '查询别的靶点'):
        with pytest.raises(ValueError, match='^' + ERROR + '$'):
            prepare(payload(target_result={'input': 'user', 'query': 'EGFR'}), query=query)


def test_preparation_permission_rejection_precedes_chemistry(monkeypatch):
    from src.agent.tools import molecular_input
    def forbidden(*args, **kwargs):
        pytest.fail('chemistry reached before permission rejection')
    monkeypatch.setattr(molecular_input, 'parse_molecular_smiles', forbidden)
    for raw in (payload(analysis_results=[{'tool_name': 'admet_predictor', 'expected_smiles': ['CCO']}]),
                payload(reverse_result={'expected_smiles': 'CCO'})):
        with pytest.raises(ValueError, match='^' + ERROR + '$'):
            prepare(raw, capabilities={'scientific_tools': False})


def test_constructed_nested_models_cycles_and_scalars():
    module = api()
    valid = parse(payload(retrieval_result={'query': 'q'}))
    for changed in ({'k': True}, {'k': 3.0}, {'query': b'q'}, {'require_hits': 1}):
        nested = valid.retrieval_result.model_copy(update=changed)
        reject(valid.model_copy(update={'retrieval_result': nested}))
    cycle = module.BindingRequirements.model_construct(version='2', profile=B1)
    object.__setattr__(cycle, 'molecular_results', (cycle,))
    reject(cycle)
    deep = {}
    for _ in range(40):
        deep = {'next': deep}
    reject(valid.model_copy(update={'retrieval_result': deep}))


def test_constructed_models_never_invoke_serializer_hooks(monkeypatch):
    module = api()
    from src.agent.contracts import decision_bindings as proof_module
    requirements = parse(payload(retrieval_result={'query': 'q'}))
    receipt = parse_proof(proof())
    def forbidden(*args, **kwargs):
        pytest.fail('unvalidated serializer hook invoked')
    monkeypatch.setattr(module.BindingRequirements, 'model_dump', forbidden)
    monkeypatch.setattr(proof_module.BindingProof, 'model_dump', forbidden)
    assert parse(requirements) == requirements
    assert parse_proof(receipt) == receipt


@pytest.mark.parametrize('source', [
    {'kind': 'rag', 'generation_id': 'A' * 32, 'epoch': 0,
        'configuration_sha256': 'a' * 64, 'source_identity_sha256': 'b' * 64},
    {'kind': 'rag', 'generation_id': 'a' * 32, 'epoch': -1,
        'configuration_sha256': 'a' * 64, 'source_identity_sha256': 'b' * 64},
    {'kind': 'rag', 'generation_id': 'a' * 32, 'epoch': 1.0,
        'configuration_sha256': 'a' * 64, 'source_identity_sha256': 'b' * 64},
    {'kind': 'rag', 'generation_id': 'a' * 32, 'epoch': 0,
        'configuration_sha256': 'a' * 64, 'source_sha256': 'b' * 64},
    {'kind': 'reverse', 'generation_id': 'a' * 32,
        'source_sha256': b'b' * 64, 'configuration_sha256': 'a' * 64},
])
def test_source_exact_native_fields(source):
    raw = proof()
    raw['own_source'] = source
    with pytest.raises(ValueError, match='^invalid_binding_proof$'):
        parse_proof(raw)


def test_proof_requires_all_fields_and_non_duplicate_upstream_roles():
    for key in proof():
        raw = proof()
        del raw[key]
        with pytest.raises(ValueError, match='^invalid_binding_proof$'):
            parse_proof(raw)
    raw = proof()
    raw['roles'] = [
        {'role': 'molecules', 'evidence_id': 'evidence-1', 'output_sha256': 'a' * 64},
        {'role': 'reverse_record', 'evidence_id': 'evidence-2', 'output_sha256': 'b' * 64},
    ]
    raw['selection_sha256'] = 'c' * 64
    with pytest.raises(ValueError, match='^invalid_binding_proof$'):
        parse_proof(raw)


def test_observation_ceiling_is_not_enlarged_by_proof_contract():
    from src.agent.contracts import ToolResult
    from src.agent.harness.decision_bounds import observation_value
    result = ToolResult.success_result('property_calculator', 'x' * 65537)
    with pytest.raises(ValueError, match='^observation_too_large$'):
        observation_value(result)


@pytest.mark.parametrize('query', ['q' * 16384, '界' * 5461 + 'q', '\n' * 8191 + 'q'],
                         ids=['ascii-limit', 'utf8-limit', 'json-escapes'])
def test_preparation_rag_query_uses_utf8_content_not_json_escape_budget(query):
    # Requirements fit 32KiB. The separately bounded observation/context codec
    # must not silently redefine the explicit 16KiB retrieval content contract.
    result = prepare(payload(retrieval_result={'query': query}), query=query,
                     capabilities={'scientific_tools': False})
    assert result.retrieval_result.query == query


def test_preparation_uses_whole_selected_structure_without_source_io():
    from src.agent.contracts import AgentContext
    from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
    from src.agent.harness.decision_bindings import prepare_binding_requirements
    selected = ResolvedScientificMolecule('source', 'presentation', 'a' * 64,
        'observation', 'candidate', 'CCO')
    context = AgentContext(query='请计算这个分子的性质', trace_id='current', resolved_molecule=selected)
    raw = payload(molecular_results=[{'tool_name': 'property_calculator', 'expected_smiles': ['OCC']}])
    assert prepare_binding_requirements(raw, context=context, request_kind='scientific',
        allowed_tools=TOOLS, required_tools=()).molecular_results[0].expected_smiles == ('OCC',)
    # An explicit new structure is authoritative, never silently replaced by selection.
    context.query = 'SMILES: CCN'
    with pytest.raises(ValueError, match='^' + ERROR + '$'):
        prepare_binding_requirements(raw, context=context, request_kind='scientific',
            allowed_tools=TOOLS, required_tools=())
    assert context.resolved_molecule is selected
