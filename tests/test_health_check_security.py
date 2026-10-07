import importlib.util
from pathlib import Path
from types import SimpleNamespace


def _load_health_check():
    path = Path(__file__).resolve().parents[1] / "scripts" / "health_check.py"
    spec = importlib.util.spec_from_file_location("medchat_health_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_vina_health_probe_uses_a_minimal_environment(monkeypatch, tmp_path):
    health_check = _load_health_check()
    vina = tmp_path / "vina.exe"
    vina.write_bytes(b"synthetic")
    monkeypatch.setenv("MOLECULAR_DOCKING_VINA", str(vina))
    monkeypatch.setenv("PATH", "synthetic-path")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-secret")
    observed = {}

    def fake_run(*args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(health_check.subprocess, "run", fake_run)

    ok, _detail = health_check.check_vina()

    assert ok is True
    environment = observed["kwargs"]["env"]
    assert environment["PATH"] == "synthetic-path"
    assert "OPENAI_API_KEY" not in environment
    assert "DEEPSEEK_API_KEY" not in environment
