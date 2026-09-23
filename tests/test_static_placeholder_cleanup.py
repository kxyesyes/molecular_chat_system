import asyncio
import json
import os
import subprocess
import sys
from contextlib import ExitStack, contextmanager
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
BACKUP = '/static/js/activity_prediction_v2.legacy.backup.js'


class ScriptSources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script' and dict(attrs).get('src'):
            self.sources.append(dict(attrs)['src'])


def _check_legacy_backup(client):
    assert not (ROOT / 'src/web/static/js/activity_prediction_v2.legacy.backup.js').exists()
    assert client.get(BACKUP).status_code == 404


PAGE_SCRIPTS = [
    ('index.html', 'home', ['config.js', 'state.js', 'theme.js', 'molecule_candidates.js',
                          'formatters.js', 'molecule_renderer.js', 'chat_renderer.js',
                          'advanced_options.js', 'ws_client.js', 'main.js']),
    ('activity_prediction.html', 'activity_prediction', ['utils.js', 'model_manager.js',
                           'charts.js', 'results_renderer.js', 'preflight.js',
                           'training_monitor.js', 'main.js']),
]


def _check_template_scripts(client, template, folder, expected):
    parser = ScriptSources()
    page = client.get('/' if template == 'index.html' else '/activity-prediction')
    assert page.status_code == 200
    parser.feed(page.text)
    local = [url for url in parser.sources if url.startswith('/static/')]
    paths = [urlsplit(url).path for url in local]
    assert BACKUP not in paths
    assert '/static/js/script.js' not in paths
    assert '/static/js/activity_prediction_v2.js' not in paths
    assert [path.rsplit('/', 1)[-1] for path in paths if path.startswith(f'/static/js/{folder}/')] == expected
    assert paths.index('/static/js/shared/safe_render.js') < paths.index(f'/static/js/{folder}/main.js')
    for url in local:
        assert client.get(url).status_code == 200, url


def _check_placeholders(client):
    for url in ('/static/js/script.js', '/static/js/activity_prediction_v2.js'):
        response = client.get(url)
        assert response.status_code == 200
        executable = [line.strip() for line in response.text.splitlines()
                      if line.strip() and not line.strip().startswith('//')]
        assert executable == ['"use strict";']


@contextmanager
def _open_client(directory):
    """Only called in a fresh worker: never borrow a parent-owned global app."""
    assert 'src.web.app' not in sys.modules
    assert Path(os.environ['AGENT_STATE_DB']).parent == directory
    with ExitStack() as isolation, ExitStack() as resources:
        isolation.enter_context(patch('src.agent.tools.get_all_tools', return_value=[]))
        isolation.enter_context(patch('src.agent.persistence.SQLiteAgentStateStore', return_value=None))
        # Import-time construction now has no scientific tools or persistent store.
        from src.web.app import app_instance

        # Register each independently: even a shutdown exception must close models.
        resources.callback(lambda: asyncio.run(app_instance.molecular_generator_model.close()))
        resources.callback(lambda: asyncio.run(app_instance.model.close()))
        resources.callback(lambda: asyncio.run(app_instance.shutdown()))
        session = TestClient(app_instance.app)
        resources.callback(session.close)
        # Deliberately not `with TestClient(...)`: that would start real lifespan.
        yield session


def test_legacy_backup_is_not_a_published_asset():
    _run_worker('backup')


@pytest.mark.parametrize('folder', ['home', 'activity_prediction'])
def test_current_template_script_order_and_local_urls(folder):
    _run_worker(folder)


def test_compatibility_placeholders_remain_available():
    _run_worker('placeholders')


