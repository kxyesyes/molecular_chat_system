"""Validate settled Pi history before the domain controller claims a resume.

Old custom-loop checkpoints remain readable as history but cannot be executed.
No data is erased, tool is replayed, or database schema is migrated here.
"""
from __future__ import annotations

from types import SimpleNamespace

from src.agent.contracts import AgentErrorCode, AgentExecutionError
from src.agent.evidence import EvidenceLedger
from src.agent.scientific_session.decision_bounds import validate_json
from src.agent.scientific_session.decision_policy import schema_correction


PI_PROTOCOL_REVISION = 1


def validate_pi_checkpoint(saved, *, mode, system_message, allowed_tools=()):
    from .provider import project_messages, tool_definitions
    if (type(saved) is not dict or type(saved.get('pi_protocol_revision')) is not int
            or saved['pi_protocol_revision'] != PI_PROTOCOL_REVISION):
        raise ValueError('unsupported_pi_checkpoint')
    entries = saved.get('pi_entries')
    validate_json(entries, max_bytes=512 * 1024, reason='continuation_rejected')
    if type(entries) is not list or not 4 <= len(entries) <= 128:
        raise ValueError('invalid_pi_checkpoint')
    header = entries[0]
    if (type(header) is not dict or header.get('type') != 'session' or header.get('version') != 3
            or type(header.get('cwd')) is not str):
        raise ValueError('invalid_pi_header')
    parent = None
    seen = set()
    messages = []
    expected_tools = tool_definitions(sorted(allowed_tools))
    catalog = {tool['name']: tool for tool in expected_tools}
    registered = {}
    cwd = header['cwd'].replace('\\', '/')
    expected_sections = {'preamble': system_message['content'], 'cwd': '<cwd>\n' + cwd + '\n</cwd>'}
    sections = {}
    calls = []
    for entry in entries[1:]:
        if (type(entry) is not dict or type(entry.get('id')) is not str
                or not 1 <= len(entry['id']) <= 128 or entry['id'] in seen
                or entry.get('parentId') != parent):
            raise ValueError('invalid_pi_entry_chain')
        seen.add(entry['id'])
        parent = entry['id']
        kind = entry.get('type')
        if kind == 'model_change':
            if entry.get('provider') != 'medchat-stdio' or entry.get('modelId') != 'medchat-controlled':
                raise ValueError('pi_model_changed')
        elif kind == 'thinking_level_change':
            if entry.get('thinkingLevel') != 'off':
                raise ValueError('pi_thinking_changed')
        elif kind == 'message':
            message = entry.get('message')
            if type(message) is not dict:
                raise ValueError('invalid_pi_message')
            if message.get('role') == 'system':
                # Pi persists incremental system/tool declarations on resume,
                # not necessarily a second full catalog. Replay only the exact
                # approved values; net removals or new instructions are forbidden.
                additions = message.get('sections', {})
                tools = message.get('toolsAdded', [])
                removals = message.get('toolsRemoved', [])
                if (message.get('content') != '' or type(additions) is not dict
                        or any(key not in expected_sections or value != expected_sections[key]
                               for key, value in additions.items())
                        or type(tools) is not list or type(removals) is not list):
                    raise ValueError('pi_instructions_changed')
                # Pi's declaration comparison observes JSON property order.
                # SQLite's sorted-key serialization can therefore produce a
                # remove/add delta for the SAME approved interface on resume.
                added_names = {tool.get('name') for tool in tools if type(tool) is dict}
                for removed in removals:
                    if (type(removed) is not dict or set(removed) != {'name'}
                            or removed['name'] not in registered or removed['name'] not in added_names):
                        raise ValueError('pi_tool_catalog_changed')
                    registered.pop(removed['name'])
                sections.update(additions)
                for tool in tools:
                    if type(tool) is not dict or tool != catalog.get(tool.get('name')):
                        raise ValueError('pi_tool_catalog_changed')
                    registered[tool['name']] = tool
            if message.get('role') == 'assistant' and isinstance(message.get('content'), list):
                calls.extend(item for item in message['content'] if item.get('type') == 'toolCall')
            messages.append(message)
        else:
            raise ValueError('pi_entry_type_forbidden')
    if sections != expected_sections or registered != catalog:
        raise ValueError('pi_instructions_incomplete')
    if (not messages or messages[-1].get('role') != 'toolResult'
            or messages[-1].get('toolName') != 'clarify_task'
            or messages[-1].get('isError') is not False):
        raise ValueError('pi_checkpoint_not_settled')
    feedback = [call for call in calls if call.get('name') == 'protocol_feedback']
    failed = [call for call in saved.get('model_calls', []) if call.get('success') is False]
    if len(feedback) != saved.get('protocol_repairs') or len(feedback) != len(failed):
        raise ValueError('pi_repair_history_invalid')
    for call, record in zip(feedback, failed):
        expected = schema_correction(SimpleNamespace(error=AgentExecutionError(
            AgentErrorCode.INVALID_OUTPUT, 'Schema rejected', {
                'reason': 'invalid_decision_schema', 'schema_issues': record.get('schema_issues')})))
        if call.get('arguments') != {'feedback': expected}:
            raise ValueError('pi_repair_history_invalid')
    proposals = saved.get('proposals', [])
    actions = [call for call in calls if call.get('name') != 'protocol_feedback']
    if len(actions) != len(proposals):
        raise ValueError('pi_proposal_history_invalid')
    for call, proposal in zip(actions, proposals):
        expected_id = proposal['tool_call_id'] if mode == 'native' else proposal['decision_id']
        if (call.get('id') != expected_id or EvidenceLedger.output_digest(call.get('arguments'))
                != EvidenceLedger.output_digest({'decision': proposal['decision']})):
            raise ValueError('pi_proposal_history_invalid')
    projected = project_messages(messages, mode=mode, system_message=system_message)
    if EvidenceLedger.output_digest(projected) != EvidenceLedger.output_digest(saved.get('messages')):
        raise ValueError('pi_projection_mismatch')
    return entries
