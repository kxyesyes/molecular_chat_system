"""A-contract only: approved F75 schema and pure transitions, not runtime authority.

No backend, generator, transport, journal owner, asset or configuration is loaded.
All grants/IDs/usage are synthetic. Missing APIs fail in the test CALL phase;
these tests do not implement normalization, persistence or a production control.
Execution requires the separately reviewed isolated runner and sole-slot transfer.
"""
import hashlib
import importlib
import inspect
import json
from collections import UserDict
from copy import deepcopy
from enum import Enum
from itertools import product
from types import MappingProxyType

import pytest


GRANT_FIELDS = (
    'version', 'generator_generation', 'backend_revision', 'model_artifact_digest',
    'tokenizer_digest', 'template_options_digest', 'bound_policy_revision',
    'total_reserved_tokens', 'prompt_token_ceiling', 'context_token_limit',
    'completion_token_limit',
)
RECEIPT_FIELDS = (
    'version', 'root_id', 'logical_slot_id', 'reservation_id', 'round_index',
    'input_sha256', 'proof_sha256', 'generator_generation', 'grant_sha256',
    'reserved_prompt_tokens', 'reserved_completion_tokens', 'phase',
    'dispatch_marker_id', 'client_dispatch_id', 'backend_request_id',
    'never_dispatched_evidence_id', 'drained', 'outcome', 'stop_code',
    'usage_status', 'usage', 'usage_issue',
)
BUDGET_FIELDS = (
    'version', 'policy', 'root_id', 'input_sha256', 'proof_sha256',
    'logical_slot_id', 'slot_state', 'grant', 'grant_sha256', 'main_limit',
    'root_limit', 'intent_requests', 'decision_requests', 'main_requests',
    'generator_request_debits', 'root_request_debits', 'reserved_tokens',
    'generator_requests',
)
RECEIPT_NULLABLE = frozenset((
    'dispatch_marker_id', 'client_dispatch_id', 'backend_request_id',
    'never_dispatched_evidence_id', 'stop_code', 'usage', 'usage_issue',
))
PHASES = ('reserved', 'dispatched', 'settled', 'uncertain', 'not_dispatched')
SLOTS = ('unused', 'claimed', 'running', 'complete', 'partial', 'failed',
         'cancelled', 'uncertain')
STOP_CODES = (
    'generation_control_invalid', 'generator_token_bound_unavailable',
    'generation_cancelled', 'generation_deadline_exceeded',
    'generation_request_budget_exhausted', 'generation_token_budget_exhausted',
    'generation_context_exceeded', 'generation_journal_unavailable',
    'generation_dispatch_uncertain', 'generation_usage_invalid',
    'generation_token_bound_violated', 'generation_cleanup_unsettled',
    'generation_slot_consumed',
)
SCHEMAS = (
    ('grant', 'GeneratorTokenGrant', 'parse_generator_token_grant',
     'invalid_generator_token_grant', GRANT_FIELDS),
    ('receipt', 'GeneratorRequestReceipt', 'parse_generator_request_receipt',
     'invalid_generator_request_receipt', RECEIPT_FIELDS),
    ('budget', 'BRootBudget', 'parse_b_root_budget',
     'invalid_b_root_budget', BUDGET_FIELDS),
)
RECEIPT_ERROR = 'invalid_generator_request_transition'
BUDGET_ERROR = 'invalid_b_root_budget_transition'
MARKER = 'synthetic-private-invalid-value'


def api():
    """Call from test bodies, never collection/fixtures; absence is API RED."""
    module = importlib.import_module('src.agent.contracts.decision_bindings')
    names = [row[1] for row in SCHEMAS] + [row[2] for row in SCHEMAS] + [
        'GeneratorUsage', 'transition_generator_request', 'transition_b_root_budget',
    ]
    missing = [name for name in names if not hasattr(module, name)]
    assert not missing, 'A-contract API RED: missing ' + ', '.join(missing)
    return module


def digest(value):
    # Fixture construction only; the expected grant digest below is independently pinned.
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


def wire_size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'))


def grant(**changes):
    value = dict(
        version='1', generator_generation='a' * 32,
        backend_revision='offline-contract-v1', model_artifact_digest='b' * 64,
        tokenizer_digest='c' * 64, template_options_digest='d' * 64,
        bound_policy_revision='offline-table-v1', total_reserved_tokens=6000,
        prompt_token_ceiling=200, context_token_limit=2048,
        completion_token_limit=1000,
    )
    value.update(changes)
    return value


def receipt(phase='settled', index=1, **changes):
    value = dict(
        version='1', root_id='root.offline.1', logical_slot_id='e' * 32,
        reservation_id=f'{index:032x}', round_index=index,
        input_sha256='2' * 64, proof_sha256='3' * 64,
        generator_generation='a' * 32, grant_sha256=digest(grant()),
        reserved_prompt_tokens=200, reserved_completion_tokens=1000,
        phase=phase, dispatch_marker_id=None, client_dispatch_id=None,
        backend_request_id=None, never_dispatched_evidence_id=None,
        drained=False, outcome='pending', stop_code=None,
        usage_status='not_observed', usage=None, usage_issue=None,
    )
    if phase != 'reserved':
        value['dispatch_marker_id'] = f'{100 + index:032x}'
    if phase == 'settled':
        value.update(client_dispatch_id=f'{200 + index:032x}', drained=True,
                     outcome='success', usage_status='valid',
                     usage=dict(prompt_tokens=11, completion_tokens=7))
    elif phase == 'uncertain':
        value.update(outcome='uncertain', stop_code='generation_dispatch_uncertain')
    elif phase == 'not_dispatched':
        value.update(drained=True, outcome='stopped',
                     stop_code='generation_cancelled',
                     never_dispatched_evidence_id='owner.no-entry.1')
    value.update(changes)
    return value


def budget(rows=(), *, state='running', main=2, token_grant=None, **changes):
    g = deepcopy(token_grant if token_grant is not None else grant())
    rows = deepcopy(list(rows))
    debited = [r for r in rows if r['phase'] != 'not_dispatched']
    value = dict(
        version='1', policy='b-root-inclusive-16-v1', root_id='root.offline.1',
        input_sha256='2' * 64, proof_sha256='3' * 64,
        logical_slot_id=None if state == 'unused' else 'e' * 32,
        slot_state=state, grant=g, grant_sha256=digest(g), main_limit=16,
        root_limit=16, intent_requests=min(main, 1), decision_requests=max(main - 1, 0),
        main_requests=main, generator_request_debits=len(debited),
        root_request_debits=main + len(debited),
        reserved_tokens=sum(r['reserved_prompt_tokens'] + r['reserved_completion_tokens']
                            for r in debited), generator_requests=rows,
    )
    value.update(changes)
    return value


