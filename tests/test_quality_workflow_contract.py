from pathlib import Path
import json
import os
import shlex
import shutil
import subprocess
import sys

import yaml
import pytest
import ast


ROOT = Path(__file__).resolve().parents[1]
AGENT_WEB_TARGET = (
    "tests/agent/test_ordinary_web_lifecycle.py "
    "tests/agent/test_ordinary_web_runtime.py "
    "tests/agent/test_web_decision_runtime_lifecycle.py"
)
AGENT_CORE_TARGET = "tests/agent " + " ".join(
    "--ignore=" + path for path in AGENT_WEB_TARGET.split()
)
AGENT_DOCKING_TARGET = "tests/agent/test_*docking*.py"
AGENT_PUBLICATION_TARGET = "tests/agent/test_docking_publication*.py"


def test_quality_workflow_uses_complete_cpu_profile() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")

    assert "python -m pip install -r requirements-ci.txt" in workflow


def test_cpu_ci_profile_contains_collection_dependencies() -> None:
    requirements = (ROOT / "requirements-ci.txt").read_text("utf-8")

    for expected in (
        "-r requirements.txt",
        "-r requirements-agent-harness.txt",
        "-r requirements-agent-temporal.txt",
        "--extra-index-url https://download.pytorch.org/whl/cpu",
        "torch==2.4.0+cpu",
        "torch-geometric==2.6.1",
        "pytest-timeout==2.3.1",
    ):
        assert expected in requirements


def test_quality_workflow_runs_every_tracked_node_contract() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")

    assert "find tests -maxdepth 1 -type f -name '*_test.js'" in workflow
    assert 'node "$test_file"' in workflow


def test_quality_workflow_runs_family_dom_selftests_in_static_job() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")
    static_job = workflow.split("  static-quality:\n", 1)[1].split("  offline-quality:\n", 1)[0]

    assert "          node tests/activity_family_acceptance_dom.js\n" in static_job


def test_quality_workflow_installs_node_for_root_python_chain() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")
    python_job = workflow.split("  python-tests:\n", 1)[1].split("  static-quality:\n", 1)[0]

    assert (
        "        if: matrix.name == 'root' || matrix.name == 'root-activity'\n"
        "        uses: actions/setup-node@v4\n"
        "        with:\n"
        '          node-version: "20"\n'
    ) in python_job
    assert python_job.index("uses: actions/setup-node@v4") < python_job.index("- name: Run Python tests")


def test_quality_workflow_disables_real_family_acceptance_for_all_jobs() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")
    workflow_settings = workflow.split("\njobs:\n", 1)[0]

    assert '\nenv:\n  MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE: "0"\n' in workflow_settings
    assert workflow.count("MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE:") == 1


def test_quality_workflow_shards_python_suite_and_preserves_final_gate() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")

    assert "python-tests:" in workflow
    assert "static-quality:" in workflow
    assert "offline-quality:" in workflow
    assert "needs: [python-tests, static-quality]" in workflow
    assert (
        "timeout ${{ matrix.command_timeout }}s python -m pytest "
        "${{ matrix.pytest_target }} -q -p no:cacheprovider "
        "${{ matrix.pytest_args }}"
    ) in workflow
    assert workflow.count("command_timeout:") == 9
    assert "command_timeout: 180" in workflow
    for target in (
        AGENT_CORE_TARGET,
        AGENT_WEB_TARGET,
        AGENT_DOCKING_TARGET,
        AGENT_PUBLICATION_TARGET,
        "tests/sandbox_broker/test_api.py",
        "tests/sandbox_broker --ignore=tests/sandbox_broker/test_api.py",
        "tests/task_runtime",
        "tests/test_activity*.py",
        "tests --ignore=tests/agent --ignore=tests/sandbox_broker --ignore=tests/task_runtime",
    ):
        assert f'pytest_target: "{target}"' in workflow

    assert workflow.count("--timeout=60") == 2
    assert workflow.count('pytest_args: "--timeout=60"') == 2

    assert "run: python -m pytest tests -q -p no:cacheprovider" not in workflow
    assert "fetch-depth: 0" not in workflow


