"""Owned chat request foundations. No preparation or execution admission yet.

An accepted target-search observation establishes which structure was offered in
this owner's conversation. It does NOT authenticate coordinate/component facts.
Official same-version source authentication must precede every preparation call.
These pointers are private server bookkeeping, never client bearer capabilities.
"""
from contextlib import closing
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import re
import time

from src.agent.contracts import ObservationStatus, ToolProvenance, ToolResult
from src.agent.evidence.ledger import EvidenceLedger
from src.agent.harness.decision_bounds import validate_json
from src.agent.persistence.scientific_references import _source
from src.target_search.remote_clients import _validate_uniprot_accession


_DOCKING_TEXT_REVISION = 'docking-whole-text-v1'
_DOCKING_FIELDS = ('receptor_ref', 'ligand_ref', 'center', 'size', 'exhaustiveness', 'num_modes')
_SPACE = r'[ \t\r\n]*'
_ACTION = r'(?:(?:做(?:一次)?|进行(?:一次)?)' + _SPACE + r')?'
_DOCK = r'(?:真实)?(?:分子对接|对接|(?:AutoDock[ \t]+Vina[ \t]+)?docking)'
_NATURAL_DOCKING = re.compile(
    r'(?:请)?' + _SPACE + r'(?:用|使用)' + _SPACE
    # A fixed label is syntax, not a chemical name resolver. Arbitrary text in
    # the label position must not conceal another requested operation.
    + r'(?:(?:阿司匹林|配体|分子)' + _SPACE + r')?'
    + r'(?P<smiles>[A-Za-z0-9@+\-\[\]()=#\\/%.:]{1,8192})' + _SPACE
    + r'(?:与|和)' + _SPACE + r'(?:上述' + _SPACE + r')?(?:PDB[ \t]+)?'
    + r'(?P<pdb_id>[1-9][A-Z0-9]{3})' + _SPACE + _ACTION + _DOCK
)
_BARE_DOCKING = re.compile(
    r'(?:(?:请)' + _SPACE + _ACTION + _DOCK
    + r'|(?:Please[ \t]+)?(?:run|perform)[ \t]+(?:real[ \t]+)?'
    + r'(?:AutoDock[ \t]+Vina[ \t]+)?docking)', re.IGNORECASE | re.ASCII
)
_POCKET_CLAUSE = '使用共晶配体定义口袋'
_NO_GUESS_CLAUSE = '缺少该信息先说明，不要猜坐标'
_TEXT_REF = re.compile(r'[A-Za-z0-9_-]{1,128}\Z', re.ASCII)
_SMILES_TOKENS = re.compile(
    r'(?:Cl|Br|[BCNOPSFIbcnops*]|[0-9@+\-()=#\\/%:.]|\[[A-Za-z0-9@+\-:*]+\])+',
    re.ASCII,
)
_JSON_NUMBER = r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?'
_VECTOR_TEXT = re.compile(r'\[' + _SPACE + _JSON_NUMBER +
    (r'[ \t\r\n]*,[ \t\r\n]*' + _JSON_NUMBER) * 2 + _SPACE + r'\]', re.ASCII)


def _text_rejection():
    return dict(assessed=False, reason_code='docking_request_clarification_required',
                input_ready=False, execution_authorized=False)


def _numeric_text_field(name, text):
    # Numeric fields have no nested JSON language. Bound and recognize the
    # scalar/triple before invoking the decoder, including on invalid inputs.
    grammar = _VECTOR_TEXT if name in {'center', 'size'} else re.compile(r'[0-9]+', re.ASCII)
    if len(text) > 256 or not grammar.fullmatch(text):
        raise ValueError('invalid_numeric_field')
    value = json.loads(text)
    if name in {'center', 'size'}:
        if (type(value) is not list or len(value) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in value)
                or (name == 'size' and any(not 0 < v <= 100 for v in value))):
            raise ValueError('invalid_numeric_field')
    elif (type(value) is not int or not 1 <= value <= (64 if name == 'exhaustiveness' else 50)):
        raise ValueError('invalid_numeric_field')
    return value


