"""Offline Pi readiness and release wiring; subprocesses are mocked, never models."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from scripts import health_check


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = '@earendil-works/pi-coding-agent'
REPAIR = 'npm ci --prefix runtime/pi --ignore-scripts'


@pytest.fixture
def installation(tmp_path, monkeypatch):
    runtime = tmp_path / 'runtime' / 'pi'
    runtime.mkdir(parents=True)
    manifest = {'dependencies': {PACKAGE: '1.4.0'}}
    lock = {'lockfileVersion': 3, 'packages': {
        '': manifest, f'node_modules/{PACKAGE}': {'version': '1.4.0'},
    }}
    (runtime / 'package.json').write_text(json.dumps(manifest), encoding='utf-8')
    (runtime / 'package-lock.json').write_text(json.dumps(lock), encoding='utf-8')
    monkeypatch.setattr(health_check, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(health_check, 'shutil_which', lambda name: 'node-fixture' if name == 'node' else None)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(health_check.subprocess, 'run', run)
    return runtime, calls


def probe():
    check = getattr(health_check, 'check_pi_runtime', None)
    assert callable(check), 'official Pi readiness check is missing'
    return check()


def test_readiness_imports_real_sdk_without_creating_session(installation):
    runtime, calls = installation
    ok, detail = probe()
    assert ok is True and PACKAGE in detail and '1.4.0' in detail
    assert '22.19.0' in detail
    assert len(calls) == 1
    argv, options = calls[0]
    assert argv[:3] == ['node-fixture', '--input-type=module', '--eval']
    code = argv[3]
    assert f"import('{PACKAGE}')" in code
    assert 'process.versions.node' in code and '[22, 19, 0]' in code
    assert "sdk.VERSION !== '1.4.0'" in code
    assert 'createAgentSession' in code
    assert 'createAgentSession(' not in code and 'ModelRuntime.create(' not in code
    assert 'session.prompt(' not in code
    assert Path(options['cwd']) == runtime
    assert options.get('shell', False) is False
    assert options['timeout'] == 15
    assert options['stdin'] == subprocess.DEVNULL
    assert options['stdout'] == options['stderr'] == subprocess.DEVNULL
    assert options['creationflags'] == getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def test_probe_child_environment_drops_secrets_injection_and_user_config(installation, monkeypatch):
    _, calls = installation
    forbidden = ['OPENAI_API_KEY', 'OPENAI_COMPATIBLE_API_KEY', 'ANTHROPIC_API_KEY',
                 'DEEPSEEK_API_KEY', 'MODELSCOPE_API_KEY', 'AWS_SECRET_ACCESS_KEY',
                 'HTTP_PROXY', 'HTTPS_PROXY', 'NODE_OPTIONS', 'NODE_PATH',
                 'PI_CODING_AGENT_DIR', 'PI_CONFIG_PATH', 'PI_API_KEY',
                 'MEDCHAT_USER_CONFIG_DIR', 'MEDCHAT_ENV_FILE', 'NPM_CONFIG_USERCONFIG']
    for key in forbidden:
        monkeypatch.setenv(key, 'private-parent-marker')
    for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'XDG_CACHE_HOME'):
        monkeypatch.setenv(key, 'private-home-marker')
    monkeypatch.setenv('PI_OFFLINE', '0')
    monkeypatch.setattr(health_check, 'load_env_file', lambda *_: pytest.fail('probe loaded user config'))
    ok, detail = probe()
    assert ok is True
    child = calls[0][1]['env']
    assert not set(forbidden) & set(child)
    assert child['PI_OFFLINE'] == '1'
    assert 'private-' not in repr(child) + detail
    assert child['HOME'] == child['USERPROFILE']
    assert child['APPDATA'] == child['LOCALAPPDATA'] == child['XDG_CONFIG_HOME'] == child['XDG_CACHE_HOME'] == child['HOME']
    assert not Path(child['HOME']).exists(), 'temporary probe home must be cleaned up'


def test_missing_node_is_actionable_and_does_not_spawn(installation, monkeypatch):
    _, calls = installation
    monkeypatch.setattr(health_check, 'shutil_which', lambda _: None)
    ok, detail = probe()
    assert not ok and 'Node' in detail and '22.19.0' in detail and REPAIR in detail
    assert calls == []


@pytest.mark.parametrize('filename', ['package.json', 'package-lock.json'])
@pytest.mark.parametrize('damage', ['missing', 'malformed', 'oversized', 'wrong_shape'])
def test_missing_or_bad_manifests_fail_before_subprocess(installation, filename, damage):
    runtime, calls = installation
    path = runtime / filename
    if damage == 'missing':
        path.unlink()
    elif damage == 'malformed':
        path.write_text('private-manifest-marker', encoding='utf-8')
    elif damage == 'oversized':
        path.write_text(' ' * (1024 * 1024 + 1), encoding='utf-8')
    else:
        path.write_text('[]', encoding='utf-8')
    ok, detail = probe()
    assert not ok and REPAIR in detail
    assert 'private-manifest-marker' not in detail and calls == []


@pytest.mark.parametrize('target', ['manifest', 'lock_root', 'lock_package'])
@pytest.mark.parametrize('version', ['^1.4.0', '1.3.0', None])
def test_fixed_package_version_must_match_manifest_and_lock(installation, target, version):
    runtime, calls = installation
    path = runtime / ('package.json' if target == 'manifest' else 'package-lock.json')
    value = json.loads(path.read_text('utf-8'))
    if target == 'manifest':
        value['dependencies'][PACKAGE] = version
    elif target == 'lock_root':
        value['packages']['']['dependencies'][PACKAGE] = version
    else:
        value['packages'][f'node_modules/{PACKAGE}']['version'] = version
    path.write_text(json.dumps(value), encoding='utf-8')
    ok, detail = probe()
    assert not ok and '1.4.0' in detail and REPAIR in detail and calls == []


@pytest.mark.parametrize('returncode,reason', [
    (10, '22.19.0'), (11, '1.4.0'), (12, 'import'), (13, 'exports'), (99, 'failed'),
])
def test_probe_failures_are_actionable_without_child_output(installation, monkeypatch, returncode, reason):
    monkeypatch.setattr(health_check.subprocess, 'run', lambda *_a, **_k: SimpleNamespace(
        returncode=returncode, stdout='private-output-marker', stderr='private-output-marker'))
    ok, detail = probe()
    assert not ok and reason in detail and REPAIR in detail
    assert 'private-output-marker' not in detail


@pytest.mark.parametrize('error', [
    OSError('private-exception-marker'),
    subprocess.TimeoutExpired('private-exception-marker', 15, output='private-output-marker'),
])
def test_probe_exception_diagnostics_do_not_echo_process_or_environment(installation, monkeypatch, error):
    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(health_check.subprocess, 'run', fail)
    ok, detail = probe()
    assert not ok and REPAIR in detail and 'private-' not in detail


@pytest.mark.parametrize('strict,expected', [(False, 0), (True, 1)])
def test_pi_readiness_is_part_of_existing_health_gate(monkeypatch, capsys, strict, expected):
    seen = []
    for name in vars(health_check):
        if name.startswith('check_'):
            monkeypatch.setattr(health_check, name, lambda: (True, 'mocked'))

    def pi():
        seen.append('pi')
        return False, 'Node unavailable'

    monkeypatch.setattr(health_check, 'check_pi_runtime', pi, raising=False)
    assert health_check.run_checks(strict) == expected
    assert seen == ['pi']
    assert '[FAIL] Pi Runtime: Node unavailable' in capsys.readouterr().out


def test_ci_installs_fixed_node_and_locked_pi_for_every_python_partition():
    workflow = yaml.safe_load((ROOT / '.github/workflows/quality.yml').read_text('utf-8'))
    for job_name in ('python-tests', 'static-quality'):
        steps = workflow['jobs'][job_name]['steps']
        setup = [step for step in steps if step.get('uses', '').startswith('actions/setup-node@')]
        assert len(setup) == 1 and setup[0]['with']['node-version'] == '24.15.0'
        assert 'if' not in setup[0]
        installs = [step for step in steps if 'npm ci --prefix runtime/pi --ignore-scripts --no-audit --no-fund' in step.get('run', '')]
        assert len(installs) == 1 and 'if' not in installs[0]
        assert steps.index(installs[0]) > steps.index(setup[0])
        first_tests = next(i for i, step in enumerate(steps) if step['name'] in {'Run Python tests', 'Run Node contract tests'})
        assert steps.index(installs[0]) < first_tests
    node_runs = [(name, step) for name, job in workflow['jobs'].items() for step in job['steps']
                 if 'npm test --prefix runtime/pi' in step.get('run', '')]
    assert len(node_runs) == 1 and node_runs[0][0] == 'static-quality'


def test_ci_preserves_partitions_timeout_budgets_and_quality_dependencies():
    source = (ROOT / '.github/workflows/quality.yml').read_text('utf-8')
    workflow = yaml.safe_load(source)
    rows = workflow['jobs']['python-tests']['strategy']['matrix']['include']
    assert {row['name']: row['command_timeout'] for row in rows} == {
        'agent': 1200, 'agent-web-lifecycle': 600, 'agent-docking': 1200,
        'agent-browser-chat': 1200, 'agent-decision-bindings': 1200,
        'agent-decision': 1200, 'agent-pi': 1200, 'agent-web': 1200,
        'agent-docking-publication': 1200, 'sandbox-api': 180, 'sandbox-core': 600,
        'task-runtime': 600, 'root': 600, 'root-activity': 900,
    }
    assert workflow['jobs']['python-tests']['timeout-minutes'] == 30
    assert workflow['jobs']['static-quality']['timeout-minutes'] == 10
    assert workflow['jobs']['offline-quality']['needs'] == ['python-tests', 'static-quality']
    for gate in ('set.union(*partitions) != original', 'COLLECTION_TOTAL_SECONDS = 180',
                 'COLLECTION_CHILD_SECONDS = 60', 'python -m compileall -q src scripts',
                 'node tests/activity_family_acceptance_dom.js',
                 'test "$PYTHON_TESTS_RESULT" = "success"', 'test "$STATIC_QUALITY_RESULT" = "success"'):
        assert gate in source
    assert 'AGENT_HARNESS_MODE' not in source and 'AGENT_LANGGRAPH_CANARY_PERCENT' not in source


def test_example_environment_uses_pi_without_obsolete_harness_knobs():
    source = (ROOT / '.env.example').read_text('utf-8')
    assert 'MEDCHAT_CHAT_PROFILE=pi' in source
    assert 'AGENT_HARNESS_MODE' not in source and 'AGENT_LANGGRAPH_CANARY_PERCENT' not in source
    assert 'MEDCHAT_CHAT_PROFILE=legacy' not in source
    assert 'MEDCHAT_DECISION_WIRE_MODE=native' in source


def test_obsolete_harness_pin_file_and_include_are_removed():
    assert not (ROOT / 'requirements-agent-harness.txt').exists()
    assert 'requirements-agent-harness.txt' not in (ROOT / 'requirements-ci.txt').read_text('utf-8')


def test_pi_runbook_explains_recovery_and_acceptance_limits():
    path = ROOT / 'docs/PI_RUNTIME.md'
    assert path.is_file(), 'Pi operator runbook is missing'
    source = path.read_text('utf-8')
    for text in ('24.15.0', '22.19.0', '1.4.0', 'npm ci --prefix runtime/pi --ignore-scripts',
                 'python main.py --port 6001', 'pi_protocol_revision', '9/10/11',
                 '不自动', '待验收', 'NODE_OPTIONS'):
        assert text in source