def payload(kind):
    return {'grant': grant, 'receipt': receipt,
            'budget': lambda: budget([receipt()])}[kind]()


def rejected(call, *args, error):
    with pytest.raises(ValueError) as caught:
        call(*args)
    assert type(caught.value) is ValueError
    assert caught.value.args == (error,)
    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


def native_equal(result, expected):
    assert result.model_dump(mode='json') == expected


@pytest.mark.parametrize('kind,model_name,parser_name,error,fields', SCHEMAS)
def test_exact_required_models_parsers_and_detached_roundtrip(
        kind, model_name, parser_name, error, fields):
    m = api()
    parser, cls = getattr(m, parser_name), getattr(m, model_name)
    signature = inspect.signature(parser)
    assert tuple(signature.parameters) == ('value',)
    assert signature.parameters['value'].default is inspect.Parameter.empty
    original = payload(kind)
    result = parser(original)
    assert type(result) is cls
    assert set(cls.model_fields) == set(fields)
    assert all(field.is_required() for field in cls.model_fields.values())
    assert cls.model_config['strict'] is True
    assert cls.model_config['frozen'] is True
    assert cls.model_config['extra'] == 'forbid'
    assert cls.model_config['revalidate_instances'] == 'always'
    native_equal(result, original)
    again = parser(result)
    assert again is not result
    native_equal(again, original)
    native_equal(parser(cls.model_construct(**deepcopy(original))), original)
    native_equal(parser(result.model_copy()), original)
    if kind == 'budget':
        assert type(result.grant) is m.GeneratorTokenGrant
        assert type(result.generator_requests) is tuple
        assert type(result.generator_requests[0]) is m.GeneratorRequestReceipt
        assert type(result.generator_requests[0].usage) is m.GeneratorUsage
        assert again.grant is not result.grant
        assert again.generator_requests[0] is not result.generator_requests[0]
    if kind == 'receipt':
        assert type(result.usage) is m.GeneratorUsage
        assert set(type(result.usage).model_fields) == {'prompt_tokens', 'completion_tokens'}
        assert all(f.is_required() for f in type(result.usage).model_fields.values())


def test_pure_transition_signatures_and_model_idempotence():
    m = api()
    for transition, parser, raw in (
        (m.transition_generator_request, m.parse_generator_request_receipt, receipt()),
        (m.transition_b_root_budget, m.parse_b_root_budget, budget([receipt()])),
    ):
        parameters = inspect.signature(transition).parameters
        assert tuple(parameters) == ('old', 'new')
        assert all(p.default is inspect.Parameter.empty for p in parameters.values())
        old = parser(raw)
        new = transition(old, old)
        assert new is not old
        native_equal(new, raw)


@pytest.mark.parametrize('kind,model_name,parser_name,error,fields', SCHEMAS)
def test_every_field_required_and_unknown_fields_rejected(
        kind, model_name, parser_name, error, fields):
    parser = getattr(api(), parser_name)
    for field in fields:
        missing = payload(kind)
        del missing[field]
        rejected(parser, missing, error=error)
    for field in ('verified', 'authorized', 'prompt', 'response', 'deadline', MARKER):
        extra = payload(kind)
        extra[field] = MARKER
        rejected(parser, extra, error=error)
    nullable = RECEIPT_NULLABLE if kind == 'receipt' else (
        frozenset(('logical_slot_id',)) if kind == 'budget' else frozenset())
    for field in set(fields) - nullable:
        invalid = payload(kind)
        invalid[field] = None
        rejected(parser, invalid, error=error)


@pytest.mark.parametrize('field', ('prompt_tokens', 'completion_tokens'))
def test_usage_required_fields_and_no_extra_fields(field):
    m = api()
    invalid = receipt()
    del invalid['usage'][field]
    rejected(m.parse_generator_request_receipt, invalid,
             error='invalid_generator_request_receipt')
    invalid = receipt()
    invalid['usage']['total_tokens'] = 18
    rejected(m.parse_generator_request_receipt, invalid,
             error='invalid_generator_request_receipt')


@pytest.mark.parametrize('kind,model_name,parser_name,error,fields', SCHEMAS)
@pytest.mark.parametrize('form', ('text', 'bytes', 'mapping', 'proxy', 'dict-subclass',
                                  'model-subclass', 'list', 'none'))
def test_only_exact_dict_or_exact_result_model_at_ingress(
        kind, model_name, parser_name, error, fields, form):
    m = api()
    value = payload(kind)
    cls, parser = getattr(m, model_name), getattr(m, parser_name)
    if form == 'text':
        value = json.dumps(value)
    elif form == 'bytes':
        value = json.dumps(value).encode()
    elif form == 'mapping':
        value = UserDict(value)
    elif form == 'proxy':
        value = MappingProxyType(value)
    elif form == 'dict-subclass':
        class DictChild(dict):
            pass
        value = DictChild(value)
    elif form == 'model-subclass':
        class ModelChild(cls):
            pass
        value = ModelChild.model_construct(**value)
    elif form == 'list':
        value = [value]
    elif form == 'none':
        value = None
    rejected(parser, value, error=error)


@pytest.mark.parametrize('kind,model_name,parser_name,error,fields', SCHEMAS)
@pytest.mark.parametrize('corruption', ('missing', 'extra', 'pydantic-extra',
                                       'invalid-copy', 'serializer-hook'))
def test_constructed_corruption_is_revalidated_without_serialization_hooks(
        kind, model_name, parser_name, error, fields, corruption):
    m = api()
    parser, cls = getattr(m, parser_name), getattr(m, model_name)
    good = parser(payload(kind))
    broken = cls.model_construct(**dict(vars(good)))
    called = []
    if corruption == 'missing':
        del vars(broken)['version']
    elif corruption == 'extra':
        vars(broken)['unexpected'] = MARKER
    elif corruption == 'pydantic-extra':
        object.__setattr__(broken, '__pydantic_extra__', {'unexpected': MARKER})
    elif corruption == 'invalid-copy':
        broken = good.model_copy(update={'version': 1})
    else:
        def forbidden(*args, **kwargs):
            called.append(True)
            raise AssertionError('untrusted model serializer executed')
        object.__setattr__(broken, 'model_dump', forbidden)
    rejected(parser, broken, error=error)
    assert called == []


def test_nested_constructed_corruption_and_raw_tuple_are_rejected():
    m = api()
    good = m.parse_b_root_budget(budget([receipt()]))
    bad_usage = good.generator_requests[0].usage.model_copy(update={'prompt_tokens': True})
    bad_receipt = good.generator_requests[0].model_copy(update={'usage': bad_usage})
    bad_grant = good.grant.model_copy(update={'completion_token_limit': 999})
    for broken in (
        good.model_copy(update={'generator_requests': (bad_receipt,)}),
        good.model_copy(update={'grant': bad_grant}),
    ):
        rejected(m.parse_b_root_budget, broken, error='invalid_b_root_budget')
    raw = budget([receipt()])
    raw['generator_requests'] = tuple(raw['generator_requests'])
    rejected(m.parse_b_root_budget, raw, error='invalid_b_root_budget')


