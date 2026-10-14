"""Deployment CLI rejects missing registries before importing scientific runtimes."""
from pathlib import Path
import os
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "activate_activity_family_bundles.py"


def run(*args, env=None):
    child_env = os.environ.copy()
    child_env.pop("ACTIVITY_MODEL_DIR", None)
    if env:
        child_env.update(env)
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), *map(str, args)],
        cwd=ROOT,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_models_dir_is_required_before_runtime_import():
    result = run()
    assert result.returncode == 2
    assert "--models-dir or ACTIVITY_MODEL_DIR is required" in result.stderr


def test_missing_registry_fails_before_any_activation(tmp_path):
    missing = tmp_path / "missing-models"
    result = run("--models-dir", missing, "--apply")
    assert result.returncode == 2
    assert "activity registry not found" in result.stderr
    assert not missing.exists()
