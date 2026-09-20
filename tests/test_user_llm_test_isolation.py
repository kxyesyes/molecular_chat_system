"""Configuration isolation must precede collection-time application imports."""
import os
from pathlib import Path
import subprocess
import sys


def test_collection_hook_isolates_and_restores_environment(tmp_path):
    conftest = Path(__file__).with_name("conftest.py")
    code = '''
import os, runpy
from pathlib import Path
from types import SimpleNamespace
ns = runpy.run_path(os.environ['TEST_CONFTEST'])
before = os.environ['MEDCHAT_USER_CONFIG_DIR']
config = SimpleNamespace()
ns['pytest_configure'](config)
isolated = Path(os.environ['MEDCHAT_USER_CONFIG_DIR'])
assert str(isolated) != before
assert isolated.parent.is_dir()
assert not isolated.exists()
ns['pytest_unconfigure'](config)
assert os.environ['MEDCHAT_USER_CONFIG_DIR'] == before
assert not isolated.parent.exists()
'''
    env = dict(os.environ, TEST_CONFTEST=str(conftest), MEDCHAT_USER_CONFIG_DIR=str(tmp_path / "sentinel"))
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
