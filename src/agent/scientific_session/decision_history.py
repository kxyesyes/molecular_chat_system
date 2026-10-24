"""Bounded server-owned ordinary text history; no Web state or authority roles."""
from dataclasses import dataclass
import json

from src.agent.contracts import RunOutcome
from src.agent.persistence.redaction import contains_secret_material
from .decision_bounds import validate_json
from .decision_policy import DecisionBoundaryError


MAX_HISTORY_PAIRS = 20
MAX_HISTORY_BYTES = 16 * 1024


def _pair_shape(pair):
    if (type(pair) is not dict or len(pair) != 2
            or any(type(key) is not str for key in pair)
            or set(pair) != {'user', 'assistant'}
            or any(type(value) is not str for value in pair.values())):
        raise DecisionBoundaryError('invalid_history_pair')


def history_pairs(memory):
    """Validate before copying/scanning; serialized size uses default JSON spacing."""
    if type(memory) is not list or len(memory) > MAX_HISTORY_PAIRS:
        raise DecisionBoundaryError('invalid_history')
    for pair in memory:
        _pair_shape(pair)
    validate_json(memory, max_bytes=MAX_HISTORY_BYTES, reason='invalid_history')
    if contains_secret_material(memory):
        raise DecisionBoundaryError('sensitive_history')
    return [dict(pair) for pair in memory]


def history_prefix(system_message, query, memory, *, request_kind):
    """Exact frozen run prefix shared with pre-CAS continuation validation."""
    if type(request_kind) is not str or request_kind not in {'chat', 'scientific'}:
        raise ValueError('request_kind must be explicit')
    pairs = history_pairs(memory)
    if (type(system_message) is not dict or set(system_message) != {'role', 'content'}
            or system_message['role'] != 'system' or type(system_message['content']) is not str
            or type(query) is not str):
        raise DecisionBoundaryError('invalid_history_prefix')
    validate_json(system_message, max_bytes=128 * 1024, reason='invalid_history_prefix')
    validate_json(query, max_bytes=16 * 1024, reason='invalid_history_prefix')
    messages = [dict(system_message)]
    if request_kind == 'chat':
        for pair in pairs:
            messages.extend([{'role': 'user', 'content': pair['user']},
                             {'role': 'assistant', 'content': pair['assistant']}])
    messages.append({'role': 'user', 'content': query})
    return messages


@dataclass(frozen=True)
class HistoryUpdate:
    memory: list[dict[str, str]]
    omission: str | None = None
    evicted_pairs: int = 0


def retain_history_pair(memory, *, user, assistant, request_kind, outcome,
                        safely_displayed, waiting_for_input=False, has_tool_content=False):
    """Pure retention decision from explicit trusted facts, never inferred status.

    A future Web caller must provide the actual display/outcome/tool facts. This
    helper neither observes a socket nor treats model-authored flags as authority.
    """
    if (type(request_kind) is not str or request_kind not in {'chat', 'scientific'}
            or type(outcome) is not RunOutcome
            or any(type(flag) is not bool for flag in (
                safely_displayed, waiting_for_input, has_tool_content))):
        raise ValueError('invalid_history_retention_facts')
    kept = history_pairs(memory)
    pair = {'user': user, 'assistant': assistant}
    _pair_shape(pair)
    if (request_kind != 'chat' or outcome != RunOutcome.COMPLETED or not safely_displayed
            or waiting_for_input or has_tool_content):
        return HistoryUpdate(kept, 'history_pair_ineligible')
    try:
        validate_json([pair], max_bytes=MAX_HISTORY_BYTES, reason='history_pair_too_large')
    except DecisionBoundaryError:
        return HistoryUpdate(kept, 'history_pair_too_large')
    if contains_secret_material(pair):
        return HistoryUpdate(kept, 'history_pair_sensitive')
    kept.append(pair)
    evicted = 0
    while (len(kept) > MAX_HISTORY_PAIRS
           or len(json.dumps(kept, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_HISTORY_BYTES):
        kept.pop(0)
        evicted += 1
    return HistoryUpdate(kept, evicted_pairs=evicted)