def _run_worker(case):
    # Never pass inherited business configuration or secrets to the worker.
    env = {key: os.environ[key] for key in
           ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC')
           if key in os.environ}
    with TemporaryDirectory(prefix='medchat-static-test-') as temporary:
        directory = Path(temporary)
        env.update({
            'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONIOENCODING': 'utf-8',
            'MOLECULAR_CHAT_CONFIG': str(directory / 'missing.yaml'),
            'MEDCHAT_ENV_FILE': str(directory / 'not-loaded.env'),
            'MEDCHAT_USER_CONFIG_DIR': str(directory / 'user-config'),
            'MEDCHAT_LLM_LOCK_DIR': str(directory / 'llm-locks'),
            'AGENT_STATE_DB': str(directory / 'agent.sqlite'),
            'MEDCHAT_TASK_DB_PATH': str(directory / 'tasks.sqlite'),
            'MEDCHAT_TASK_BACKEND': 'local',
            'MEDCHAT_TEMPORAL_CANARY_PERCENT': '0',
        })
        result = subprocess.run(
            [sys.executable, '-B', str(Path(__file__).resolve()), '--worker', case],
            cwd=directory, env=env, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        records = [line for line in result.stdout.splitlines()
                   if line.startswith('STATIC_TEST_RESULT=')]
        assert len(records) == 1, result.stdout + result.stderr
        evidence = json.loads(records[0].split('=', 1)[1])
    evidence['temporary_removed'] = not directory.exists()
    return evidence


@pytest.mark.parametrize('case', ['cleanup-normal', 'cleanup-error'])
def test_worker_isolation_and_resource_cleanup(case):
    evidence = _run_worker(case)
    assert evidence['tool_factory_calls'] == 0, evidence
    assert evidence['agent_db_created'] is False, evidence
    assert evidence['open_clients'] == 0, evidence
    assert evidence['test_clients'] >= 1, evidence
    assert evidence['shutdown_calls'] == 1, evidence
    assert evidence['model_close_calls'] == [1, 1], evidence
    assert evidence['startup_calls'] == 0, evidence
    assert evidence['network_attempts'] == 0, evidence
    assert evidence['temporary_removed'] is True
    assert evidence['injected_error_seen'] is (case == 'cleanup-error')


def test_worker_does_not_replace_or_close_parent_app(monkeypatch):
    # A sentinel represents another test's already imported application.
    owned_elsewhere = ModuleType('src.web.app')
    owned_elsewhere.app_instance = object()
    monkeypatch.setitem(sys.modules, 'src.web.app', owned_elsewhere)
    monkeypatch.setenv('MOLECULAR_CHAT_CONFIG', 'parent-owned-config-do-not-read')
    _run_worker('cleanup-normal')
    assert sys.modules['src.web.app'] is owned_elsewhere
    assert os.environ['MOLECULAR_CHAT_CONFIG'] == 'parent-owned-config-do-not-read'


def _worker(case):
    import httpx

    sys.path.insert(0, str(ROOT))
    clients = []
    factory_calls = []
    network_attempts = []
    directory = Path.cwd()

    def protect_factory(*args, **kwargs):
        factory_calls.append(True)
        return []  # Protect the RED probe from initializing real science tools.

    def block_network(*args, **kwargs):
        network_attempts.append(True)
        raise AssertionError('Static-page tests must not connect to a service')

    def track(original):
        def init(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            clients.append(instance)
        return init

    with ExitStack() as stack:
        # Protect the historical fixture probe from unrelated import side effects.
        for name, setup in (
            ('src.web.routes.design_routes', 'setup_design_routes'),
            ('src.target_search.routes', 'setup_target_search_routes'),
        ):
            module = ModuleType(name)
            setattr(module, setup, lambda *args, **kwargs: None)
            stack.enter_context(patch.dict(sys.modules, {name: module}))
        stack.enter_context(patch('src.agent.tools.get_all_tools', protect_factory))
        # Block HTTP egress, not Windows asyncio's internal socketpair transport.
        stack.enter_context(patch.object(httpx.HTTPTransport, 'handle_request', side_effect=block_network))
        stack.enter_context(patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', side_effect=block_network))
        stack.enter_context(patch.object(httpx.Client, '__init__', track(httpx.Client.__init__)))
        stack.enter_context(patch.object(httpx.AsyncClient, '__init__', track(httpx.AsyncClient.__init__)))
        injected = False
        try:
            try:
                with _open_client(directory) as session:
                    from src.web.app import app_instance
                    shutdown = stack.enter_context(patch.object(
                        app_instance, 'shutdown', wraps=app_instance.shutdown))
                    close_main = stack.enter_context(patch.object(
                        app_instance.model, 'close', wraps=app_instance.model.close))
                    close_generator = stack.enter_context(patch.object(
                        app_instance.molecular_generator_model, 'close',
                        wraps=app_instance.molecular_generator_model.close))
                    startup = stack.enter_context(patch.object(
                        app_instance, 'initialize',
                        side_effect=AssertionError('Real lifespan must not start')))
                    if case == 'backup':
                        _check_legacy_backup(session)
                    elif case == 'placeholders':
                        _check_placeholders(session)
                    elif case in ('home', 'activity_prediction'):
                        for template, folder, expected in PAGE_SCRIPTS:
                            if folder == case:
                                _check_template_scripts(session, template, folder, expected)
                    elif case in ('cleanup-normal', 'cleanup-error'):
                        assert session.get('/static/js/script.js').status_code == 200
                    else:
                        raise AssertionError(f'Unknown worker case: {case}')
                    if case == 'cleanup-error':
                        raise RuntimeError('injected static request failure')
            except RuntimeError as exc:
                if str(exc) != 'injected static request failure':
                    raise
                injected = True
            evidence = {
                'tool_factory_calls': len(factory_calls),
                'agent_db_created': (directory / 'agent.sqlite').exists(),
                'open_clients': sum(not item.is_closed for item in clients),
                'test_clients': sum(isinstance(item, TestClient) for item in clients),
                'injected_error_seen': injected,
                'shutdown_calls': shutdown.call_count,
                'model_close_calls': [close_main.call_count, close_generator.call_count],
                'startup_calls': startup.call_count,
                'network_attempts': len(network_attempts),
            }
            assert evidence['tool_factory_calls'] == 0, evidence
            assert evidence['agent_db_created'] is False, evidence
            assert evidence['open_clients'] == 0, evidence
            assert evidence['shutdown_calls'] == 1, evidence
            assert evidence['model_close_calls'] == [1, 1], evidence
            assert evidence['startup_calls'] == 0, evidence
            assert evidence['network_attempts'] == 0, evidence
        finally:
            # Probe safety net, AFTER recording leaks; not credited as fixture cleanup.
            for item in clients:
                if not item.is_closed:
                    if isinstance(item, httpx.AsyncClient):
                        asyncio.run(item.aclose())
                    else:
                        item.close()
    print('STATIC_TEST_RESULT=' + json.dumps(evidence))


if __name__ == '__main__':
    assert sys.argv[1] == '--worker'
    _worker(sys.argv[2])
