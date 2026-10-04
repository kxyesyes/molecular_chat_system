"""Offline lifecycle tests; protocol fixtures are not scientific predictions."""

import io
import json
from pathlib import Path
import subprocess
import sys
from threading import Thread
from types import SimpleNamespace

import pytest

from src.agent.tools import admet_ai_backend as backend_module
from src.agent.tools.admet_predictor import ADMETPredictor


@pytest.fixture(autouse=True)
def isolated_backend(monkeypatch):
    monkeypatch.setattr(backend_module, "_DEFAULT_BACKEND", None)
    monkeypatch.setattr(backend_module, "_DEFAULT_BACKEND_ERROR", None)
    monkeypatch.delenv("ADMET_AI_PYTHON", raising=False)
    yield
    backend_module.reset_admet_ai_backend_cache()


def test_missing_worker_configuration_never_imports_model_in_web_process(monkeypatch):
    imports = []
    monkeypatch.setattr(backend_module.ADMETAIBackend, "from_installed_package",
                        lambda: imports.append(True) or object())
    assert backend_module.get_admet_ai_backend() is None
    assert imports == []
    assert "ADMET_AI_PYTHON" in backend_module.admet_ai_backend_error()


def test_explicit_worker_is_used_even_if_main_process_has_model(monkeypatch):
    imports = []
    worker = object()
    monkeypatch.setenv("ADMET_AI_PYTHON", "selected-worker")
    monkeypatch.setattr(backend_module.ADMETAIBackend, "from_installed_package",
                        lambda: imports.append(True) or object())
    monkeypatch.setattr(backend_module, "ADMETAISubprocessBackend", lambda path: worker)
    assert backend_module.get_admet_ai_backend() is worker
    assert imports == []


def test_unsupported_python_is_rejected_before_worker_launch(monkeypatch):
    monkeypatch.setattr(backend_module.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(stdout="3.12\n"))
    with pytest.raises(RuntimeError, match="Python 3.10"):
        backend_module.ADMETAISubprocessBackend._assert_python310(sys.executable)


def hanging_worker(monkeypatch, tmp_path):
    # Only the test interpreter-version probe is stubbed.  The subprocess,
    # pipes, waiting reader and process termination are real OS resources.
    monkeypatch.setattr(backend_module.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(stdout="3.10\n"))
    script = tmp_path / "hung_protocol_fixture.py"
    script.write_text(
        "import json, sys, time\n"
        "print(json.dumps({'ready': True, 'version': '1.4.0', "
        "'python_version': '3.10', 'weights_id': 'sha256:' + 'a' * 64}), flush=True)\n"
        "for line in sys.stdin:\n    time.sleep(60)\n",
        encoding="utf-8",
    )
    return backend_module.ADMETAISubprocessBackend(sys.executable, script)


def test_prediction_timeout_reaps_real_process_and_reader(monkeypatch, tmp_path):
    backend = hanging_worker(monkeypatch, tmp_path)
    try:
        result = ADMETPredictor(backend=backend, timeout_seconds=0.05).execute("SMILES: CCO")
        assert not result["success"] and result["data"] is None
        assert backend._process.poll() is not None, "timeout left a live worker"
        assert not backend._reader.is_alive(), "timeout left a blocked pipe reader"
    finally:
        backend.close()


def test_shutdown_reaps_worker_even_after_other_shutdown_error(monkeypatch, tmp_path):
    from src.web.app import MolecularChatApp

    backend = hanging_worker(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "_DEFAULT_BACKEND", backend)
    app = MolecularChatApp.__new__(MolecularChatApp)

    async def broken_shutdown():
        raise RuntimeError("other shutdown error")

    app._shutdown = broken_shutdown
    import asyncio
    try:
        with pytest.raises(RuntimeError, match="other shutdown error"):
            asyncio.run(app.shutdown())
        assert backend._process.poll() is not None
        assert not backend._reader.is_alive()
        assert backend_module._DEFAULT_BACKEND is None
    finally:
        backend.close()


def test_model_does_not_enable_unbounded_molecule_cache(monkeypatch):
    import pandas as pd
    import importlib.resources

    options = []
    package = SimpleNamespace(__version__="1.4.0", ADMETModel=lambda **kw: options.append(kw))
    info = SimpleNamespace(get_admet_info=lambda: pd.DataFrame(columns=["id", "name", "category", "task_type", "units"]))
    monkeypatch.setitem(sys.modules, "admet_ai", package)
    monkeypatch.setitem(sys.modules, "admet_ai.admet_info", info)
    monkeypatch.setattr(importlib.resources, "files", lambda name: Path("test-weights"))
    monkeypatch.setattr(backend_module, "_weight_digest", lambda root: "sha256:" + "a" * 64)
    backend_module.ADMETAIBackend.from_installed_package()
    assert options == [{"num_workers": 0, "cache_molecules": False}]
