"""Small, opt-in result frames; full scientific data stays in a detail snapshot."""
import json
import re

from src.agent.contracts import AgentResult, AgentExecutionError, AgentErrorCode, RunOutcome
from src.agent.scientific_session.decision_bounds import validate_json
from src.system.scientific_public_data import public_scientific_data
from .result_details_projection import RESULT_FORMAT, ResultDetailsTooLarge, project_result_details


MAX_SUMMARY_BYTES = 24 * 1024
MAX_SUMMARY_FRAME_BYTES = 64 * 1024
TOOL_LABELS = {
    'property_calculator': '分子性质计算', 'drug_likeness_assessment': '类药性评估',
    'admet_predictor': 'ADMET 预测', 'activity_predictor': '活性预测',
    'target_database_search': '靶点检索', 'reverse_target_predictor': '反向寻靶',
    'llm_molecular_generator': '分子生成', 'candidate_ranker': '候选排序',
    'rag_search': '知识库检索', 'molecular_docking': '分子对接',
}
_STATUS_LABELS = {'partial': '部分完成', 'failed': '失败', 'rejected': '未执行',
                  'cancelled': '已取消', 'timeout': '超时', 'unavailable': '不可用',
                  'not_calculated': '未计算', 'skipped': '已跳过', 'skipped_precondition': '已跳过'}
# Exact domain informational messages (src/admet/backend.py). Contract parity
# with the validator is tested; do not classify arbitrary warnings by keywords.
_ADMET_INFORMATIONAL_WARNINGS = frozenset({
    'ADMET-AI outputs are model predictions; they are not experimental results.',
    'Physicochemical endpoints are calculated by RDKit inside ADMET-AI.',
    'Risk summary is a screening heuristic, not a safety conclusion.',
})
_ACTIVITY_INFORMATIONAL_SUFFIX = '；模型预测不等同于实验结论。'


def _append_summary_line(lines, candidate):
    """Avoid repeating one failure reason through issue/warning/body paths."""
    if not candidate:
        return
    def activity_reason(value):
        if not value.startswith('活性预测：'):
            return None
        _, separator, remainder = value.partition('。')
        return remainder if separator else None

    for existing in lines:
        if candidate == existing:
            return
        candidate_reason = activity_reason(candidate)
        existing_reason = activity_reason(existing)
        if (candidate_reason and existing_reason
                and candidate_reason == existing_reason):
            return
        if candidate_reason and candidate_reason == existing:
            return
        if existing_reason and existing_reason == candidate:
            return
        if (candidate.startswith('活性预测：')
                or existing.startswith('活性预测：')):
            if candidate in existing or existing in candidate:
                return
    lines.append(candidate)


def _bounded_summary(text, max_bytes=MAX_SUMMARY_BYTES, *, details_available=True):
    encoded = text.encode('utf-8')
    # Each result frame contains two summary strings. Bound their actual JSON
    # string encoding too, leaving room for the small state/error envelope.
    escaped_bytes = len(json.dumps(text, ensure_ascii=False).encode('utf-8'))
    if len(encoded) <= max_bytes and escaped_bytes <= max_bytes:
        return text
    notice = ('\n\n摘要仅展示部分内容；完整结果请展开“查看明细”。' if details_available
              else '\n\n摘要仅展示部分内容。')
    budget = max_bytes - len(json.dumps(notice, ensure_ascii=False).encode('utf-8')) - 2
    if budget < 0:
        raise ValueError('result_display_invalid')
    low, high = 0, min(len(text), budget)
    while low < high:
        middle = (low + high + 1) // 2
        if len(json.dumps(text[:middle], ensure_ascii=False).encode('utf-8')) <= budget:
            low = middle
        else:
            high = middle - 1
    prefix = text[:low]
    if '\n' in prefix:
        prefix = prefix.rsplit('\n', 1)[0]
    return prefix + notice


def _status(result):
    metadata = result.metadata
    if ('waiting_for_input' in metadata and type(metadata['waiting_for_input']) is not bool
            or 'stop_reason' in metadata and (type(metadata['stop_reason']) is not str
                or re.fullmatch(r'[a-z0-9_]{1,128}', metadata['stop_reason']) is None)
            or 'continuation_id' in metadata and (type(metadata['continuation_id']) is not str
                or re.fullmatch(r'[a-f0-9]{32}', metadata['continuation_id']) is None)
            or metadata.get('waiting_for_input') is True and 'continuation_id' not in metadata):
        raise ValueError('result_display_invalid')
    if metadata.get('waiting_for_input') is True:
        return 'waiting_for_input'
    if result.outcome is not None:
        if type(result.outcome) is not RunOutcome:
            raise ValueError('result_display_invalid')
        return result.outcome.value
    return 'completed' if result.success else 'partial' if result.partial else 'failed'