def assess_docking_request_text(query, *, enabled, enable_tools):
    """Consume a closed whole-request language before considering C preparation.

    This is neither a keyword classifier nor general natural-language support.
    Unrecognized prose, questions, multiple actions and unknown fields require
    clarification. Nothing is stripped from the original text/digest. Supported
    forms are an explicit single docking imperative with six named input fields,
    or one explicit SMILES/PDB pair and an optional co-crystal pocket request.

    The result is private lexical evidence only. References still need ownership,
    chemistry still needs D1/P6, the C proposal still needs span/value validation,
    and missing inputs still need explicit confirmation. Even all six textual
    fields never make input_ready true. This function performs no I/O, molecular
    parsing, source lookup, staging, model call or execution.
    """
    if (enabled is not True or enable_tools is not True or type(query) is not str
            or not 0 < len(query) <= 16384):
        return _text_rejection()
    try:
        encoded = query.encode('utf-8')
        if len(encoded) > 32768 or any(ord(c) < 32 and c not in '\t\r\n' for c in query):
            return _text_rejection()
        # Only explicit whitespace is padding; no Unicode normalization or
        # invisible-character deletion can turn a different request into this one.
        leading = re.match(_SPACE, query).end()
        match = _NATURAL_DOCKING.match(query, leading) or _BARE_DOCKING.match(query, leading)
        if match is None:
            return _text_rejection()
        inputs = {k: v for k, v in match.groupdict().items() if v is not None}
        if inputs:
            candidate = inputs['smiles']
            # Recognize chemical tokens without attempting chemical validity.
            # Slash/backslash stereobonds remain literal SMILES characters;
            # explicit URL, absolute/relative-file and drive syntax is not a
            # substitute for an owned attachment and is never interpreted as I/O.
            if (candidate.startswith(('.', '/', '\\')) or '://' in candidate
                    or re.match(r'[A-Za-z]:[\\/]', candidate, re.ASCII)
                    or not _SMILES_TOKENS.fullmatch(candidate)):
                return _text_rejection()
        position = match.end()
        fields, spans = {}, {}
        pocket = no_guess = False
        clauses = 0
        while position < len(query):
            whitespace = re.match(_SPACE, query[position:])
            position += whitespace.end()
            if position == len(query):
                break
            # A final full stop is permitted, but not an unfinished field separator.
            separator = query[position]
            if separator not in '。.;；':
                return _text_rejection()
            position += 1
            position += re.match(_SPACE, query[position:]).end()
            if position == len(query):
                if separator not in '。.':
                    return _text_rejection()
                break
            clauses += 1
            if clauses > 8:
                return _text_rejection()
            # Numbers may contain decimal dots; fields end only at semicolons,
            # Chinese full stops, or a final English full stop.
            end = re.search(r'[；;。]', query[position:])
            stop = position + end.start() if end else len(query)
            token = query[position:stop].rstrip(' \t\r\n')
            if end is None and token.endswith('.'):
                token = token[:-1].rstrip(' \t\r\n')
            if token == _POCKET_CLAUSE:
                if pocket:
                    return _text_rejection()
                pocket = True
            elif token == _NO_GUESS_CLAUSE:
                if not pocket or no_guess:
                    return _text_rejection()
                no_guess = True
            else:
                name_text, equals, raw_text = token.partition('=')
                name, text = name_text.rstrip(' \t'), raw_text.lstrip(' \t')
                if not equals or not re.fullmatch(r'[a-z_]+', name, re.ASCII) or not text:
                    return _text_rejection()
                if name not in _DOCKING_FIELDS or name in fields:
                    return _text_rejection()
                if name.endswith('_ref'):
                    from src.agent.persistence.redaction import contains_sensitive_text
                    if not _TEXT_REF.fullmatch(text) or contains_sensitive_text(text):
                        return _text_rejection()
                    value = text
                else:
                    value = _numeric_text_field(name, text)
                fields[name] = value
                value_start = len(name_text) + 1 + len(raw_text) - len(text)
                spans[name] = [position + value_start, position + len(token)]
            position = stop
        if pocket and any(name in fields for name in ('center', 'size')):
            return _text_rejection()  # Two conflicting pocket origins are not merged.
        if inputs and any(name in fields for name in ('receptor_ref', 'ligand_ref')):
            return _text_rejection()  # No silent replacement of the explicit pair.
        return dict(assessed=True, assessment_revision=_DOCKING_TEXT_REVISION,
            query_digest=hashlib.sha256(encoded).hexdigest(), inputs=inputs, fields=fields,
            field_spans=spans, pocket_rule_requested='co_crystal_reference' if pocket else None,
            missing_fields=[name for name in _DOCKING_FIELDS if name not in fields],
            input_ready=False, execution_authorized=False)
    except (ValueError, TypeError, OverflowError, UnicodeError, RecursionError):
        return _text_rejection()