def test_constructed_nested_cycle_rejected_before_serialization(monkeypatch):
    m = api()
    usage = m.GeneratorUsage.model_construct(prompt_tokens=None, completion_tokens=7)
    object.__setattr__(usage, 'prompt_tokens', usage)
    broken = m.GeneratorRequestReceipt.model_construct(**{**receipt(), 'usage': usage})
    def no_dump(*args, **kwargs):
        raise AssertionError('constructed model cycle reached JSON serialization')
    with monkeypatch.context() as patch:
        patch.setattr(json, 'dumps', no_dump)
        rejected(m.parse_generator_request_receipt, broken,
                 error='invalid_generator_request_receipt')


def test_deep_immutability_and_input_dump_detachment():
    m = api()
    raw = budget([receipt()])
    expected = deepcopy(raw)
    result = m.parse_b_root_budget(raw)
    raw['grant']['backend_revision'] = 'changed'
    raw['generator_requests'][0]['usage']['prompt_tokens'] = 0
    raw['generator_requests'].clear()
    dumped = result.model_dump(mode='json')
    dumped['generator_requests'][0]['usage']['completion_tokens'] = 0
    dumped['grant']['total_reserved_tokens'] = 1
    native_equal(result, expected)
    for obj, field, changed in (
        (result, 'root_id', 'changed'), (result.grant, 'total_reserved_tokens', 1),
        (result.generator_requests[0], 'round_index', 2),
        (result.generator_requests[0].usage, 'prompt_tokens', 0),
    ):
        with pytest.raises((ValueError, TypeError, AttributeError)):
            setattr(obj, field, changed)
    assert type(result.generator_requests) is tuple
    native_equal(result, expected)


@pytest.mark.parametrize('shape', ('cycle', 'depth', 'nodes', 'nonfinite', 'enum'))
def test_bounded_projection_rejects_before_json_dump(shape, monkeypatch, caplog):
    m = api()
    value = grant()
    if shape == 'cycle':
        value['backend_revision'] = value
    elif shape == 'depth':
        nested = 0
        for _ in range(34):
            nested = [nested]
        value['backend_revision'] = nested
    elif shape == 'nodes':
        value['backend_revision'] = [0] * 16385
    elif shape == 'nonfinite':
        value['total_reserved_tokens'] = float('nan')
    else:
        class TextEnum(str, Enum):
            REVISION = 'offline-contract-v1'
        value['backend_revision'] = TextEnum.REVISION
    def no_dump(*args, **kwargs):
        raise AssertionError('invalid bounded input reached JSON serialization')
    with monkeypatch.context() as patch:
        patch.setattr(json, 'dumps', no_dump)
        rejected(m.parse_generator_token_grant, value, error='invalid_generator_token_grant')
    assert MARKER not in caplog.text


@pytest.mark.parametrize('field', ('generator_generation', 'model_artifact_digest',
                                  'tokenizer_digest', 'template_options_digest'))
@pytest.mark.parametrize('change', ('upper', 'short', 'long', 'newline', 'nonhex'))
def test_grant_hex_id_grammars_are_full_string(field, change):
    m = api()
    raw = grant()
    text = raw[field]
    raw[field] = {'upper': text.upper(), 'short': text[:-1], 'long': text + 'a',
                  'newline': text + '\n', 'nonhex': 'z' + text[1:]}[change]
    rejected(m.parse_generator_token_grant, raw, error='invalid_generator_token_grant')


@pytest.mark.parametrize('field', ('backend_revision', 'bound_policy_revision'))
def test_grant_revision_evidence_id_grammar(field):
    m = api()
    for good in ('A', 'a' * 128, 'A_0.b:c-d', 'CCO'):
        native_equal(m.parse_generator_token_grant(grant(**{field: good})), grant(**{field: good}))
    for bad in ('', 'a' * 129, '_bad', 'a b', 'a/b', 'a\n', '非ASCII', MARKER + '\n'):
        rejected(m.parse_generator_token_grant, grant(**{field: bad}),
                 error='invalid_generator_token_grant')


@pytest.mark.parametrize('field', ('total_reserved_tokens', 'prompt_token_ceiling',
                                  'context_token_limit', 'completion_token_limit'))
@pytest.mark.parametrize('bad', (True, False, 0, -1, '1000', 1000.0, float('inf'), None))
def test_grant_token_fields_require_positive_native_ints(field, bad):
    m = api()
    rejected(m.parse_generator_token_grant, grant(**{field: bad}),
             error='invalid_generator_token_grant')


def test_grant_cross_fields_digest_and_small_allowance_are_not_authority():
    m = api()
    assert digest(grant()) == '3217d3e88fd406c48510be4d73586df8e5be567095a9d2b97e532e65d93d74c4'
    for bad in (grant(version=1), grant(version='2'), grant(completion_token_limit=999),
                grant(completion_token_limit=1001), grant(context_token_limit=1199)):
        rejected(m.parse_generator_token_grant, bad, error='invalid_generator_token_grant')
    native_equal(m.parse_generator_token_grant(grant(context_token_limit=1200)),
                 grant(context_token_limit=1200))
    small = grant(total_reserved_tokens=1)
    native_equal(m.parse_generator_token_grant(small), small)
    native_equal(m.parse_b_root_budget(budget(token_grant=small)), budget(token_grant=small))
    assert 'verified' not in m.GeneratorTokenGrant.model_fields
    for native_max in (1 << 4095, (1 << 4096) - 1):
        large = grant(total_reserved_tokens=native_max)
        assert wire_size(large) < 4096
        native_equal(m.parse_generator_token_grant(large), large)


@pytest.mark.parametrize('field', ('root_id', 'logical_slot_id', 'reservation_id',
                                  'input_sha256', 'proof_sha256', 'generator_generation',
                                  'grant_sha256', 'dispatch_marker_id', 'client_dispatch_id',
                                  'backend_request_id', 'never_dispatched_evidence_id'))
def test_receipt_id_syntax_and_no_trailing_newline(field):
    m = api()
    raw = receipt('not_dispatched') if field == 'never_dispatched_evidence_id' else receipt()
    if field == 'backend_request_id':
        raw[field] = 'backend.observation.1'
    m.parse_generator_request_receipt(raw)
    for bad in ('', ' ' + (raw[field] or 'A'), (raw[field] or 'A') + '\n', 'x' * 129):
        broken = deepcopy(raw)
        broken[field] = bad
        rejected(m.parse_generator_request_receipt, broken,
                 error='invalid_generator_request_receipt')


