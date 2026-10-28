"""Rank reuse over real synthetic receipts and SQLite, not scientific predictions."""
from copy import deepcopy
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest

from src.agent.contracts import ToolResult
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.tools.reverse_target_tool import ReverseTargetTool
from src.agent.validators.result_validator import AgentResultValidator
from tests.test_reverse_target_invocation_receipts import make_writer_database, close_fixture_mmaps


def api():
    name = 'src.web.reverse_target_followup'
    assert importlib.util.find_spec(name) is not None, 'current ranked evidence boundary is missing'
    return importlib.import_module(name)


@pytest.fixture
def ranked(tmp_path, request):
    options = getattr(request, 'param', {})
    count = options.get('count', 10)
    producer = make_writer_database(tmp_path / 'source', smiles=('CCO',) * count)
    if options.get('organism') or options.get('identifier') or options.get('name'):
        path = producer.training_data_path
        text = path.read_text(encoding='utf-8')
        if options.get('organism'):
            text = text.replace('\tHuman', '\t' + options['organism'])
        if options.get('name'):
            text = text.replace('Synthetic A', options['name'])
        if options.get('identifier'):
            lines = text.splitlines()
            text = '\n'.join([lines[0] + '\t' + options.get('identifier_field', 'target_uniprot_id')] +
                [line + '\t' + options['identifier'] for line in lines[1:]]) + '\n'
        path.write_text(text, encoding='utf-8')
    producer.initialize_strict()
    tool = ReverseTargetTool(producer)
    raw = tool.execute('CCO')
    assert raw['success'] and len(raw['data']) == count
    result = AgentResultValidator().validate_tool_result(ToolResult.success_result(
        tool.name, data=raw['data'], evidence=raw['evidence'], quality=raw['quality']))
    assert result.success
    result.quality['step_id'] = 'reverse'
    ledger = EvidenceLedger('rank-trace')
    ledger.prepare_provenance('a' * 64, result)
    result.quality['evidence_id'] = ledger.register_tool_result('reverse', 'a' * 64, result)
    payload = result.to_legacy_dict()
    store = SQLiteAgentStateStore(tmp_path / 'agent.sqlite')
    store.start_run(dict(trace_id='rank-trace', session_id='owner-session', user_id='owner',
        status='completed', workflow_version='1', query='CCO', metadata={}))
    checkpoint = dict(id='rank-observation', trace_id='rank-trace', step_id='reverse',
        status='succeeded', input_hash='a' * 64, workflow_version='1', tool_name=tool.name,
        tool_version='1', output=payload)
    store.save_checkpoint(checkpoint)
    agent = dict(trace_id='rank-trace', status='completed', success=True,
        tool_result_sequence=[dict(step_id='reverse', tool_name=tool.name, **payload)])
    try:
        yield SimpleNamespace(producer=producer, tool=tool, store=store, agent=agent,
                              checkpoint=checkpoint, data=raw['data'])
    finally:
        producer.close_strict()
        close_fixture_mmaps(producer)


def service(case):
    return api().RankedReverseEvidence(case.store, case.tool)


def test_capture_and_resolve_preserve_full_rank_and_receipt(ranked, monkeypatch):
    boundary = service(ranked)
    before = deepcopy(ranked.agent)
    context = boundary.capture(ranked.agent, session_id='owner-session')
    assert context and context['trace_id'] == 'rank-trace'
    assert context['observation_id'] == 'rank-observation'
    assert context['source_version'] and context['result_digest']
    assert context['current_source']['kind'] == 'reverse'
    for method in ('predict_with_receipt', 'initialize_strict', 'load', 'compute_query_fingerprints'):
        monkeypatch.setattr(ranked.producer, method, lambda *a, **kw: pytest.fail('rank reuse reran science'))
    selected = boundary.resolve(context, session_id='owner-session', rank=1)
    assert selected['record'] == ranked.data[0]
    assert selected['rank'] == 1 and selected['record_count'] == 10
    assert selected['record']['target_identifier'].startswith('name-sha256:')
    assert selected['source_row_index'] == 0
    assert ranked.agent == before
    selected['record']['target_name'] = 'changed copy'
    assert boundary.resolve(context, session_id='owner-session', rank=1)['record'] == ranked.data[0]