def prepare_summary(handler, result, *, business_view=None):
    """Build a small public envelope and detached full details before storage."""
    from .agent_result_presentation import partial_projection
    from .decision_chat import web_scientific_display, scientific_display_answer

    if (type(result) is not AgentResult or type(result.metadata) is not dict
            or type(result.warnings) is not list or type(result.success) is not bool
            or result.metadata.get('binding_profile') != 'medchat-web-v1'):
        raise ValueError('result_display_invalid')
    status = _status(result)
    validate_json(result.metadata, max_bytes=4 * 1024 * 1024, max_nodes=65536,
                  reason='result_display_invalid')
    error = None
    if result.error is not None:
        if type(result.error) is not AgentExecutionError or type(result.error.code) is not AgentErrorCode:
            raise ValueError('result_display_invalid')
        validate_json({'message': result.error.message, 'details': result.error.details},
                      max_bytes=64 * 1024, reason='result_display_invalid')
        error = result.error.to_dict()
        from .result_details_projection import scrub_run_error
        error = scrub_run_error(result, error)
    warnings = result.warnings
    if business_view is not None:
        validate_json(business_view, max_bytes=4 * 1024 * 1024, max_nodes=65536,
                      reason='result_display_invalid')
        warnings = [*warnings, *business_view.get('warnings', [])]
        if business_view.get('status') == 'partial' and status == 'completed':
            status = 'partial'
    details_error = None
    try:
        details = project_result_details(result, summary='', status=status, error=error, warnings=warnings)
    except ResultDetailsTooLarge as exc:
        # This view contains bounded diagnostics only, never a detail artifact.
        details_error = 'too_large'
        details = exc.summary_inputs
    has_observations = bool(result.tool_results)
    terminal_failure = status not in {'completed', 'partial', 'waiting_for_input'}
    validate_json({'answer': result.final_answer, 'message': result.message},
                  max_bytes=4 * 1024 * 1024, reason='result_display_invalid')
    body_without_details = None
    if terminal_failure:
        body = handler._agent_failure_content({**details,
            'final_answer': result.final_answer or result.message, 'success': False,
            'metadata': result.metadata})
        if (business_view is not None and business_view.get('mixed_history_failure') is True
                and type(business_view.get('summary_answer')) is str):
            body = business_view['summary_answer'] + '\n\n' + body
    elif status == 'waiting_for_input' or not has_observations:
        body = result.final_answer or result.message
        if (status != 'waiting_for_input' and business_view is not None
                and type(business_view.get('summary_answer')) is str):
            body = business_view['summary_answer']
    else:
        if business_view is not None and type(business_view.get('summary_answer')) is str:
            body = business_view['summary_answer']
            if type(business_view.get('summary_answer_without_details')) is str:
                body_without_details = business_view['summary_answer_without_details']
        elif (status == 'completed' and result.success and not result.warnings
              and all(item.tool_name == 'property_calculator' and not item.warnings
                      for item in result.tool_results)):
            body = scientific_display_answer(result.tool_results, concise=True)
        else:
            try:
                body = web_scientific_display(result, concise=True) if result.outcome is not None else None
            except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
                body = None  # Legacy prose cannot discard verified status/diagnostics.
            if body:
                # This standalone line is emitted by the concise ADMET renderer,
                # never by the business/user-text branch or a scientific cell.
                body_without_details = '\n'.join(line for line in body.split('\n')
                    if line != '完整端点请展开“查看明细”。')
        if not body:
            if status == 'partial':
                # A failed observation is still an observation, not proof that
                # a scientific result exists. Specific diagnostics follow below.
                body = '' if details_error else '本轮记录请展开“查看明细”。'
                body_without_details = ''
            elif details_error:
                body = '科学计算结果已产生。'
            else:
                body = '科学计算结果已产生；完整结果请展开“查看明细”。'
            if body_without_details is None:
                body_without_details = '科学计算结果已产生。'
        if business_view is not None and type(business_view.get('prefix')) is str:
            body = business_view['prefix'] + '\n\n' + body
            if body_without_details is not None:
                body_without_details = business_view['prefix'] + '\n\n' + body_without_details

    identifiers = {result.trace_id: '当前任务'}
    optional_failure_prefixes = []
    for index, item in enumerate(result.tool_results, 1):
        step_id = str(item.quality.get('step_id') or f'{item.tool_name}:{index}')
        identifiers[step_id] = '该步骤'
        optional_failure_prefixes.append((
            f'Optional step {step_id} ({item.tool_name}) failed: ',
            TOOL_LABELS.get(item.tool_name, '计算步骤') + '：失败。'))
        evidence_id = item.quality.get('evidence_id')
        if type(evidence_id) is str and evidence_id:
            identifiers[evidence_id] = '该记录'

    def human_text(text):
        rank_warning = re.fullmatch(r'ADMET evidence was absent or unusable for (\d+) ranked candidate\(s\)', text)
        if rank_warning:
            return f'{rank_warning.group(1)} 个候选缺少可用的 ADMET 证据。'
        rank_warning = re.fullmatch(r'Activity evidence was absent, demo, fallback, or unusable for (\d+) ranked candidate\(s\)', text)
        if rank_warning:
            return f'{rank_warning.group(1)} 个候选缺少可用的活性证据。'
        # This exact executor wrapper is bound to a verified observation. Keep
        # its concrete reason and the original warning in the detail snapshot.
        for prefix, label in optional_failure_prefixes:
            if text.startswith(prefix):
                text = label + text[len(prefix):]
                break
        for identifier, label in identifiers.items():
            # Identical text may be a measurement or a molecular structure.
            # Only explicitly labelled technical fields establish an ID context.
            if identifier:
                text = re.sub(r'\b(?:trace_id|step_id|evidence_id)\s*[:=：]\s*[`\"]?'
                              + re.escape(identifier) + r'(?![\w.])[`\"]?',
                              lambda match: label, text)
        for name, label in TOOL_LABELS.items():
            text = text.replace(name, label)
        return text

    safe_body, body_redacted = public_scientific_data(body)
    # Ordinary chat text is not a source of scientific evidence; audit-shaped
    # fallbacks from empty scientific finishes must not become raw JSON UI.
    if safe_body.lstrip().startswith('{'):
        try:
            audit = json.loads(safe_body)
        except (ValueError, RecursionError):
            audit = None
        if isinstance(audit, dict) and isinstance(audit.get('acceptance'), dict):
            safe_body = '本轮未得到可展示的科学结果。'
    safe_body_without_details = (public_scientific_data(body_without_details)[0]
        if body_without_details is not None else safe_body)

    diagnostic = partial_projection({**details, 'metadata': result.metadata})
    internal_view_warnings = {'rank_source_unavailable'} if (business_view is not None
        and 'rank_source_unavailable' in business_view.get('warnings', [])) else set()
    public_warnings = [human_text(item) for item in handler._sanitize_agent_warnings(
        [item for item in details['warnings']
         if item not in _ADMET_INFORMATIONAL_WARNINGS and item not in internal_view_warnings],
        reject_claims=terminal_failure)]
    issues = []
    generic_partial_issue = False
    if status == 'partial':
        if result.metadata.get('stop_reason') == 'activity_target_conflict_or_unknown':
            issues.append('活性预测的靶点不受支持或存在冲突；本次未进行活性预测。')
        for step in diagnostic['failed_steps']:
            name = TOOL_LABELS.get(step.get('tool_name'), '计算步骤')
            state = _STATUS_LABELS.get(step.get('status'), '未完成')
            message = step.get('message') or ''
            if step.get('tool_name') == 'activity_predictor' and step.get('status') == 'partial':
                message = message.removesuffix(_ACTIVITY_INFORMATIONAL_SUFFIX)
            message = human_text(message)
            issues.append(f'{name}：{state}。{message}')
        if not issues and not public_warnings:
            if has_observations and not details_error:
                issues.append('部分请求尚未完成，详情见明细。')
                generic_partial_issue = True
            elif not safe_body:
                issues.append('本轮仅部分请求完成。')
    # Put specific issues first so a long result table cannot hide them.
    lines = []
    for line in [*issues, *public_warnings]:
        _append_summary_line(lines, line)
    if safe_body:
        _append_summary_line(lines, human_text(safe_body))
    full_content = '\n\n'.join(lines)
    # Freeze both bounded variants before publication. Only our generated
    # guidance differs; supplied prose/warnings are never searched or rewritten.
    unavailable_issues = (issues[:-1] + ['部分请求尚未完成。'] if generic_partial_issue else issues)
    unavailable_lines = []
    for line in [*unavailable_issues, *public_warnings]:
        _append_summary_line(unavailable_lines, line)
    if safe_body_without_details:
        _append_summary_line(unavailable_lines, human_text(safe_body_without_details))
    unavailable_full = '\n\n'.join(unavailable_lines)
    unavailable_content = _bounded_summary(unavailable_full, details_available=False)
    details_may_publish = not details_error and has_observations
    if not details_may_publish:
        full_content = unavailable_full
    content = _bounded_summary(full_content, details_available=details_may_publish)
    metadata = {'binding_profile': 'medchat-web-v1'}
    if business_view is not None and business_view.get('summary_kind') == 'reverse_target_rank_v1':
        metadata['display_kind'] = 'reverse_target_rank_v1'
    for key in ('waiting_for_input', 'continuation_id', 'stop_reason'):
        if key in result.metadata:
            metadata[key] = result.metadata[key]
    validate_json(metadata, max_bytes=4096, reason='result_display_invalid')
    metadata, _ = public_scientific_data(metadata)
    retrievals = [row for row in result.tool_results if row.tool_name == 'rag_search'
                  and row.status.value != 'not_calculated']
    metadata['retrieval_performed'] = (False if not retrievals else True if any(
        row.status.value in {'succeeded', 'partial'} and row.error is None
        for row in retrievals) else None)
    public_error = diagnostic['error']
    if public_error:
        public_error = {key: human_text(value) for key, value in public_error.items()}
    frame = {'type': 'agent_result', 'result_format': RESULT_FORMAT, 'trace_id': result.trace_id,
             'success': status == 'completed' and result.success and result.error is None,
             'partial': status == 'partial', 'status': status, 'final_answer': content,
             'display_answer': content, 'metadata': metadata, 'error': public_error,
             'warnings': public_warnings, 'details': None,
             'details_state': 'pending' if has_observations else 'none',
             'display_redacted_or_truncated': bool(body_redacted or details['display_redacted']
                                                   or content != full_content)}
    if details_error:
        frame['_details_error'] = details_error  # Consumed before wire encoding.
    if details_may_publish:
        frame['_unavailable_answer'] = unavailable_content  # Also consumed before encoding.
        frame['display_redacted_or_truncated'] |= unavailable_content != unavailable_full
    return frame, details if has_observations and not details_error else None


