"""Dependency discovery belongs to startup, not the first accepted chat."""
import asyncio
import ast
import logging
from pathlib import Path
from types import SimpleNamespace


def test_web_startup_discovers_node_without_launching_a_child(monkeypatch):
    from src.agent.pi_runtime import bridge
    # Run the actual lifecycle method without constructing the module's global
    # production app, model clients, data stores or configuration readers.
    source = Path(__file__).resolve().parents[1] / "src/web/app.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "MolecularChatApp")
    method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef)
                  and node.name == "initialize")
    namespace = {"asyncio": asyncio, "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)

    calls = []
    monkeypatch.setattr(bridge, "_NODE_LOOKUP", None)
    monkeypatch.setattr(bridge.shutil, "which", lambda name: calls.append(name) or None)

    def no_child(*args, **kwargs):
        raise AssertionError("Dependency warmup must not start a Pi child")

    monkeypatch.setattr(bridge.subprocess, "Popen", no_child)
    application = SimpleNamespace(
        config={"rag": {"enabled": False}},
        _llm_watch_task=SimpleNamespace(done=lambda: False),
    )
    asyncio.run(namespace["initialize"](application))
    assert calls == ["node"]
