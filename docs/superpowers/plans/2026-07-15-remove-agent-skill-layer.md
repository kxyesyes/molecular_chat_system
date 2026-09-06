# Remove MedChat Agent Skill Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete `src/agent/skills/` and replace its runtime responsibilities with a declarative Workflow Policy catalog without changing scientific workflows, tool permissions, events, or compatibility response fields.

**Architecture:** `WorkflowPolicy` becomes the immutable source for workflow names, descriptions, tool allowlists, and multi-step metadata. Existing hybrid routing still selects a workflow identifier; `SkillRouter` remains a temporary class-name compatibility surface but returns a `WorkflowPolicy`. Supervisor and WorkflowExecutor consume policies, while `active_skill` and `skill_name` remain serialized compatibility fields containing workflow names.

**Tech Stack:** Python 3.10+, dataclasses, FastAPI integration, pytest, existing MedChat Agent contracts and acceptance runner.

---

## File map

- Create `src/agent/workflows/__init__.py`: export workflow policy types and catalog.
- Create `src/agent/workflows/catalog.py`: immutable policies and lookup catalog; the only tool-allowlist source.
- Create `tests/agent/test_workflow_catalog.py`: policy completeness, uniqueness, immutability, and allowlist tests.
- Create `tests/agent/test_no_legacy_skill_layer.py`: architectural guard preventing reintroduction of `src.agent.skills`.
- Modify `src/agent/router.py`: resolve route decisions through `WorkflowCatalog`.
- Modify `src/agent/routing/hybrid.py`: derive route tool metadata from workflow policies.
- Modify `src/agent/routing/__init__.py`: export the derived workflow mapping.
- Modify `src/agent/runtime/workflow_executor.py`: accept `WorkflowPolicy` instead of `BaseSkill`.
- Modify `src/agent/supervisor.py`: replace `SkillRegistry` with `WorkflowCatalog`.
- Modify `src/agent/react_agent.py`: remove Skill persona-prompt and iteration overrides while retaining legacy workflow execution compatibility.
- Modify `src/agent/evaluation/scientific.py`, `scripts/run_agent_acceptance.py`, and `scripts/health_check.py`: resolve policies through the catalog.
- Modify affected tests under `tests/agent/`: replace concrete Skill classes with catalog policies.
- Delete every tracked Python file under `src/agent/skills/`.

## Task 1: Introduce the declarative Workflow Policy catalog

**Files:**
- Create: `tests/agent/test_workflow_catalog.py`
- Create: `src/agent/workflows/__init__.py`
- Create: `src/agent/workflows/catalog.py`

- [ ] **Step 1: Write the failing catalog tests**

```python
from dataclasses import FrozenInstanceError

import pytest

from src.agent.workflows import WorkflowCatalog


EXPECTED_WORKFLOWS = {
    "admet_assessment",
    "activity_prediction",
    "reverse_target_prediction",
    "target_database_search",
    "molecular_design",
    "docking_simulation",
    "comprehensive_evaluation",
    "hit_to_lead_optimization",
    "target_driven_design",
    "rag_search",
}


def test_catalog_declares_each_supported_workflow_once():
    catalog = WorkflowCatalog()
    names = [policy.name for policy in catalog.policies]
    assert set(names) == EXPECTED_WORKFLOWS
    assert len(names) == len(set(names))


def test_target_design_policy_preserves_tool_allowlist():
    policy = WorkflowCatalog().require("target_driven_design")
    assert policy.allowed_tools == (
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "molecular_docking",
    )
    assert policy.is_multi_step is True


def test_policy_is_immutable():
    policy = WorkflowCatalog().require("molecular_design")
    with pytest.raises(FrozenInstanceError):
        policy.name = "changed"
```

