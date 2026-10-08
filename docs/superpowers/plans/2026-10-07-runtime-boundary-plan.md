# Runtime Boundary Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the first batch of `src.web` reverse dependencies from Agent and scientific-core code, centralize model-client/security/lifecycle implementations under the existing neutral `src/system/` package, and preserve all public compatibility paths and runtime behavior.

**Architecture:** Keep `MolecularChatApp` as the composition root and reuse its existing `AsyncExitStack` and `ModelRequestGate` semantics. Move shared implementations into a small number of modules under `src/system/`; leave old Web/Agent import paths as one-line compatibility exports. Do not change `api_routes._support`, scientific algorithms, workflow execution, task state mapping, or frontend structure in this plan.

**Tech Stack:** Python 3.10+, FastAPI, httpx/httpcore, Pydantic 2, pytest/pytest-asyncio, existing native JavaScript checks.

---

## Scope and invariants

The plan covers only the first approved stage:

- `src/agent` and `src/molecular_design` must stop importing `src.web`.
- URL policy, pinned HTTP transport, model clients, and model request lifecycle must have one implementation each.
- `src.web` and `src.agent` legacy import paths remain importable and expose the same objects as the neutral modules.
- Application-owned clients are closed exactly once; registry wrappers and duplicate references do not close the same owner twice; existing explicit injected-client semantics remain unchanged.
- Existing request payloads, stream parsing, error strings/codes, metadata, DNS pinning, cancellation draining, and model-switch behavior remain unchanged.

Explicitly out of scope: replacing `api_routes._support`, moving `task_runtime.routes` response helpers, changing fixed workflow or decision-loop ownership, redesigning scientific result contracts, and frontend connection/protocol/state/render splitting.

## Files and responsibilities

Create only under existing packages:

- `src/system/network_policy.py`: canonical `validate_llm_url` and `resolve_llm_host` implementation.
- `src/system/model_lifecycle.py`: canonical `ModelRequestGate`, `model_request`, `finish_on_cancel`, and `close_owned_model` implementation.
- `src/system/llm_transport.py`: canonical pinned HTTPX/httpcore transport helpers currently shared by model calls and decision transport.
- `src/system/ollama_model.py`: canonical Ollama client and `OllamaGenerationError`.
- `src/system/openai_compatible_model.py`: canonical OpenAI-compatible client.
- `src/system/modelscope_model.py`: canonical ModelScope adapter/manager.
- `src/system/model_clients.py`: stable neutral import surface for the three client families.
- `tests/test_runtime_boundary_contracts.py`: import-boundary, compatibility-identity, and ownership contract tests.

Modify only the following existing modules in this stage:

- `src/web/security/url_policy.py`, `src/web/model_lifecycle.py`, `src/web/models/ollama_model.py`, `src/web/models/__init__.py`: compatibility exports only after canonical implementations move.
- `src/agent/openai_compatible_model.py`, `src/agent/modelscope_model.py`: compatibility exports only.
- `src/agent/decision_transport.py`: keep decision protocol code; import/re-export low-level transport helpers from `src.system.llm_transport`.
- `src/agent/tools/__init__.py`, `src/agent/tools/llm_molecular_generator.py`, `src/agent/evaluation/scientific.py`: import Ollama objects from `src.system.model_clients`.
- `src/molecular_design/service.py`: import lifecycle decorator from `src.system.model_lifecycle`.
- `src/web/app.py`: use the neutral model-client surface for application assembly while retaining patchable public names where existing tests require them.

## Task 1: Establish the baseline and dependency inventory

**Files:**
- Read: `AGENTS.md`, `docs/PROJECT_STANDARDS.md`, `docs/superpowers/specs/2026-10-07-runtime-boundary-design.md`
- Test: existing focused suites listed below

- [ ] **Step 1: Confirm the checkout and preserve unrelated files**

Run:

```powershell
git status --short --branch
git log -5 --oneline --decorate
```

Expected: branch is not `main`; the existing untracked `data/molecular_faiss_index.index.manifest.json` remains untouched and is not staged.

- [ ] **Step 2: Run the baseline focused suites in the scientific environment**

Run:

```powershell
conda run --no-capture-output -n MedChat python -m pytest `
  tests/test_openai_compatible_model.py `
  tests/test_llm_security_policy.py `
  tests/test_model_request_lifecycle.py `
  tests/test_molecular_design_architecture.py `
  tests/test_llm_molecular_generator.py `
  tests/agent/test_decision_transport_boundaries.py -q
```

Record the real pass/fail/skip counts before implementation. Do not repair unrelated baseline failures in this task.

- [ ] **Step 3: Record the current reverse-import count**

Run:

```powershell
rg -n "^\s*(from|import) src\.web" src/agent src/molecular_design src/activity src/docking src/rag src/reverse_target --glob '*.py'
```

