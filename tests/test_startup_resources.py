"""Exercise CLI preflight without starting a Web server or loading models."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from src.system.env import load_env_file


ROOT = Path(__file__).resolve().parents[1]


def run_preflight(tmp_path, snippet):
    env = {key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP")
           if key in os.environ}
    env.update(PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    child = "from pathlib import Path\nimport main\n" + textwrap.dedent(snippet)
    result = subprocess.run([sys.executable, "-B", "-c", child], env=env, cwd=tmp_path,
                            capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_frontend_preflight_checks_published_files_and_leaves_checkout_untouched(tmp_path):
    run_preflight(tmp_path, """
        main.project_root = Path.cwd()
        home = Path('src/web/templates/index.html')
        script = Path('src/web/static/js/home/main.js')
        home.parent.mkdir(parents=True)
        script.parent.mkdir(parents=True)
        home.write_text('<script src="/static/js/home/main.js?v=1"></script>', encoding='utf8')
        script.write_text('', encoding='utf8')
        assert main.check_frontend_files()
        script.unlink()
        assert not main.check_frontend_files()
        other = Path('archive/index.html')
        other.parent.mkdir()
        other.write_text('retired page', encoding='utf8')
        home.unlink()
        assert not main.check_frontend_files()
        assert not home.exists()
    """)


def test_startup_does_not_create_source_packages(tmp_path):
    run_preflight(tmp_path, """
        main.setup_project_structure()
        assert not Path('src').exists()
    """)


@pytest.mark.parametrize("path", ["absent.yaml", "."])
def test_invalid_explicit_config_stops_before_uvicorn(tmp_path, path):
    run_preflight(tmp_path, f"""
        import sys
        from types import SimpleNamespace
        main.setup_project_structure = lambda: None
        main.check_environment = lambda: (True, [])
        main.check_frontend_files = lambda: True
        main.create_default_config = lambda: 'fallback.yaml'
        calls = []
        sys.modules['uvicorn'] = SimpleNamespace(run=lambda *a, **kw: calls.append(kw))
        sys.argv = ['main.py', '--config', {path!r}]
        assert main.main() != 0
        assert not calls
    """)


def test_ollama_startup_probe_uses_configured_endpoint(tmp_path):
    run_preflight(tmp_path, """
        import httpx, os
        from unittest.mock import Mock
        os.environ['OLLAMA_BASE_URL'] = 'http://127.0.0.1:12345/'
        httpx.get = Mock(return_value=httpx.Response(200, json={'models': []}))
        main.check_environment()
        httpx.get.assert_called_once_with('http://127.0.0.1:12345/api/tags', timeout=2)
    """)


def test_env_file_accepts_utf8_bom_without_overriding_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_TEST_FIRST_KEY", "")
    monkeypatch.delenv("MEDCHAT_TEST_FIRST_KEY")
    monkeypatch.setenv("MEDCHAT_TEST_EXISTING", "process")
    path = tmp_path / "settings.env"
    path.write_text("MEDCHAT_TEST_FIRST_KEY=first\nMEDCHAT_TEST_EXISTING=file\n", encoding="utf-8-sig")
    load_env_file(path)
    assert os.environ.get("MEDCHAT_TEST_FIRST_KEY") == "first"
    assert os.environ["MEDCHAT_TEST_EXISTING"] == "process"