@pytest.mark.parametrize('field,bad', (
    ('round_index', 0), ('round_index', 6), ('round_index', True), ('round_index', 1.0),
    ('reserved_prompt_tokens', 0), ('reserved_prompt_tokens', True),
    ('reserved_completion_tokens', 999), ('reserved_completion_tokens', 1000.0),
    ('drained', 1), ('phase', 'done'), ('outcome', 'ok'), ('usage_status', 'unknown'),
    ('usage_issue', 'provider-says-so'), ('stop_code', MARKER),
))
def test_receipt_native_bounds_and_closed_enums(field, bad):
    m = api()
    broken = receipt(**{field: bad})
    rejected(m.parse_generator_request_receipt, broken,
             error='invalid_generator_request_receipt')


@pytest.mark.parametrize('code', STOP_CODES)
def test_fixed_stop_codes_are_closed_but_not_authorization(code):
    m = api()
    raw = receipt(outcome='stopped', stop_code=code)
    native_equal(m.parse_generator_request_receipt(raw), raw)


@pytest.mark.parametrize('phase', PHASES)
def test_receipt_phase_shapes_and_exact_idempotence(phase):
    m = api()
    raw = receipt(phase)
    native_equal(m.parse_generator_request_receipt(raw), raw)
    original = m.parse_generator_request_receipt(raw)
    result = m.transition_generator_request(original, raw)
    native_equal(result, raw)
    assert result is not original


@pytest.mark.parametrize('phase,field,value', (
    ('reserved', 'dispatch_marker_id', 'f' * 32),
    ('reserved', 'drained', True), ('reserved', 'outcome', 'success'),
    ('dispatched', 'dispatch_marker_id', None), ('dispatched', 'drained', True),
    ('dispatched', 'stop_code', 'generation_cancelled'),
    ('settled', 'client_dispatch_id', None), ('settled', 'dispatch_marker_id', None),
    ('settled', 'drained', False), ('settled', 'never_dispatched_evidence_id', 'fake'),
    ('uncertain', 'stop_code', None), ('uncertain', 'outcome', 'success'),
    ('not_dispatched', 'never_dispatched_evidence_id', None),
    ('not_dispatched', 'client_dispatch_id', 'f' * 32),
    ('not_dispatched', 'drained', False), ('not_dispatched', 'backend_request_id', 'remote'),
))
def test_receipt_parser_rejects_inconsistent_phase_facts(phase, field, value):
    m = api()
    broken = receipt(phase, **{field: value})
    rejected(m.parse_generator_request_receipt, broken,
             error='invalid_generator_request_receipt')


USAGE_CASES = (
    ('valid', {'prompt_tokens': 0, 'completion_tokens': 0}, None, 'success', None),
    ('valid', {'prompt_tokens': 200, 'completion_tokens': 1000}, None, 'success', None),
    ('missing', {'prompt_tokens': None, 'completion_tokens': 0}, None, 'success', None),
    ('missing', {'prompt_tokens': 0, 'completion_tokens': None}, None, 'success', None),
    ('missing', {'prompt_tokens': None, 'completion_tokens': None}, None, 'success', None),
    ('not_observed', None, None, 'backend_failed', None),
    ('malformed', {'prompt_tokens': None, 'completion_tokens': 7}, 'invalid_prompt',
     'stopped', 'generation_usage_invalid'),
    ('malformed', {'prompt_tokens': 11, 'completion_tokens': None}, 'invalid_completion',
     'stopped', 'generation_usage_invalid'),
    ('malformed', {'prompt_tokens': None, 'completion_tokens': None}, 'invalid_both',
     'stopped', 'generation_usage_invalid'),
    ('over_bound', {'prompt_tokens': 201, 'completion_tokens': 7}, None,
     'bound_violation', 'generation_token_bound_violated'),
    ('over_bound', {'prompt_tokens': 11, 'completion_tokens': 1001}, None,
     'bound_violation', 'generation_token_bound_violated'),
    ('over_bound', {'prompt_tokens': 201, 'completion_tokens': None}, 'invalid_completion',
     'bound_violation', 'generation_token_bound_violated'),
)


@pytest.mark.parametrize('status,usage,issue,outcome,stop', USAGE_CASES)
def test_usage_missing_zero_malformed_over_bound_preserves_debits(
        status, usage, issue, outcome, stop):
    m = api()
    old = receipt('dispatched')
    new = receipt(usage_status=status, usage=usage, usage_issue=issue,
                  outcome=outcome, stop_code=stop)
    native_equal(m.parse_generator_request_receipt(new), new)
    native_equal(m.transition_generator_request(old, new), new)
    old_budget, new_budget = budget([old]), budget([new])
    result = m.transition_b_root_budget(old_budget, new_budget)
    native_equal(result, new_budget)
    assert result.generator_request_debits == 1
    assert result.root_request_debits == 3
    assert result.reserved_tokens == 1200


@pytest.mark.parametrize('field', ('prompt_tokens', 'completion_tokens'))
@pytest.mark.parametrize('bad', (True, False, -1, 1.0, '1', float('nan'), float('inf')))
def test_parser_never_normalizes_invalid_usage_scalars(field, bad):
    m = api()
    broken = receipt()
    broken['usage'][field] = bad
    rejected(m.parse_generator_request_receipt, broken,
             error='invalid_generator_request_receipt')


@pytest.mark.parametrize('changes', (
    {'usage_status': 'missing'}, {'usage_status': 'not_observed'},
    {'usage_status': 'malformed', 'usage_issue': None},
    {'usage_status': 'over_bound', 'outcome': 'bound_violation',
     'stop_code': 'generation_token_bound_violated'},
    {'usage': {'prompt_tokens': 201, 'completion_tokens': 7}},
    {'usage': {'prompt_tokens': None, 'completion_tokens': 7}},
    {'usage_issue': 'invalid_prompt'},
))
def test_usage_status_cannot_lie_about_present_counts(changes):
    m = api()
    rejected(m.parse_generator_request_receipt, receipt(**changes),
             error='invalid_generator_request_receipt')


LEGAL_RECEIPT_EDGES = frozenset((
    (None, 'reserved'), ('reserved', 'dispatched'), ('reserved', 'not_dispatched'),
    ('reserved', 'uncertain'), ('dispatched', 'settled'), ('dispatched', 'uncertain'),
    ('dispatched', 'not_dispatched'), ('uncertain', 'settled'),
    ('uncertain', 'not_dispatched'),
))


def receipt_edge(old_phase, new_phase):
    old = None if old_phase is None else receipt(old_phase)
    if old_phase == new_phase:
        return old, deepcopy(old)
    new = receipt(new_phase)
    if old_phase == 'reserved' and new_phase in ('uncertain', 'not_dispatched'):
        new['dispatch_marker_id'] = None
    return old, new