@pytest.mark.parametrize('session', ['foreign', '', None, True])
def test_foreign_capture_cannot_read_rank(ranked, session):
    assert service(ranked).capture(ranked.agent, session_id=session) is None


def test_source_generation_retirement_revokes_rank(ranked):
    boundary = service(ranked)
    context = boundary.capture(ranked.agent, session_id='owner-session')
    assert context
    ranked.producer.close_strict()
    assert boundary.resolve(context, session_id='owner-session', rank=1) is None


@pytest.mark.parametrize('mutation', ['score', 'raw', 'evidence', 'success', 'status', 'step', 'trace', 'duplicate'])
def test_unaccepted_live_projection_never_captures_rank(ranked, mutation):
    changed = deepcopy(ranked.agent)
    item = changed['tool_result_sequence'][0]
    if mutation == 'score': item['data'][0]['final_similarity'] = 0.123
    elif mutation == 'raw': item['evidence'][0]['records'][0]['target_name'] = 'invented'
    elif mutation == 'evidence': item['quality']['evidence_id'] = 'evidence-invented'
    elif mutation == 'success': item['success'] = False
    elif mutation == 'status': changed['status'] = 'failed'
    elif mutation == 'step': item['step_id'] = 'another-step'
    elif mutation == 'trace': changed['trace_id'] = 'another-trace'
    else: changed['tool_result_sequence'].append(deepcopy(item))
    assert service(ranked).capture(changed, session_id='owner-session') is None


@pytest.mark.parametrize('rank', [0, -1, 11, True, 1.0, '1', None])
def test_missing_or_non_native_rank_rejects(ranked, rank):
    boundary = service(ranked)
    context = boundary.capture(ranked.agent, session_id='owner-session')
    assert boundary.resolve(context, session_id='owner-session', rank=rank) is None


def test_five_row_display_does_not_crop_or_renumber_proof(ranked):
    boundary = service(ranked)
    context = boundary.capture(ranked.agent, session_id='owner-session')
    display = boundary.render_prefix(context, session_id='owner-session', count=5)
    assert '前 5' in display and '10' in display
    assert 'Synthetic E' in display and 'Synthetic F' not in display
    assert str(ranked.data[0]['final_similarity']) in display
    assert '非实验' in display and 'ChEMBL' in display
    assert boundary.resolve(context, session_id='owner-session', rank=10)['record'] == ranked.data[9]


@pytest.mark.parametrize('ranked', [{'count': 0}], indirect=True)
def test_empty_reverse_result_is_valid_and_renders_no_match(ranked):
    boundary = service(ranked)
    context = boundary.capture(ranked.agent, session_id='owner-session')

    assert context is not None
    display = boundary.render_prefix(context, session_id='owner-session', count=5)
    assert display is not None
    assert '未找到满足阈值的靶点匹配' in display
    assert boundary.resolve(context, session_id='owner-session', rank=1) is None


@pytest.mark.parametrize('mutation', ['owner', 'run_status', 'checkpoint_status', 'workflow',
    'input_hash', 'tool_version', 'error', 'score', 'evidence_id', 'validated', 'provenance_demo',
    'provenance_tool', 'provenance_input', 'provenance_output', 'raw_count', 'artifact'])
def test_persisted_change_revokes_existing_rank_and_rejects_capture(ranked, mutation):
    boundary = service(ranked)
    context = boundary.capture(ranked.agent, session_id='owner-session')
    assert context
    with ranked.store._connect() as connection:
        if mutation == 'owner':
            connection.execute("UPDATE agent_runs SET session_id='foreign'")
        elif mutation == 'run_status':
            connection.execute("UPDATE agent_runs SET status='failed'")
        else:
            columns = {'checkpoint_status': ('status', 'failed'), 'workflow': ('workflow_version', '2'),
                'input_hash': ('input_hash', 'b' * 64), 'tool_version': ('tool_version', '2'),
                'error': ('error_json', '{}')}
            if mutation in columns:
                column, value = columns[mutation]
                connection.execute('UPDATE agent_checkpoints SET ' + column + '=?', (value,))
            else:
                value = deepcopy(ranked.checkpoint['output'])
                if mutation == 'score': value['data'][0]['final_similarity'] = 0.123
                elif mutation == 'evidence_id': value['quality']['evidence_id'] = 'evidence-forged'
                elif mutation == 'validated': value['quality']['validated'] = False
                elif mutation == 'provenance_demo': value['provenance']['demo_mode'] = True
                elif mutation == 'provenance_tool': value['provenance']['tool_name'] = 'other'
                elif mutation == 'provenance_input': value['provenance']['input_digest'] = 'b' * 64
                elif mutation == 'provenance_output': value['provenance']['output_digest'] = 'b' * 64
                elif mutation == 'raw_count': value['evidence'][0]['record_count'] = 1
                elif mutation == 'artifact': value['artifacts'] = [{'path': 'untrusted'}]
                connection.execute('UPDATE agent_checkpoints SET output_json=?', (json.dumps(value),))
    assert boundary.resolve(context, session_id='owner-session', rank=1) is None
    assert boundary.capture(ranked.agent, session_id='owner-session') is None