_TARGET_TOOL = 'target_database_search'
_STRUCTURE_POINTER = {'schema_version', 'session_id', 'trace_id', 'observation_id',
    'step_id', 'source_version', 'evidence_id', 'result_digest'}
_CHECKPOINT_FIELDS = {'success', 'message', 'data', 'formatted', 'error', 'elapsed_ms',
    'warnings', 'evidence', 'artifacts', 'quality', 'status', 'provenance'}


def _identity(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) is not None


def _canonical(value):
    validate_json(value, max_bytes=256 * 1024, reason='docking_source_unavailable')
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _target_checkpoint(checkpoint, run, trace):
    if (checkpoint['status'] != 'succeeded' or checkpoint['tool_name'] != _TARGET_TOOL
            or checkpoint['workflow_version'] != run['workflow_version']
            or checkpoint['error_json'] is not None):
        raise ValueError('docking_source_unavailable')
    raw = checkpoint['output_json']
    if type(raw) is not str or len(raw.encode('utf-8')) > 256 * 1024:
        raise ValueError('docking_source_unavailable')
    value = json.loads(raw)
    _canonical(value)
    if (type(value) is not dict or set(value) != _CHECKPOINT_FIELDS
            or value['success'] is not True or value['status'] != 'succeeded'
            or value['error'] is not None or value['artifacts'] != []
            or type(value['data']) is not list or not 1 <= len(value['data']) <= 16
            or any(type(row) is not dict for row in value['data'])
            or type(value['quality']) is not dict or value['quality'].get('validated') is not True
            or value['quality'].get('lookup_status') != 'resolved'
            or value['quality'].get('step_id') != checkpoint['step_id']):
        raise ValueError('docking_source_unavailable')
    provenance = ToolProvenance.from_dict(value['provenance'])
    digest = EvidenceLedger.output_digest(value['data'])
    if (provenance.tool_name != _TARGET_TOOL or provenance.tool_version != checkpoint['tool_version']
            or provenance.input_digest != checkpoint['input_hash'] or provenance.demo_mode
            or provenance.fallback_used or value['quality'].get('demo_mode')
            or value['quality'].get('fallback_used')
            or (provenance.output_digest is not None and provenance.output_digest != digest)):
        raise ValueError('docking_source_unavailable')
    result = ToolResult(tool_name=_TARGET_TOOL, success=True, message=value['message'],
        data=value['data'], formatted=value['formatted'], elapsed_ms=value['elapsed_ms'],
        warnings=value['warnings'], evidence=value['evidence'], quality=value['quality'],
        status=ObservationStatus.SUCCEEDED, provenance=provenance)
    evidence_id = EvidenceLedger(trace).register_tool_result(checkpoint['step_id'],
        checkpoint['input_hash'], result, binding_proof=value['quality'].get('binding_proof'))
    if evidence_id != value['quality'].get('evidence_id'):
        raise ValueError('docking_source_unavailable')
    return value, digest


def _fresh(retrieved, expires, now):
    if any(type(v) is not str or not 0 < len(v) <= 128 for v in (retrieved, expires)):
        return False
    dates = [datetime.fromisoformat(v.replace('Z', '+00:00')) for v in (retrieved, expires)]
    if any(v.tzinfo is None or v.utcoffset() is None for v in dates):
        return False
    return dates[0].timestamp() <= now < dates[1].timestamp()


