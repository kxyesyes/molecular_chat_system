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
    ROOT / "src/molecular_design/service.py",
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
