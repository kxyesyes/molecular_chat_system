from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_FILES = (
    ROOT / "src/agent/openai_compatible_model.py",
    ROOT / "src/agent/decision_transport.py",
    ROOT / "src/agent/tools/__init__.py",
    ROOT / "src/agent/tools/llm_molecular_generator.py",
    ROOT / "src/agent/evaluation/scientific.py",
    ROOT / "src/agent/tools/rag_search_tool.py",
    ROOT / "src/molecular_design/service.py",
    ROOT / "src/system/model_clients.py",
    ROOT / "src/system/ollama_model.py",
    ROOT / "src/system/openai_compatible_model.py",
    ROOT / "src/system/modelscope_model.py",
    ROOT / "src/system/model_lifecycle.py",
    ROOT / "src/system/network_policy.py",
    ROOT / "src/system/llm_transport.py",
)


def _web_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return [name for name in imports if name == "src.web" or name.startswith("src.web.")]


def test_scientific_and_agent_core_do_not_import_web_modules():
    violations = {
        str(path.relative_to(ROOT)): _web_imports(path)
        for path in CORE_FILES
        if _web_imports(path)
    }
    assert violations == {}


def test_task_runtime_routes_do_not_import_web_adapters():
    path = ROOT / "src/task_runtime/routes.py"
    assert _web_imports(path) == []


def test_api_response_legacy_exports_are_canonical_objects():
    from src.system.api_response import api_error, api_success
    from src.web.api_response import (
        api_error as legacy_api_error,
        api_success as legacy_api_success,
    )

    assert legacy_api_error is api_error
    assert legacy_api_success is api_success


def test_scientific_status_legacy_exports_are_canonical_objects():
    from src.system.scientific_status import ObservationStatus, RunOutcome
    from src.agent.contracts.scientific import (
        ObservationStatus as LegacyObservationStatus,
        RunOutcome as LegacyRunOutcome,
    )

    assert LegacyObservationStatus is ObservationStatus
    assert LegacyRunOutcome is RunOutcome


def test_url_policy_legacy_exports_are_the_canonical_objects():
    from src.system.network_policy import resolve_llm_host, validate_llm_url
    from src.web.security.url_policy import (
        resolve_llm_host as legacy_resolve_llm_host,
        validate_llm_url as legacy_validate_llm_url,
    )

    assert legacy_resolve_llm_host is resolve_llm_host
    assert legacy_validate_llm_url is validate_llm_url


def test_model_lifecycle_legacy_exports_are_canonical_objects():
    from src.system.model_lifecycle import (
        ModelRequestGate,
        close_owned_model,
        finish_on_cancel,
        model_request,
    )
    from src.web.model_lifecycle import (
        ModelRequestGate as LegacyGate,
        close_owned_model as legacy_close_owned_model,
        finish_on_cancel as legacy_finish_on_cancel,
        model_request as legacy_model_request,
    )

    assert LegacyGate is ModelRequestGate
    assert legacy_close_owned_model is close_owned_model
    assert legacy_finish_on_cancel is finish_on_cancel
    assert legacy_model_request is model_request


def test_model_client_legacy_exports_are_canonical_objects():
    from src.system.model_clients import (
        ModelScopeModel,
        ModelScopeModelManager,
        OllamaGenerationError,
        OllamaModel,
        OpenAICompatibleModel,
    )
    from src.agent.modelscope_model import (
        ModelScopeModel as LegacyModelScopeModel,
        ModelScopeModelManager as LegacyModelScopeModelManager,
    )
    from src.agent.openai_compatible_model import (
        OpenAICompatibleModel as LegacyOpenAICompatibleModel,
    )
    from src.web.models.ollama_model import (
        OllamaGenerationError as LegacyOllamaGenerationError,
        OllamaModel as LegacyOllamaModel,
    )

    assert LegacyModelScopeModel is ModelScopeModel
    assert LegacyModelScopeModelManager is ModelScopeModelManager
    assert LegacyOpenAICompatibleModel is OpenAICompatibleModel
    assert LegacyOllamaGenerationError is OllamaGenerationError
    assert LegacyOllamaModel is OllamaModel


def test_application_shutdown_closes_shared_model_owner_once():
    import asyncio

    from src.agent.tooling import build_tool_registry
    from src.web.app import MolecularChatApp
    from src.web.model_lifecycle import ModelRequestGate

    class Owner:
        def __init__(self):
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    owner = Owner()
    app = MolecularChatApp.__new__(MolecularChatApp)
    app._llm_watch_task = None
    app.model_request_gate = ModelRequestGate()
    app.model = owner
    app.molecular_generator_model = owner
    app.agent_system = type("AgentSystem", (), {"tools": {}})()
    app.agent_tool_registry = build_tool_registry([])
    app._assembly_owners_transferred = False

    asyncio.run(app.shutdown())
    asyncio.run(app.shutdown())

    assert owner.close_calls == 1


def test_cancelled_assembly_drains_shared_owner_once(monkeypatch):
    import asyncio

    import pytest
    from src.web.app import MolecularChatApp

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        class Owner:
            close_calls = 0
            closed = False

            async def close(self):
                self.close_calls += 1
                entered.set()
                await release.wait()
                self.closed = True

        owner = Owner()

        def cancelled_assembly(self, *args, **kwargs):
            self.model = self.molecular_generator_model = owner
            self._register_assembly_owner(self.model)
            self._register_assembly_owner(self.molecular_generator_model)
            raise asyncio.CancelledError()

        monkeypatch.setattr(MolecularChatApp, "_assemble", cancelled_assembly)
        task = asyncio.create_task(MolecularChatApp.create_async())
        try:
            await asyncio.wait_for(entered.wait(), timeout=3)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done()
            assert not owner.closed
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert owner.closed
            assert owner.close_calls == 1
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_neutral_clients_import_without_loading_web_or_agent():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", """
import sys
import httpx

def forbidden(*args, **kwargs):
    raise AssertionError('Import must not create a model client')

httpx.Client = httpx.AsyncClient = forbidden
from src.system import model_clients, model_lifecycle, llm_transport, network_policy
assert not any(name == 'src.web' or name.startswith('src.web.')
               or name == 'src.agent' or name.startswith('src.agent.')
               for name in sys.modules)
"""],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_pinned_transport_exports_remain_identical():
    from src.agent import decision_transport
    from src.system import llm_transport

    for name in (
        "PinnedNetworkBackend", "PinnedAsyncHTTPTransport", "PinnedClientTransport",
        "pin_supplied_async_client", "create_pinned_async_client",
    ):
        assert getattr(decision_transport, name) is getattr(llm_transport, name)


def test_rag_presentation_legacy_exports_are_canonical_objects():
    from src.rag.presentation import format_rag_context, rag_info_molecule
    from src.web.rag_presentation import (
        format_rag_context as legacy_format_rag_context,
        rag_info_molecule as legacy_rag_info_molecule,
    )

    assert legacy_format_rag_context is format_rag_context
    assert legacy_rag_info_molecule is rag_info_molecule
