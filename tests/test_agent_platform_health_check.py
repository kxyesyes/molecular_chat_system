from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import types

from scripts import health_check


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def test_agent_components_health_check_uses_supported_generation_count():
    ok, detail = health_check.check_agent_components()

    assert ok is True, detail
    assert "planner" in detail


def test_temporal_health_local_default_is_safe_and_non_blocking(monkeypatch):
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "local")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_ADDRESS", "temporal.internal:7233")

    ok, detail = health_check.check_temporal_runtime()

    assert ok is True
    assert "local" in detail
    assert "temporal.internal" not in detail
    assert "configured=" in detail


def test_temporal_health_requires_reachable_service_and_fresh_worker():
    from src.task_runtime.models import BackendHealth

    class _Config:
        backend = "temporal_canary"
        warnings = ()
        temporal_address = "private.temporal.internal:7233"
        temporal_namespace = "science"
        docking_queue = "medchat-docking"

        def to_safe_dict(self):
            return {
                "backend": self.backend,
                "warnings": self.warnings,
                "temporal_address_configured": True,
            }

    class _Backend:
        async def health(self):
            return BackendHealth(
                "temporal",
                False,
                "worker unavailable",
                {
                    "configured": True,
                    "durable_execution": True,
                    "process_restart_recovery": True,
                    "running": 0,
                },
            )

        async def close(self):
            return None

    ok, detail = health_check.check_temporal_runtime(
        config_factory=lambda: _Config(),
        backend_factory=lambda _config: _Backend(),
    )

    assert ok is False
    assert "worker unavailable" in detail
    assert "science" in detail
    assert "medchat-docking" in detail
    assert "private.temporal.internal" not in detail


def test_superseded_runtime_implementations_are_absent_and_unimported():
    superseded_files = {
        PROJECT_ROOT / "src" / "web" / "rag_service.py",
        PROJECT_ROOT / "src" / "rag" / "molecular_rag.py",
        PROJECT_ROOT / "src" / "web" / "models.py",
        PROJECT_ROOT / "src" / "web" / "static" / "js" / "script.legacy.backup.js",
    }
    assert not {path for path in superseded_files if path.exists()}

    forbidden_modules = ("src.web.rag_service", "src.rag.molecular_rag")
    imports: list[tuple[Path, str]] = []
    for path in (PROJECT_ROOT / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        imports.extend(
            (path.relative_to(PROJECT_ROOT), module)
            for module in forbidden_modules
            if f"from {module} import" in source or f"import {module}" in source
        )
    assert imports == []


def test_web_runtime_uses_the_canonical_ollama_model_class():
    app_module = importlib.import_module("src.web.app")
    model_module = importlib.import_module("src.web.models.ollama_model")

    assert app_module.OllamaModel is model_module.OllamaModel
    assert app_module.OllamaModel.__module__ == "src.web.models.ollama_model"
    assert app_module.RAGSystem.__module__ == "src.web.app"

    tool_registry = (
        PROJECT_ROOT / "src" / "agent" / "tools" / "__init__.py"
    ).read_text(encoding="utf-8")
    scientific_runner = (
        PROJECT_ROOT / "src" / "agent" / "evaluation" / "scientific.py"
    ).read_text(encoding="utf-8")
    canonical_import = "from src.web.models.ollama_model import OllamaModel"
    assert canonical_import in tool_registry
    assert canonical_import in scientific_runner


def test_chat_generation_adapter_supports_canonical_and_external_models():
    models_module = importlib.import_module("src.web.models")
    assert hasattr(models_module, "generate_for_chat")

    class CanonicalStyleModel:
        def generate(self, *_args, **_kwargs):
            raise AssertionError("chat path must prefer generate_async")

        async def generate_async(self, prompt, **_kwargs):
            return f"canonical:{prompt}"

    class ExternalStyleModel:
        async def generate(self, prompt, **_kwargs):
            return f"external:{prompt}"

    class SyncStyleModel:
        def generate(self, prompt, **_kwargs):
            return f"sync:{prompt}"

    async def exercise_models():
        return (
            await models_module.generate_for_chat(CanonicalStyleModel(), "one"),
            await models_module.generate_for_chat(ExternalStyleModel(), "two"),
            await models_module.generate_for_chat(SyncStyleModel(), "three"),
        )

    assert asyncio.run(exercise_models()) == (
        "canonical:one",
        "external:two",
        "sync:three",
    )


def test_application_route_ownership_remains_complete():
    app_module = importlib.import_module("src.web.app")
    route_paths = {getattr(route, "path", "") for route in app_module.app.routes}
    expected_paths = {
        "/",
        "/ws",
        "/api/llm/config",
        "/api/agent/workflows/plan",
        "/api/agent/workflows/run",
        "/api/tasks/{task_id}",
        "/api/system/data-versions",
        "/api/activity/predict",
        "/api/reverse_target/predict",
        "/api/target-db/search",
        "/api/docking/submit",
    }

    assert expected_paths <= route_paths