@pytest.mark.parametrize('old_phase,new_phase', tuple(product((None,) + PHASES, PHASES)))
def test_complete_receipt_edge_matrix(old_phase, new_phase):
    m = api()
    old, new = receipt_edge(old_phase, new_phase)
    if old is not None:
        m.parse_generator_request_receipt(old)
    m.parse_generator_request_receipt(new)
    if old_phase == new_phase or (old_phase, new_phase) in LEGAL_RECEIPT_EDGES:
        native_equal(m.transition_generator_request(old, new), new)
    else:
        rejected(m.transition_generator_request, old, new, error=RECEIPT_ERROR)


@pytest.mark.parametrize('field,new_value', (
    ('root_id', 'another.root'), ('logical_slot_id', 'f' * 32),
    ('reservation_id', 'f' * 32), ('round_index', 2), ('input_sha256', 'f' * 64),
    ('proof_sha256', 'f' * 64), ('generator_generation', 'f' * 32),
    ('grant_sha256', 'f' * 64), ('reserved_prompt_tokens', 201),
))
def test_receipt_immutable_identity_and_reservations(field, new_value):
    m = api()
    old, new = receipt('reserved'), receipt('dispatched', **{field: new_value})
    m.parse_generator_request_receipt(new)
    rejected(m.transition_generator_request, old, new, error=RECEIPT_ERROR)


def test_dispatch_marker_is_not_client_entry_and_ids_install_only_once():
    m = api()
    old = receipt('dispatched')
    assert old['client_dispatch_id'] is None
    entered = deepcopy(old)
    entered.update(client_dispatch_id='f' * 32, backend_request_id='backend.1')
    native_equal(m.transition_generator_request(old, entered), entered)
    for field, value in (('dispatch_marker_id', 'd' * 32),
                         ('client_dispatch_id', 'd' * 32), ('client_dispatch_id', None),
                         ('backend_request_id', 'backend.2'), ('backend_request_id', None)):
        broken = deepcopy(entered)
        broken[field] = value
        rejected(m.transition_generator_request, entered, broken, error=RECEIPT_ERROR)
    released = receipt('not_dispatched')
    rejected(m.transition_generator_request, entered, released, error=RECEIPT_ERROR)


def test_uncertain_facts_can_grow_but_not_erase_or_replace_observations():
    m = api()
    old = receipt('uncertain')
    observed = deepcopy(old)
    observed.update(client_dispatch_id='f' * 32, usage_status='valid',
                    usage=dict(prompt_tokens=11, completion_tokens=7), drained=True)
    native_equal(m.transition_generator_request(old, observed), observed)
    for field, value in (('client_dispatch_id', None), ('drained', False),
                         ('usage', None), ('usage_status', 'not_observed')):
        broken = deepcopy(observed)
        broken[field] = value
        rejected(m.transition_generator_request, observed, broken, error=RECEIPT_ERROR)
    for count in (0, 10, 12):
        broken = deepcopy(observed)
        broken['usage']['prompt_tokens'] = count
        rejected(m.transition_generator_request, observed, broken, error=RECEIPT_ERROR)


@pytest.mark.parametrize('phase', ('settled', 'not_dispatched'))
def test_terminal_receipt_cannot_be_amended(phase):
    m = api()
    old = receipt(phase)
    new = deepcopy(old)
    if phase == 'settled':
        new['usage']['prompt_tokens'] = 12
    else:
        new['never_dispatched_evidence_id'] = 'different.proof'
    m.parse_generator_request_receipt(new)
    rejected(m.transition_generator_request, old, new, error=RECEIPT_ERROR)


def test_invalid_old_transition_input_is_not_repaired_and_new_none_is_rejected():
    m = api()
    for transition, old, new, error in (
        (m.transition_generator_request, receipt(), receipt(), RECEIPT_ERROR),
        (m.transition_b_root_budget, budget(), budget(), BUDGET_ERROR),
    ):
        bad_old = deepcopy(old)
        bad_old['version'] = 1
        rejected(transition, bad_old, new, error=error)
        rejected(transition, old, None, error=error)
    rejected(m.transition_b_root_budget, None, budget(), error=BUDGET_ERROR)


@pytest.mark.parametrize('field,bad', (
    ('version', '2'), ('policy', 'B2'), ('main_limit', 0), ('main_limit', 17),
    ('main_limit', True), ('root_limit', 15), ('root_limit', 16.0),
    ('intent_requests', 2), ('intent_requests', True), ('decision_requests', -1),
    ('decision_requests', 17), ('main_requests', True),
    ('generator_request_debits', 6), ('root_request_debits', 17),
    ('reserved_tokens', -1), ('reserved_tokens', True), ('slot_state', 'ready'),
    ('logical_slot_id', None),
))
def test_budget_native_bounds_and_closed_fields(field, bad):
    m = api()
    rejected(m.parse_b_root_budget, budget([receipt()], **{field: bad}),
             error='invalid_b_root_budget')


@pytest.mark.parametrize('field', ('main_requests', 'generator_request_debits',
                                  'root_request_debits', 'reserved_tokens'))
@pytest.mark.parametrize('delta', (-1, 1))
def test_budget_aggregate_arithmetic_is_exact(field, delta):
    m = api()
    broken = budget([receipt()])
    broken[field] += delta
    rejected(m.parse_b_root_budget, broken, error='invalid_b_root_budget')


@pytest.mark.parametrize('field,new_value', (
    ('root_id', 'foreign.root'), ('logical_slot_id', 'f' * 32),
    ('input_sha256', 'f' * 64), ('proof_sha256', 'f' * 64),
    ('generator_generation', 'f' * 32), ('grant_sha256', 'f' * 64),
))
def test_budget_requires_same_root_slot_input_proof_and_grant(field, new_value):
    m = api()
    row = receipt(**{field: new_value})
    m.parse_generator_request_receipt(row)
    rejected(m.parse_b_root_budget, budget([row]), error='invalid_b_root_budget')


def test_budget_grant_digest_prompt_ceiling_and_total_reservation_limits():
    m = api()
    insufficient = grant(total_reserved_tokens=1199)
    for bad in (
        budget([receipt()], grant_sha256='f' * 64),
        budget([receipt(reserved_prompt_tokens=201)]),
        budget([receipt(grant_sha256=digest(insufficient))], token_grant=insufficient),
    ):
        rejected(m.parse_b_root_budget, bad, error='invalid_b_root_budget')
    g = grant(total_reserved_tokens=1200)
    row = receipt(grant_sha256=digest(g))
    exact = budget([row], token_grant=g)
    native_equal(m.parse_b_root_budget(exact), exact)


