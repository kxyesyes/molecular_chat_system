"""Permanent boundary regressions; actual validator, independent wire oracle.

No copied validator, timing threshold, profiler or scientific/model workload.
The ord spy observes the fast-path mechanism without replacing validation.
"""
import builtins
from copy import deepcopy
from decimal import Decimal
from fractions import Fraction
import json
import math

import pytest


_REASON = 'ascii_accounting_boundary'


@pytest.fixture
def boundary():
    from src.agent.harness import decision_bounds
    from src.agent.harness.decision_policy import DecisionBoundaryError
    return decision_bounds, DecisionBoundaryError


def _accept(boundary, value, **kwargs):
    assert boundary[0].validate_json(value, reason=_REASON, **kwargs) is None


def _reject(boundary, value, **kwargs):
    with pytest.raises(boundary[1]) as caught:
        boundary[0].validate_json(value, reason=_REASON, **kwargs)
    assert caught.value.args == (_REASON,)


def _wire_bytes(value, html_safe):
    text = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if html_safe:
        for char, escape in (('<', '\\u003c'), ('>', '\\u003e'),
                             ('&', '\\u0026'), ('`', '\\u0060')):
            text = text.replace(char, escape)
    return len(text.encode('utf-8'))


def _byte_edges(boundary, value, html_safe):
    size = _wire_bytes(value, html_safe)
    _reject(boundary, value, max_bytes=size - 1, html_safe=html_safe)
    for budget in (size, size + 1):
        _accept(boundary, value, max_bytes=budget, html_safe=html_safe)


@pytest.mark.parametrize('html_safe', [False, True], ids=['json', 'html'])
def test_all_ascii_exact_byte_edges(boundary, html_safe):
    for code in range(128):
        text = chr(code)
        for value in (text, {text: [text]}):
            _byte_edges(boundary, value, html_safe)
    for value in ('', 'a' * 64, 'x' * 8190, 'C/C=C\\C', '<>&`', '\"\\\b\f\n\r\t'):
        _byte_edges(boundary, value, html_safe)


@pytest.mark.parametrize('html_safe', [False, True], ids=['json', 'html'])
def test_unicode_numeric_and_container_edges(boundary, html_safe):
    values = [None, False, True, 0, -12, 1.25, -0.0, 1e-12, 1e100,
              (1 << 4096) - 1, -((1 << 4096) - 1), [], {},
              '\u0080\u07ff\u0800\ud7ff\ue000\uffff\U00010000\U0010ffff',
              '中文🙂', 'e\u0301\u2028\u2029', 'a"\\\x00\n中文🙂<>&`',
              {'nested': [1, 2, {'key': 'value'}]}, ['same', 'same']]
    for value in values:
        before = deepcopy(value)
        _byte_edges(boundary, value, html_safe)
        assert bool(value == before), 'validator_mutated_input'


@pytest.mark.parametrize('kind', [
    'nan', 'inf', 'negative_inf', 'surrogate_high', 'surrogate_low',
    'surrogate_pair', 'integer_over', 'negative_integer_over', 'key', 'bytes',
    'tuple', 'set', 'object', 'str_subclass', 'int_subclass', 'float_subclass',
    'dict_subclass', 'list_subclass', 'cycle_list', 'cycle_dict', 'cycle_indirect',
])
def test_rejection_matrix(boundary, kind):
    class Hostile:
        def __repr__(self):
            raise AssertionError('unexpected representation hook')
        def __str__(self):
            raise AssertionError('unexpected conversion hook')
        def __iter__(self):
            raise AssertionError('unexpected iteration hook')

    value = {'nan': float('nan'), 'inf': float('inf'), 'negative_inf': -float('inf'),
             'surrogate_high': '\ud800', 'surrogate_low': '\udfff',
             'surrogate_pair': '\ud800\udc00', 'integer_over': 1 << 4096,
             'negative_integer_over': -(1 << 4096), 'key': {1: 'x'}, 'bytes': b'x',
             'tuple': (1,), 'set': {1}, 'object': Hostile(),
             'str_subclass': type('Text', (str,), {})('x'),
             'int_subclass': type('Integer', (int,), {})(1),
             'float_subclass': type('Number', (float,), {})(1.0),
             'dict_subclass': type('Mapping', (dict,), {})(),
             'list_subclass': type('Sequence', (list,), {})()}.get(kind)
    if kind == 'cycle_list':
        value = []
        value.append(value)
    elif kind == 'cycle_dict':
        value = {}
        value['self'] = value
    elif kind == 'cycle_indirect':
        value = []
        value.append({'child': value})
    for html_safe in (False, True):
        _reject(boundary, value, max_bytes=512 * 1024, html_safe=html_safe)