def summary_frames(handler, result, *, business_view=None, details_reference=None, details_error=None):
    frame, details = prepare_summary(handler, result, business_view=business_view)
    return encode_summary(frame, details_reference=details_reference, details_error=details_error)


def encode_summary(prepared_frame, *, details_reference=None, details_error=None):
    """Finish one frozen projection after snapshot publication; never reproject."""
    frame = dict(prepared_frame)
    details_error = frame.pop('_details_error', None) or details_error
    unavailable_answer = frame.pop('_unavailable_answer', None)
    if details_error == 'too_large':
        details_reference = None
    if frame['details_state'] == 'pending':
        if details_reference is not None:
            if (type(details_reference) is not dict
                    or set(details_reference) != {'view_id', 'revision', 'byte_length'}
                    or type(details_reference['view_id']) is not str
                    or re.fullmatch(r'[a-f0-9]{32}', details_reference['view_id']) is None
                    or type(details_reference['revision']) is not str
                    or re.fullmatch(r'[a-f0-9]{64}', details_reference['revision']) is None
                    or type(details_reference['byte_length']) is not int
                    or not 0 < details_reference['byte_length'] <= 4 * 1024 * 1024):
                raise ValueError('result_display_invalid')
            frame.update(details=dict(details_reference), details_state='available')
        else:
            frame['details_state'] = 'unavailable'
            if unavailable_answer is not None:
                frame.update(final_answer=unavailable_answer, display_answer=unavailable_answer)
            notice = ('结果明细超过展示容量，请缩小请求范围。' if details_error == 'too_large'
                      else '结果明细暂不可用。')
            content = _bounded_summary(notice + '\n\n' + frame['display_answer'],
                                       details_available=False)
            frame['display_redacted_or_truncated'] |= content != notice + '\n\n' + frame['display_answer']
            frame.update(final_answer=content, display_answer=content)
    # Measure the actual variable envelope, including Unicode warnings/errors
    # and the published reference. Reserve sender correlation metadata as well.
    overhead = len(json.dumps({**frame, 'final_answer': '', 'display_answer': ''},
        ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8'))
    content = _bounded_summary(frame['display_answer'],
        min(MAX_SUMMARY_BYTES, (MAX_SUMMARY_FRAME_BYTES - overhead - 1024) // 2),
        details_available=frame['details_state'] == 'available' and frame['details'] is not None)
    frame['display_redacted_or_truncated'] |= content != frame['display_answer']
    frame.update(final_answer=content, display_answer=content)
    complete = {'type': 'complete', 'result_format': RESULT_FORMAT, 'trace_id': frame['trace_id'],
        'status': frame['status'], 'content': frame['display_answer'],
        'continuation_id': frame['metadata'].get('continuation_id'),
        'display_redacted_or_truncated': frame['display_redacted_or_truncated']}
    if frame['status'] not in {'completed', 'waiting_for_input'}:
        complete.update(partial=frame['partial'], error=frame['error'], warnings=frame['warnings'])
    encoded = [json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
               for value in (frame, complete)]
    if any(len(value.encode('utf-8')) > MAX_SUMMARY_FRAME_BYTES for value in encoded):
        raise ValueError('result_display_invalid')
    return encoded