- [ ] **Step 2: Run the catalog test and verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_workflow_catalog.py -q -p no:cacheprovider
```

Expected: collection fails with `ModuleNotFoundError: No module named 'src.agent.workflows'`.

- [ ] **Step 3: Implement the minimal catalog**

Create `src/agent/workflows/catalog.py` with this public shape and all ten policies:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class WorkflowPolicy:
    name: str
    description: str
    allowed_tools: tuple[str, ...]
    is_multi_step: bool = False


WORKFLOW_POLICIES = (
    WorkflowPolicy(
        "comprehensive_evaluation",
        "对候选分子执行性质、类药性、ADMET、活性、反向寻靶和靶点结构综合评价。",
        (
            "property_calculator",
            "drug_likeness_assessment",
            "admet_predictor",
            "activity_predictor",
            "reverse_target_predictor",
            "target_database_search",
        ),
        True,
    ),
    WorkflowPolicy(
        "hit_to_lead_optimization",
        "先诊断原分子，再生成和比较优化候选。",
        (
            "property_calculator",
            "drug_likeness_assessment",
            "admet_predictor",
            "activity_predictor",
            "llm_molecular_generator",
        ),
        True,
    ),
    WorkflowPolicy(
        "target_driven_design",
        "检索靶点后生成并筛选候选分子。",
        (
            "target_database_search",
            "llm_molecular_generator",
            "property_calculator",
            "admet_predictor",
            "activity_predictor",
            "molecular_docking",
        ),
        True,
    ),
    WorkflowPolicy("molecular_design", "使用本地生成模型生成候选分子。", ("llm_molecular_generator",)),
    WorkflowPolicy("activity_prediction", "使用真实活性模型预测分子活性。", ("activity_predictor",)),
    WorkflowPolicy("reverse_target_prediction", "基于结构证据预测潜在靶点。", ("reverse_target_predictor",)),
    WorkflowPolicy("target_database_search", "检索靶点和结构数据库。", ("target_database_search",)),
    WorkflowPolicy(
        "admet_assessment",
        "计算性质、类药性和 ADMET。",
        ("property_calculator", "admet_predictor", "drug_likeness_assessment"),
        True,
    ),
    WorkflowPolicy(
        "docking_simulation",
        "使用结构化受体、配体和 box 输入执行 docking。",
        ("molecular_docking", "prepare_receptor", "prepare_ligand", "run_docking", "get_docking_result"),
    ),
    WorkflowPolicy("rag_search", "检索本地知识库并保留来源。", ("rag_search",)),
)


class WorkflowCatalog:
    def __init__(self, policies: Iterable[WorkflowPolicy] = WORKFLOW_POLICIES):
        self.policies = tuple(policies)
        self._by_name = {policy.name: policy for policy in self.policies}
        if len(self._by_name) != len(self.policies):
            raise ValueError("Workflow policy names must be unique")

    def get(self, name: str) -> WorkflowPolicy | None:
        return self._by_name.get(name)

    def require(self, name: str) -> WorkflowPolicy:
        policy = self.get(name)
        if policy is None:
            raise KeyError(f"Unknown workflow: {name}")
        return policy

    def llm_catalog(self) -> str:
        return "\n".join(
            f"{index}. **{policy.name}**: {policy.description}"
            for index, policy in enumerate(self.policies, start=1)
        )
```

Create `src/agent/workflows/__init__.py`:

```python
from .catalog import WORKFLOW_POLICIES, WorkflowCatalog, WorkflowPolicy

__all__ = ["WORKFLOW_POLICIES", "WorkflowCatalog", "WorkflowPolicy"]
```

- [ ] **Step 4: Run the catalog tests and verify GREEN**

Run the command from Step 2. Expected: `3 passed`.

- [ ] **Step 5: Commit only the new catalog and test**

```powershell
git add -- src/agent/workflows/__init__.py src/agent/workflows/catalog.py tests/agent/test_workflow_catalog.py
git commit -m "refactor(agent): add workflow policy catalog"
```

## Task 2: Migrate routing from SkillRegistry to WorkflowCatalog

