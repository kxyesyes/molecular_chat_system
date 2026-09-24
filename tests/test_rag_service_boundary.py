"""Offline import boundaries and identity of the historical RAG entry points."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_INDEX_OBJECTS = (
    "CURRENT_SCHEMA_VERSION",
    "RAGIndexCompatibilityError",
    "RAGIndexManifest",
    "atomic_save_index_pair",
    "file_sha256",
    "immutable_index_snapshot",
    "load_manifest",
    "manifest_path",
    "validate_manifest",
)


def _run_isolated(code, tmp_path):
    # Never inherit credentials, application settings, or the caller's cwd.
    env = {key: os.environ[key] for key in
           ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC")
           if key in os.environ}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "MOLECULAR_CHAT_CONFIG": str(tmp_path / "missing.yaml"),
        "MEDCHAT_ENV_FILE": str(tmp_path / "not-loaded.env"),
        "MEDCHAT_USER_CONFIG_DIR": str(tmp_path / "config"),
        "MEDCHAT_LLM_LOCK_DIR": str(tmp_path / "locks"),
        "MEDCHAT_AGENT_SESSION_DB": str(tmp_path / "sessions.sqlite"),
        "AGENT_STATE_DB": str(tmp_path / "agent.sqlite"),
        "MEDCHAT_TASK_DB_PATH": str(tmp_path / "tasks.sqlite"),
        "TARGET_DB_PATH": str(tmp_path / "targets.sqlite"),
        "TARGET_CACHE_DIR": str(tmp_path / "target-cache"),
        "MEDCHAT_TASK_BACKEND": "local",
        "MEDCHAT_TEMPORAL_CANARY_PERCENT": "0",
        "AGENT_HARNESS_MODE": "legacy",
        "MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE": "0",
        "MEDCHAT_RUN_OPENSANDBOX_ACCEPTANCE": "0",
        "MEDCHAT_RUN_OPENSANDBOX_STABILITY_SOAK": "0",
        "RUN_REAL_TARGET_SEARCH": "0",
    })
    bootstrap = """
import logging
import socket
import sys
sys.path.insert(0, sys.argv[1])
def forbidden_connection(*args, **kwargs):
    raise AssertionError("RAG import must not connect to external services")
socket.socket.connect = forbidden_connection
socket.socket.connect_ex = forbidden_connection
socket.create_connection = forbidden_connection
"""
    script = bootstrap + "\ntry:\n" + textwrap.indent(textwrap.dedent(code), "    ")
    script += "\nfinally:\n    logging.shutdown()\n"
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(ROOT)],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_canonical_rag_import_does_not_load_web_or_initialize_assets(tmp_path):
    _run_isolated("""
        from unittest.mock import patch
        import pandas as pd

        with patch.object(pd, "read_csv", side_effect=AssertionError("No CSV on import")):
            from src.rag.service import RAGSystem
            from src.rag.index import RAGIndexManifest

        assert RAGSystem.__module__ == "src.rag.service"
        assert RAGIndexManifest.__module__ == "src.rag.index"
        assert "src.web.app" not in sys.modules
        assert "src.web.rag_index" not in sys.modules
        assert not any(name == "src.web" or name.startswith("src.web.")
                       for name in sys.modules)
    """, tmp_path)


@pytest.mark.parametrize("name", PUBLIC_INDEX_OBJECTS)
def test_legacy_index_exports_the_same_canonical_object(name):
    from src.rag import index
    from src.web import rag_index

    assert getattr(rag_index, name) is getattr(index, name)
    assert set(rag_index.__all__) == set(PUBLIC_INDEX_OBJECTS)
    assert len(rag_index.__all__) == len(PUBLIC_INDEX_OBJECTS)


def test_legacy_app_exports_the_same_canonical_service(tmp_path):
    _run_isolated("""
        import importlib
        from contextlib import ExitStack
        from types import ModuleType
        from unittest.mock import patch
        import pandas as pd
        from src.rag.service import RAGSystem

        class OfflineModel:
            def __init__(self, *args, **kwargs):
                self.model_name = "offline-boundary"

            async def generate(self, *args, **kwargs):
                raise AssertionError("Import must not execute a model")

        with ExitStack() as patches:
            patches.enter_context(patch("src.web.models.OllamaModel", OfflineModel))
            patches.enter_context(patch(
                "src.agent.openai_compatible_model.OpenAICompatibleModel", OfflineModel))
            patches.enter_context(patch("src.agent.tools.get_all_tools", return_value=[]))
            patches.enter_context(patch.object(
                RAGSystem, "initialize", side_effect=AssertionError("No RAG startup")))
            patches.enter_context(patch.object(
                pd, "read_csv", side_effect=AssertionError("No production CSV")))
            # These unrelated routes discover checkout assets at import time.
            for name, setup in (("src.web.routes.design_routes", "setup_design_routes"),
                                ("src.target_search.routes", "setup_target_search_routes")):
                module = ModuleType(name)
                setattr(module, setup, lambda *args, **kwargs: None)
                patches.enter_context(patch.dict(sys.modules, {name: module}))
            app_module = importlib.import_module("src.web.app")

        assert app_module.RAGSystem is RAGSystem
        assert app_module.RAGSystem.__module__ == "src.rag.service"
    """, tmp_path)