def _quality_jobs():
    return yaml.safe_load((ROOT / ".github/workflows/quality.yml").read_text("utf-8"))["jobs"]


def test_agent_timeout_diagnostics_only_add_named_progress_and_durations():
    matrix = {row["name"]: row for row in
              _quality_jobs()["python-tests"]["strategy"]["matrix"]["include"]}
    assert matrix["agent"] == {
        "name": "agent", "pytest_target": AGENT_CORE_TARGET,
        "pytest_exclude": AGENT_DOCKING_TARGET,
        "pytest_args": "-vv --durations=25", "command_timeout": 1200,
    }
    assert matrix["agent-web-lifecycle"] == {
        "name": "agent-web-lifecycle", "pytest_target": AGENT_WEB_TARGET,
        "pytest_args": "-vv --durations=25", "command_timeout": 600,
    }
    assert matrix["agent-docking"] == {
        "name": "agent-docking", "pytest_target": AGENT_DOCKING_TARGET,
        "pytest_exclude": AGENT_PUBLICATION_TARGET,
        "pytest_args": "-vv --durations=25", "command_timeout": 1200,
    }
    assert {name: row["pytest_args"] for name, row in matrix.items()
            if name not in {"agent", "agent-web-lifecycle", "agent-docking", "agent-docking-publication"}} == {
        "sandbox-api": "--timeout=60", "sandbox-core": "--timeout=60",
        "task-runtime": "", "root": "", "root-activity": "",
    }


def test_publication_partition_has_its_own_unchanged_budget_and_diagnostics():
    matrix = {row["name"]: row for row in
              _quality_jobs()["python-tests"]["strategy"]["matrix"]["include"]}
    assert "agent-docking-publication" in matrix
    assert matrix["agent-docking-publication"] == {
        "name": "agent-docking-publication", "pytest_target": AGENT_PUBLICATION_TARGET,
        "pytest_args": "-vv --durations=25", "command_timeout": 1200,
    }


def test_core_suite_budget_fits_unchanged_job_deadline():
    python = _quality_jobs()["python-tests"]
    matrix = {row["name"]: row for row in python["strategy"]["matrix"]["include"]}
    assert python["timeout-minutes"] == 30
    assert matrix["agent"]["command_timeout"] == 1200
    collection_seconds = 180
    assert python["timeout-minutes"] * 60 - (
        matrix["agent"]["command_timeout"] + collection_seconds
    ) >= 300


def test_root_partition_preserves_deadlines_and_all_jobs_gate():
    jobs = _quality_jobs()
    python = jobs["python-tests"]
    matrix = {row["name"]: row for row in python["strategy"]["matrix"]["include"]}
    assert set(matrix) == {"agent", "agent-web-lifecycle", "agent-docking", "agent-docking-publication", "sandbox-api", "sandbox-core", "task-runtime", "root", "root-activity"}
    assert python["strategy"]["fail-fast"] is False
    assert matrix["root"]["pytest_target"] == "tests --ignore=tests/agent --ignore=tests/sandbox_broker --ignore=tests/task_runtime"
    assert matrix["root"]["pytest_args"] == ""
    assert matrix["root-activity"]["pytest_target"] == "tests/test_activity*.py"
    assert matrix["root-activity"]["pytest_args"] == ""
    assert all(row["command_timeout"] == (
                   1200 if name in {"agent", "agent-docking", "agent-docking-publication"} else
                   900 if name == "root-activity" else
                   180 if name == "sandbox-api" else 600)
               for name, row in matrix.items())
    assert jobs["offline-quality"]["needs"] == ["python-tests", "static-quality"]
    assert jobs["offline-quality"]["if"] == "${{ always() }}"
    gate = jobs["offline-quality"]["steps"][0]
    assert 'test "$PYTHON_TESTS_RESULT" = "success"' in gate["run"]
    assert 'test "$STATIC_QUALITY_RESULT" = "success"' in gate["run"]
    run = next(step for step in python["steps"] if step["name"] == "Run Python tests")
    assert run["shell"] == "bash"
    assert 'if [ "${{ matrix.name }}" = "root" ]; then' in run["run"]
    assert 'for activity_test in tests/test_activity*.py; do' in run["run"]
    assert 'extra_args+=(--ignore "$activity_test")' in run["run"]
    assert '"${extra_args[@]}"' in run["run"]
    assert "--ignore-glob" not in run["run"]
    assert run['env']['PYTEST_EXCLUDE'] == '${{ matrix.pytest_exclude }}'
    assert 'for excluded_test in $PYTEST_EXCLUDE; do' in run['run']
    assert 'if [ -f "$excluded_test" ]; then' in run['run']
    assert 'extra_args+=(--ignore "$excluded_test")' in run['run']
    assert 'extra_args+=(--deselect "$excluded_test")' in run['run']


