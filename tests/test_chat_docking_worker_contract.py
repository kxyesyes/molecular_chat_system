"""Synthetic startup only: no serving sockets, models, tools or real env files."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]


def run_startup(tmp_path, *, arguments=(), environment=None, env_text="",
                env_source="cli", rejected=False, expected_workers=1,
                expected_reload=False):
    # Do not inherit application configuration, credentials or live services.
    environment = environment or {}
    child_env = {
        key: value for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP",
                           "COMSPEC", "PATHEXT"}
    }
    child_env.update(environment)
    child_env["PYTHONDONTWRITEBYTECODE"] = "1"
    env_file = tmp_path / (".env" if env_source == "default" else "synthetic.env")
    env_file.write_text(env_text, encoding="utf-8")
    argv = ["main.py", *arguments]
    if env_source == "cli":
        argv += ["--env-file", str(env_file)]
    elif env_source == "environment":
        child_env["MEDCHAT_ENV_FILE"] = str(env_file)
    script = f'''
        import os
        import sys
        from types import ModuleType
        sys.path.insert(0, {str(ROOT)!r})
        # Import the actual entrypoint, not src.web.app (which assembles tools).
        import main
        calls = []
        def record(name, result=None):
            def call(*args, **kwargs):
                calls.append((name, args, kwargs))
                return result
            return call
        main.setup_project_structure = record('structure')
        main.check_environment = record('environment', (True, []))
        main.check_frontend_files = record('frontend', True)
        main.create_default_config = record('config', 'synthetic-not-opened.yaml')
        uvicorn = ModuleType('uvicorn')
        uvicorn.run = record('server')
        sys.modules['uvicorn'] = uvicorn
        sys.argv = {argv!r}
        result = main.main()
        assert 'src.web.app' not in sys.modules
        if {rejected!r}:
            assert result != 0, ('unsafe startup accepted', result, calls)
            assert calls == [], ('fail-fast must precede startup effects', calls)
            assert os.environ['MEDCHAT_CHAT_DOCKING_ENABLED'] == '1'
        else:
            assert result == 0, result
            assert [call[0] for call in calls] == [
                'structure', 'environment', 'frontend', 'config', 'server']
            _, args, kwargs = calls[-1]
            assert args == ('src.web.app:app',)
            assert kwargs['workers'] == {expected_workers!r}, kwargs
            assert kwargs['reload'] is {expected_reload!r}, kwargs
    '''
    result = subprocess.run(
        [sys.executable, "-B", "-"], input=textwrap.dedent(script),
        cwd=tmp_path, env=child_env, capture_output=True, text=True,
        encoding="utf-8", timeout=30,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    if rejected:
        assert "MEDCHAT_CHAT_DOCKING_ENABLED=1" in output
        assert "requires exactly one worker" in output
        assert "process-local" in output
        assert "--workers 1" in output
        assert "MEDCHAT_WORKERS=1" in output
        assert "using workers=1" not in output


@pytest.mark.parametrize("arguments,environment", [
    (("--workers", "2"), {"MEDCHAT_CHAT_DOCKING_ENABLED": "1"}),
    ((), {"MEDCHAT_CHAT_DOCKING_ENABLED": "1", "MEDCHAT_WORKERS": "2"}),
    (("--workers", "3"), {"MEDCHAT_CHAT_DOCKING_ENABLED": "1", "MEDCHAT_WORKERS": "1"}),
    (("--workers", "2", "--reload"), {"MEDCHAT_CHAT_DOCKING_ENABLED": "1"}),
    ((), {"MEDCHAT_CHAT_DOCKING_ENABLED": "1", "MEDCHAT_WORKERS": "2",
          "MEDCHAT_RELOAD": "1"}),
])
def test_chat_docking_rejects_multiple_workers_before_any_startup(
        tmp_path, arguments, environment):
    run_startup(tmp_path, arguments=arguments, environment=environment, rejected=True)


@pytest.mark.parametrize("env_source", ["cli", "environment", "default"])
@pytest.mark.parametrize("cli_workers", [False, True])
def test_env_file_is_loaded_before_chat_docking_worker_validation(
        tmp_path, env_source, cli_workers):
    run_startup(
        tmp_path, env_source=env_source,
        env_text='MEDCHAT_CHAT_DOCKING_ENABLED="1"\nMEDCHAT_WORKERS=2\n',
        arguments=("--workers", "2") if cli_workers else (), rejected=True,
    )


@pytest.mark.parametrize("flag", [None, "0", "", "true", "TRUE", "yes", "on", " 1 ", "01"])
@pytest.mark.parametrize("via_environment", [False, True])
def test_only_exact_one_enables_chat_docking_and_disabled_preserves_multiworker(
        tmp_path, flag, via_environment):
    environment = {} if flag is None else {"MEDCHAT_CHAT_DOCKING_ENABLED": flag}
    if via_environment:
        environment["MEDCHAT_WORKERS"] = "3"
    run_startup(
        tmp_path, environment=environment,
        arguments=() if via_environment else ("--workers", "3"), expected_workers=3,
    )


@pytest.mark.parametrize("arguments,environment,env_text", [
    ((), {"MEDCHAT_CHAT_DOCKING_ENABLED": "1"}, ""),
    ((), {"MEDCHAT_CHAT_DOCKING_ENABLED": "1", "MEDCHAT_WORKERS": "1"}, ""),
    (("--workers", "1"), {"MEDCHAT_CHAT_DOCKING_ENABLED": "1", "MEDCHAT_WORKERS": "2"}, ""),
    (("--workers", "1"), {}, "MEDCHAT_CHAT_DOCKING_ENABLED=1\nMEDCHAT_WORKERS=2\n"),
    ((), {"MEDCHAT_WORKERS": "1"}, "MEDCHAT_CHAT_DOCKING_ENABLED=1\nMEDCHAT_WORKERS=2\n"),
    ((), {"MEDCHAT_CHAT_DOCKING_ENABLED": "1", "MEDCHAT_WORKERS": "invalid"}, ""),
])
def test_chat_docking_single_worker_and_existing_worker_fallback_remain_supported(
        tmp_path, arguments, environment, env_text):
    run_startup(tmp_path, arguments=arguments, environment=environment, env_text=env_text)


def test_process_disabled_flag_keeps_precedence_over_env_file(tmp_path):
    run_startup(
        tmp_path, environment={"MEDCHAT_CHAT_DOCKING_ENABLED": "0"},
        env_text="MEDCHAT_CHAT_DOCKING_ENABLED=1\nMEDCHAT_WORKERS=2\n",
        expected_workers=2,
    )


@pytest.mark.parametrize("arguments", [("--reload",), ("--reload", "--no-reload")])
def test_chat_docking_single_worker_retains_reload_controls(tmp_path, arguments):
    run_startup(
        tmp_path, arguments=arguments, environment={"MEDCHAT_CHAT_DOCKING_ENABLED": "1"},
        expected_reload="--no-reload" not in arguments,
    )


def test_disabled_chat_docking_retains_existing_reload_worker_behavior(tmp_path):
    run_startup(tmp_path, arguments=("--workers", "2", "--reload"), expected_reload=True)


def test_deployment_documents_entrypoint_guarantee_and_external_asgi_boundary():
    source = (ROOT / "deployment/README.md").read_text(encoding="utf-8")
    assert "MEDCHAT_CHAT_DOCKING_ENABLED=1" in source
    assert "聊天对接必须使用单 worker" in source
    assert "process-local" in source
    assert "fail-fast" in source
    assert "无法可靠获知外部 Uvicorn" in source
    # Preserve the existing RDKit startup boundary for all other functionality.
    assert "`python main.py --workers N`（或 `MEDCHAT_WORKERS=N`）" in source
    assert "不属于支持的多 worker 启动方式" in source