The current inventory must include the four approved first-stage groups: lifecycle, URL policy, Ollama model imports, and Agent decision transport URL resolution.

## Task 2: Add a failing import-boundary contract

**Files:**
- Create: `tests/test_runtime_boundary_contracts.py`

- [ ] **Step 1: Write the failing static boundary test**

Use AST parsing so the test does not require RDKit or instantiate a model:

```python
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
```

- [ ] **Step 2: Run the new test and verify the expected failure**

Run:

```powershell
conda run --no-capture-output -n MedChat python -m pytest tests/test_runtime_boundary_contracts.py::test_scientific_and_agent_core_do_not_import_web_modules -q
```

Expected: FAIL listing the existing `src.web` imports. This proves the test is detecting the intended dependency problem.

## Task 3: Move URL policy and pinned transport to the neutral boundary

**Files:**
- Create: `src/system/network_policy.py`
- Create: `src/system/llm_transport.py`
- Modify: `src/web/security/url_policy.py`
- Modify: `src/agent/openai_compatible_model.py`
- Modify: `src/agent/decision_transport.py`
- Test: `tests/test_llm_security_policy.py`, `tests/agent/test_decision_transport_boundaries.py`, `tests/test_runtime_boundary_contracts.py`

- [ ] **Step 1: Add compatibility-identity assertions before changing imports**

Extend `tests/test_runtime_boundary_contracts.py` with the intended public contract:

```python
def test_url_policy_legacy_exports_are_the_canonical_objects():
    from src.system.network_policy import resolve_llm_host, validate_llm_url
    from src.web.security.url_policy import (
        resolve_llm_host as legacy_resolve_llm_host,
        validate_llm_url as legacy_validate_llm_url,
    )

    assert legacy_resolve_llm_host is resolve_llm_host
    assert legacy_validate_llm_url is validate_llm_url
```

Run the test and observe the expected import failure because the canonical module does not yet exist.

- [ ] **Step 2: Move the existing URL policy implementation without changing behavior**

Copy the complete current implementation from `src/web/security/url_policy.py` to `src/system/network_policy.py` unchanged, including `_OFFICIAL_HOSTS`, `_OLLAMA_HOSTS`, `_configured_hosts`, `_is_blocked_address`, `resolve_llm_host`, `_resolved_addresses`, and `validate_llm_url`.

Replace the Web module body with only:

```python
from src.system.network_policy import (
    _resolved_addresses,
    resolve_llm_host,
    validate_llm_url,
)

__all__ = ["resolve_llm_host", "validate_llm_url"]
```

- [ ] **Step 3: Extract the low-level pinned transport helpers**

Move only `PinnedNetworkBackend`, `PinnedAsyncHTTPTransport`, `PinnedClientTransport`, `pin_supplied_async_client`, and `create_pinned_async_client` from `src/agent/decision_transport.py` to `src/system/llm_transport.py`. Keep decision protocol constants, parsers, journals, and request functions in `src/agent/decision_transport.py`.

At the top of `src/agent/decision_transport.py`, import and re-export the moved helpers:

```python
from src.system.llm_transport import (
    PinnedAsyncHTTPTransport,
    PinnedClientTransport,
    PinnedNetworkBackend,
    create_pinned_async_client,
    pin_supplied_async_client,
)
```

Inside `src/system/llm_transport.py`, import `resolve_llm_host` from `src.system.network_policy`, never from `src.web`.

- [ ] **Step 4: Change model and decision transport imports**

Use these exact imports:

```python
# src/agent/openai_compatible_model.py
from src.system.llm_transport import create_pinned_async_client, pin_supplied_async_client
from src.system.network_policy import validate_llm_url

# src/agent/decision_transport.py
from src.system.network_policy import resolve_llm_host
```

Do not change request payloads, stream parsing, exception messages, or DNS behavior.

- [ ] **Step 5: Run the focused security and transport tests**

Run:

```powershell
conda run --no-capture-output -n MedChat python -m pytest `
  tests/test_llm_security_policy.py `
  tests/agent/test_decision_transport_boundaries.py `
  tests/test_runtime_boundary_contracts.py -q
