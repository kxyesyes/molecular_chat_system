"""Single-copy presentation DTOs, without changing scientific execution state."""
import re
from src.agent.contracts import AgentResult
from src.agent.scientific_session.decision_bounds import observation_value, validate_json
from src.agent.scientific_session.decision_policy import DecisionBoundaryError
from src.system.scientific_public_data import public_scientific_data


RESULT_FORMAT = 'summary_details_v1'
_STATUSES = frozenset({'completed', 'partial', 'failed', 'rejected', 'cancelled',
                       'timeout', 'unavailable', 'not_calculated', 'waiting_for_input'})


class ResultDetailsTooLarge(DecisionBoundaryError):
    """Only bounded public diagnostics, never a truncated publishable snapshot."""
    def __init__(self, summary_inputs):
        super().__init__('result_details_too_large')
        self.summary_inputs = summary_inputs


def accepted_ranking_ids(result):
    """None preserves non-Web contracts; Web ranks require a positive check."""
    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    if metadata.get('binding_profile') != 'medchat-web-v1':
        return None
    acceptance = metadata.get('task_acceptance', {})
    checks = acceptance.get('checks', []) if isinstance(acceptance, dict) else []
    accepted, rejected = set(), set()
    if isinstance(checks, list):
        for check in checks:
            if (isinstance(check, dict) and check.get('tool_name') == 'candidate_ranker'
                    and isinstance(check.get('evidence_ids'), list)):
                target = accepted if check.get('passed') is True and check.get('reason_codes') == [] else rejected
                target.update(x for x in check['evidence_ids'] if isinstance(x, str))
    return accepted - rejected


def hide_unaccepted_ranking(value):
    """Presentation only: keep the source observation intact in the ledger."""
    message = '候选排序结果未通过或尚未完成校验，未展示排名。'
    value.update(success=False, status='failed', message=message, data=None, formatted='',
                 evidence=[], artifacts=[], error={'code': 'validation_error', 'message': message, 'details': {}})
    _restrict_ranking_quality(value)
    return value


def _restrict_ranking_quality(value):
    quality = value.get('quality')
    value['quality'] = ({key: quality[key] for key in ('evidence_id', 'step_id')
                         if isinstance(quality.get(key), str) and len(quality[key]) <= 128}
                        if isinstance(quality, dict) else {})


def scrub_unaccepted_ranking_failure(value):
    """Retain the concrete error, but not producer-owned result copies."""
    value['evidence'], value['artifacts'] = [], []
    if isinstance(value.get('error'), dict):
        value['error'] = {**value['error'], 'details': {}}
    if 'formatted' in value:
        value['formatted'] = ''
    _restrict_ranking_quality(value)
    return value


def scrub_run_error(result, error):
    accepted = accepted_ranking_ids(result)
    if (accepted is not None and isinstance(error, dict)
            and any(item.tool_name == 'candidate_ranker'
                    and item.quality.get('evidence_id') not in accepted
                    for item in result.tool_results)):
        return {**error, 'details': {}}
    return error


def _capacity_summary_inputs(payload):
    from .agent_result_presentation import partial_projection
    diagnostic = partial_projection(payload)
    # The existing diagnostic projection bounds messages/warnings and preserves
    # concrete partial failures. No scientific data or snapshot format is copied.
    public, changed = public_scientific_data({
        'trace_id': payload['trace_id'], 'status': payload['status'],
        'error': diagnostic['error'], 'warnings': diagnostic['warnings'],
        'tool_result_sequence': [dict(
            tool_name=step['tool_name'], step_id=step['step_id'],
            status=step['status'], success=False, message=step['message'],
            error={'code': step['error_code'], 'message': step['message']}
                  if step['error_code'] else None,
        ) for step in diagnostic['failed_steps']],
    })
    if public.get('trace_id') != payload['trace_id']:
        raise ValueError('result_details_invalid')
    public['display_redacted'] = changed
    return public


def project_result_details(result, *, summary, status, error, warnings):
    """Project verified ToolResults once; never parse assistant prose as data."""
    if (type(result) is not AgentResult or type(result.tool_results) is not list
            or len(result.tool_results) > 256 or type(result.trace_id) is not str
            or re.fullmatch(r'[A-Za-z0-9_-]{1,128}', result.trace_id) is None or status not in _STATUSES
            or type(summary) is not str or type(warnings) is not list
            or any(type(item) is not str for item in warnings)
            or (error is not None and type(error) is not dict)):
        raise ValueError('result_details_invalid')
    sequence = []
    accepted = accepted_ranking_ids(result)
    for index, observation in enumerate(result.tool_results, 1):
        value = observation_value(observation)
        # The legacy formatted rendering duplicates data and is never authority.
        value.pop('formatted', None)
        value['step_id'] = str(observation.quality.get('step_id') or f'{observation.tool_name}:{index}')
        if (observation.tool_name == 'candidate_ranker' and accepted is not None
                and observation.quality.get('evidence_id') not in accepted):
            if value.get('data') is not None or value.get('success') is True:
                hide_unaccepted_ranking(value)
                value.pop('formatted', None)
            else:
                scrub_unaccepted_ranking_failure(value)
        validate_json(value, max_bytes=64 * 1024, max_depth=32, reason='result_details_invalid')
        sequence.append(value)
    payload = {'format': RESULT_FORMAT, 'trace_id': result.trace_id, 'status': status,
               'tool_result_sequence': sequence, 'error': scrub_run_error(result, error), 'warnings': warnings,
               'display_redacted': False}
    # Aggregated warnings can exceed the node/byte budget even when every
    # observation fits. Check every scalar's syntax before aggregate capacity;
    # six bytes per character covers JSON escaping without hiding invalid tails.
    for warning in warnings:
        validate_json(warning, max_bytes=6 * len(warning) + 2, reason='result_details_invalid')
    # Every observation and warning is valid. Check the remaining envelope too,
    # then let the unchanged aggregate gate classify display capacity below.
    validate_json({**payload, 'tool_result_sequence': [], 'warnings': []}, max_bytes=4 * 1024 * 1024,
                  max_nodes=65536, reason='result_details_invalid')
    try:
        public, changed = public_scientific_data(payload, capacity_reason='result_details_too_large')
    except DecisionBoundaryError as exc:
        if str(exc) != 'result_details_too_large':
            raise
        raise ResultDetailsTooLarge(_capacity_summary_inputs(payload)) from None
    if public.get('trace_id') != result.trace_id:
        raise ValueError('result_details_invalid')
    public['display_redacted'] = changed
    return public
