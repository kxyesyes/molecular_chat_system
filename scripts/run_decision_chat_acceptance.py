"""Small isolated real-model acceptance. No .env loading or production changes."""
import argparse
import asyncio
import json
import os
import stat
from pathlib import Path, PureWindowsPath
import sys
import tempfile
import time
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Closed transport vocabulary: syntactically safe arbitrary strings can still be secrets.
PUBLIC_REASONS = frozenset('''unexpected_finish_reason decision_http_error
decision_timeout decision_provider_unavailable decision_model_not_configured
invalid_decision_schema invalid_decision_json invalid_decision_document
duplicate_json_key nonfinite_json_number decision_too_large decision_too_deep
invalid_decision_response invalid_decision_choices invalid_decision_message
invalid_decision_refusal decision_refused incomplete_decision_response
ambiguous_decision_response invalid_function_call duplicate_call_id
unsupported_decision_encoding decision_response_too_large invalid_decision_messages
unmatched_tool_message missing_tool_observation invalid_decision_options'''.split())


class Capture:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))


class DecisionProbe:
    """Record only public transport reasons, never responses or secret attributes."""
    def __init__(self, model):
        self._model = model
        self.calls = []
        self.model_name = 'runtime-configured'
        self.provider_name = 'caller-supplied'

    async def decide(self, *args, **kwargs):
        from src.agent.contracts.decision import public_schema_issues
        response = await self._model.decide(*args, **kwargs)
        reason = (response.error.details or {}).get('reason') if response.error else None
        http_status = (response.error.details or {}).get('http_status') if response.error else None
        self.calls.append({'success': response.success,
            'error_code': response.error.code.value if response.error else None,
            'reason': reason if type(reason) is str and reason in PUBLIC_REASONS else None,
            'http_status': http_status if type(http_status) is int and 100 <= http_status <= 599 else None,
            'schema_issues': public_schema_issues((response.error.details or {}).get('schema_issues')) if response.error else []})
        return response


async def run_acceptance(model, mode='native'):
    from src.agent.contracts import AgentContext
    from src.agent.harness.decision_loop import ModelDecisionLoop
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.agent.persistence.redaction import sanitize_bounded
    from src.agent.tooling.factory import build_tool_registry
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.web.chat_handler import ChatHandler

    reports = []
    probe = DecisionProbe(model)
    registry = build_tool_registry([PropertyCalculator()])
    try:
        with tempfile.TemporaryDirectory(prefix='medchat-isolated-chat-') as directory:
            store = SQLiteAgentStateStore(Path(directory) / 'state.sqlite')
            loop = ModelDecisionLoop(probe, registry, store, mode=mode, max_model_requests=4,
                                     max_tool_attempts=2, timeout_seconds=90)
            handler = ChatHandler(None, None, None, {})
            cases = [
                ('CHAT-001', 'chat', '你好，请简短介绍你能提供哪些帮助。不要调用科研工具。', []),
                ('CHAT-002', 'scientific', '请计算这两个分子的性质。\nSMILES: CCO\nSMILES: CCN', ['CCO', 'CCN']),
                ('CHAT-003', 'scientific', '请计算分子性质。我还没有提供 SMILES，请先询问我，不要自行选择分子。', ['CCN']),
                ('CHAT-004', 'scientific', '请计算分子性质，无效时不要返回模拟性质。\nSMILES: CC(C)((', []),
            ]
            for case_id, kind, prompt, subjects in cases:
                context = AgentContext(prompt, 'acceptance-' + uuid4().hex,
                    user_id='isolated-acceptance', session_id=case_id)
                criteria = None if not subjects else {'version': '1', 'molecular_results': [{
                    'tool_name': 'property_calculator', 'exact_molecule_count': len(subjects),
                    'expected_smiles': subjects,
                    'required_metrics': ['molecular_weight', 'logp', 'tpsa', 'hbd', 'hba', 'qed'],
                }]}
                kwargs = dict(context=context, decision_loop=loop, request_kind=kind,
                    allowed_tools={'property_calculator'} if kind == 'scientific' else set(),
                    required_tools={'property_calculator'} if kind == 'scientific' else set(), requirements=criteria)
                capture = Capture()
                first_call = len(probe.calls)
                started = time.monotonic()
                result = await handler.process_decision_message(capture, **kwargs)
                checks = {}
                terminal_count = 1
                if case_id == 'CHAT-003':
                    waiting = result.metadata.get('waiting_for_input', False)
                    continuation = result.metadata.get('continuation_id')
                    checks['clarification_without_calculation'] = bool(waiting and continuation and not result.tool_results)
                    if waiting and continuation:
                        result = await handler.process_decision_message(capture, **kwargs,
                            continuation_id=continuation, clarified_query='SMILES: CCN')
                        terminal_count += 1
                    checks['clarification_resumed'] = bool(terminal_count == 2 and result.success)
                completes = [m for m in capture.messages if m['type'] == 'complete']
                checks['one_terminal_per_turn'] = len(completes) == terminal_count
                checks['model_invoked'] = result.metadata.get('model_requests', 0) > 0
                if case_id == 'CHAT-004':
                    checks['expected_rejection'] = bool(not result.success and result.tool_results
                        # Legacy adapter labels parser failures internal_error. Match the
                        # parser's fixed diagnostic, not arbitrary dependency failures;
                        # preserve the original code in the report rather than relabel it.
                        and any(r.error and r.error.message ==
                            'SMILES 无效或缺失；请提供完整结构，使用换行或分号分隔，不会提取片段替代。'
                            for r in result.tool_results)
                        and all(call['success'] for call in probe.calls[first_call:]))
                    checks['no_fabricated_properties'] = bool(not result.success
                        and all(not r.success and not r.data and not r.artifacts for r in result.tool_results)
                        and not result.artifacts)
                else:
                    checks['success'] = result.success
                checks['trace_retained'] = bool(completes and completes[-1]['trace_id'] == context.trace_id)
                checks['answer_not_truncated'] = bool(completes and completes[-1]['content'] == result.final_answer)
                if kind == 'scientific':
                    public = [m for m in capture.messages if m['type'] == 'agent_result'][-1]
                    checks['scientific_data_preserved'] = ([r['data'] for r in public['tool_result_sequence']]
                                                          == [r.data for r in result.tool_results])
                    if subjects:
                        checks['rdkit_subjects_and_metrics'] = bool(result.metadata.get('task_acceptance', {}).get('satisfied'))
                    checks['only_authorized_tools'] = [r.tool_name for r in result.tool_results] == ['property_calculator']
                else:
                    checks['no_scientific_tools'] = not result.tool_results
                reports.append({
                    'case_id': case_id, 'status': 'passed' if all(checks.values()) else 'failed',
                    'result_status': result.outcome.value if result.outcome else ('completed' if result.success else 'failed'),
                    'latency_ms': round((time.monotonic() - started) * 1000, 2),
                    'trace_id': context.trace_id, 'checks': checks,
                    'model_calls': probe.calls[first_call:],
                    'events': [m['event'].get('event') for m in capture.messages if m['type'] == 'agent_event'],
                    'actual_tools': [r.tool_name for r in result.tool_results],
                    'error_code': result.error.code.value if result.error else None,
                    'stop_reason': result.metadata.get('stop_reason'),
                    'tools': [{'tool_name': r.tool_name, 'success': r.success,
                               'error_code': r.error.code.value if r.error else None,
                               'input_digest': r.provenance.input_digest if r.provenance else None,
                               'output_digest': r.provenance.output_digest if r.provenance else None,
                               'evidence_id': r.quality.get('evidence_id')} for r in result.tool_results],
                })
    finally:
        registry.close()
    count = sum(r['status'] == 'passed' for r in reports)
    report = {'status': 'passed' if count == len(reports) else 'partial' if count else 'failed',
              'provider': 'caller-supplied', 'model': 'runtime-configured', 'mode': mode,
              'pass_rate': count / len(reports), 'cases': reports,
              'scope': 'isolated ChatHandler entry; not production browser acceptance; temporary state removed'}
    return sanitize_bounded(report, max_depth=12, max_items=128, max_text_chars=512)[0]


