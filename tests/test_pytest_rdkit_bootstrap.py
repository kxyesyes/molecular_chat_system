"""The pytest embedder must bootstrap privacy before test-owned worker threads."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


def fresh_python(script):
    # This helper must remain dependency-light: importing the RDKit test module
    # just to reuse a subprocess wrapper would fail before optional tests collect.
    result = subprocess.run([sys.executable, '-'], input=textwrap.dedent(script),
        text=True, capture_output=True, timeout=30, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.mark.skipif(importlib.util.find_spec('rdkit') is None, reason='RDKit is not installed')
def test_pytest_configure_initializes_before_threads_and_preserves_isolation():
    fresh_python('''
        from types import SimpleNamespace
        import os
        import threading
        from tests.conftest import pytest_configure, pytest_unconfigure
        from src.system import rdkit_logging
        assert not rdkit_logging._installed
        config = SimpleNamespace()
        before = os.environ.get('MEDCHAT_USER_CONFIG_DIR')
        try:
            pytest_configure(config)
            assert rdkit_logging._installed, 'pytest must bootstrap before starting test workers'
            assert os.environ['MEDCHAT_USER_CONFIG_DIR'] != before
            failures = []
            def worker():
                try:
                    with rdkit_logging.suppress_rdkit_diagnostics():
                        pass
                except Exception as error:
                    failures.append(type(error).__name__)
            task = threading.Thread(target=worker)
            task.start()
            task.join()
            assert failures == []
        finally:
            pytest_unconfigure(config)
        assert os.environ.get('MEDCHAT_USER_CONFIG_DIR') == before
    ''')


def test_dependency_light_pytest_configuration_does_not_require_rdkit():
    fresh_python('''
        from types import SimpleNamespace
        import importlib.util
        import sys
        from tests.conftest import pytest_configure, pytest_unconfigure
        find = importlib.util.find_spec
        importlib.util.find_spec = lambda name, *a, **kw: None if name == 'rdkit' else find(name, *a, **kw)
        config = SimpleNamespace()
        try:
            pytest_configure(config)
            assert 'rdkit' not in sys.modules
        finally:
            pytest_unconfigure(config)
    ''')


def test_bootstrap_test_module_can_collect_without_rdkit():
    fresh_python('''
        import builtins
        import importlib.util
        import pytest
        original_import = builtins.__import__
        original_find = importlib.util.find_spec
        def blocked_import(name, *args, **kwargs):
            if name == 'rdkit' or name.startswith('rdkit.'):
                raise ModuleNotFoundError('rdkit intentionally absent for test')
            return original_import(name, *args, **kwargs)
        builtins.__import__ = blocked_import
        importlib.util.find_spec = lambda name, *a, **kw: None if name == 'rdkit' else original_find(name, *a, **kw)
        raise SystemExit(pytest.main(['tests/test_pytest_rdkit_bootstrap.py', '--collect-only', '-q', '-p', 'no:cacheprovider']))
    ''')
