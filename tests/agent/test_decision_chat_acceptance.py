"""CLI reporting contracts, never real provider requests in unit tests."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path

import pytest
from src.system import llm_transport


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    # Real Pi needs Node and the OS runtime paths, never caller credentials/config.
    environment = {key: os.environ[key] for key in
                   ('PATH', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP') if key in os.environ}
    monkeypatch.setattr(os, 'environ', environment)
    import httpx
    import socket
    # Bind the production type before individual tests replace the constructor.
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    def no_network(*a, **kw):
        raise AssertionError('Real network connections are forbidden in CLI tests')
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', no_network)
    monkeypatch.setattr(socket, 'getaddrinfo', no_network)


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


def test_ollama_without_mode_defaults_to_json(monkeypatch, tmp_path):
    m = module()
    monkeypatch.setenv('OLLAMA_CHAT_MODEL', 'synthetic-chat-model')
    monkeypatch.setattr(m, 'execute', lambda mode, provider: {
        'status': 'skipped', 'mode': mode, 'provider': provider, 'cases': [],
    })
    target = tmp_path / 'report.json'
    assert m.main(['--provider', 'ollama', '--output', str(target)]) == 2
    assert json.loads(target.read_text(encoding='utf-8'))['mode'] == 'json'


@pytest.mark.parametrize('error, expected', [
    ('timeout', {'reason': 'decision_timeout'}),
    ('transport', {'reason': 'decision_provider_unavailable'}),
    ('endpoint', {'reason': 'decision_endpoint_rejected'}),
])
def test_public_acceptance_failure_classification_is_fixed_and_redacted(error, expected):
    import httpx
    errors = {
        'timeout': httpx.ReadTimeout('private-timeout-body'),
        'transport': httpx.ConnectError('private-host.invalid'),
        'endpoint': ValueError('unsafe_endpoint_configuration: private-host.invalid'),
    }
    result = module().classify_acceptance_failure(errors[error])
    assert result == expected
    assert 'private-' not in json.dumps(result)


def test_public_acceptance_failure_keeps_only_allowlisted_http_status():
    import httpx
    response = httpx.Response(503, request=httpx.Request('POST', 'https://private.invalid'))
    result = module().classify_acceptance_failure(httpx.HTTPStatusError(
        'private-response-body', request=response.request, response=response))
    assert result == {'reason': 'decision_http_error', 'http_status': 503}
    assert 'private' not in json.dumps(result)


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
    assert all(case['checks']['pi_runtime_backend'] for case in report['cases'])
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


@pytest.mark.parametrize('provider', ['cloud', 'ollama'])
@pytest.mark.parametrize('mode', ['native', 'json'])
def test_cli_with_stub_http_transport_real_pi_rdkit_and_private_temp_state(tmp_path, monkeypatch, mode, provider):
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
        assert request.url.host == ('127.0.0.1' if provider == 'ollama' else 'example.invalid')
        assert request.url.path == '/v1/chat/completions'
        payload = json.loads(request.content)
        assert payload['model'] == 'synthetic-chat-model'
        if provider == 'ollama':
            assert request.headers['authorization'].startswith('Bearer ')
            assert 'synthetic-only-key' not in request.headers['authorization']
        requests.append(payload)
        if mode == 'native':
            assert payload['tools'][0]['function']['name'] == 'agent_decision'
            assert 'response_format' not in payload
        else:
            if provider == 'ollama':
                response_format = payload['response_format']
                assert response_format['type'] == 'json_schema'
                assert response_format['json_schema']['name'] == 'agent_decision'
                assert response_format['json_schema']['strict'] is True
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
        kwargs.setdefault('transport', httpx.MockTransport(respond))
        return original_client(**kwargs)
    original_init = SQLiteAgentStateStore.__init__
    def init(self, path, *args, **kwargs):
        paths.append(Path(path))
        return original_init(self, path, *args, **kwargs)
    monkeypatch.setattr(SQLiteAgentStateStore, '__init__', init)
    if provider == 'cloud':
        monkeypatch.setattr(httpx, 'AsyncClient', client)
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', 'https://example.invalid')
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'synthetic-chat-model')
    monkeypatch.setenv('OLLAMA_CHAT_MODEL', 'synthetic-chat-model')
    monkeypatch.setenv('OLLAMA_MODEL', 'generation-model-must-not-change')
    m = module()
    monkeypatch.setattr(llm_transport, 'create_ollama_transport', lambda: httpx.MockTransport(respond))
    observed_models = []
    run_acceptance = m.run_acceptance
    async def run_actual_model(model, wire_mode):
        if provider == 'ollama':
            from src.system.ollama_model import OllamaModel
            assert type(model) is OllamaModel, 'CLI must use the same actual type as Web'
            observed_models.append(model)
        return await run_acceptance(model, wire_mode)
    monkeypatch.setattr(m, 'run_acceptance', run_actual_model)
    target = tmp_path / 'report.json'
    options = ['--provider', 'ollama'] if provider == 'ollama' else []  # Cloud remains the default.
    assert m.main([*options, '--mode', mode, '--output', str(target)]) == 0
    report = json.loads(target.read_text(encoding='utf-8'))
    assert report['status'] == 'passed'
    assert report['mode'] == mode
    assert report['provider'] == provider
    assert all(case['checks']['pi_runtime_backend'] for case in report['cases'])
    assert 'isolated ChatHandler' in report['scope']
    assert 'not production browser acceptance' in report['scope']
    assert len(requests) == 8
    assert paths and all(not p.parent.exists() for p in paths)
    assert 'synthetic-only-key' not in target.read_text(encoding='utf-8')
    assert 'synthetic-chat-model' not in target.read_text(encoding='utf-8')
    assert os.environ['OLLAMA_MODEL'] == 'generation-model-must-not-change'
    if provider == 'ollama':
        assert len(observed_models) == 1
        assert observed_models[0].client.is_closed and observed_models[0].sync_client.is_closed


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


def test_probe_retains_fixed_endpoint_policy_diagnostic():
    from src.agent.contracts import AgentErrorCode, AgentExecutionError
    from src.agent.decision_transport import DecisionResponse
    class RejectedModel:
        async def decide(self, *a, **kw):
            return DecisionResponse(None, AgentExecutionError(AgentErrorCode.MODEL_UNAVAILABLE,
                'private-host-or-dns-detail', {'reason': 'decision_endpoint_rejected',
                                             'raw': 'private-host-or-dns-detail'}), None, {})
    probe = module().DecisionProbe(RejectedModel())
    asyncio.run(probe.decide([]))
    assert probe.calls[0]['reason'] == 'decision_endpoint_rejected'
    assert 'private-' not in json.dumps(probe.calls)


def test_ollama_requires_explicit_chat_model_not_generation_or_cloud_config(tmp_path, monkeypatch):
    monkeypatch.setenv('OLLAMA_MODEL', 'generation-only-private')
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'cloud-only-private')
    target = tmp_path / 'missing.json'
    assert module().main(['--provider', 'ollama', '--mode', 'json', '--output', str(target)]) == 2
    report = json.loads(target.read_text(encoding='utf-8'))
    assert report['status'] == 'skipped'
    assert report['reason'] == 'missing_ollama_chat_model'
    assert report['provider'] == 'ollama'
    assert report['cases'] == []
    assert 'isolated ChatHandler' in report['scope']
    assert 'private' not in json.dumps(report)


@pytest.mark.parametrize('base', [
    'https://example.invalid', 'http://192.168.1.2:11434', 'http://127.0.0.2:11434',
    'http://private@localhost:11434', 'http://localhost:11434/?',
    'http://localhost:11434/#', 'http://localhost:0', 'http://localhost:99999',
])
def test_local_unsafe_endpoint_never_creates_client(tmp_path, monkeypatch, base):
    import httpx
    monkeypatch.setenv('OLLAMA_CHAT_MODEL', 'private-model')
    monkeypatch.setenv('OLLAMA_BASE_URL', base)
    monkeypatch.setenv('MEDCHAT_LLM_ALLOWED_HOSTS', 'example.invalid,192.168.1.2')
    calls = []
    def client(*a, **kw):
        calls.append(True)
        raise AssertionError('Client must not be created')
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    target = tmp_path / 'unsafe.json'
    assert module().main(['--provider', 'ollama', '--output', str(target)]) == 2
    assert not calls
    report = json.loads(target.read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert report['reason'] == 'decision_endpoint_rejected'
    assert base not in json.dumps(report)


def test_ollama_unavailable_is_sanitized_failure_without_cloud_key(tmp_path, monkeypatch, capsys):
    import httpx
    m = module()
    def refuse(request):
        raise httpx.ConnectError('private-connection-detail', request=request)
    monkeypatch.setattr(llm_transport, 'create_ollama_transport', lambda: httpx.MockTransport(refuse))
    monkeypatch.setenv('OLLAMA_CHAT_MODEL', 'private-local-model')
    target = tmp_path / 'unavailable.json'
    assert m.main(['--provider', 'ollama', '--mode', 'json', '--output', str(target)]) == 2
    report = json.loads(target.read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert len(report['cases']) == 4
    assert all(case['status'] == 'failed' for case in report['cases'])
    assert report['cases'][0]['model_calls'][0]['reason'] == 'decision_provider_unavailable'
    output = capsys.readouterr()
    assert 'private-' not in json.dumps(report) + output.out + output.err


def test_backend_check_covers_initial_clarification_not_only_resume(monkeypatch):
    from src.agent.pi_runtime.runtime import PiAgentRuntime
    from test_decision_loop import ScriptedModel, finish, tool, finish_last, clarify
    original = PiAgentRuntime.run
    async def wrong_backend(self, *args, **kwargs):
        result = await original(self, *args, **kwargs)
        if result.metadata.get('waiting_for_input'):
            result.metadata.pop('backend', None)
        return result
    monkeypatch.setattr(PiAgentRuntime, 'run', wrong_backend)
    model = ScriptedModel([finish(kind='chat'), tool(), finish_last, clarify(),
                           tool(), finish_last, tool(), finish(['missing-evidence'])])
    report = asyncio.run(module().run_acceptance(model, 'json'))
    assert report['cases'][2]['status'] == 'failed'
    assert report['cases'][2]['checks']['pi_runtime_backend'] is False
    assert report['cases'][2]['checks']['clarification_resumed'] is True


@pytest.mark.parametrize('url,address', [
    ('http://localhost:11434/v1/chat/completions', '127.0.0.1'),
    ('http://127.0.0.1:11434/v1/chat/completions', '127.0.0.1'),
    ('http://[::1]:11434/v1/chat/completions', '::1'),
])
def test_local_transport_pins_validated_loopback_without_real_requests(monkeypatch, url, address):
    import httpx
    import socket
    transport = llm_transport.create_ollama_transport()
    connections = []
    class Backend:
        async def connect_tcp(self, host, port, **kwargs):
            connections.append((host, port))
    transport._backend._backend = Backend()
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET6 if ':' in address else socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 11434))])
    async def handle(self, request):
        await self._pool._network_backend.connect_tcp(request.url.host, request.url.port)
        return httpx.Response(200, json={})
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', handle)
    async def request():
        try:
            await transport.handle_async_request(httpx.Request('POST', url))
        finally:
            await transport.aclose()
    asyncio.run(request())
    assert connections == [(address, 11434)]


@pytest.mark.parametrize('addresses', [[], ['203.0.113.1'], ['127.0.0.1', '10.0.0.1']])
def test_local_transport_rejects_nonloopback_dns_before_http(monkeypatch, addresses):
    import httpx
    import socket
    transport = llm_transport.create_ollama_transport()
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 11434)) for address in addresses])
    async def request():
        try:
            with pytest.raises(ValueError):
                await transport.handle_async_request(httpx.Request('POST', 'http://localhost:11434/v1/chat/completions'))
        finally:
            await transport.aclose()
    asyncio.run(request())


def test_local_model_keeps_explicit_policy_and_scoped_pinned_transport():
    import httpx
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    transport = llm_transport.create_ollama_transport()
    async def inspect():
        async with httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False) as client:
            model = OpenAICompatibleModel('local-dummy', 'test-model', 'http://localhost:11434',
                                          provider='ollama', enforce_url_policy=True, client=client)
            assert model.provider == 'ollama'
            assert model.enforce_url_policy is True
            assert model.client._transport is transport
            model._validate_request_endpoint()
    asyncio.run(inspect())


def test_local_transport_does_not_inherit_cloud_host_allowlist(monkeypatch):
    import httpx
    monkeypatch.setenv('MEDCHAT_LLM_ALLOWED_HOSTS', 'example.invalid')
    transport = llm_transport.create_ollama_transport()
    async def request():
        try:
            with pytest.raises(ValueError):
                await transport.handle_async_request(httpx.Request('POST', 'https://example.invalid/v1/chat/completions'))
        finally:
            await transport.aclose()
    asyncio.run(request())


def test_cloud_does_not_automatically_allow_configured_host(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', 'https://example.invalid')
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'private-cloud-model')
    target = tmp_path / 'cloud-policy.json'
    assert module().main(['--provider', 'cloud', '--output', str(target)]) == 2
    report = json.loads(target.read_text(encoding='utf-8'))
    assert report['status'] == 'failed'
    assert report['cases'] == []  # Real client policy rejects before any model call.
    assert 'MEDCHAT_LLM_ALLOWED_HOSTS' not in os.environ
    assert 'private-cloud-model' not in json.dumps(report)