def test_root_partition_actual_pytest_collection_is_disjoint_and_exhaustive(tmp_path):
    # A future nested activity module must stay in root, not disappear under a
    # broad filename filter. All three pre-existing excluded domains stay out.
    files = (
        "tests/test_activity.py", "tests/test_activity_future.py",
        "tests/test_activity_family_chain.py", "tests/test_new_root.py",
        "tests/new_area/test_activity_nested.py", "tests/new_area/feature_test.py",
        "tests/test_activity_extra/test_nested.py",
        "tests/agent/test_agent.py", "tests/sandbox_broker/test_sandbox.py",
        "tests/task_runtime/test_tasks.py",
    )
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import pytest\n@pytest.mark.parametrize('value', [1, 2])\ndef test_case(value):\n    assert value\n", encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    matrix = {row["name"]: row for row in _quality_jobs()["python-tests"]["strategy"]["matrix"]["include"]}
    probe = """
import json, sys, pytest
class Capture:
    def pytest_collection_finish(self, session):
        print("COLLECTED_JSON=" + json.dumps([item.nodeid for item in session.items]))
raise SystemExit(pytest.main(sys.argv[1:], plugins=[Capture()]))
"""
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC")
                   if key in os.environ}
    environment.update(PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")

    def collect(target, options="", exclusions=()):
        arguments = []
        for part in shlex.split(target):
            if "*" in part:
                arguments.extend(str(path.relative_to(tmp_path)) for path in sorted(tmp_path.glob(part)))
            else:
                arguments.append(part)
        command = [sys.executable, "-B", "-c", probe, *arguments, *shlex.split(options),
                   *["--ignore=" + path for path in exclusions],
                   "--collect-only", "-q", "-p", "no:cacheprovider"]
        completed = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True,
                                   encoding="utf-8", timeout=30, check=False)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        lines = [line for line in completed.stdout.splitlines() if line.startswith("COLLECTED_JSON=")]
        assert len(lines) == 1
        ids = json.loads(lines[0].split("=", 1)[1])
        assert len(ids) == len(set(ids))
        return set(ids)

    old = collect(matrix["root"]["pytest_target"])
    # Exact same top-level paths selected by the activity job's shell expansion.
    exclusions = [str(path.relative_to(tmp_path))
                  for path in sorted(tmp_path.glob(matrix["root-activity"]["pytest_target"]))]
    root = collect(matrix["root"]["pytest_target"], matrix["root"]["pytest_args"], exclusions)
    activity = collect(matrix["root-activity"]["pytest_target"], matrix["root-activity"]["pytest_args"])
    assert root and activity
    assert len(old) == 14
    assert root.isdisjoint(activity)
    assert root | activity == old
    assert any("test_activity_nested.py" in item for item in root)


def _agent_collection_verifier():
    job = _quality_jobs()["python-tests"]
    steps = job["steps"]
    matches = [step for step in steps if step["name"] == "Verify complete Agent partition collection"]
    assert len(matches) == 1
    step = matches[0]
    assert step["if"] == "matrix.name == 'agent'"
    assert step["shell"] == "bash"
    assert steps.index(step) > next(i for i, value in enumerate(steps)
                                   if value["name"] == "Install CPU development dependencies")
    assert steps.index(step) < next(i for i, value in enumerate(steps)
                                   if value["name"] == "Run Python tests")
    lines = step["run"].strip().splitlines()
    assert lines[0] == "timeout --signal=KILL 180s python - <<'PY'" and lines[-1] == "PY"
    return "\n".join(lines[1:-1])