def test_depth_nodes_aliases_and_current_object(boundary):
    deep32 = 0
    for _ in range(32):
        deep32 = [deep32]
    cases = [([], 0, 1, True), ([], 0, 0, False), ([0], 0, 2, False),
             ([0], 1, 2, True), ({'k': 0}, 1, 3, True), ({'k': 0}, 1, 2, False),
             (deep32, 32, 16384, True), ([deep32], 32, 16384, False),
             ([None] * 16383, 32, 16384, True), ([None] * 16384, 32, 16384, False)]
    shared = {'x': 'a'}
    aliases = [shared, shared]
    cases.extend([(aliases, 32, 7, True), (aliases, 32, 6, False)])
    for value, depth, nodes, accepted in cases:
        (_accept if accepted else _reject)(boundary, value, max_bytes=512 * 1024,
                                           max_depth=depth, max_nodes=nodes)
    _byte_edges(boundary, aliases, False)
    assert aliases[0] is shared and aliases[1] is shared
    assert bool(shared == {'x': 'a'}), 'alias_mutation'
    shared['x'] = float('nan')
    _reject(boundary, aliases, max_bytes=4096)
    assert aliases[0] is shared and aliases[1] is shared and math.isnan(shared['x'])


@pytest.mark.parametrize('html_safe', [False, True], ids=['json', 'html'])
def test_native_ascii_avoids_python_character_walk(boundary, monkeypatch, html_safe):
    calls = []
    def counted_ord(char):
        calls.append(1)
        return builtins.ord(char)
    monkeypatch.setattr(boundary[0], 'ord', counted_ord, raising=False)
    values = ['plain-ASCII_123 /:', 'a' * 2474, {'key': ['value', 'value']}]
    if not html_safe:
        values.append('<>&`')
    for value in values:
        _accept(boundary, value, max_bytes=_wire_bytes(value, html_safe), html_safe=html_safe)
    assert len(calls) == 0, 'eligible_ascii_used_python_character_walk'


@pytest.mark.parametrize('case', [
    'quote', 'backslash', 'control', 'unicode', 'html_escape',
    'flag_int', 'flag_none', 'flag_list', 'budget_float', 'budget_nan',
    'budget_inf', 'budget_decimal', 'budget_fraction', 'budget_subclass',
])
def test_fallback_keeps_python_character_accounting(boundary, monkeypatch, case):
    class Budget(int):
        def __sub__(self, other):
            return Budget(int(self) - other)
    text, flag, budget = 'abc', False, 64
    text = {'quote': 'a"b', 'backslash': 'a\\b', 'control': 'a\x00b',
            'unicode': 'a🙂b', 'html_escape': '<>&`'}.get(case, text)
    flag = {'html_escape': True, 'flag_int': 1, 'flag_none': None,
            'flag_list': [1]}.get(case, flag)
    budget = {'budget_float': 64.0, 'budget_nan': float('nan'),
              'budget_inf': float('inf'), 'budget_decimal': Decimal(64),
              'budget_fraction': Fraction(64), 'budget_subclass': Budget(64)}.get(case, budget)
    calls = []
    def counted_ord(char):
        calls.append(1)
        return builtins.ord(char)
    monkeypatch.setattr(boundary[0], 'ord', counted_ord, raising=False)
    _accept(boundary, text, max_bytes=budget, html_safe=flag)
    assert len(calls) == len(text)


@pytest.mark.parametrize('truth', [False, True])
def test_non_native_flag_truthiness_per_character(boundary, truth):
    calls = []
    class Flag:
        def __bool__(self):
            calls.append(1)
            return truth
    _accept(boundary, 'A<&B', max_bytes=64, html_safe=Flag())
    assert calls == [1, 1, 1, 1]
    calls.clear()
    _accept(boundary, '', max_bytes=2, html_safe=Flag())
    assert calls == []


def test_non_native_flag_exception_identity(boundary):
    failure = ValueError('synthetic-truth-error')
    calls = []
    class Flag:
        def __bool__(self):
            calls.append(1)
            raise failure
    with pytest.raises(ValueError) as caught:
        boundary[0].validate_json('abc', max_bytes=64, reason=_REASON, html_safe=Flag())
    assert caught.value is failure
    assert calls == [1]


def test_non_native_budget_operator_trace(boundary):
    events = []
    class Budget(int):
        def __sub__(self, other):
            events.append(('subtract', other))
            return Budget(int(self) - other)
        def __lt__(self, other):
            events.append(('less', other))
            return int(self) < other
    _accept(boundary, 'A<&B', max_bytes=Budget(64), html_safe=False)
    assert events == [('less', 6), ('subtract', 2)] + [
        event for _ in range(4) for event in (('subtract', 1), ('less', 0))
    ] + [('less', 0)]


def test_nonstandard_budget_depth_nodes_and_early_rejection(boundary, monkeypatch):
    for budget in (None, '64'):
        with pytest.raises(TypeError):
            boundary[0].validate_json('abc', max_bytes=budget, reason=_REASON)
    _reject(boundary, 'abc', max_bytes=True)
    _accept(boundary, 'abc', max_bytes=64, max_depth=1.0, max_nodes=3.0)
    def forbidden_ord(char):
        raise AssertionError('lower_bound_must_reject_before_character_walk')
    monkeypatch.setattr(boundary[0], 'ord', forbidden_ord, raising=False)
    _reject(boundary, 'x' * 65537, max_bytes=65536)
