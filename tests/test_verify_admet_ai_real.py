from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_admet_ai_real.py"


def test_admet_real_probe_reports_missing_worker_from_repo_root() -> None:
    env = os.environ.copy()
    env.pop("ADMET_AI_PYTHON", None)
    env.pop("PYTHONPATH", None)

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repeat", "1"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    combined = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert "ADMET_AI_PYTHON is required" in combined
    assert "ModuleNotFoundError: No module named 'src'" not in combined
