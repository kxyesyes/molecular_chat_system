"""Bounded pure Pi/MedChat projection and one borrowed-model transaction.

The caller owns the Pi transcript, authorization, schema-repair budget and tool
execution. In particular, protocol_feedback pairs may only be issued by the
adapter/controller after schema rejection, never supplied by a model or client.
No scientific observation is interpreted, repaired or certified here.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

from src.agent.contracts.decision import (
    ClarifyDecision, DecisionProtocolError, FinishDecision, ToolDecision,
    parse_decision_json, decode_protocol_json, public_schema_issues,
)
from src.agent.decision_transport import DecisionResponse, _snapshot_messages
from src.agent.scientific_session.decision_bounds import validate_json
from src.agent.scientific_session.decision_policy import DecisionBoundaryError, schema_correction
from src.agent.contracts import AgentErrorCode, AgentExecutionError


_MAX_BYTES = 128 * 1024
_CALL_ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
_TOOL_NAME = re.compile(r'[a-z][a-z0-9_]{0,63}\Z')
_RESERVED = frozenset({'finish_task', 'clarify_task', 'protocol_feedback'})
_CLARIFY_HISTORY = '需要用户补充完整输入；尚未完成任务。'
_MESSAGE_FIELDS = {
    'system': {'role', 'content', 'timestamp'},
    'user': {'role', 'content', 'timestamp'},
    'assistant': {'role', 'content', 'api', 'provider', 'model', 'usage', 'stopReason', 'timestamp',
                  'durationMs', 'thinkingLevel'},
    'toolResult': {'role', 'content', 'toolCallId', 'toolName', 'isError', 'details', 'timestamp',
                   'durationMs', 'usage'},
}


def _bounded(value, *, max_bytes=_MAX_BYTES):
    try:
        validate_json(value, max_bytes=max_bytes, reason='invalid_pi_protocol')
    except DecisionBoundaryError:
        raise DecisionProtocolError('invalid_pi_protocol') from None


def _mode(mode):
    if type(mode) is not str or mode not in {'native', 'json'}:
        raise DecisionProtocolError('invalid_decision_options')


def _call_id(value):
    if type(value) is not str or not _CALL_ID.fullmatch(value):
        raise DecisionProtocolError('invalid_pi_call_id')


def _name(decision):
    if decision.action == 'tool':
        if decision.tool_name in _RESERVED:
            raise DecisionProtocolError('reserved_pi_tool_name')
        return decision.tool_name
    return {'finish': 'finish_task', 'clarify': 'clarify_task'}[decision.action]


def _validated_decision(decision):
    if type(decision) not in (ToolDecision, ClarifyDecision, FinishDecision):
        raise DecisionProtocolError('invalid_pi_decision')
    # model_copy/model_construct can bypass Pydantic validation. Bound plain
    # fields before model_dump, then use the same strict parser as the transport.
    _bounded(vars(decision), max_bytes=32768)
    parsed = parse_decision_json(json.dumps({'decision': decision.model_dump()}, ensure_ascii=False))
    _name(parsed)
    return parsed


def decision_tool_call(decision, call_id):
    """Return a Pi callback envelope; a proposal is still not execution authority."""
    _call_id(call_id)
    parsed = _validated_decision(decision)
    return {'toolCall': {'id': call_id, 'name': _name(parsed),
                         'arguments': {'decision': parsed.model_dump()}}}


def tool_definitions(allowed_tools):
    """JSON-only Pi descriptors for an already authorized, bounded name set.

    The deliberately broad decision property avoids cross-tool $ref resolution
    in Pi. Python's DecisionEnvelope parser remains the strict schema authority.
    protocol_feedback is internal plumbing, not a legal model proposal.
    """
    if type(allowed_tools) not in (list, tuple, set, frozenset) or len(allowed_tools) > 128:
        raise DecisionProtocolError('invalid_pi_tool_catalog')
    for name in allowed_tools:
        if type(name) is not str or not _TOOL_NAME.fullmatch(name) or name in _RESERVED:
            raise DecisionProtocolError('invalid_pi_tool_catalog')
    definitions = []
    for name in [*sorted(set(allowed_tools)), 'finish_task', 'clarify_task', 'protocol_feedback']:
        feedback = name == 'protocol_feedback'
        field = 'feedback' if feedback else 'decision'
        definitions.append({
            'name': name,
            'description': ('Internal adapter-issued schema rejection only; never propose this tool.'
                            if feedback else 'Submit one complete MedChat decision envelope for validation.'),
            'parameters': {
                'type': 'object', 'properties': {field: {'type': 'string' if feedback else 'object'}},
                'required': [field], 'additionalProperties': False,
            },
        })
    return definitions


def _text(content):
    if type(content) is str:
        return content
    if type(content) is not list:
        raise DecisionProtocolError('invalid_pi_text')
    parts = []
    for block in content:
        if (type(block) is not dict or set(block) != {'type', 'text'}
                or block['type'] != 'text' or type(block['text']) is not str):
            raise DecisionProtocolError('invalid_pi_text')
        parts.append(block['text'])
    return ''.join(parts)


def _message(message, *, trusted_system=False):
    if type(message) is not dict:
        raise DecisionProtocolError('invalid_pi_message')
    role = message.get('role')
    if type(role) is not str or role not in _MESSAGE_FIELDS:
        raise DecisionProtocolError('invalid_pi_message')
    allowed = _MESSAGE_FIELDS[role]
    if role == 'system' and trusted_system:
        # Official Pi 1.4 carries generated prompt/tool state in these fields.
        # They never become model instructions or authorization in MedChat.
        allowed = allowed | {'sections', 'toolsAdded', 'toolsRemoved'}
    if (not {'role', 'content'} <= set(message) or set(message) - allowed):
        raise DecisionProtocolError('invalid_pi_message')
    return role


def _safe_feedback(text):
    def reconstruct(issues):
        return schema_correction(SimpleNamespace(error=SimpleNamespace(
            code=AgentErrorCode.INVALID_OUTPUT, details={
                'reason': 'invalid_decision_schema', 'schema_issues': issues})))
    prefix = reconstruct([]).removesuffix('[]')
    if not text.startswith(prefix):
        raise DecisionProtocolError('invalid_pi_feedback')
    issues = decode_protocol_json(text[len(prefix):], max_bytes=8192)
    if (type(issues) is not list or issues != public_schema_issues(issues)
            or text != reconstruct(issues)):
        raise DecisionProtocolError('invalid_pi_feedback')
    return text


def _proposal(call):
    if type(call) is not dict or set(call) != {'type', 'id', 'name', 'arguments'}:
        raise DecisionProtocolError('invalid_pi_tool_call')
    _call_id(call['id'])
    if call['type'] != 'toolCall' or type(call['name']) is not str or type(call['arguments']) is not dict:
        raise DecisionProtocolError('invalid_pi_tool_call')
    arguments = call['arguments']
    if call['name'] == 'protocol_feedback':
        if (set(arguments) != {'feedback'} or type(arguments['feedback']) is not str
                or not arguments['feedback'].strip() or len(arguments['feedback']) > 8192):
            raise DecisionProtocolError('invalid_pi_feedback')
        _safe_feedback(arguments['feedback'])
        return None
    decision = parse_decision_json(json.dumps(arguments, ensure_ascii=False))
    if call['name'] != _name(decision):
        raise DecisionProtocolError('pi_tool_name_mismatch')
    return decision


def project_messages(pi_messages, *, mode, system_message=None, include_feedback=False,
                     compact_observations=False):
    """Project a trusted Pi transcript without mutation or lossy science rewrites.

    All calls (including terminals and internal feedback) require an immediately
    matched result. Historical clarification pairs become the legacy fixed
    marker only when followed by a new user; the current terminal pair is omitted
    for pre-checkpoint validation. A trusted system_message replaces Pi's leading
    generated system messages exactly, including Pi's system entry after seeded
    memory, without weakening content validation. Generated sections/tool
    declarations are ignored only with an explicit trusted replacement.
    Audit projections omit internal feedback by default (but validate its pair).
    Model requests opt in to fixed feedback in the trusted system prefix.
    """
    _mode(mode)
    if type(include_feedback) is not bool:
        raise DecisionProtocolError('invalid_pi_feedback_option')
    if type(compact_observations) is not bool:
        raise DecisionProtocolError('invalid_pi_compaction_option')
    # Raw Pi history includes system declaration deltas and terminal tool pairs.
    # Bound that envelope separately; _snapshot_messages below still enforces
    # the existing 64-message/128-KiB domain-model contract after projection.
    if type(pi_messages) is not list or not 1 <= len(pi_messages) <= 128:
        raise DecisionProtocolError('invalid_pi_messages')
    _bounded(pi_messages, max_bytes=512 * 1024)
    output = []
    if system_message is not None:
        _bounded(system_message)
        if (type(system_message) is not dict or set(system_message) != {'role', 'content'}
                or system_message['role'] != 'system' or type(system_message['content']) is not str):
            raise DecisionProtocolError('invalid_pi_system_message')
        output.append(dict(system_message))
    prefix_size = len(output)
    feedback = []
    seen = set()
    non_system = False
    index = 0
    while index < len(pi_messages):
        message = pi_messages[index]
        role = _message(message, trusted_system=system_message is not None)
        if role == 'system':
            if non_system and system_message is None:
                raise DecisionProtocolError('invalid_pi_system_position')
            content = _text(message['content'])
            if system_message is None:
                output.append({'role': 'system', 'content': content})
                prefix_size += 1
            index += 1
            continue
        non_system = True
        if role == 'toolResult':
            raise DecisionProtocolError('unmatched_pi_tool_result')
        content = message['content']
        has_call = (role == 'assistant' and type(content) is list
                    and any(type(block) is dict and block.get('type') == 'toolCall' for block in content))
        if not has_call:
            output.append({'role': role, 'content': _text(content)})
            index += 1
            continue
        if len(content) != 1:
            raise DecisionProtocolError('invalid_pi_tool_call')
        call = content[0]
        decision = _proposal(call)
        if call['id'] in seen:
            raise DecisionProtocolError('duplicate_call_id')
        seen.add(call['id'])
        if index + 1 >= len(pi_messages):
            raise DecisionProtocolError('missing_pi_tool_result')
        result = pi_messages[index + 1]
        if (_message(result) != 'toolResult' or result.get('toolCallId') != call['id']
                or result.get('toolName') != call['name']
                or ('isError' in result and type(result['isError']) is not bool)):
            raise DecisionProtocolError('unmatched_pi_tool_result')
        observation = _text(result['content'])
        index += 2
        if decision is None:
            # Never elevate callback text, schema exception text, or an invalid
            # raw proposal to instructions. The correction is fixed server text.
            if include_feedback and index == len(pi_messages):
                feedback.append({'role': 'system', 'content': _safe_feedback(call['arguments']['feedback'])})
        elif decision.action == 'clarify':
            # Pi records a current system/tool-state entry when restoring a
            # session. It is not a user turn and cannot replace clarification.
            next_turn = index
            while (system_message is not None and next_turn < len(pi_messages)
                   and _message(pi_messages[next_turn], trusted_system=True) == 'system'):
                next_turn += 1
            if next_turn < len(pi_messages):
                if _message(pi_messages[next_turn]) != 'user':
                    raise DecisionProtocolError('invalid_pi_clarification_history')
                output.append({'role': 'assistant', 'content': _CLARIFY_HISTORY})
        else:
            if compact_observations:
                # Only a validated, paired tool result is compacted for outbound
                # Web requests. Keep every token (including numeric spelling and
                # HTML escapes) intact; audit/checkpoint projections stay exact.
                decode_protocol_json(observation, max_bytes=64 * 1024, max_depth=32)
                observation = re.sub(r'("(?:\\.|[^"\\])*")|[ \t\r\n]+',
                                     lambda match: match.group(1) or '', observation)
            envelope = json.dumps({'decision': decision.model_dump()}, ensure_ascii=False)
            if mode == 'native':
                output.extend([
                    {'role': 'assistant', 'content': None, 'tool_calls': [{
                        'id': call['id'], 'type': 'function',
                        'function': {'name': 'agent_decision', 'arguments': envelope}}]},
                    {'role': 'tool', 'tool_call_id': call['id'], 'content': observation},
                ])
            else:
                output.extend([{'role': 'assistant', 'content': envelope},
                               {'role': 'user', 'content': observation}])
    output[prefix_size:prefix_size] = feedback
    # Reuse the actual transport's aggregate bounds, native pairing validator
    # and detached snapshot. JSON projections also satisfy its message grammar.
    return _snapshot_messages(output)[0]


class ModelProviderAdapter:
    """Borrow a model for one decide call per request; no retry or tool execution."""

    def __init__(self, model, mode):
        _mode(mode)
        self.model = model
        self.mode = mode

    async def request(self, messages, timeout_seconds, *, system_message=None,
                      temperature=None, max_tokens=None, input_budget=None,
                      compact_observations=False):
        try:
            projected = project_messages(messages, mode=self.mode, system_message=system_message,
                                         include_feedback=True, compact_observations=compact_observations)
        except DecisionProtocolError:
            # Feedback contributes to the actual model-input limit even when
            # omitted from the audit projection. Do not dispatch or classify
            # this pre-transport failure as an invalid model response.
            return DecisionResponse(None, AgentExecutionError(
                AgentErrorCode.INVALID_INPUT, 'Model decision input invalid',
                {'reason': 'invalid_decision_messages'}), None,
                {'mode': self.mode, 'request_attempts': 0})
        options = {}
        if temperature is not None:
            options['temperature'] = temperature
        if max_tokens is not None:
            options['max_tokens'] = max_tokens
        if input_budget is not None:
            options['input_budget'] = input_budget
        response = await self.model.decide(projected, mode=self.mode, timeout_seconds=timeout_seconds, **options)
        if response.decision is not None:
            _validated_decision(response.decision)
        return response
