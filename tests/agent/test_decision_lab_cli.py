"""CLI checks never launch a server or call a real provider."""
import importlib.util
import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.setattr(os, 'environ', {})
    import httpx
    import uvicorn
    def no_network(*a, **kw):
        raise AssertionError('Real network connections and servers are forbidden in CLI tests')
    monkeypatch.setattr(httpx.AsyncHTTPTransport, 'handle_async_request', no_network)
    monkeypatch.setattr(uvicorn, 'run', no_network)


def module():
    path = Path(__file__).resolve().parents[2] / 'scripts/run_decision_browser_lab.py'
    assert path.exists(), 'missing standalone lab launcher'
    spec = importlib.util.spec_from_file_location('lab_cli', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_missing_runtime_env_fails_without_launch(capsys):
    assert module().main([]) == 2
    assert 'missing_runtime_configuration' in capsys.readouterr().out


@pytest.mark.parametrize('mode', ['native', 'json'])
def test_launcher_is_loopback_only_and_temp_state_removed(monkeypatch, mode):
    import uvicorn
    from fastapi import FastAPI
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', 'https://example.invalid/chat/completions')
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'test-model')
    m = module()
    paths = []
    def factory(model, db_path, **kwargs):
        paths.append(Path(db_path))
        assert paths[-1].parent.exists()
        assert kwargs['mode'] == mode
        assert kwargs['port'] == 6012
        assert model.client is not None
        assert model.client.trust_env is False
        assert model.client.follow_redirects is False
        return FastAPI()
    monkeypatch.setattr(m, 'create_decision_lab', factory)
    def serve(app, **kwargs):
        assert kwargs['host'] == '127.0.0.1'
        assert kwargs['proxy_headers'] is False
        assert kwargs['access_log'] is False
        assert kwargs['ws_max_size'] == 16384
    monkeypatch.setattr(uvicorn, 'run', serve)
    assert m.main(['--mode', mode]) == 0
    assert not paths[0].parent.exists()


@pytest.mark.parametrize('base', ['http://example.invalid', 'https://user:secret@example.invalid',
                                 'https://example.invalid/?key=synthetic', 'https://example.invalid/#secret',
                                 'https://example.invalid:99999', 'https://example.invalid:bad',
                                 'https://example.invalid/\nsecret', 'https://@example.invalid',
                                 'https://example.invalid/?', 'https://example.invalid/#'])
def test_unsafe_endpoints_rejected(monkeypatch, base):
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', base)
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'test-model')
    m = module()
    calls = []
    def factory(*a, **kw):
        calls.append(True)
        raise RuntimeError('Unexpected lab creation')
    monkeypatch.setattr(m, 'create_decision_lab', factory)
    assert m.main([]) == 2
    assert not calls


def test_server_exception_closes_client_removes_state_and_hides_details(monkeypatch, capsys):
    import asyncio
    import uvicorn
    from fastapi import FastAPI
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', 'https://example.invalid')
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'synthetic-model')
    m = module()
    paths, clients = [], []
    def factory(model, db_path, **kwargs):
        paths.append(Path(db_path))
        clients.append(model.client)
        return FastAPI()
    def serve(app, **kwargs):
        async def run():
            async with app.router.lifespan_context(app):
                raise RuntimeError('synthetic-only-key private-provider-body')
        asyncio.run(run())
    monkeypatch.setattr(m, 'create_decision_lab', factory)
    monkeypatch.setattr(uvicorn, 'run', serve)
    assert m.main([]) == 2
    assert paths and not paths[0].parent.exists()
    assert clients[0].is_closed
    output = capsys.readouterr()
    assert 'synthetic-only-key' not in output.out + output.err
    assert 'private-provider-body' not in output.out + output.err


@pytest.mark.parametrize('port', ['0', '80', '65536'])
def test_invalid_ports_fail_without_launch(monkeypatch, port):
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', 'https://example.invalid')
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'synthetic-model')
    m = module()
    monkeypatch.setattr(m, 'create_decision_lab', lambda *a, **kw: pytest.fail('unsafe launch'))
    assert m.main(['--port', port]) == 2