@pytest.mark.parametrize('corruption', ('duplicate-reservation', 'duplicate-marker',
                                       'duplicate-client', 'gap', 'reorder', 'sixth'))
def test_receipt_list_order_identity_uniqueness_and_five_round_ceiling(corruption):
    m = api()
    rows = [receipt(index=1), receipt(index=2)]
    if corruption.startswith('duplicate'):
        field = {'duplicate-reservation': 'reservation_id',
                 'duplicate-marker': 'dispatch_marker_id',
                 'duplicate-client': 'client_dispatch_id'}[corruption]
        rows[1][field] = rows[0][field]
    elif corruption == 'gap':
        rows[1] = receipt(index=3)
    elif corruption == 'reorder':
        rows.reverse()
    else:
        rows = [receipt(index=i) for i in range(1, 7)]
    rejected(m.parse_b_root_budget, budget(rows), error='invalid_b_root_budget')


LEGAL_SLOT_EDGES = frozenset((
    ('unused', 'claimed'), ('claimed', 'running'), ('claimed', 'failed'),
    ('claimed', 'cancelled'), ('claimed', 'uncertain'), ('running', 'complete'),
    ('running', 'partial'), ('running', 'failed'), ('running', 'cancelled'),
    ('running', 'uncertain'), ('uncertain', 'failed'), ('uncertain', 'cancelled'),
))


def slot_pair(old_state, new_state):
    needs_success = {'complete', 'partial'}
    old_rows = [receipt()] if old_state in needs_success else []
    new_rows = [receipt()] if new_state in needs_success else []
    if old_state == 'running' and new_state in needs_success:
        old_rows = deepcopy(new_rows)
    if new_state == 'running' and old_state in needs_success:
        new_rows = deepcopy(old_rows)
    old = budget(old_rows, state=old_state)
    new = deepcopy(old) if old_state == new_state else budget(new_rows, state=new_state)
    return old, new


@pytest.mark.parametrize('old_state,new_state', tuple(product(SLOTS, SLOTS)))
def test_complete_slot_edge_matrix_and_consumed_terminal_states(old_state, new_state):
    m = api()
    old, new = slot_pair(old_state, new_state)
    m.parse_b_root_budget(old)
    m.parse_b_root_budget(new)
    if old_state == new_state or (old_state, new_state) in LEGAL_SLOT_EDGES:
        result = m.transition_b_root_budget(old, new)
        native_equal(result, new)
        assert result is not old
    else:
        rejected(m.transition_b_root_budget, old, new, error=BUDGET_ERROR)


@pytest.mark.parametrize('state', SLOTS)
def test_main_charge_is_monotonic_and_does_not_reopen_slot(state):
    m = api()
    rows = [receipt()] if state in ('complete', 'partial') else []
    old, new = budget(rows, state=state, main=2), budget(rows, state=state, main=3)
    native_equal(m.transition_b_root_budget(old, new), new)
    rejected(m.transition_b_root_budget, new, old, error=BUDGET_ERROR)
    rejected(m.transition_b_root_budget, old, budget(rows, state=state, main=4),
             error=BUDGET_ERROR)


@pytest.mark.parametrize('state', ('complete', 'partial'))
def test_successful_slot_requires_settled_success_and_no_control_stop(state):
    m = api()
    stopped = receipt(outcome='stopped', stop_code='generation_cancelled')
    for rows in ([], [receipt('reserved')], [receipt('uncertain')],
                 [receipt(outcome='backend_failed')], [receipt(), stopped]):
        # The two-row case uses distinct round/operation identities so a stop,
        # rather than a duplicate identity, is the invalid completion fact.
        if len(rows) == 2:
            rows[1] = receipt(index=2, outcome='stopped', stop_code='generation_cancelled')
        rejected(m.parse_b_root_budget, budget(rows, state=state), error='invalid_b_root_budget')


@pytest.mark.parametrize('state', ('failed', 'cancelled'))
def test_failed_or_cancelled_slot_cannot_claim_undrained_cleanup(state):
    m = api()
    rejected(m.parse_b_root_budget, budget([receipt('uncertain')], state=state),
             error='invalid_b_root_budget')


def test_intent_and_decision_charges_cannot_be_relabelled_or_combined():
    m = api()
    old = budget(state='unused', main=0)
    intent = budget(state='unused', main=1)
    native_equal(m.transition_b_root_budget(old, intent), intent)
    decision = budget(state='unused', main=1, intent_requests=0, decision_requests=1)
    native_equal(m.transition_b_root_budget(old, decision), decision)
    rejected(m.transition_b_root_budget, intent, decision, error=BUDGET_ERROR)
    rejected(m.transition_b_root_budget, old, budget(state='unused', main=2),
             error=BUDGET_ERROR)
    rejected(m.transition_b_root_budget, old, budget(state='claimed', main=1),
             error=BUDGET_ERROR)


@pytest.mark.parametrize('change', ('root', 'input', 'proof', 'slot', 'main-limit',
                                   'grant', 'generation'))
def test_budget_immutable_admission_identity_and_grant(change):
    m = api()
    old = budget()
    new = deepcopy(old)
    if change in ('grant', 'generation'):
        g = grant(total_reserved_tokens=6001) if change == 'grant' else grant(generator_generation='f' * 32)
        new['grant'], new['grant_sha256'] = g, digest(g)
    else:
        field, value = {
            'root': ('root_id', 'other.root'), 'input': ('input_sha256', 'f' * 64),
            'proof': ('proof_sha256', 'f' * 64), 'slot': ('logical_slot_id', 'f' * 32),
            'main-limit': ('main_limit', 15),
        }[change]
        new[field] = value
    m.parse_b_root_budget(new)
    rejected(m.transition_b_root_budget, old, new, error=BUDGET_ERROR)


def test_append_settle_and_main_charge_are_separate_atomic_operations():
    m = api()
    old = budget()
    reserved = budget([receipt('reserved')])
    native_equal(m.transition_b_root_budget(old, reserved), reserved)
    rejected(m.transition_b_root_budget, old, budget([receipt('reserved')], main=3),
             error=BUDGET_ERROR)
    dispatched = budget([receipt('dispatched')])
    native_equal(m.transition_b_root_budget(reserved, dispatched), dispatched)
    settled = budget([receipt()])
    native_equal(m.transition_b_root_budget(dispatched, settled), settled)
    next_round = budget([receipt(), receipt('reserved', index=2)])
    native_equal(m.transition_b_root_budget(settled, next_round), next_round)
    rejected(m.transition_b_root_budget, next_round, settled, error=BUDGET_ERROR)
    rejected(m.transition_b_root_budget, old, budget([receipt('reserved'),
                                                   receipt('reserved', index=2)]),
             error=BUDGET_ERROR)


