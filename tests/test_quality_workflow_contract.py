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


def test_quality_workflow_runs_family_dom_selftests_in_static_job() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")
    static_job = workflow.split("  static-quality:\n", 1)[1].split("  offline-quality:\n", 1)[0]

    assert "          node tests/activity_family_acceptance_dom.js\n" in static_job


def test_quality_workflow_installs_node_for_root_python_chain() -> None:
    workflow = (ROOT / ".github/workflows/quality.yml").read_text("utf-8")
    python_job = workflow.split("  python-tests:\n", 1)[1].split("  static-quality:\n", 1)[0]

    assert (
        "        if: matrix.name == 'root'\n"
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
    assert workflow.count('pytest_args: "--timeout=60"') == 2

    assert "run: python -m pytest tests -q -p no:cacheprovider" not in workflow
    assert "fetch-depth: 0" not in workflow
