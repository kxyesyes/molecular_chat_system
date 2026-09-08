"""CLI checks never launch a server or call a real provider."""
import importlib.util
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[2] / 'scripts/run_decision_browser_lab.py'
    assert path.exists(), 'missing standalone lab launcher'
    spec = importlib.util.spec_from_file_location('lab_cli', path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_missing_runtime_env_fails_without_launch(monkeypatch, capsys):
    for name in ('OPENAI_COMPATIBLE_API_KEY', 'OPENAI_COMPATIBLE_BASE_URL', 'OPENAI_COMPATIBLE_MODEL'):
        monkeypatch.delenv(name, raising=False)
    assert module().main([]) == 2
    assert 'missing_runtime_configuration' in capsys.readouterr().out


def test_launcher_is_loopback_only_and_temp_state_removed(monkeypatch):
    import uvicorn
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', 'https://example.invalid/chat/completions')
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'test-model')
    m = module()
    paths = []
    def factory(model, db_path, **kwargs):
        paths.append(Path(db_path))
        assert paths[-1].parent.exists()
        return object()
    monkeypatch.setattr(m, 'create_decision_lab', factory)
    def serve(app, **kwargs):
        assert kwargs['host'] == '127.0.0.1'
        assert kwargs['proxy_headers'] is False
        assert kwargs['access_log'] is False
        assert kwargs['ws_max_size'] == 16384
    monkeypatch.setattr(uvicorn, 'run', serve)
    assert m.main([]) == 0
    assert not paths[0].parent.exists()


@pytest.mark.parametrize('base', ['http://example.invalid', 'https://user:secret@example.invalid',
                                 'https://example.invalid/?key=synthetic', 'https://example.invalid/#secret'])
def test_unsafe_endpoints_rejected(monkeypatch, base):
    monkeypatch.setenv('OPENAI_COMPATIBLE_API_KEY', 'synthetic-only-key')
    monkeypatch.setenv('OPENAI_COMPATIBLE_BASE_URL', base)
    monkeypatch.setenv('OPENAI_COMPATIBLE_MODEL', 'test-model')
    assert module().main([]) == 2