@pytest.mark.parametrize('phase', ('reserved', 'dispatched', 'uncertain', 'not_dispatched'))
def test_unfinished_or_stopped_previous_receipt_forbids_next_round(phase):
    m = api()
    old = budget([receipt(phase)])
    new = budget([receipt(phase), receipt('reserved', index=2)])
    rejected(m.transition_b_root_budget, old, new, error=BUDGET_ERROR)


@pytest.mark.parametrize('outcome', ('success', 'backend_failed'))
def test_settled_success_or_backend_failure_can_admit_next_round_without_refund(outcome):
    m = api()
    first = receipt(outcome=outcome)
    old = budget([first])
    new = budget([first, receipt('reserved', index=2)])
    result = m.transition_b_root_budget(old, new)
    assert result.generator_request_debits == 2
    assert result.reserved_tokens == 2400


@pytest.mark.parametrize('state', ('unused', 'claimed', 'complete', 'partial',
                                  'failed', 'cancelled', 'uncertain'))
def test_only_running_slot_can_append_receipts(state):
    m = api()
    rows = [receipt()] if state in ('complete', 'partial') else []
    old = budget(rows, state=state)
    new = budget(rows + [receipt('reserved', index=len(rows) + 1)], state=state)
    rejected(m.transition_b_root_budget, old, new, error=BUDGET_ERROR)


def test_root16_main_limit_five_rounds_and_finish_credit_are_independent():
    m = api()
    five = [receipt(index=i) for i in range(1, 6)]
    full = budget(five, main=11)
    native_equal(m.parse_b_root_budget(full), full)
    rejected(m.transition_b_root_budget, full, budget(five, main=12), error=BUDGET_ERROR)
    one_left = budget([receipt()], main=14)
    finish = budget([receipt()], main=15)
    native_equal(m.transition_b_root_budget(one_left, finish), finish)
    second = budget([receipt(), receipt('reserved', index=2)], main=14)
    native_equal(m.transition_b_root_budget(one_left, second), second)
    rejected(m.transition_b_root_budget, finish, budget([receipt()], main=16),
             error=BUDGET_ERROR)
    main_full = budget(main=16)
    rejected(m.transition_b_root_budget, main_full, budget([receipt('reserved')], main=16),
             error=BUDGET_ERROR)
    low = budget(main=2, main_limit=2)
    native_equal(m.parse_b_root_budget(low), low)
    rejected(m.transition_b_root_budget, low, budget(main=3, main_limit=2), error=BUDGET_ERROR)
    rejected(m.transition_b_root_budget, budget(five),
             budget(five + [receipt('reserved', index=6)]), error=BUDGET_ERROR)


def test_never_dispatched_release_is_syntax_claim_not_owner_proof():
    m = api()
    for phase in ('reserved', 'dispatched', 'uncertain'):
        old = receipt(phase)
        released = receipt('not_dispatched', dispatch_marker_id=old['dispatch_marker_id'],
                           never_dispatched_evidence_id='caller.claim.not-authenticated')
        native_equal(m.transition_generator_request(old, released), released)
        result = m.transition_b_root_budget(budget([old]), budget([released]))
        assert result.generator_request_debits == 0
        assert result.reserved_tokens == 0
        assert result.main_requests == result.root_request_debits == 2
        assert len(result.generator_requests) == 1
        assert result.logical_slot_id == 'e' * 32
        assert result.slot_state == 'running'  # not unused/reopened
        rejected(m.transition_b_root_budget, result, budget(), error=BUDGET_ERROR)
    # Acceptance of a fake evidence ID above is intentionally NOT authenticated
    # release evidence. Runtime owner rejection/drain/CAS belong to later C.


@pytest.mark.parametrize('phase', ('reserved', 'dispatched', 'uncertain', 'settled'))
def test_no_refund_from_usage_timeout_missing_or_ambiguous_dispatch(phase):
    m = api()
    old = budget([receipt(phase)])
    for field in ('generator_request_debits', 'root_request_debits', 'reserved_tokens'):
        reduced = deepcopy(old)
        reduced[field] = 0
        rejected(m.transition_b_root_budget, old, reduced, error=BUDGET_ERROR)


def grant_at_size(target):
    g = grant(prompt_token_ceiling=10 ** 1199,
              context_token_limit=10 ** 1199 + 1000, total_reserved_tokens=1)
    digits = target - wire_size(g) + 1
    assert 1 <= digits <= 1233, 'synthetic grant byte-boundary fixture does not fit'
    g['total_reserved_tokens'] = 10 ** (digits - 1)
    assert wire_size(g) == target
    assert all(v.bit_length() <= 4096 for v in g.values() if type(v) is int)
    return g


def budget_at_size(target):
    # All records remain schema-valid, with success usage <= reservations.
    # Decimal digit growth, not extra padding keys or production grant numbers.
    prompt = 10 ** 1000
    g = grant(prompt_token_ceiling=prompt, context_token_limit=prompt + 1000,
              total_reserved_tokens=5 * (prompt + 1000))
    rows = [receipt(index=i, grant_sha256=digest(g), reserved_prompt_tokens=prompt,
                    usage=dict(prompt_tokens=0, completion_tokens=0)) for i in range(1, 6)]
    value = budget(rows, token_grant=g)
    assert wire_size(g) <= 4096
    assert wire_size(value) <= target
    for row in value['generator_requests']:
        extra = min(1000, target - wire_size(value))
        if extra:
            row['usage']['prompt_tokens'] = 10 ** extra
    assert wire_size(value) == target, 'synthetic budget byte-boundary fixture does not fit'
    assert prompt.bit_length() <= 4096
    return value


@pytest.mark.parametrize('size,accepted', ((4096, True), (4097, False)))
def test_grant_exact_byte_boundary_independent_of_scalar_guard(size, accepted):
    m = api()
    raw = grant_at_size(size)
    if accepted:
        native_equal(m.parse_generator_token_grant(raw), raw)
    else:
        rejected(m.parse_generator_token_grant, raw, error='invalid_generator_token_grant')
    envelope = budget(token_grant=raw)
    assert wire_size(envelope) < 16384
    if accepted:
        native_equal(m.parse_b_root_budget(envelope), envelope)
    else:
        rejected(m.parse_b_root_budget, envelope, error='invalid_b_root_budget')


@pytest.mark.parametrize('size,accepted', ((16384, True), (16385, False)))
@pytest.mark.parametrize('form', ('raw', 'constructed'))
def test_budget_exact_byte_boundary_independent_of_scalar_guard(size, accepted, form):
    m = api()
    raw = budget_at_size(size)
    value = raw if form == 'raw' else m.BRootBudget.model_construct(**deepcopy(raw))
    if accepted:
        native_equal(m.parse_b_root_budget(value), raw)
    else:
        rejected(m.parse_b_root_budget, value, error='invalid_b_root_budget')
        rejected(m.transition_b_root_budget, budget(), value, error=BUDGET_ERROR)