**Files:**
- Modify: `src/agent/router.py`
- Modify: `src/agent/routing/hybrid.py`
- Modify: `src/agent/routing/__init__.py`
- Test: `tests/agent/test_hybrid_router.py`
- Test: `tests/agent/test_routing_prompt_matrix.py`

- [ ] **Step 1: Add a failing high-level router policy test**

Append to `tests/agent/test_hybrid_router.py`:

```python
from src.agent.router import SkillRouter
from src.agent.workflows import WorkflowPolicy


def test_high_level_router_returns_workflow_policy_without_skill_registry():
    policy = SkillRouter().route("请计算 CCO 的分子量和 LogP")
    assert isinstance(policy, WorkflowPolicy)
    assert policy.name == "admet_assessment"
    assert "property_calculator" in policy.allowed_tools
```

- [ ] **Step 2: Run the new test and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_hybrid_router.py::test_high_level_router_returns_workflow_policy_without_skill_registry -q -p no:cacheprovider
```

Expected: failure because `SkillRouter` still returns a `BaseSkill` subclass.

- [ ] **Step 3: Replace registry use in both routers**

In `src/agent/routing/hybrid.py`, replace the SkillRegistry import and duplicated mapping with:

```python
from src.agent.workflows import WorkflowCatalog

WORKFLOW_TOOLS = {
    policy.name: list(policy.allowed_tools)
    for policy in WorkflowCatalog().policies
}
```

Change the constructor to accept `catalog: WorkflowCatalog | None = None`, assign
`self.catalog = catalog or WorkflowCatalog()`, and replace all `SKILL_TOOLS` lookups with
`WORKFLOW_TOOLS`.

In `src/agent/router.py`, initialize one `WorkflowCatalog`, pass it into
`HybridSkillRouter(catalog=self.catalog)`, and resolve `decision.selected_skill` with
`self.catalog.get(...)`. Delete `route_by_rules`, `_llm_route`, `_parse_skill_name`, and
all `BaseSkill`/`SkillRegistry` imports. Keep the public class name `SkillRouter` and
`get_tools_for_skill()` temporarily, but type its argument as `WorkflowPolicy`.

Update `src/agent/routing/__init__.py` to export `WORKFLOW_TOOLS` instead of `SKILL_TOOLS`.

- [ ] **Step 4: Run routing tests and verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_hybrid_router.py tests\agent\test_routing_prompt_matrix.py tests\agent\test_prompt_acceptance.py -q -p no:cacheprovider
```

Expected: all selected workflow identifiers and confirmation behavior remain unchanged.

- [ ] **Step 5: Commit routing migration**

```powershell
git add -- src/agent/router.py src/agent/routing/hybrid.py src/agent/routing/__init__.py tests/agent/test_hybrid_router.py
git commit -m "refactor(agent): route through workflow policies"
```

## Task 3: Migrate Supervisor and WorkflowExecutor policy consumption

**Files:**
- Modify: `src/agent/runtime/workflow_executor.py`
- Modify: `src/agent/supervisor.py`
- Modify: `src/agent/react_agent.py`
- Modify: `tests/agent/test_workflow_executor.py`
- Modify: `tests/agent/test_supervisor_agent.py`
- Modify: `tests/agent/test_comprehensive_workflow.py`
- Modify: `tests/agent/test_target_driven_design_workflow.py`
- Replace: `tests/agent/test_workflow_skills.py`
- Modify: `tests/agent/test_react_agent_workflow_routing.py`

- [ ] **Step 1: Replace fake Skill inheritance with a policy and verify RED**

In `tests/agent/test_workflow_executor.py`, remove `BaseSkill` and define:

```python
from src.agent.workflows import WorkflowPolicy

FAKE_POLICY = WorkflowPolicy(
    name="test_workflow",
    description="test",
    allowed_tools=("property_calculator",),
)
```