def test_agent_collection_verifier_has_bounded_isolated_children():
    code = _agent_collection_verifier()
    assert "COLLECTION_TOTAL_SECONDS = 180" in code
    assert "COLLECTION_CHILD_SECONDS = 60" in code
    assert "shell=True" not in code and "eval(" not in code
    assert "socket.socket.connect = guarded_connect" in code
    assert code.index("socket.socket.connect = guarded_connect") < code.index("site.main()")
    assert "PYTEST_DISABLE_PLUGIN_AUTOLOAD" not in code
    assert "subprocess.run" in code and "timeout=" in code


def test_agent_collection_has_process_group_deadline():
    steps = _quality_jobs()['python-tests']['steps']
    step = next(value for value in steps if value['name'] == 'Verify complete Agent partition collection')
    assert step['run'].splitlines()[0] == "timeout --signal=KILL 180s python - <<'PY'"


@pytest.mark.skipif(sys.platform != 'linux', reason='CI watchdog uses Linux GNU timeout')
def test_agent_collection_outer_deadline_kills_term_ignoring_descendant(tmp_path):
    import signal
    import time

    step = next(value for value in _quality_jobs()['python-tests']['steps']
                if value['name'] == 'Verify complete Agent partition collection')
    # Same shell/process-group boundary, reduced deadline only. The payload
    # stands in for a collector stuck in cleanup with an owned child still live.
    wrapper = step['run'].splitlines()[0].replace('180s', '5s')
    marker = tmp_path / 'owned-child.json'
    child = ("import json,os,signal,time\nfrom pathlib import Path\n"
             "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
             f"Path({str(marker)!r}).write_text(json.dumps([os.getpid(),os.getpgrp()]))\n"
             "while True: time.sleep(0.1)\n")
    payload = ("import subprocess,sys,time\n"
               f"child = subprocess.Popen([sys.executable,'-I','-S','-B','-c',{child!r}])\n"
               "try:\n    time.sleep(60)\nfinally:\n    child.wait()\n")
    environment = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'COMSPEC')
                   if key in os.environ}
    try:
        result = subprocess.run(['bash', '-c', wrapper + '\n' + payload + '\nPY'],
                                cwd=tmp_path, env=environment, capture_output=True,
                                text=True, timeout=12, check=False)
        assert result.returncode != 0
        assert marker.exists(), 'watchdog payload never started'
        pid, group = json.loads(marker.read_text())

        def still_running():
            try:
                fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
            except (FileNotFoundError, ProcessLookupError):
                return False
            return int(fields[2]) == group and fields[0] not in {'Z', 'X'}

        deadline = time.monotonic() + 2
        while still_running() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not still_running(), 'owned descendant survived outer deadline'
    finally:
        # Failure cleanup targets only the recorded test child, with group
        # identity checked; it cannot make the preceding assertion pass.
        if marker.exists():
            pid, group = json.loads(marker.read_text())
            try:
                if os.getpgid(pid) == group:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize('state', ['missing_file', 'missing_process', 'zombie', 'live', 'permission'])
