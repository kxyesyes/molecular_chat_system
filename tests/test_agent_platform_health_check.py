from __future__ import annotations

import types

from scripts import health_check


def test_docking_health_checks_fall_back_to_service_diagnostics(monkeypatch):
    diagnostics = {
        "vina": {"path": "D:/tools/vina.exe", "exists": True},
        "adfr_prepare_receptor": {
            "path": "D:/tools/prepare_receptor.bat",
            "exists": True,
        },
        "mk_prepare_ligand": {
            "path": "D:/tools/mk_prepare_ligand.exe",
            "exists": True,
        },
    }
    fake_service = types.SimpleNamespace(env_diagnostics=lambda: diagnostics)
    monkeypatch.setitem(
        __import__("sys").modules,
        "src.docking",
        types.SimpleNamespace(docking_service=fake_service),
    )
    monkeypatch.setattr(health_check, "shutil_which", lambda command: None)
    monkeypatch.delenv("MOLECULAR_DOCKING_VINA", raising=False)
    monkeypatch.delenv("MOLECULAR_DOCKING_ADFR_BIN", raising=False)
    monkeypatch.delenv("MOLECULAR_DOCKING_PREPARE_RECEPTOR", raising=False)
    monkeypatch.delenv("MOLECULAR_DOCKING_PREPARE_LIGAND", raising=False)

    assert health_check.check_vina()[0] is True
    assert health_check.check_adfrsuite()[0] is True
    assert health_check.check_ligand_preparation()[0] is True


def test_agent_platform_health_check_exercises_persistence_and_evaluation():
    ok, detail = health_check.check_agent_platform()

    assert ok is True
    assert "persistence" in detail
    assert "evaluation" in detail
