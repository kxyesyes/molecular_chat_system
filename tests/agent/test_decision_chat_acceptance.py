"""CLI reporting contracts, never real provider requests in unit tests."""
import importlib.util
import json
from pathlib import Path


def module():
    path = Path(__file__).resolve().parents[2] / 'scripts/run_decision_chat_acceptance.py'
    assert path.is_file(), 'isolated acceptance script is missing'
    spec = importlib.util.spec_from_file_location('decision_chat_acceptance', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_missing_runtime_configuration_is_skip_not_success(tmp_path, monkeypatch):
    for name in ('OPENAI_COMPATIBLE_API_KEY', 'OPENAI_COMPATIBLE_BASE_URL', 'OPENAI_COMPATIBLE_MODEL'):
        monkeypatch.delenv(name, raising=False)
    output = tmp_path / 'report.json'
    assert module().main(['--output', str(output)]) == 2
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['status'] == 'skipped'
    assert report['reason'] == 'missing_runtime_configuration'


def test_report_checks_real_tools_and_clarification_with_decision_doubles():
    import asyncio
    from test_decision_loop import ScriptedModel, finish, tool, finish_last, clarify
    model = ScriptedModel([finish(text='hello', kind='chat'), tool(), finish_last,
                           clarify(), tool(), finish_last])
    report = asyncio.run(module().run_acceptance(model))
    assert report['status'] == 'passed', report
    assert len(report['cases']) == 3
    assert report['cases'][1]['checks']['rdkit_subjects_and_metrics']
    assert report['cases'][2]['checks']['clarification_resumed']
    assert 'api_key' not in json.dumps(report).lower()


def test_report_keeps_public_protocol_reason_not_raw_error():
    import asyncio
    from src.agent.contracts import AgentErrorCode, AgentExecutionError
    from src.agent.decision_transport import DecisionResponse
    class InvalidModel:
        async def decide(self, *a, **kw):
            return DecisionResponse(None, AgentExecutionError(AgentErrorCode.INVALID_OUTPUT,
                'synthetic-private-provider-body', {'reason': 'unexpected_finish_reason'}), None,
                {'request_attempts': 1})
    report = asyncio.run(module().run_acceptance(InvalidModel()))
    assert report['status'] == 'failed'
    assert report['cases'][0]['model_calls'][0]['reason'] == 'unexpected_finish_reason'
    assert 'synthetic-private-provider-body' not in json.dumps(report)


def test_probe_preserves_http_status_and_schema_diagnostics_not_private_fields():
    import asyncio
    from src.agent.contracts import AgentErrorCode, AgentExecutionError
    from src.agent.decision_transport import DecisionResponse
    class InvalidModel:
        async def decide(self, *a, **kw):
            return DecisionResponse(None, AgentExecutionError(AgentErrorCode.PROVIDER_ERROR,
                'private-body', {'reason': 'decision_http_error', 'http_status': 503,
                                 'raw': 'private-value'}), None, {'request_attempts': 1})
    probe = module().DecisionProbe(InvalidModel())
    asyncio.run(probe.decide([]))
    assert probe.calls[0]['http_status'] == 503
    assert 'private-' not in json.dumps(probe.calls)