class OwnedDockingStructureEvidence:
    """Current owned S03 references; caller must capture from an actual chat turn."""

    def __init__(self, store, *, clock=time.time):
        self.store, self.clock = store, clock

    def _read(self, trace, session):
        if not _identity(trace) or not _identity(session):
            raise ValueError('docking_source_unavailable')
        with closing(self.store._connect()) as conn, conn:
            conn.execute('BEGIN')
            run, _, _, latest, version = _source(conn, trace, session)
            matches = [c for c in latest.values() if c['tool_name'] == _TARGET_TOOL]
            if len(matches) != 1:
                raise ValueError('docking_source_unavailable')
            checkpoint = matches[0]
            value, digest = _target_checkpoint(checkpoint, run, trace)
            pointer = dict(schema_version=1, session_id=session, trace_id=trace,
                observation_id=checkpoint['id'], step_id=checkpoint['step_id'],
                source_version=version, evidence_id=value['quality']['evidence_id'], result_digest=digest)
            _canonical(pointer)
            return pointer, value

    def capture(self, result, *, session_id):
        try:
            if (type(result) is not dict or result.get('status') != 'completed'
                    or result.get('success') is not True):
                return None
            sequence = result.get('tool_result_sequence')
            if type(sequence) is not list or len(sequence) > 32:
                return None
            matches = [r for r in sequence if type(r) is dict and r.get('tool_name') == _TARGET_TOOL]
            if len(matches) != 1:
                return None
            pointer, stored = self._read(result.get('trace_id'), session_id)
            if _canonical(matches[0]) != _canonical(dict(step_id=pointer['step_id'], tool_name=_TARGET_TOOL, **stored)):
                return None
            if self._read(pointer['trace_id'], session_id)[0] != pointer:
                return None
            return deepcopy(pointer)
        except Exception:
            return None

    def _current(self, context, session):
        _canonical(context)
        if (type(context) is not dict or set(context) != _STRUCTURE_POINTER
                or type(context['schema_version']) is not int or context['schema_version'] != 1
                or context['session_id'] != session):
            raise ValueError('docking_source_unavailable')
        pointer, value = self._read(context['trace_id'], session)
        if _canonical(pointer) != _canonical(context):
            raise ValueError('docking_source_unavailable')
        return pointer, value

    def resolve(self, context, *, session_id, pdb_id):
        try:
            if type(pdb_id) is not str or re.fullmatch(r'[0-9][A-Z0-9]{3}', pdb_id) is None:
                return None
            now = self.clock()
            if type(now) not in (float, int) or not math.isfinite(now):
                return None
            pointer, value = self._current(context, session_id)
            matches = []
            for target in value['data']:
                structures = target.get('recommended_structures')
                if type(structures) is not list or len(structures) > 100:
                    return None
                for structure in structures:
                    if type(structure) is not dict:
                        return None
                    if structure.get('structure_id') == pdb_id:
                        matches.append((target, structure))
            if len(matches) != 1:
                return None
            target, structure = matches[0]
            accession, organism = target.get('uniprot_id'), target.get('organism')
            if (type(accession) is not str or _validate_uniprot_accession(accession, 'UniProt') != accession
                    or target.get('source') != 'UniProt'
                    or target.get('source_record_id') != accession
                    or type(organism) is not str or not 0 < len(organism) <= 128
                    or any(target.get(k) is not False for k in ('stale', 'target_stale', 'structures_stale'))
                    or structure.get('stale') is not False or target.get('structure_evidence_status') != 'fresh'
                    or structure.get('source') != 'RCSB_PDB' or structure.get('structure_type') != 'experimental'
                    or structure.get('organism') != organism
                    or structure.get('source_url') != f'https://www.rcsb.org/structure/{pdb_id}'
                    or not _fresh(target.get('target_retrieved_at'), target.get('target_expires_at'), now)
                    or not _fresh(structure.get('retrieved_at'), structure.get('expires_at'), now)):
                return None
            # Return identities only. Stored filenames/download URLs are not
            # promoted to source-fetch instructions or mounted artifact paths.
            resolved = dict(context=pointer, target=dict(uniprot_id=accession, organism=organism),
                structure={k: structure[k] for k in ('structure_id', 'source', 'structure_type', 'organism', 'source_url')},
                input_ready=False, source_authentication='required_before_preparation')
            if self._current(context, session_id)[0] != pointer:
                return None
            current_time = self.clock()
            if (type(current_time) not in (int, float) or not math.isfinite(current_time)
                    or current_time < now
                    or not _fresh(target.get('target_retrieved_at'), target.get('target_expires_at'), current_time)
                    or not _fresh(structure.get('retrieved_at'), structure.get('expires_at'), current_time)):
                return None
            return deepcopy(resolved)
        except Exception:
            return None
