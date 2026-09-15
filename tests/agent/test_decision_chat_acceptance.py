"""CLI reporting contracts, never real provider requests in unit tests."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setattr(os, 'environ', {})
    import httpx
    def no_network(*a, **kw):
        raise AssertionError('Real network connections are forbidden in CLI tests')
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', no_network)


def module():
    path = Path(__file__).resolve().parents[2] / 'scripts/run_decision_chat_acceptance.py'
    assert path.is_file(), 'isolated acceptance script is missing'
    spec = importlib.util.spec_from_file_location('decision_chat_acceptance', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_missing_runtime_configuration_is_skip_not_success(tmp_path):
    output = tmp_path / 'report.json'
    assert module().main(['--output', str(output)]) == 2
    report = json.loads(output.read_text(encoding='utf-8'))
    assert report['status'] == 'skipped'
    assert report['reason'] == 'missing_runtime_configuration'


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_report_checks_real_tools_and_clarification_with_decision_doubles(mode):
    from test_decision_loop import ScriptedModel, finish, tool, finish_last, clarify
    model = ScriptedModel([finish(text='hello', kind='chat'), tool(), finish_last,
                           clarify(), tool(), finish_last, tool(), finish(['missing-evidence'])])
    report = asyncio.run(module().run_acceptance(model, mode))
    assert report['status'] == 'passed', report
    assert len(report['cases']) == 4
    assert report['mode'] == mode
    assert report['cases'][1]['checks']['rdkit_subjects_and_metrics']
    assert report['cases'][2]['checks']['clarification_resumed']
    assert 'api_key' not in json.dumps(report).lower()
    invalid = report['cases'][3]
    assert invalid['status'] == 'passed'
    assert invalid['result_status'] in ('failed', 'rejected')
    assert invalid['checks']['expected_rejection']
    assert invalid['checks']['no_fabricated_properties']
    assert invalid['actual_tools'] == ['property_calculator']
    assert invalid['tools'][0]['success'] is False
    # The legacy property adapter currently retains input rejection as internal_error.
    assert invalid['tools'][0]['error_code'] == 'internal_error'
    assert 'tool_failed' in invalid['events']


def test_private_model_labels_and_arbitrary_reason_are_not_reported():
    from src.agent.contracts import AgentErrorCode, AgentExecutionError
    from src.agent.decision_transport import DecisionResponse
    class InvalidModel:
        provider_name = 'private_provider_label'
        model_name = 'private_model_label'
        async def decide(self, *a, **kw):
            return DecisionResponse(None, AgentExecutionError(AgentErrorCode.INVALID_OUTPUT,
                'private_body', {'reason': 'private_reason'}), None, {})
    report = asyncio.run(module().run_acceptance(InvalidModel()))
    assert 'private_' not in json.dumps(report)
    assert report['cases'][-1]['status'] == 'failed'  # Provider failure is not input rejection.


def test_dependency_failure_is_not_expected_smiles_rejection(monkeypatch):
    from test_decision_loop import ScriptedModel, finish, tool, clarify
    from src.agent.tools.property_calculator import PropertyCalculator
    monkeypatch.setattr(PropertyCalculator, 'execute', lambda *a: {
        'success': False, 'message': 'Synthetic dependency unavailable'})
    model = ScriptedModel([finish(kind='chat'), tool(), finish(['missing']),
                           clarify(), tool(), finish(['missing']), tool(), finish(['missing'])])
    report = asyncio.run(module().run_acceptance(model))
    assert report['cases'][-1]['status'] == 'failed'
    assert report['cases'][-1]['checks']['expected_rejection'] is False


@pytest.mark.parametrize('provider_failure', [False, True])
def test_invalid_smiles_clarification_is_rejection_but_provider_error_is_not(provider_failure):
    from src.agent.contracts import AgentErrorCode, AgentExecutionError
    from src.agent.decision_transport import DecisionResponse
    from test_decision_loop import ScriptedModel, finish, tool, finish_last, clarify
    class Model(ScriptedModel):
        async def decide(self, *a, **kw):
            if provider_failure and len(self.messages) == 7:
                return DecisionResponse(None, AgentExecutionError(AgentErrorCode.PROVIDER_ERROR,
                    'private-provider-failure', {'reason': 'decision_timeout'}), None, {})
            return await super().decide(*a, **kw)
    model = Model([finish(kind='chat'), tool(), finish_last, clarify(), tool(), finish_last,
                   tool(), clarify()])
    report = asyncio.run(module().run_acceptance(model))
    case = report['cases'][-1]
    assert case['status'] == ('failed' if provider_failure else 'passed')
    assert case['checks']['expected_rejection'] is (not provider_failure)
    if not provider_failure:
        assert case['result_status'] == 'rejected'
    assert 'private-provider-failure' not in json.dumps(report)


@pytest.mark.parametrize('name', ['.env', 'report.py', 'report.json:stream'])
def test_report_path_rejects_non_json_without_writing(tmp_path, name, capsys):
    target = tmp_path / name
    assert module().main(['--output', str(target)]) == 2
    assert not target.exists()
    assert 'unsafe_report_path' in capsys.readouterr().out


def test_report_does_not_overwrite_existing_file(tmp_path, capsys):
    target = tmp_path / 'existing.json'
    target.write_text('preserve-user-content', encoding='utf-8')
    assert module().main(['--output', str(target)]) == 2
    assert target.read_text(encoding='utf-8') == 'preserve-user-content'
    assert 'unsafe_report_path' in capsys.readouterr().out


@pytest.mark.parametrize('name', ['existing.txt:report.json', 'NUL.json'])
def test_relative_report_refuses_streams_and_devices(tmp_path, monkeypatch, capsys, name):
    monkeypatch.chdir(tmp_path)
    m = module()
    calls = []
    original = Path.open
    def open_file(path, *a, **kw):
        if path.name == name:
            calls.append(True)
            raise OSError('Test guard: no device or stream writes')
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'open', open_file)
    assert m.main(['--output', name]) == 2
    assert not calls
    assert 'unsafe_report_path' in capsys.readouterr().out


def test_report_refuses_symlink_parent_before_provider_call(tmp_path, capsys):
    link = tmp_path / 'link'
    destination = tmp_path / 'destination'
    destination.mkdir()
    try:
        link.symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip('directory symlinks unavailable')
    assert module().main(['--output', str(link / 'report.json')]) == 2
    assert not (destination / 'report.json').exists()
    assert 'unsafe_report_path' in capsys.readouterr().out


def test_report_refuses_windows_reparse_parent(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace
    import stat
    original = Path.lstat
    def lstat(path, *a, **kw):
        if path == tmp_path:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'lstat', lstat)
    target = tmp_path / 'report.json'
    assert module().main(['--output', str(target)]) == 2
    assert not target.exists()
    assert 'unsafe_report_path' in capsys.readouterr().out


@pytest.mark.parametrize('base', ['http://example.invalid', 'https://@example.invalid',
    'https://example.invalid/?', 'https://example.invalid/#',
    'https://example.invalid:99999', 'https://example.invalid/\nsecret'])
def test_unsafe_endpoint_never_creates_client(tmp_path, monkeypatch, base):
    import httpx
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', base)
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'synthetic-model')
    calls = []
    def client(*a, **kw):
        calls.append(True)
        raise RuntimeError('No network permitted')
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    assert module().main(['--output', str(tmp_path / 'report.json')]) == 2
    assert not calls


def test_report_write_failure_is_sanitized(tmp_path, monkeypatch, capsys):
    m = module()
    def fail(*a, **kw):
        raise OSError('private-write-failure')
    monkeypatch.setattr(Path, 'mkdir', fail)
    assert m.main(['--output', str(tmp_path / 'report.json')]) == 2
    output = capsys.readouterr()
    assert 'private-write-failure' not in output.out + output.err


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_cli_with_stub_http_transport_real_rdkit_and_private_temp_state(tmp_path, monkeypatch, mode):
    import httpx
    from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
    from src.agent.contracts.decision import DecisionEnvelope
    from test_decision_loop import finish, tool, finish_last, clarify
    from rdkit import Chem
    from rdkit.Chem import Descriptors
    decisions = iter([finish(text='hello', kind='chat'), tool(), finish_last,
                      clarify(), tool(), finish_last, tool(), finish(['missing-evidence'])])
    requests, paths = [], []
    def respond(request):
        assert request.url.host == 'example.invalid'
        payload = json.loads(request.content)
        requests.append(payload)
        if mode == 'native':
            assert payload['tools'][0]['function']['name'] == 'agent_decision'
            assert 'response_format' not in payload
        else:
            assert payload['response_format'] == {'type': 'json_object'}
            assert 'tools' not in payload
        decision = next(decisions)
        if callable(decision):
            observation = json.loads(payload['messages'][-1]['content'])
            for item in observation['data']:
                assert item['properties']['molecular_weight'] == round(Descriptors.MolWt(Chem.MolFromSmiles(item['smiles'])), 2)
            decision = decision(payload['messages'])
        raw = DecisionEnvelope(decision=decision).model_dump_json()
        message = {'role': 'assistant', 'content': raw}
        if mode == 'native':
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': f'call-{len(requests)}', 'type': 'function',
                'function': {'name': 'agent_decision', 'arguments': raw}}]}
        return httpx.Response(200, json={'choices': [{'finish_reason': 'tool_calls' if mode == 'native' else 'stop', 'message': message}]})
    original_client = httpx.AsyncClient
    def client(**kwargs):
        assert kwargs['trust_env'] is False
        assert kwargs['follow_redirects'] is False
        return original_client(transport=httpx.MockTransport(respond), **kwargs)
    original_init = SQLiteAgentStateStore.__init__
    def init(self, path, *args, **kwargs):
        paths.append(Path(path))
        return original_init(self, path, *args, **kwargs)
    monkeypatch.setattr(SQLiteAgentStateStore, '__init__', init)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', 'https://example.invalid')
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'synthetic-model')
    target = tmp_path / 'report.json'
    assert module().main(['--mode', mode, '--output', str(target)]) == 0
    report = json.loads(target.read_text(encoding='utf-8'))
    assert report['status'] == 'passed'
    assert report['mode'] == mode
    assert len(requests) == 8
    assert paths and all(not p.parent.exists() for p in paths)
    assert 'synthetic-only-key' not in target.read_text(encoding='utf-8')


def test_report_keeps_public_protocol_reason_not_raw_error():
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