def test_watchdog_proc_exit_observation_is_narrow_and_race_safe(tmp_path, monkeypatch, state):
    """Exercise the actual watchdog assertion with deterministic procfs races."""
    import itertools
    import time
    from types import SimpleNamespace

    pid, group = 987654321, 987654320
    read_text = Path.read_text

    def run(*args, **kwargs):
        (tmp_path / 'owned-child.json').write_text(json.dumps([pid, group]))
        return SimpleNamespace(returncode=124)

    def proc_read(path, *args, **kwargs):
        if path.name == 'stat' and path.parent.name == str(pid):
            if state == 'missing_file':
                raise FileNotFoundError('fixture process exited before open')
            if state == 'missing_process':
                raise ProcessLookupError('fixture process exited during read')
            if state == 'permission':
                raise PermissionError('fixture denial is not process exit')
            return f'{pid} (fixture) {"Z" if state == "zombie" else "S"} 1 {group} 0'
        return read_text(path, *args, **kwargs)

    def gone(*args):
        raise ProcessLookupError('fixture child is already reaped')

    def forbidden_kill(*args):
        pytest.fail('observation fixture must never signal a real process')

    ticks = itertools.count()
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(Path, 'read_text', proc_read)
    monkeypatch.setattr(time, 'monotonic', lambda: 100 * next(ticks))
    monkeypatch.setattr(os, 'getpgid', gone, raising=False)
    monkeypatch.setattr(os, 'kill', forbidden_kill)
    if state == 'live':
        with pytest.raises(AssertionError, match='owned descendant survived'):
            test_agent_collection_outer_deadline_kills_term_ignoring_descendant(tmp_path)
    elif state == 'permission':
        with pytest.raises(PermissionError):
            test_agent_collection_outer_deadline_kills_term_ignoring_descendant(tmp_path)
    else:
        test_agent_collection_outer_deadline_kills_term_ignoring_descendant(tmp_path)


@pytest.mark.parametrize('api', ['sendto', 'sendmsg', 'getaddrinfo', 'gethostbyname',
                                'gethostbyname_ex', 'gethostbyaddr', 'getnameinfo'])
def test_agent_collection_blocks_datagrams_and_dns_before_native_call(tmp_path, api):
    import socket

    if api == 'sendmsg' and not hasattr(socket.socket, api):
        pytest.skip('native sendmsg is unavailable on this platform')

    # Execute the actual pre-site child boundary; replace native operations so
    # the failing version cannot perform even a diagnostic network request.
    tree = ast.parse(_agent_collection_verifier())
    assignment = next(node for node in tree.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'child'
                              for target in node.targets))
    child = ast.parse(ast.literal_eval(assignment.value))
    end = next(i for i, node in enumerate(child.body) if isinstance(node, ast.Import)
               and any(alias.name == 'site' for alias in node.names))
    guard = ast.unparse(ast.Module(body=child.body[:end], type_ignores=[]))
    owner = 'socket.socket' if api in {'sendto', 'sendmsg'} else 'socket'
    before = (f"import socket\napi = {api!r}\nnative_calls = []\n"
              f"setattr({owner}, api, lambda *a, **kw: native_calls.append(api))\n")
    probe = '''
denied = False
try:
    if api in {'sendto', 'sendmsg'}:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as channel:
            getattr(channel, api)(b'no-network', ('127.0.0.1', 9))
    else:
        getattr(socket, api)('not-contacted.invalid')
except RuntimeError as error:
    denied = str(error) == 'offline_network_forbidden'
assert denied and not native_calls, 'native callback reached'
left, right = socket.socketpair()
try:
    left.sendall(b'x')
    assert right.recv(1) == b'x'
finally:
    left.close()
    right.close()
print('NETWORK_GUARD_OK')
'''
    # A fresh -S child avoids stacking the launcher's own Windows socketpair
    # call-stack guard underneath the boundary being tested.
    environment = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC')
                   if key in os.environ}
    result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', before + guard + '\n' + probe],
                            cwd=tmp_path, env=environment, capture_output=True,
                            text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'NETWORK_GUARD_OK'


