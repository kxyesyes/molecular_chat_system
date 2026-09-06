from pathlib import Path


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
    assert workflow.count("command_timeout:") == 5
    assert "command_timeout: 180" in workflow
    for target in (
        "tests/agent",
        "tests/sandbox_broker/test_api.py",
        "tests/sandbox_broker --ignore=tests/sandbox_broker/test_api.py",
        "tests/task_runtime",
        "tests --ignore=tests/agent --ignore=tests/sandbox_broker --ignore=tests/task_runtime",
    ):
        assert f'pytest_target: "{target}"' in workflow

    assert workflow.count("--timeout=60") == 2
    assert 'pytest_args: "--timeout=60 -vv"' in workflow

    assert "run: python -m pytest tests -q -p no:cacheprovider" not in workflow
    assert "fetch-depth: 0" not in workflow