Pass `policy=FAKE_POLICY` to `WorkflowExecutor.execute()`. Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_workflow_executor.py -q -p no:cacheprovider
```

Expected: `TypeError` because the executor still expects the `skill` argument.

- [ ] **Step 2: Change WorkflowExecutor to policy input**

Use this signature in `src/agent/runtime/workflow_executor.py`:

```python
def execute(
    self,
    context: AgentContext,
    policy: WorkflowPolicy,
    all_tools: Mapping[str, Any],
    event_callback=None,
    idempotency_key: str | None = None,
) -> WorkflowExecution:
    plan = self.planner.plan(context)
    allowed = set(policy.allowed_tools)
```

Use `policy.name` in preflight result metadata and errors.

- [ ] **Step 3: Change Supervisor to catalog policies**

Replace the `registry` constructor parameter with `catalog: WorkflowCatalog | None = None`.
Add a helper that accepts either a `WorkflowPolicy`, a workflow-name string, or `None`:

```python
def _resolve_policy(self, query: str, active_workflow=None) -> WorkflowPolicy | None:
    if isinstance(active_workflow, WorkflowPolicy):
        return active_workflow
    if isinstance(active_workflow, str):
        return self.catalog.get(active_workflow)
    return self.skill_router.route(query, llm=self.llm)
```

Use the policy name for `AgentContext.active_skill` and pass `policy=policy` to every executor call.
For `run()`, resolve `context.active_skill` with `self.catalog.get(...)`; unknown names must return the
existing structured `Unknown workflow` failure.

- [ ] **Step 4: Remove ReAct Skill-only prompt behavior**

In `src/agent/react_agent.py`:

- replace `is_workflow` with `is_multi_step`;
- remove `system_prompt` injection;
- remove `max_iterations_override` handling;
- pass `policy=self._active_skill` to WorkflowExecutor;
- retain `.name` and `.allowed_tools` use because WorkflowPolicy provides both.

- [ ] **Step 5: Replace concrete Skill tests with catalog policy tests**

Use:

```python
from src.agent.workflows import WorkflowCatalog

policy = WorkflowCatalog().require("comprehensive_evaluation")
result = agent.execute("全面评估 CCO", active_skill=policy)
```

Replace `tests/agent/test_workflow_skills.py` assertions with catalog allowlist assertions already
covered by `test_workflow_catalog.py`; keep only the Supervisor behavior test if it is not duplicated.

- [ ] **Step 6: Run execution and Supervisor tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_workflow_executor.py tests\agent\test_supervisor_agent.py tests\agent\test_comprehensive_workflow.py tests\agent\test_target_driven_design_workflow.py tests\agent\test_react_agent_workflow_routing.py -q -p no:cacheprovider
```

Expected: all tests pass and no test imports `src.agent.skills`.

- [ ] **Step 7: Stage only task-owned hunks**

Before staging, run `git diff -- src/agent/supervisor.py`. Because this file had pre-existing user
changes, do not stage the whole file if unrelated hunks remain. Stage clean files normally and leave
overlapping Supervisor changes unstaged for final review.

## Task 4: Migrate evaluation, health checks, and acceptance scripts

**Files:**
- Modify: `src/agent/evaluation/scientific.py`
- Modify: `scripts/run_agent_acceptance.py`
- Modify: `scripts/health_check.py`
- Modify: `tests/agent/test_evaluation_runner.py`

- [ ] **Step 1: Add a failing acceptance import guard**

Add to `tests/agent/test_evaluation_runner.py`:

```python
def test_acceptance_modules_import_without_legacy_skill_package():
    import scripts.run_agent_acceptance
    import src.agent.evaluation.scientific

    assert scripts.run_agent_acceptance is not None
    assert src.agent.evaluation.scientific is not None
```

This becomes RED after the legacy package is removed unless all imports are migrated.

- [ ] **Step 2: Replace registry and concrete class construction**

Use one catalog in each runtime:

```python
from src.agent.workflows import WorkflowCatalog

catalog = WorkflowCatalog()
policy = catalog.require("comprehensive_evaluation")
```

Replace `skills.get_skill_by_name(actual_skill)` with `catalog.get(actual_skill)`, and pass
`policy=policy` into WorkflowExecutor. In `scripts/health_check.py`, report the number and names of
`catalog.policies` instead of SkillRegistry entries.

- [ ] **Step 3: Run evaluation and health-check tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_evaluation_runner.py tests\test_agent_platform_health_check.py -q -p no:cacheprovider
```

Expected: all tests pass without importing concrete Skill classes.

## Task 5: Add the architecture guard and delete the Skill package

**Files:**
- Create: `tests/agent/test_no_legacy_skill_layer.py`
- Delete: `src/agent/skills/__init__.py`
- Delete: `src/agent/skills/base_skill.py`
- Delete: `src/agent/skills/skill_registry.py`
- Delete: every `*_skill.py` file under `src/agent/skills/`

- [ ] **Step 1: Write the failing architecture guard**

```python
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_IMPORT = "src.agent." + "skills"
FORBIDDEN_RELATIVE_IMPORT = "from ." + "skills"
LEGACY_SYMBOLS = ("Base" + "Skill", "Skill" + "Registry")


def test_legacy_skill_package_is_removed():
    assert not (ROOT / "src" / "agent" / "skills").exists()


def test_python_sources_do_not_import_legacy_skill_package():
    offenders = []
    for base in (ROOT / "src", ROOT / "scripts", ROOT / "tests"):
        for path in base.rglob("*.py"):
            if path == Path(__file__).resolve():
                continue
            content = path.read_text(encoding="utf-8")
            if (
                FORBIDDEN_IMPORT in content
                or FORBIDDEN_RELATIVE_IMPORT in content
                or any(symbol in content for symbol in LEGACY_SYMBOLS)
            ):
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []
```

- [ ] **Step 2: Run the guard and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_no_legacy_skill_layer.py -q -p no:cacheprovider
```

Expected: both tests fail and identify the existing directory/importers.

- [ ] **Step 3: Delete tracked Skill sources and local cache**

Delete every tracked source with `apply_patch`. Before removing ignored bytecode, resolve and verify:

```powershell
$target = (Resolve-Path src\agent\skills\__pycache__).Path
$root = (Resolve-Path .).Path
if ($target.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $target -Recurse -Force
}
```

After source and cache removal, the now-empty `src/agent/skills/` directory disappears.

- [ ] **Step 4: Run the guard and verify GREEN**

Run the Step 2 command. Expected: `2 passed`.

- [ ] **Step 5: Search for residual imports**

```powershell
rg -n "src\.agent\.skills|from \.skills|BaseSkill|SkillRegistry" src scripts tests -g "*.py"
```

Expected: no output. Compatibility field names such as `active_skill` and `skill_name` are allowed.

## Task 6: Full regression and contract acceptance

**Files:**
- No new production files.
- Update the plan checkboxes with actual results only after commands complete.

- [ ] **Step 1: Compile Python sources**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
```

Expected: exit code 0.

- [ ] **Step 2: Run all Agent tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
```

Expected: all tests pass; any dependency skip is reported explicitly.

- [ ] **Step 3: Run anti-hallucination and platform tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py tests\agent\test_real_acceptance_checks.py -q -p no:cacheprovider
```

Expected: all available tests pass; no demo/fallback is reclassified as real success.

- [ ] **Step 4: Run contract acceptance**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract
```

Expected: report status `passed`; no real external tool is required by contract mode.

- [ ] **Step 5: Audit the final worktree**

```powershell
git status --short
git diff --check
git diff --name-status
```

Confirm that no API key, `.env`, local index manifest, output artifact, or unrelated working-tree file
is staged. Report any pre-existing modifications separately from this migration.