def _collect_agent_with_actual_bash(root, row, environment):
    # Execute the workflow's actual expansion and exclusion loop. Only replace
    # the test runner with a collecting probe; never execute scientific tests.
    bash = shutil.which('bash')
    git_bash = Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'Git/bin/bash.exe'
    if sys.platform == 'win32' and git_bash.is_file():
        bash = str(git_bash)
    if not bash:
        pytest.skip('Bash unavailable; actual shell collection requires Bash')
    probe = """
import json, sys, pytest
class Capture:
    def pytest_collection_finish(self, session):
        print('BASH_IDS_JSON=' + json.dumps([item.nodeid for item in session.items]))
raise SystemExit(pytest.main(sys.argv[1:], plugins=[Capture()]))
"""
    step = next(step for step in _quality_jobs()['python-tests']['steps']
                if step['name'] == 'Run Python tests')
    command = 'timeout ${{ matrix.command_timeout }}s python -m pytest'
    assert step['run'].count(command) == 1
    script = step['run'].replace(command,
        shlex.quote(Path(sys.executable).as_posix()) + ' -B -c ' + shlex.quote(probe) + ' --collect-only')
    for key in ('name', 'pytest_target', 'pytest_args'):
        script = script.replace('${{ matrix.' + key + ' }}', row[key])
    result = subprocess.run([bash, '--noprofile', '--norc', '-c', script], cwd=root,
        env=dict(environment, PYTEST_EXCLUDE=row.get('pytest_exclude', ''),
                 PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'),
        capture_output=True, text=True, encoding='utf-8', timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    captures = [line.split('=', 1)[1] for line in result.stdout.splitlines()
                if line.startswith('BASH_IDS_JSON=')]
    assert len(captures) == 1
    ids = json.loads(captures[0])
    assert ids and len(ids) == len(set(ids))
    return set(ids)


@pytest.mark.parametrize('fault', [None, 'overlap', 'omission', 'docking_overlap', 'docking_omission', 'docking_empty',
    'publication_overlap', 'publication_core_overlap', 'publication_web_overlap',
    'publication_omission', 'publication_empty', 'duplicate_rows',
    'duplicate_ids', 'collection_error', 'timeout', 'final_cleanup_deadline'])
def test_agent_partition_inline_verifier_actual_synthetic_collection(tmp_path, fault):
    # Execute the exact workflow program, not a separately reimplemented selector.
    code = _agent_collection_verifier()
    workflow = yaml.safe_load((ROOT / '.github/workflows/quality.yml').read_text('utf-8'))
    rows = workflow['jobs']['python-tests']['strategy']['matrix']['include']
    matrix = {row['name']: row for row in rows}
    assert len(matrix) == len(rows)
    assert 'agent-docking' in matrix, 'missing bounded docking partition'
    files = (*AGENT_WEB_TARGET.split(), 'tests/agent/test_future_agent.py',
             'tests/agent/test_ordinary_web_runtime_future.py',
             'tests/agent/future/test_nested.py',
             'tests/agent/future/test_web_decision_runtime_lifecycle.py',
             'tests/agent/test_web_docking_future.py',
             'tests/agent/test_docking_new_future.py',
             'tests/agent/test_docking_publication.py',
             'tests/agent/test_docking_publication_future.py',
             'tests/agent/future/test_docking_publication_future.py',
             'tests/agent/test_docking_publication_extra/test_nested_publication.py',
             'tests/agent/future/test_web_docking_nested.py',
             'tests/agent/test_docking_area/test_nested.py')
    for name in files:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import pytest\n@pytest.mark.parametrize('n',[1,2])\ndef test_case(n):\n    pass\n", encoding='utf-8')
    for name in ('tests/__init__.py', 'tests/agent/__init__.py', 'tests/agent/future/__init__.py'):
        (tmp_path / name).write_text('', encoding='utf-8')
    (tmp_path / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
    (tmp_path / 'tests/test_outside_agent.py').write_text(
        "raise RuntimeError('OUTSIDE_AGENT_MUST_NOT_BE_IMPORTED')\n", encoding='utf-8')
    if fault == 'overlap':
        matrix['agent']['pytest_target'] = 'tests/agent'
    elif fault == 'omission':
        matrix['agent']['pytest_target'] += ' --ignore=tests/agent/test_future_agent.py'
    elif fault == 'docking_overlap':
        matrix['agent']['pytest_exclude'] = ''
    elif fault == 'docking_omission':
        matrix['agent-docking']['pytest_target'] = 'tests/agent/test_web_docking_future.py'
    elif fault == 'docking_empty':
        matrix['agent-docking']['pytest_target'] = 'tests/agent/test_missing_docking_*.py'
    elif fault == 'publication_overlap':
        matrix['agent-docking']['pytest_exclude'] = ''
    elif fault == 'publication_core_overlap':
        matrix['agent-docking-publication']['pytest_target'] += ' tests/agent/test_future_agent.py'
    elif fault == 'publication_web_overlap':
        matrix['agent-docking-publication']['pytest_target'] += ' ' + AGENT_WEB_TARGET.split()[0]
    elif fault == 'publication_omission':
        matrix['agent-docking-publication']['pytest_target'] = 'tests/agent/test_docking_publication.py'
    elif fault == 'publication_empty':
        matrix['agent-docking-publication']['pytest_target'] = 'tests/agent/test_missing_publication_*.py'
    elif fault == 'duplicate_rows':
        rows.append(dict(matrix['agent']))
    elif fault == 'duplicate_ids':
        (tmp_path / 'tests/agent/conftest.py').write_text(
            'def pytest_collection_modifyitems(items):\n    items.append(items[0])\n', encoding='utf-8')
    elif fault == 'collection_error':
        (tmp_path / files[0]).write_text(
            "raise RuntimeError('CHILD_ERROR_MUST_NOT_BE_LOGGED')\n", encoding='utf-8')
    elif fault == 'timeout':
        # Exercise real subprocess termination/reaping with a reduced test-only
        # child deadline, leaving the exact workflow program unmodified.
        code = ("import subprocess\n_run = subprocess.run\n"
                "def _bounded(*a, **kw):\n    kw['timeout'] = 0.001\n    return _run(*a, **kw)\n"
                "subprocess.run = _bounded\n" + code)
    elif fault == 'final_cleanup_deadline':
        # Real collection/cleanup still execute. Advance only the logical clock
        # after the final temporary directory has exited; no 180-second sleep.
        code = ("import tempfile, time\n_real_clock = time.monotonic\n_offset = 0\n_exits = 0\n"
                "time.monotonic = lambda: _real_clock() + _offset\n"
                "_Directory = tempfile.TemporaryDirectory\n"
                "class DelayedFinalCleanup(_Directory):\n"
                "    def __exit__(self, *args):\n"
                "        global _offset, _exits\n"
                "        result = super().__exit__(*args)\n"
                "        _exits += 1\n"
                "        if _exits == 5:\n            _offset += 181\n"
                "        return result\n"
                "tempfile.TemporaryDirectory = DelayedFinalCleanup\n" + code)
    configuration = tmp_path / '.github/workflows/quality.yml'
    configuration.parent.mkdir(parents=True)
    configuration.write_text(yaml.safe_dump(workflow), encoding='utf-8')
    environment = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR', 'PATH', 'TEMP', 'TMP', 'COMSPEC')
                   if key in os.environ}
    environment.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8',
                       MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE='0')
    completed = subprocess.run([sys.executable, '-I', '-B', '-c', code], cwd=tmp_path,
        env=environment, capture_output=True, text=True, encoding='utf-8', timeout=45, check=False)
    assert completed.stderr == ''
    if fault is None:
        assert completed.returncode == 0, completed.stdout
        assert completed.stdout.strip() == 'AGENT_COLLECTION_OK baseline=30 agent=16 web=6 docking=4 publication=4'
        baseline = _collect_agent_with_actual_bash(tmp_path,
            dict(name='baseline', pytest_target='tests/agent', pytest_args=''), environment)
        names = ('agent', 'agent-web-lifecycle', 'agent-docking', 'agent-docking-publication')
        partitions = [_collect_agent_with_actual_bash(tmp_path, matrix[name], environment) for name in names]
        expected = {f'{path}::test_case[{n}]' for path in files for n in (1, 2)}
        assert baseline == expected
        assert set.union(*partitions) == baseline
        assert all(left.isdisjoint(right) for i, left in enumerate(partitions) for right in partitions[i + 1:])
        assert [len(ids) for ids in partitions] == [16, 6, 4, 4]
        assert 'tests/agent/future/test_docking_publication_future.py::test_case[1]' in partitions[0]
        assert 'tests/agent/test_docking_publication_extra/test_nested_publication.py::test_case[2]' in partitions[0]
    else:
        assert completed.returncode != 0
        assert completed.stdout.strip() == 'AGENT_COLLECTION_FAILED'