@pytest.mark.parametrize('field', ['schema_version', 'session_id', 'trace_id', 'observation_id',
    'step_id', 'source_version', 'evidence_id', 'result_digest', 'current_source', 'extra'])
def test_changed_pointer_never_restores_rank(ranked, field):
    boundary = service(ranked)
    context = boundary.capture(ranked.agent, session_id='owner-session')
    assert context
    context[field] = True if field == 'schema_version' else 'forged'
    assert boundary.resolve(context, session_id='owner-session', rank=1) is None


@pytest.mark.parametrize('invalid', [None, [], True, {'cycle': []}, 'x' * (129 * 1024)],
                         ids=['null', 'list', 'bool', 'cycle', 'oversize'])
def test_bad_context_fails_without_store_read(ranked, monkeypatch, invalid):
    boundary = service(ranked)
    if type(invalid) is dict:
        invalid['cycle'].append(invalid)
    reads = []
    monkeypatch.setattr(ranked.store, '_connect', lambda: reads.append(True))
    assert boundary.resolve(invalid, session_id='owner-session') is None
    assert reads == []


def test_a_different_rank_checkpoint_is_ambiguous_not_latest_wins(ranked):
    extra = deepcopy(ranked.checkpoint)
    extra.update(id='other-observation', step_id='other-reverse')
    ranked.store.save_checkpoint(extra)
    assert service(ranked).capture(ranked.agent, session_id='owner-session') is None


def test_context_is_detached_and_metadata_changes_do_not_renumber(ranked):
    boundary = service(ranked)
    first = boundary.capture(ranked.agent, session_id='owner-session')
    second = boundary.capture(ranked.agent, session_id='owner-session')
    ranked.store.update_run_metadata('rank-trace', {'unrelated': 'annotation'})
    assert boundary.resolve(second, session_id='owner-session')['record'] == ranked.data[0]
    first['current_source']['generation_id'] = '0' * 32
    assert boundary.resolve(first, session_id='owner-session') is None
    assert boundary.resolve(second, session_id='owner-session')


def test_source_change_during_capture_is_not_published(ranked, monkeypatch):
    boundary = service(ranked)
    validate = ranked.tool.validate_current_observation
    calls = []
    def retire(*args, **kwargs):
        result = validate(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            ranked.producer.close_strict()
        return result
    monkeypatch.setattr(ranked.tool, 'validate_current_observation', retire)
    assert boundary.capture(ranked.agent, session_id='owner-session') is None


def test_false_assistant_prose_does_not_override_rank(ranked):
    ranked.agent['final_answer'] = '排名第一是一个虚构靶点 UniProt P06276'
    boundary = service(ranked)
    context = boundary.capture(ranked.agent, session_id='owner-session')
    assert boundary.resolve(context, session_id='owner-session')['record'] == ranked.data[0]


@pytest.mark.parametrize('operation', ['resolve', 'render'])
def test_retirement_during_rank_read_cannot_publish_late_result(ranked, monkeypatch, operation):
    boundary = service(ranked)
    context = boundary.capture(ranked.agent, session_id='owner-session')
    assert context
    validate = ranked.tool.validate_current_observation
    def retire(*args, **kwargs):
        result = validate(*args, **kwargs)
        ranked.producer.close_strict()
        return result
    monkeypatch.setattr(ranked.tool, 'validate_current_observation', retire)
    result = (boundary.resolve(context, session_id='owner-session') if operation == 'resolve'
              else boundary.render_prefix(context, session_id='owner-session', count=5))
    assert result is None