```

Expected: all tests in these files pass, including the AST boundary and object-identity assertions.

- [ ] **Step 6: Commit the isolated transport migration**

```powershell
git add -- src/system/network_policy.py src/system/llm_transport.py src/web/security/url_policy.py src/agent/openai_compatible_model.py src/agent/decision_transport.py tests/test_runtime_boundary_contracts.py
git commit -m "refactor: centralize model network policy"
```

## Task 4: Move request lifecycle ownership to the neutral boundary

**Files:**
- Create: `src/system/model_lifecycle.py`
- Modify: `src/web/model_lifecycle.py`
- Modify: `src/molecular_design/service.py`
- Test: `tests/test_model_request_lifecycle.py`, `tests/test_molecular_design_architecture.py`, `tests/test_runtime_boundary_contracts.py`

- [ ] **Step 1: Add lifecycle object-identity and cancellation tests**

Add these assertions to `tests/test_runtime_boundary_contracts.py` while retaining the existing detailed lifecycle tests:

```python
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
```

- [ ] **Step 2: Move the implementation and preserve the Web import path**

Copy the complete current implementation of `src/web/model_lifecycle.py` to `src/system/model_lifecycle.py` without changing the gate predicates, cancellation loop, background lease handling, or sanitized logging.

Replace `src/web/model_lifecycle.py` with:

```python
from src.system.model_lifecycle import (
    ModelRequestGate,
    close_owned_model,
    finish_on_cancel,
    model_request,
)

__all__ = [
    "ModelRequestGate",
    "close_owned_model",
    "finish_on_cancel",
    "model_request",
]
```

- [ ] **Step 3: Remove the scientific-core import of Web lifecycle code**

Change `src/molecular_design/service.py` to:

```python
from src.system.model_lifecycle import model_request
```

Leave the decorator usage and constructor behavior unchanged.

- [ ] **Step 4: Run lifecycle and molecular-design regression**

```powershell
conda run --no-capture-output -n MedChat python -m pytest `
  tests/test_model_request_lifecycle.py `
  tests/test_molecular_design_architecture.py `
  tests/test_runtime_boundary_contracts.py -q
```

Expected: existing cancellation, gate, ownership, and architecture assertions pass.

- [ ] **Step 5: Commit the lifecycle migration**

```powershell
git add -- src/system/model_lifecycle.py src/web/model_lifecycle.py src/molecular_design/service.py tests/test_runtime_boundary_contracts.py
git commit -m "refactor: move model lifecycle to system boundary"
```

## Task 5: Centralize model-client implementations and preserve compatibility seams

**Files:**
- Create: `src/system/ollama_model.py`
- Create: `src/system/openai_compatible_model.py`
- Create: `src/system/modelscope_model.py`
- Create: `src/system/model_clients.py`
- Modify: `src/web/models/ollama_model.py`
- Modify: `src/web/models/__init__.py`
- Modify: `src/agent/openai_compatible_model.py`
- Modify: `src/agent/modelscope_model.py`
- Modify: `src/agent/tools/__init__.py`
- Modify: `src/agent/tools/llm_molecular_generator.py`
- Modify: `src/agent/evaluation/scientific.py`
- Modify: `src/web/app.py`
- Test: `tests/test_openai_compatible_model.py`, `tests/test_llm_security_policy.py`, `tests/test_llm_molecular_generator.py`, `tests/test_web_app_lifecycle.py`, `tests/test_runtime_boundary_contracts.py`

- [ ] **Step 1: Add canonical model identity tests**

Add:

```python
def test_legacy_model_paths_export_the_same_canonical_clients():
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
    from src.agent.openai_compatible_model import OpenAICompatibleModel as LegacyOpenAI
    from src.web.models.ollama_model import (
        OllamaGenerationError as LegacyOllamaError,
        OllamaModel as LegacyOllama,
    )

    assert LegacyModelScopeModel is ModelScopeModel
    assert LegacyModelScopeModelManager is ModelScopeModelManager
    assert LegacyOpenAI is OpenAICompatibleModel
    assert LegacyOllama is OllamaModel
    assert LegacyOllamaError is OllamaGenerationError
```

Run the test and verify it fails before adding the canonical modules.

- [ ] **Step 2: Move the Ollama implementation**

Move the complete current class and error implementation from `src/web/models/ollama_model.py` to `src/system/ollama_model.py` unchanged. Keep its existing HTTP behavior, strict-generation checks, logging redaction, and `close` method.

Replace `src/web/models/ollama_model.py` with a compatibility export:

```python
from src.system.ollama_model import OllamaGenerationError, OllamaModel

__all__ = ["OllamaGenerationError", "OllamaModel"]
```

- [ ] **Step 3: Move the OpenAI-compatible and ModelScope implementations**

Move the complete current implementations from `src/agent/openai_compatible_model.py` and `src/agent/modelscope_model.py` to the corresponding `src/system/` modules. Change only their internal imports to `src.system.llm_transport` and `src.system.network_policy`.

Replace the old Agent modules with compatibility exports. `src/system/model_clients.py` must contain only stable exports:

```python
from .modelscope_model import ModelScopeModel, ModelScopeModelManager
from .ollama_model import OllamaGenerationError, OllamaModel
from .openai_compatible_model import OpenAICompatibleModel

__all__ = [
    "ModelScopeModel",
    "ModelScopeModelManager",
    "OllamaGenerationError",
    "OllamaModel",
    "OpenAICompatibleModel",
]
```

