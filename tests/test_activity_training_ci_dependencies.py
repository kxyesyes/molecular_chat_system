"""Keep RGNN's compiled CPU extensions aligned with the existing CI Torch ABI."""
from pathlib import Path

import pytest


@pytest.mark.parametrize("requirement", [
    "torch-scatter==2.1.2+pt24cpu", "torch-sparse==0.6.18+pt24cpu",
])
def test_cpu_training_extensions_are_pinned_to_ci_torch(requirement):
    path = Path(__file__).resolve().parents[1] / "requirements-ci.txt"
    lines = {line.strip() for line in path.read_text(encoding="utf-8").splitlines()}
    assert "torch==2.4.0+cpu" in lines
    assert requirement in lines
    assert "--find-links https://data.pyg.org/whl/torch-2.4.0+cpu.html" in lines