@pytest.mark.parametrize('number', (1 << 4095, (1 << 4096) - 1, 1 << 4096),
                         ids=('4096-bit-low', '4096-bit-high', '4097-bit'))
@pytest.mark.parametrize('form', ('raw', 'constructed', 'copy'))
def test_usage_parser_4096_4097_bit_boundary_not_byte_overflow(number, form, monkeypatch, caplog):
    m = api()
    old = receipt('dispatched')
    raw = receipt(outcome='bound_violation', stop_code='generation_token_bound_violated',
                  usage_status='over_bound',
                  usage=dict(prompt_tokens=number, completion_tokens=7))
    envelope = budget([raw])
    assert wire_size(raw) < 16384 and wire_size(envelope) < 16384
    if form == 'raw':
        value = raw
    elif form == 'constructed':
        value = m.GeneratorRequestReceipt.model_construct(**deepcopy(raw))
    else:
        value = m.parse_generator_request_receipt(receipt()).model_copy(update={
            k: deepcopy(raw[k]) for k in ('outcome', 'stop_code', 'usage_status', 'usage')})
    if number.bit_length() <= 4096:
        result = m.parse_generator_request_receipt(value)
        assert result.usage.prompt_tokens == number
        native_equal(m.transition_generator_request(old, value), raw)
        native_equal(m.parse_b_root_budget(envelope), envelope)
        native_equal(m.transition_b_root_budget(budget([old]), envelope), envelope)
    else:
        assert number.bit_length() == 4097
        assert len(str(number)) == 1234  # fixture fact, not production formatting
        nested = m.BRootBudget.model_construct(**{**envelope, 'generator_requests': (value,)})
        def no_dump(*args, **kwargs):
            raise AssertionError('4097-bit scalar reached JSON serialization')
        with monkeypatch.context() as patch:
            patch.setattr(json, 'dumps', no_dump)
            rejected(m.parse_generator_request_receipt, value,
                     error='invalid_generator_request_receipt')
            rejected(m.parse_b_root_budget, envelope, error='invalid_b_root_budget')
            rejected(m.parse_b_root_budget, nested, error='invalid_b_root_budget')
        # A transition may serialize the valid old operand before rejecting new;
        # do not impose a stronger all-operands prewalk order than the plan.
        rejected(m.transition_generator_request, old, value, error=RECEIPT_ERROR)
        rejected(m.transition_b_root_budget, budget([old]), envelope, error=BUDGET_ERROR)
        assert str(number) not in caplog.text


@pytest.mark.parametrize('field', ('total_reserved_tokens', 'prompt_token_ceiling',
                                  'context_token_limit'))
def test_grant_overflow_is_rejection_not_usage_normalization(field):
    m = api()
    raw = grant(**{field: 1 << 4096})
    rejected(m.parse_generator_token_grant, raw, error='invalid_generator_token_grant')
    row = receipt(reserved_prompt_tokens=1 << 4096)
    rejected(m.parse_generator_request_receipt, row, error='invalid_generator_request_receipt')


@pytest.mark.parametrize('issue', ('usage_scalar_overflow', 'usage_wire_overflow',
                                  'usage_scalar_and_wire_overflow'))
def test_bounded_overflow_fact_is_syntax_not_upstream_normalization(issue):
    m = api()
    old = receipt('dispatched')
    normalized = receipt(
        usage_status='over_bound', usage=dict(prompt_tokens=None, completion_tokens=7),
        usage_issue=issue, outcome='bound_violation',
        stop_code='generation_token_bound_violated')
    native_equal(m.parse_generator_request_receipt(normalized), normalized)
    native_equal(m.transition_generator_request(old, normalized), normalized)
    settled = m.transition_b_root_budget(budget([old]), budget([normalized]))
    assert settled.reserved_tokens == 1200
    assert settled.generator_request_debits == 1
    assert settled.root_request_debits == 3
    assert settled.generator_requests[0].usage.prompt_tokens is None
    assert settled.generator_requests[0].usage.completion_tokens == 7
    for replacement in (0, 200):
        clipped = deepcopy(normalized)
        clipped['usage']['prompt_tokens'] = replacement
        rejected(m.parse_generator_request_receipt, clipped,
                 error='invalid_generator_request_receipt')
    raw_overflow = deepcopy(normalized)
    raw_overflow['usage']['prompt_tokens'] = 1 << 4096
    rejected(m.parse_generator_request_receipt, raw_overflow,
             error='invalid_generator_request_receipt')
    for status, outcome, stop in (
        ('missing', 'success', None), ('valid', 'success', None),
        ('over_bound', 'success', None),
    ):
        false_success = deepcopy(normalized)
        false_success.update(usage_status=status, outcome=outcome, stop_code=stop)
        rejected(m.parse_generator_request_receipt, false_success,
                 error='invalid_generator_request_receipt')
    later = budget([normalized, receipt('reserved', index=2)])
    rejected(m.transition_b_root_budget, settled, later, error=BUDGET_ERROR)
    # No client or upstream normalizer is invoked: this accepted fact is not proof.


@pytest.mark.parametrize('issue', ('usage_scalar_overflow', 'usage_scalar_and_wire_overflow'))
def test_known_positive_scalar_overflow_cannot_be_downgraded_to_malformed(issue):
    m = api()
    downgraded = receipt(usage_status='malformed',
                         usage=dict(prompt_tokens=None, completion_tokens=7),
                         usage_issue=issue, outcome='stopped',
                         stop_code='generation_usage_invalid')
    rejected(m.parse_generator_request_receipt, downgraded,
             error='invalid_generator_request_receipt')


def test_wire_only_overflow_without_known_inequality_still_stops_without_refund():
    m = api()
    new = receipt(usage_status='malformed', usage=dict(prompt_tokens=None, completion_tokens=7),
                  usage_issue='usage_wire_overflow', outcome='stopped',
                  stop_code='generation_usage_invalid')
    result = m.transition_b_root_budget(budget([receipt('dispatched')]), budget([new]))
    assert result.reserved_tokens == 1200
    assert result.generator_request_debits == 1
    assert result.generator_requests[0].usage_issue == 'usage_wire_overflow'


def test_fixed_public_errors_do_not_echo_values_or_log_raw_usage(caplog):
    m = api()
    rejected(m.parse_generator_token_grant, grant(backend_revision=MARKER + '\n'),
             error='invalid_generator_token_grant')
    bad = receipt(usage_issue=MARKER)
    rejected(m.parse_generator_request_receipt, bad,
             error='invalid_generator_request_receipt')
    rejected(m.transition_generator_request, receipt('reserved'), bad, error=RECEIPT_ERROR)
    assert MARKER not in caplog.text