- [ ] **Step 4: Change Agent and Web runtime consumers to the neutral surface**

Use:

```python
from src.system.model_clients import OllamaGenerationError, OllamaModel
```

in `src/agent/tools/__init__.py`, `src/agent/tools/llm_molecular_generator.py`, and `src/agent/evaluation/scientific.py` wherever those modules currently import Web Ollama classes.

Update `src/web/app.py` model construction to import `ModelScopeModel`, `OllamaModel`, and `OpenAICompatibleModel` from `src.system.model_clients`. Keep the app-level names unchanged so existing tests and supported monkeypatch seams continue to work.

- [ ] **Step 5: Run model-client and application lifecycle tests**

```powershell
conda run --no-capture-output -n MedChat python -m pytest `
  tests/test_openai_compatible_model.py `
  tests/test_llm_security_policy.py `
  tests/test_llm_molecular_generator.py `
  tests/test_web_app_lifecycle.py `
  tests/test_runtime_boundary_contracts.py -q
```

Expected: all existing request, streaming, security, model-switch, close, and compatibility tests pass.

- [ ] **Step 6: Commit the canonical client migration**

```powershell
git add -- src/system/ollama_model.py src/system/openai_compatible_model.py src/system/modelscope_model.py src/system/model_clients.py src/web/models/ollama_model.py src/web/models/__init__.py src/agent/openai_compatible_model.py src/agent/modelscope_model.py src/agent/tools/__init__.py src/agent/tools/llm_molecular_generator.py src/agent/evaluation/scientific.py src/web/app.py tests/test_runtime_boundary_contracts.py
git commit -m "refactor: centralize model client implementations"
```

## Task 6: Verify application ownership and remove the first-stage reverse imports

**Files:**
- Modify: `tests/test_runtime_boundary_contracts.py`
- Test: all first-stage focused suites and static checks

- [ ] **Step 1: Add an application-owned close-once test**

Add a small fake owner and use the existing shutdown seam without asserting private stack layout:

```python
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
```

Add a sibling cancellation-path test using the existing `create_async` cancellation fixture in `tests/test_web_app_lifecycle.py`; assert the same owner counter is one after the canceled assembly is drained.

- [ ] **Step 2: Preserve explicit injected-client behavior**

Do not introduce an ownership flag or change adapter close semantics in this stage. Run the existing injected-client and nested-stream tests as the regression contract:

```powershell
conda run --no-capture-output -n MedChat python -m pytest `
  tests/test_openai_compatible_model.py::OpenAICompatibleModelTest `
  tests/test_model_request_lifecycle.py -q
```

The new canonical modules must preserve the current behavior exactly; any ownership-policy change requires a separate design and approval.

- [ ] **Step 3: Re-run the import inventory**

```powershell
rg -n "^\s*(from|import) src\.web" src/agent src/molecular_design src/activity src/docking src/rag src/reverse_target --glob '*.py'
```

Expected: no output for the first-stage target modules. Any remaining output must be documented as a later-scope dependency, not silently ignored.

- [ ] **Step 4: Run compile and JavaScript checks**

```powershell
python -m compileall -q src scripts
Get-ChildItem src/web/static/js -Recurse -Filter *.js -File |
  Where-Object { $_.FullName -notmatch 'ketcher|backup' } |
  ForEach-Object { node --check $_.FullName }
node tests/frontend_safe_render_test.js
node tests/chat_url_policy_test.js
```

Expected: all commands exit successfully.

- [ ] **Step 5: Run the full Python regression in `MedChat`**

```powershell
conda run --no-capture-output -n MedChat python -m pytest tests -q
```

Record the actual pass/skip/fail result. If the full suite is too long, do not claim it passed; report the completed focused suites and the exact stopping point.

- [ ] **Step 6: Inspect and commit only this stage**

```powershell
git status --short
git diff --check
git diff HEAD~4..HEAD --stat
```

Verify that `data/molecular_faiss_index.index.manifest.json` is still untracked and not staged, then commit any final test-only changes:

```powershell
git add -- tests/test_runtime_boundary_contracts.py
git commit -m "test: verify runtime boundary ownership"
```

## Completion report requirements

At the end of this plan, report:

- the exact number of first-stage `src.web` reverse-import edges removed;
- which responsibilities moved to `src/system` and which remain intentionally in Web or Agent;
- compatibility paths preserved and their identity-test results;
- model request, stream, URL security, cancellation, close-once, and injected-client compatibility test results;
- full-suite result, including real skips/failures or environment blockers;
- remaining `api_routes._support`, task-runtime, scientific-adapter, and frontend work as separate next-stage items.
