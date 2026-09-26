"""Pure native-JSON receipt validation, not producer authentication.

The mapping digest names the captured mapping; it cannot recover that mapping or
verify remote model weights. No filesystem resolution or scientific work occurs.
"""
from copy import deepcopy
import hashlib
import json
from math import isfinite
import re


def _require_plain_json(item, active=None):
    if type(item) in (str, int, bool, type(None)):
        return
    if type(item) is float and isfinite(item):
        return
    if active is None:
        active = set()
    if type(item) not in (list, dict) or id(item) in active:
        raise ValueError('Native acyclic JSON required')
    if type(item) is dict and any(type(key) is not str for key in item):
        raise ValueError('Native string keys required')
    active.add(id(item))
    try:
        for child in item.values() if type(item) is dict else item:
            _require_plain_json(child, active)
    finally:
        active.remove(id(item))


def canonical_digest(value):
    """Receipt codec, deliberately distinct from downstream ledger codecs."""
    def require_plain_json(item):
        if type(item) in (str, int, float, bool, type(None)):
            return
        if type(item) is list:
            for child in item:
                require_plain_json(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                require_plain_json(child)
            return
        raise TypeError('RAG receipt requires native JSON values and string keys')

    require_plain_json(value)
    body = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(body).hexdigest()


_DIGESTS = {'input_sha256', 'source_sha256', 'index_sha256', 'row_mapping_sha256',
            'embedding_endpoint_sha256', 'result_sha256'}
_RECEIPT_FIELDS = _DIGESTS | {
    'schema_version', 'validation_revision', 'invocation_id', 'generation_id',
    'source_path', 'source_row_count', 'vector_dimension', 'vector_count',
    'manifest_schema_version', 'builder_version', 'embedding_model',
    'embedding_weights_verified', 'index_embedding_endpoint_sha256', 'diagnostics',
}
_DIAGNOSTIC_FIELDS = {
    'version', 'status', 'requested_k', 'effective_k', 'index_search_executed',
    'score_count', 'label_count', 'accepted_count', 'discarded_count', 'reason_codes',
}
_PROVENANCE_FIELDS = ('source_path', 'source_sha256', 'index_sha256',
                      'embedding_model', 'manifest_schema_version', 'builder_version')


def _require(condition):
    if not condition:
        raise ValueError('Invalid RAG retrieval envelope')


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _validate(value, query, k):
    _require(type(value) is dict and set(value) == {'records', 'receipt'})
    records, receipt = value['records'], value['receipt']
    _require(type(records) is list and all(type(row) is dict for row in records))
    _require(type(receipt) is dict and set(receipt) == _RECEIPT_FIELDS)
    for field in _DIGESTS | {'invocation_id', 'generation_id'}:
        size = 64 if field in _DIGESTS else 32
        _require(type(receipt[field]) is str
                 and re.fullmatch('[0-9a-f]{' + str(size) + '}', receipt[field]) is not None)
    for field, expected in {
        'schema_version': '1', 'validation_revision': 'rag-owned-generation-v1',
        'manifest_schema_version': 2, 'builder_version': '1',
        'embedding_weights_verified': False, 'index_embedding_endpoint_sha256': None,
    }.items():
        _require(type(receipt[field]) is type(expected) and receipt[field] == expected)
    for field in ('source_path', 'embedding_model'):
        _require(type(receipt[field]) is str and bool(receipt[field]))
    for field in ('source_row_count', 'vector_count', 'vector_dimension'):
        _require(_integer(receipt[field], 1 if field == 'vector_dimension' else 0))
    d = receipt['diagnostics']
    _require(type(d) is dict and set(d) == _DIAGNOSTIC_FIELDS)
    _require(d['version'] == '1' and type(d['index_search_executed']) is bool)
    for field in ('requested_k', 'effective_k', 'accepted_count'):
        _require(_integer(d[field], 1 if field == 'requested_k' else 0))
    for field in ('score_count', 'label_count', 'discarded_count'):
        _require(d[field] is None or _integer(d[field]))
    reasons = d['reason_codes']
    _require(type(reasons) is list and all(type(reason) is str for reason in reasons))
    _require(len(set(reasons)) == len(reasons))
    effective = d['effective_k']
    _require(effective == min(d['requested_k'], receipt['vector_count']))
    _require(len(records) == d['accepted_count'])
    if query is not None:
        _require(type(query) is str)
        _require(receipt['input_sha256'] == hashlib.sha256(query.encode('utf-8')).hexdigest())
    if k is not None:
        _require(_integer(k, 1) and k == d['requested_k'])
    _require(receipt['result_sha256'] == canonical_digest(records))
    if d['status'] == 'valid_empty':
        _require(receipt['vector_count'] == effective == 0 and not d['index_search_executed'])
        _require(all(d[field] == 0 for field in (
            'score_count', 'label_count', 'accepted_count', 'discarded_count')) and not reasons)
    else:
        _require(effective > 0 and d['index_search_executed'])
        if d['status'] == 'valid_hits':
            _require(d['score_count'] == d['label_count'] == d['accepted_count'] == effective)
            _require(d['discarded_count'] == 0 and not reasons)
        elif d['status'] == 'invalid_discard':
            if reasons == ['invalid_result_shape']:
                _require(d['accepted_count'] == 0 and d['discarded_count'] is None)
                _require(d['score_count'] != effective or d['label_count'] != effective)
            else:
                _require(d['score_count'] == d['label_count'] == effective)
                _require(_integer(d['discarded_count'], 1) and d['discarded_count'] <= effective)
                _require(d['accepted_count'] + d['discarded_count'] == effective)
                _require(bool(reasons) and set(reasons) <= {'invalid_label', 'invalid_score', 'duplicate_hit'})
                _require(len(reasons) <= d['discarded_count'])
        else:
            _require(False)
    labels, rows = set(), set()
    for row in records:
        position, score, provenance = row['source_index'], row['similarity_score'], row['provenance']
        _require(_integer(position) and position < receipt['source_row_count'] and position not in rows)
        _require(type(score) in (float, int) and isfinite(score))
        _require(type(provenance) is dict)
        for field in _PROVENANCE_FIELDS:
            _require(type(provenance[field]) is type(receipt[field]) and provenance[field] == receipt[field])
        label = provenance['vector_label']
        _require(_integer(label) and label < receipt['vector_count'] and label not in labels)
        labels.add(label)
        rows.add(position)


def validate_retrieval_envelope(value, *, query=None, k=None):
    """Validate without projecting extensions; return detached native JSON values.

    All malformed proof errors have the same non-sensitive public message.
    Native checks precede copying, hashing or any other conversion.
    """
    try:
        _require_plain_json(value)
        detached = deepcopy(value)
        _validate(detached, query, k)
        return detached
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise ValueError('Invalid RAG retrieval envelope') from None
