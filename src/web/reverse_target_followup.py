"""Server-only ranked receipt references; never recover authority from prose.

Every read checks an owned persisted checkpoint and the current attached producer.
Pointers are socket-local bookkeeping, not HTTP capabilities. This module neither
resolves target names nor executes tools; provider/chat wiring is a separate gate.
"""
from contextlib import closing
from copy import deepcopy
from html import escape
import json
import re

from src.agent.contracts import ObservationStatus, ToolProvenance, ToolResult
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.harness.decision_bounds import validate_json
from src.agent.persistence.scientific_references import _source
from src.reverse_target.receipt import validate_prediction_observation


_TOOL = 'reverse_target_predictor'
_POINTER = {'schema_version', 'session_id', 'trace_id', 'observation_id', 'step_id',
            'source_version', 'evidence_id', 'result_digest', 'current_source'}
_PAYLOAD = {'success', 'message', 'data', 'formatted', 'error', 'elapsed_ms',
            'warnings', 'evidence', 'artifacts', 'quality', 'status', 'provenance'}


def _identity(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) is not None


def _bounded(value):
    validate_json(value, max_bytes=128 * 1024, reason='rank_source_unavailable')


def _canonical(value):
    _bounded(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _checkpoint_value(checkpoint, run, trace):
    if (checkpoint['tool_name'] != _TOOL or checkpoint['status'] != 'succeeded'
            or checkpoint['workflow_version'] != run['workflow_version']
            or checkpoint['error_json'] is not None):
        raise ValueError('rank_source_unavailable')
    raw = checkpoint['output_json']
    if type(raw) is not str or len(raw.encode('utf-8')) > 128 * 1024:
        raise ValueError('rank_source_unavailable')
    value = json.loads(raw)
    _bounded(value)
    if (type(value) is not dict or set(value) != _PAYLOAD
            or value['success'] is not True or value['status'] != 'succeeded'
            or value['error'] is not None or value['artifacts'] != []
            or type(value['quality']) is not dict or value['quality'].get('validated') is not True
            or value['quality'].get('step_id') != checkpoint['step_id']
            or type(value['warnings']) is not list
            or any(type(w) is not str for w in value['warnings'])):
        raise ValueError('rank_source_unavailable')
    provenance = ToolProvenance.from_dict(value['provenance'])
    if (provenance.tool_name != _TOOL or provenance.tool_version != checkpoint['tool_version']
            or provenance.input_digest != checkpoint['input_hash']
            or provenance.demo_mode or provenance.fallback_used
            or value['quality'].get('demo_mode') or value['quality'].get('fallback_used')
            or (provenance.output_digest is not None
                and provenance.output_digest != EvidenceLedger.output_digest(value['data']))):
        raise ValueError('rank_source_unavailable')
    result = ToolResult(tool_name=_TOOL, success=True, message=value['message'], data=value['data'],
        formatted=value['formatted'], elapsed_ms=value['elapsed_ms'], warnings=value['warnings'],
        evidence=value['evidence'], quality=value['quality'], status=ObservationStatus.SUCCEEDED,
        provenance=provenance)
    evidence_id = EvidenceLedger(trace).register_tool_result(checkpoint['step_id'],
        checkpoint['input_hash'], result, binding_proof=value['quality'].get('binding_proof'))
    if evidence_id != value['quality'].get('evidence_id'):
        raise ValueError('rank_source_unavailable')
    # The complete raw/normalized receipt codec rejects edits before rank reuse.
    digest = validate_prediction_observation(value['data'], value['evidence'])
    if digest is None or not value['data']:
        raise ValueError('rank_source_unavailable')
    receipt_entry = next(v for v in value['evidence'] if 'prediction_receipt' in v)
    return value, digest, receipt_entry['input_smiles']


class RankedReverseEvidence:
    def __init__(self, store, reverse_tool):
        self.store, self.reverse_tool = store, reverse_tool

    def _read(self, trace, session):
        if not _identity(trace) or not _identity(session):
            raise ValueError('rank_source_unavailable')
        with closing(self.store._connect()) as connection, connection:
            connection.execute('BEGIN')
            run, _, _, latest, version = _source(connection, trace, session)
            checkpoints = [item for item in latest.values() if item['tool_name'] == _TOOL]
            if len(checkpoints) != 1:
                raise ValueError('rank_source_unavailable')
            checkpoint = checkpoints[0]
            value, digest, smiles = _checkpoint_value(checkpoint, run, trace)
            current = self.reverse_tool.validate_current_observation(
                value['data'], value['evidence'], input_data=smiles)
            pointer = dict(schema_version=1, session_id=session, trace_id=trace,
                observation_id=checkpoint['id'], step_id=checkpoint['step_id'], source_version=version,
                evidence_id=value['quality']['evidence_id'], result_digest=digest, current_source=current)
            _bounded(pointer)
            return pointer, value

    def capture(self, result, *, session_id):
        try:
            if (type(result) is not dict or result.get('status') not in {'completed', 'partial'}
                    or type(result.get('success')) is not bool
                    or (result['status'] == 'completed' and result['success'] is not True)):
                return None
            sequence = result.get('tool_result_sequence')
            if type(sequence) is not list or len(sequence) > 32:
                return None
            observations = [v for v in sequence if type(v) is dict and v.get('tool_name') == _TOOL]
            if len(observations) != 1:
                return None
            live = observations[0]
            _bounded(live)
            pointer, stored = self._read(result.get('trace_id'), session_id)
            expected = dict(step_id=pointer['step_id'], tool_name=_TOOL, **stored)
            if _canonical(live) != _canonical(expected):
                return None
            # Reject source/owner changes while validating the live projection.
            if self._read(pointer['trace_id'], session_id)[0] != pointer:
                return None
            return deepcopy(pointer)
        except Exception:
            return None

    def _current(self, context, session_id):
        _bounded(context)
        if (type(context) is not dict or set(context) != _POINTER
                or type(context['schema_version']) is not int or context['schema_version'] != 1
                or not _identity(session_id) or context['session_id'] != session_id):
            raise ValueError('rank_source_unavailable')
        pointer, value = self._read(context['trace_id'], session_id)
        if _canonical(pointer) != _canonical(context):
            raise ValueError('rank_source_unavailable')
        return pointer, value

    def resolve(self, context, *, session_id, rank=1):
        try:
            if type(rank) is not int or not 1 <= rank <= 10:
                return None
            pointer, value = self._current(context, session_id)
            if rank > len(value['data']):
                return None
            record = value['data'][rank - 1]
            resolved = deepcopy(dict(context=pointer, rank=rank, record_count=len(value['data']),
                                     source_row_index=record['row_index'], record=record))
            if self._current(context, session_id)[0] != pointer:
                return None
            return resolved
        except Exception:
            return None

    def render_prefix(self, context, *, session_id, count):
        try:
            if type(count) is not int or not 1 <= count <= 10:
                return None
            pointer, value = self._current(context, session_id)
            rows = value['data']
            shown = min(count, len(rows))
            def cell(item):
                # Escape HTML and Markdown controls before a chat Markdown sink.
                text = escape(str(item))
                return ''.join(f'&#{ord(c)};' if c in '|`*_[\\]' else ' ' if c in '\r\n' else c
                               for c in text)
            lines = [f'反向寻靶：展示前 {shown} 行；完整证据保留 {len(rows)} 行，顺序与原始评分未改变。', '',
                '来源：ChEMBL 本地完整指纹来源，Morgan/MACCS 二维结构相似度；非实验靶点确认、非结合能、非活性预测。',
                '局限：相似化合物可能具有不同作用谱；物种、测定条件及覆盖度限制仍需核验。', '',
                '| 排名 | 靶点 | 物种 | 综合相似度 | Morgan | MACCS | 匹配分子数 |',
                '|---|---|---|---|---|---|---|']
            for rank, row in enumerate(rows[:shown], 1):
                lines.append('| ' + ' | '.join(cell(v) for v in (rank, row['target_name'], row['organism'],
                    row['final_similarity'], row['morgan_similarity'], row['maccs_similarity'], row['similar_count'])) + ' |')
            lines.extend(['', '来源 trace：' + cell(pointer['trace_id']),
                          '证据摘要：' + cell(pointer['result_digest'])])
            rendered = '\n'.join(lines)
            if self._current(context, session_id)[0] != pointer:
                return None
            return rendered
        except Exception:
            return None
