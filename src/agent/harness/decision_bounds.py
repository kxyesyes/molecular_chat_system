"""Allocation-bounded plain JSON validation before copying, hashing or scanning.

Walk iteratively, cap depth/work, and count the UTF-8 JSON representation without
serializing it. Repeated aliases count each time (as on the wire); cycles fail.
"""
import math


def observation_value(result):
    """Bound fields before invoking any dataclass conversion/copy hooks."""
    from dataclasses import fields
    from src.agent.contracts import (
        ToolResult, ToolProvenance, WorkflowArtifact, AgentExecutionError,
        AgentErrorCode, ObservationStatus,
    )
    from .decision_policy import DecisionBoundaryError

    def shallow(value, cls):
        names = {f.name for f in fields(cls)}
        if type(value) is not cls or set(vars(value)) != names:
            raise DecisionBoundaryError('observation_too_large')
        return {name: getattr(value, name) for name in names}

    value = shallow(result, ToolResult)
    if type(result.success) is not bool:
        raise DecisionBoundaryError('invalid_observation_success')
    if type(result.status) is not ObservationStatus:
        raise DecisionBoundaryError('observation_too_large')
    value['status'] = result.status.value
    if result.error is not None:
        value['error'] = shallow(result.error, AgentExecutionError)
        if type(result.error.code) is not AgentErrorCode:
            raise DecisionBoundaryError('observation_too_large')
        value['error']['code'] = result.error.code.value
    if result.provenance is not None:
        value['provenance'] = shallow(result.provenance, ToolProvenance)
    if type(result.artifacts) is not list or len(result.artifacts) > 128:
        raise DecisionBoundaryError('observation_too_large')
    value['artifacts'] = [shallow(a, WorkflowArtifact) for a in result.artifacts]
    validate_json(value, max_bytes=64 * 1024, reason='observation_too_large')
    # Contract conversion is safe only after every nested field is bounded.
    return {'tool_name': result.tool_name, **result.to_legacy_dict()}


def validate_raw_observation(raw):
    from src.agent.contracts import ToolResult
    if type(raw) is ToolResult:
        observation_value(raw)
    else:
        validate_json(raw, max_bytes=64 * 1024, reason='observation_too_large')
        # Legacy conversion applies truthiness and drops status/error on its
        # success branch. Never let that promote contradictory observations.
        if type(raw) is dict and (
            ('success' in raw and type(raw['success']) is not bool)
            or (raw.get('success') is True and (
                raw.get('error') is not None
                or ('status' in raw and raw['status'] != 'succeeded')
            ))
        ):
            from .decision_policy import DecisionBoundaryError
            raise DecisionBoundaryError('conflicting_tool_result')


def validate_json(value, *, max_bytes, reason, max_depth=32, max_nodes=16384, html_safe=False):
    from .decision_policy import DecisionBoundaryError

    def reject():
        raise DecisionBoundaryError(reason)

    remaining = max_bytes
    nodes = 0
    active = set()
    stack = [('value', value, 0)]
    while stack:
        action, item, depth = stack.pop()
        if action == 'exit':
            active.remove(item)
            continue
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            reject()
        kind = type(item)
        if kind is str:
            if len(item) + 2 > remaining:
                reject()
            remaining -= 2
            for char in item:
                code = ord(char)
                if 0xD800 <= code <= 0xDFFF:
                    reject()
                remaining -= (6 if html_safe and char in '<>&`' else
                              2 if char in '"\\\b\f\n\r\t' else
                              6 if code < 32 else
                              1 if code < 128 else 2 if code < 2048 else
                              3 if code < 65536 else 4)
                if remaining < 0:
                    reject()
        elif item is None:
            remaining -= 4
        elif kind is bool:
            remaining -= 4 if item else 5
        elif kind is int:
            if item.bit_length() > 4096:
                reject()
            remaining -= len(str(item))
        elif kind is float:
            if not math.isfinite(item):
                reject()
            remaining -= len(str(item))
        elif kind in (dict, list):
            identity = id(item)
            if identity in active or len(item) > max_nodes - nodes:
                reject()
            # Default json.dumps separators: comma-space and colon-space.
            remaining -= 2 + max(0, len(item) - 1) * 2
            if kind is dict:
                remaining -= len(item) * 2
            if remaining < 0:
                reject()
            active.add(identity)
            stack.append(('exit', identity, depth))
            if kind is dict:
                for key, child in item.items():
                    if type(key) is not str:
                        reject()
                    stack.append(('value', child, depth + 1))
                    stack.append(('value', key, depth + 1))
            else:
                stack.extend(('value', child, depth + 1) for child in item)
        else:
            reject()
        if remaining < 0:
            reject()
