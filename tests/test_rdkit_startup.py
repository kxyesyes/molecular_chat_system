"""Offline real-spawn/Uvicorn startup probes; no sockets, services, or assets.

Keep this module free of RDKit imports: Uvicorn must unpickle the probe AFTER
spawn has replayed main.py, without accidentally warming the privacy router.
"""
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


class AppPrefixProbe:
    def __init__(self, connection):
        self.connection = connection

    def __call__(self, sockets):
        import ast
        import multiprocessing
        import threading

        assert not sockets
        assert multiprocessing.get_start_method() == 'spawn'
        # Process.target has started the real heartbeat thread by this point.
        assert len(threading.enumerate()) > 1
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'src/web/app.py').read_text(encoding='utf-8'))
        prefix = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                break
            prefix.append(node)
        try:
            # Execute the actual app bootstrap, stopping before configuration,
            # database/model loading and other external application dependencies.
            exec(compile(ast.Module(body=prefix, type_ignores=[]), 'app-bootstrap', 'exec'), {})
            from src.system import rdkit_logging as privacy
            assert privacy._installed
            self.connection.send(('ok',))
        except RuntimeError as error:
            self.connection.send(('error', str(error)))
        assert self.connection.recv() == 'finish'


def run_spawn_probe(tmp_path, entry):
    root = Path(__file__).resolve().parents[1]
    script = '''
        import importlib.util
        import multiprocessing
        from pathlib import Path
        import sys
        sys.path.insert(0, ROOT)
        from uvicorn import Config
        from uvicorn.supervisors.multiprocess import Process
        from tests.test_rdkit_startup import AppPrefixProbe

        # Supply exactly the __main__ metadata seen by spawn for these launchers.
        # The real preparation/unpickling/Process.target path is NOT mocked.
        main = sys.modules['__main__']
        main.__file__ = str(Path(ROOT) / 'main.py')
        main.__spec__ = (None if ENTRY == 'main-path' else
                        importlib.util.find_spec('main' if ENTRY == 'main-module'
                                                else 'uvicorn.__main__'))
        assert 'rdkit' not in sys.modules
        parent, child = multiprocessing.get_context('spawn').Pipe()
        worker = Process(Config('src.web.app:app', workers=2, log_config=None),
                         AppPrefixProbe(child), [])
        try:
            worker.start()
            assert parent.poll(15), 'child failed before reporting app bootstrap'
            result = parent.recv()
            assert worker.ping(5), 'real Uvicorn heartbeat did not answer'
            parent.send('finish')
            worker.process.join(10)
            assert worker.process.exitcode == 0
            if ENTRY == 'direct-uvicorn':
                assert result[0] == 'error' and 'before starting worker threads' in result[1], result
            else:
                assert result == ('ok',), result
        finally:
            if worker.process.is_alive():
                worker.process.kill()
                worker.process.join(5)
            parent.close()
            child.close()
            worker.parent_conn.close()
            worker.child_conn.close()
            worker.process.close()
    '''.replace('ROOT', repr(str(root))).replace('ENTRY', repr(entry))
    result = subprocess.run([sys.executable, '-'], input=textwrap.dedent(script),
                            cwd=tmp_path, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('entry', ['main-path', 'main-module'])
def test_spawn_child_bootstraps_before_uvicorn_supervisor(tmp_path, entry):
    run_spawn_probe(tmp_path, entry)


def test_direct_uvicorn_multiworker_still_fails_closed(tmp_path):
    run_spawn_probe(tmp_path, 'direct-uvicorn')


@pytest.mark.parametrize('via_environment', [False, True])
def test_main_cli_preserves_multiworker_configuration_without_external_startup(tmp_path, via_environment):
    root = Path(__file__).resolve().parents[1]
    script = '''
        import os
        import sys
        from unittest.mock import patch
        sys.path.insert(0, ROOT)
        import main
        # Bypass only unrelated filesystem/configuration/service effects. Parse
        # the real CLI/env configuration and inspect the actual Uvicorn call.
        main.load_env_file = lambda path: None
        main.setup_project_structure = lambda: None
        main.check_environment = lambda: (True, [])
        main.check_frontend_files = lambda: True
        main.create_default_config = lambda: 'synthetic-not-opened.yaml'
        os.environ['MEDCHAT_WORKERS'] = '2' if VIA_ENV else '1'
        sys.argv = ['main.py', '--no-reload'] + ([] if VIA_ENV else ['--workers', '2'])
        with patch('uvicorn.run') as run:
            assert main.main() == 0
            run.assert_called_once()
            assert run.call_args.args == ('src.web.app:app',)
            assert run.call_args.kwargs['workers'] == 2
            assert run.call_args.kwargs['reload'] is False
    '''.replace('ROOT', repr(str(root))).replace('VIA_ENV', repr(via_environment))
    result = subprocess.run([sys.executable, '-'], input=textwrap.dedent(script),
                            cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