def open_report(path):
    """Create a new local JSON report; never replace files or follow reparse points."""
    path = Path(path)
    if (str(path).startswith(('\\\\', '//')) or path.suffix.lower() != '.json'
            or any(':' in part for part in path.parts if part != path.anchor)
            or (path.drive and not path.root) or PureWindowsPath(path).is_reserved()
            or '..' in path.parts):
        raise ValueError('unsafe_report_path')
    for candidate in (*reversed(path.parents), path):
        if candidate.is_symlink():
            raise ValueError('unsafe_report_path')
        try:
            attributes = candidate.lstat()
        except FileNotFoundError:
            continue
        if getattr(attributes, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('unsafe_report_path')
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also rejects existing hardlinks and a racing destination.
    return path.open('x', encoding='utf-8')


def validate_provider_endpoint(base):
    """Reject ambiguous or credential-bearing URLs before constructing a client."""
    if any(c.isspace() or ord(c) < 32 or c in '\\?#' for c in base):
        raise ValueError('unsafe_endpoint_configuration')
    url = urlsplit(base)
    if (url.scheme != 'https' or not url.hostname or url.username is not None
            or url.password is not None or url.port == 0):
        raise ValueError('unsafe_endpoint_configuration')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('native', 'json'), default='native')
    parser.add_argument('--output', type=Path, default=ROOT / f'outputs/agent_evaluation/decision_chat_{uuid4().hex}.json')
    args = parser.parse_args(argv)
    try:
        with open_report(args.output) as output:
            report = execute(args.mode)
            output.write(json.dumps(report, ensure_ascii=False, indent=2))
    except (OSError, ValueError):
        print(json.dumps({'status': 'failed', 'reason': 'unsafe_report_path'}))
        return 2
    print(json.dumps({'status': report['status'], 'case_count': len(report['cases'])}))
    return 0 if report['status'] == 'passed' else 2


def execute(mode):
    # Values stay in memory only. Never inspect a secret file or print configuration.
    key = os.environ.get('OPENAI_COMPATIBLE_API_KEY', '').strip()
    base = os.environ.get('OPENAI_COMPATIBLE_BASE_URL', '').strip()
    name = os.environ.get('OPENAI_COMPATIBLE_MODEL', '').strip()
    if not all((key, base, name)):
        report = {'status': 'skipped', 'reason': 'missing_runtime_configuration', 'cases': []}
    else:
        try:
            validate_provider_endpoint(base)
            import httpx
            from src.agent.openai_compatible_model import OpenAICompatibleModel
            async def request():
                async with httpx.AsyncClient(timeout=90, follow_redirects=False, trust_env=False) as client:
                    model = OpenAICompatibleModel(key, name, base, client=client)
                    return await run_acceptance(model, mode)
            report = asyncio.run(request())
        except Exception:
            report = {'status': 'failed', 'reason': 'isolated_acceptance_unavailable', 'cases': []}
    return report


if __name__ == '__main__':
    raise SystemExit(main())
