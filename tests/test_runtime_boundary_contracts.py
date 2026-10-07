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
