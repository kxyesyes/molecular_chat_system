from pathlib import Path
import json
import os
import shlex
import subprocess
import sys

import yaml


ROOT = Path(__file__).resolve().parents[1]


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
    assert workflow.count("command_timeout:") == 6
    assert "command_timeout: 180" in workflow
    for target in (
        "tests/agent",
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
        "name": "agent", "pytest_target": "tests/agent",
        "pytest_args": "-vv --durations=25", "command_timeout": 600,
    }
    assert {name: row["pytest_args"] for name, row in matrix.items()
            if name != "agent"} == {
        "sandbox-api": "--timeout=60", "sandbox-core": "--timeout=60",
        "task-runtime": "", "root": "", "root-activity": "",
    }


def test_root_partition_preserves_deadlines_and_all_jobs_gate():
    jobs = _quality_jobs()
    python = jobs["python-tests"]
    matrix = {row["name"]: row for row in python["strategy"]["matrix"]["include"]}
    assert set(matrix) == {"agent", "sandbox-api", "sandbox-core", "task-runtime", "root", "root-activity"}
    assert python["strategy"]["fail-fast"] is False
    assert matrix["root"]["pytest_target"] == "tests --ignore=tests/agent --ignore=tests/sandbox_broker --ignore=tests/task_runtime"
    assert matrix["root"]["pytest_args"] == ""
    assert matrix["root-activity"]["pytest_target"] == "tests/test_activity*.py"
    assert matrix["root-activity"]["pytest_args"] == ""
    assert all(row["command_timeout"] == (180 if name == "sandbox-api" else 600)
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
