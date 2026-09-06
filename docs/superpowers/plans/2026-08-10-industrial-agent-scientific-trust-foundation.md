# MedChat Industrial Agent Scientific Trust Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-safe increment of the approved industrial Agent platform: versioned scientific outcomes, capability resolution, compiled data bindings, strict generated-molecule validation, evidence-backed claims, and deterministic terminal-state reporting.

**Architecture:** Preserve the current `SupervisorAgent → WorkflowExecutor → WorkflowOrchestrator → ToolResult` path while inserting additive contracts and deterministic trust gates. Existing tools remain callable through `execute_tool_compat`; new capability, plan, binding, candidate, evidence, and claim components are framework-neutral so the next plan can place LangGraph above them without rewriting the scientific core.

**Tech Stack:** Python 3.10+, Pydantic 2, dataclasses, FastAPI-compatible contracts, RDKit, pytest, existing SQLite state store and Agent acceptance runner.

---

## Scope and sequencing

This plan implements phases 0 and 1 of the approved design in `docs/superpowers/specs/2026-08-10-industrial-agent-platform-design.md`.

It intentionally does not install LangGraph, Temporal, Kubernetes, NATS, OPA, MinIO, or LiteLLM. Those are separate implementation plans created after this plan's exit gates pass:

1. `industrial-agent-langgraph-harness`;
2. `industrial-agent-temporal-runtime`;
3. `industrial-agent-platform-services`;
4. `industrial-agent-governance-and-release-gates`.

Before execution, finish or separately preserve the current uncommitted target-design/UI work. Implement this plan from a clean `codex/industrial-agent-scientific-trust` branch. Do not stage unrelated changes and do not use `git add -A`.

## File map

### New files

- `src/agent/contracts/scientific.py`: observation/run status, provenance, and claim contracts.
- `src/agent/capabilities/catalog.py`: versioned scientific capability-to-tool catalog.
- `src/agent/capabilities/__init__.py`: public capability exports.
- `src/agent/planning/compiler.py`: deterministic workflow-plan validation.
- `src/agent/planning/bindings.py`: restricted upstream-output selector and transform resolver.
- `src/agent/validators/molecule_candidates.py`: RDKit canonicalization, validation, and deduplication.
- `src/agent/evidence/ledger.py`: append-only in-memory evidence projection for one run.
- `src/agent/evidence/renderer.py`: claim-placeholder renderer that rejects unsupported claims.
- `src/agent/evidence/__init__.py`: public evidence exports.
- `tests/agent/test_scientific_contracts.py`: status/provenance compatibility.
- `tests/agent/test_capability_catalog.py`: capability resolution and risk metadata.
- `tests/agent/test_plan_compiler.py`: plan, policy, and binding validation.
- `tests/agent/test_binding_resolver.py`: structured upstream data flow.
- `tests/agent/test_generated_candidate_validation.py`: invalid and duplicate SMILES handling.
- `tests/agent/test_evidence_ledger.py`: evidence and claim rendering safety.
- `tests/agent/test_scientific_trust_runtime.py`: orchestrator-level integration and terminal outcomes.

### Modified files

- `src/agent/contracts/result.py`: additive status and provenance fields.
- `src/agent/contracts/__init__.py`: scientific contract exports.
- `src/agent/orchestrators/base.py`: additive capability/binding/output-contract fields.
- `src/agent/planning/task_planner.py`: explicit capabilities and structured bindings.
- `src/agent/planning/__init__.py`: compiler/binding exports.
- `src/agent/runtime/workflow_executor.py`: invoke plan compiler during preflight.
- `src/agent/orchestrators/workflow.py`: resolve bindings, register evidence, and derive terminal outcome.
- `src/agent/validators/result_validator.py`: enforce generated-candidate sanitization.
- `src/agent/runtime/task_state.py`: distinguish partial terminal events.
- `src/agent/evaluation/scientific.py`: truth checks for candidates, evidence, and data flow.
- `tests/agent/test_task_planner.py`: explicit binding assertions.
- `tests/agent/test_workflow_orchestrator.py`: compiled data-flow regression coverage.
- `tests/agent/test_real_acceptance_checks.py`: replayable truth-gate coverage.

## Task 1: Versioned scientific contracts

**Files:**
- Create: `src/agent/contracts/scientific.py`
- Modify: `src/agent/contracts/__init__.py`
- Test: `tests/agent/test_scientific_contracts.py`

- [ ] **Step 1: Write the failing scientific-contract tests**

Create `tests/agent/test_scientific_contracts.py`:

```python
from src.agent.contracts import (
    ObservationStatus,
    RunOutcome,
    ScientificClaim,
    ToolProvenance,
)


def test_tool_provenance_serializes_truth_state():
    provenance = ToolProvenance(
        tool_name="activity_predictor",
        tool_version="2.1",
        model_name="rg-mpnn",
        model_version="weights-sha256:abc",
        demo_mode=False,
        fallback_used=False,
        input_digest="input-sha",
        output_digest="output-sha",
    )

    assert provenance.to_dict() == {
        "tool_name": "activity_predictor",
        "tool_version": "2.1",
        "model_name": "rg-mpnn",
        "model_version": "weights-sha256:abc",
        "demo_mode": False,
        "fallback_used": False,
        "input_digest": "input-sha",
        "output_digest": "output-sha",
    }


def test_scientific_claim_requires_evidence():
    try:
        ScientificClaim(
            claim_id="claim-mw",
            subject="aspirin",
            metric="molecular_weight",
            value=180.16,
            unit="g/mol",
            evidence_ids=(),
        )
    except ValueError as exc:
        assert "evidence" in str(exc).lower()
    else:
        raise AssertionError("claim without evidence must be rejected")


def test_status_enums_are_stable_strings():
    assert ObservationStatus.UNAVAILABLE.value == "unavailable"
    assert ObservationStatus.INVALID_INPUT.value == "invalid_input"
    assert RunOutcome.PARTIAL.value == "partial"
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_scientific_contracts.py -q -p no:cacheprovider
```

Expected: collection fails because `ObservationStatus`, `RunOutcome`, `ScientificClaim`, and `ToolProvenance` are not exported.

- [ ] **Step 3: Implement the scientific contracts**

Create `src/agent/contracts/scientific.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ObservationStatus(str, Enum):
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    INVALID_INPUT = "invalid_input"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class RunOutcome(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ToolProvenance:
    tool_name: str
    tool_version: str = "1"
    model_name: str | None = None
    model_version: str | None = None
    demo_mode: bool = False
    fallback_used: bool = False
    input_digest: str | None = None
    output_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "demo_mode": self.demo_mode,
            "fallback_used": self.fallback_used,
            "input_digest": self.input_digest,
            "output_digest": self.output_digest,
        }


@dataclass(frozen=True)
class ScientificClaim:
    claim_id: str
    subject: str
    metric: str
    value: Any
    unit: str | None
    evidence_ids: tuple[str, ...]
    confidence: float | None = None
    uncertainty: str | None = None

    def __post_init__(self) -> None:
        if not self.claim_id.strip():
            raise ValueError("claim_id cannot be empty")
        if not self.evidence_ids:
            raise ValueError("scientific claims require at least one evidence id")

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "subject": self.subject,
            "metric": self.metric,
            "value": self.value,
            "unit": self.unit,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
        }
```

Export the four names from `src/agent/contracts/__init__.py`:

```python
from .scientific import (
    ObservationStatus,
    RunOutcome,
    ScientificClaim,
    ToolProvenance,
)

__all__.extend(
    [
        "ObservationStatus",
        "RunOutcome",
        "ScientificClaim",
        "ToolProvenance",
    ]
)
```

- [ ] **Step 4: Run the tests and verify GREEN**

Run the Task 1 command. Expected: `3 passed`.

- [ ] **Step 5: Commit Task 1**

```powershell
git add -- src/agent/contracts/scientific.py src/agent/contracts/__init__.py tests/agent/test_scientific_contracts.py
git commit -m "feat(agent): add scientific outcome contracts"
```

## Task 2: Add truth status and provenance to ToolResult and AgentResult

**Files:**
- Modify: `src/agent/contracts/result.py`
- Modify: `tests/agent/test_scientific_contracts.py`
- Test: `tests/agent/test_contracts.py`

- [ ] **Step 1: Add failing backward-compatibility tests**

Append to `tests/agent/test_scientific_contracts.py`:

```python
from src.agent.contracts import AgentErrorCode, AgentResult, ToolResult


def test_tool_result_exposes_status_and_provenance_without_breaking_legacy_dict():
    provenance = ToolProvenance(tool_name="property_calculator", tool_version="3")
    result = ToolResult.success_result(
        "property_calculator",
        data={"molecular_weight": 46.07},
        provenance=provenance,
    )

    payload = result.to_legacy_dict()
    assert result.status == ObservationStatus.SUCCEEDED
    assert payload["status"] == "succeeded"
    assert payload["provenance"]["tool_version"] == "3"


def test_partial_observation_makes_agent_result_partial():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}],
        status=ObservationStatus.PARTIAL,
    )

    agent_result = AgentResult.from_tool_results(
        trace_id="partial-generation",
        skill_name="molecular_design",
        tool_results=[result],
    )

    assert agent_result.success is False
    assert agent_result.partial is True
    assert agent_result.outcome == RunOutcome.PARTIAL
    assert agent_result.to_legacy_dict()["status"] == "partial"


def test_failed_tool_uses_failed_observation_status():
    result = ToolResult.error_result(
        "activity_predictor",
        AgentErrorCode.MODEL_UNAVAILABLE,
        "weights missing",
    )
    assert result.status == ObservationStatus.FAILED
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_scientific_contracts.py tests\agent\test_contracts.py -q -p no:cacheprovider
```

Expected: failures for unsupported `status`/`provenance` arguments and missing `outcome`.

- [ ] **Step 3: Extend result contracts additively**

In `src/agent/contracts/result.py`, import the scientific types and append these fields to `ToolResult`:

```python
from .scientific import ObservationStatus, RunOutcome, ToolProvenance

# Inside ToolResult, after quality:
status: ObservationStatus | None = None
provenance: ToolProvenance | None = None

def __post_init__(self) -> None:
    if self.status is None:
        self.status = (
            ObservationStatus.SUCCEEDED
            if self.success
            else ObservationStatus.FAILED
        )
```

Add these keyword-only-compatible parameters at the end of both factories and pass them into the constructor:

```python
# success_result parameters
status: ObservationStatus = ObservationStatus.SUCCEEDED,
provenance: ToolProvenance | None = None,

# error_result parameters
status: ObservationStatus = ObservationStatus.FAILED,
provenance: ToolProvenance | None = None,
```

Add to `ToolResult.to_legacy_dict()`:

```python
"status": self.status.value if self.status else None,
"provenance": self.provenance.to_dict() if self.provenance else None,
```

Append an `outcome` field to `AgentResult`:

```python
outcome: RunOutcome | None = None
```

Replace the status derivation at the beginning of `AgentResult.from_tool_results()` with:

```python
has_partial_observation = any(
    item.status == ObservationStatus.PARTIAL for item in tool_results
)
all_succeeded = bool(tool_results) and all(item.success for item in tool_results)
any_succeeded = any(item.success for item in tool_results)
success = all_succeeded and not has_partial_observation
partial = any_succeeded and (not all_succeeded or has_partial_observation)
outcome = (
    RunOutcome.COMPLETED
    if success
    else RunOutcome.PARTIAL
    if partial
    else RunOutcome.FAILED
)
```

Pass `outcome=outcome` to the constructor and add to `AgentResult.to_legacy_dict()`:

```python
"status": self.outcome.value if self.outcome else (
    "completed" if self.success else "partial" if self.partial else "failed"
),
```

- [ ] **Step 4: Verify GREEN and contract regressions**

Run the Task 2 command. Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add -- src/agent/contracts/result.py tests/agent/test_scientific_contracts.py
git commit -m "feat(agent): preserve tool truth status and provenance"
```

## Task 3: Introduce the scientific capability catalog

**Files:**
- Create: `src/agent/capabilities/catalog.py`
- Create: `src/agent/capabilities/__init__.py`
- Modify: `src/agent/tooling/registry.py`
- Test: `tests/agent/test_capability_catalog.py`
- Test: `tests/agent/test_tool_registry.py`

- [ ] **Step 1: Write failing capability tests**

Create `tests/agent/test_capability_catalog.py`:

```python
import pytest

from src.agent.capabilities import CAPABILITY_CATALOG, capability_for_tool


def test_capability_catalog_maps_real_scientific_tools():
    assert capability_for_tool("property_calculator").name == "molecule.properties"
    assert capability_for_tool("llm_molecular_generator").name == "molecule.generate"
    assert capability_for_tool("molecular_docking").approval_required is True
    assert capability_for_tool("activity_predictor").scientific_result is True


def test_unknown_tool_has_no_implicit_capability():
    with pytest.raises(KeyError, match="Unknown tool capability"):
        capability_for_tool("run_arbitrary_python")


def test_capability_names_are_unique():
    names = [item.name for item in CAPABILITY_CATALOG]
    assert len(names) == len(set(names))
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_capability_catalog.py tests\agent\test_tool_registry.py -q -p no:cacheprovider
```

Expected: import failure for `src.agent.capabilities`.

- [ ] **Step 3: Implement the catalog and registry resolver**

Create `src/agent/capabilities/catalog.py`:

```python
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    tool_names: tuple[str, ...]
    risk: str
    approval_required: bool
    scientific_result: bool = True


CAPABILITY_CATALOG = (
    CapabilitySpec("molecule.properties", ("property_calculator",), "low", False),
    CapabilitySpec("molecule.drug_likeness", ("drug_likeness_assessment",), "low", False),
    CapabilitySpec("molecule.admet", ("admet_predictor",), "medium", False),
    CapabilitySpec("molecule.activity", ("activity_predictor",), "medium", False),
    CapabilitySpec("molecule.generate", ("llm_molecular_generator",), "medium", False),
    CapabilitySpec("target.reverse_predict", ("reverse_target_predictor",), "medium", False),
    CapabilitySpec("target.structure.search", ("target_database_search",), "low", False),
    CapabilitySpec("docking.execute", ("molecular_docking",), "high", True),
    CapabilitySpec("knowledge.retrieve", ("rag_search",), "low", False, False),
)

_BY_NAME = {item.name: item for item in CAPABILITY_CATALOG}
_BY_TOOL = {
    tool_name: item
    for item in CAPABILITY_CATALOG
    for tool_name in item.tool_names
}


def get_capability(name: str) -> CapabilitySpec:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise KeyError(f"Unknown capability: {name}") from exc


def capability_for_tool(tool_name: str) -> CapabilitySpec:
    try:
        return _BY_TOOL[tool_name]
    except KeyError as exc:
        raise KeyError(f"Unknown tool capability: {tool_name}") from exc
```

Create `src/agent/capabilities/__init__.py`:

```python
from .catalog import (
    CAPABILITY_CATALOG,
    CapabilitySpec,
    capability_for_tool,
    get_capability,
)

__all__ = [
    "CAPABILITY_CATALOG",
    "CapabilitySpec",
    "capability_for_tool",
    "get_capability",
]
```

Add this method to `ToolRegistry` without changing existing `ToolSpec.capabilities` behavior:

```python
def resolve_capability(
    self,
    capability: str,
    agent_name: str | None = None,
    require_available: bool = True,
) -> ToolAdapter:
    from src.agent.capabilities import get_capability

    spec = get_capability(capability)
    candidates = []
    for tool_name in spec.tool_names:
        try:
            candidates.append(
                self.resolve(
                    tool_name,
                    agent_name=agent_name,
                    require_available=require_available,
                )
            )
        except (KeyError, RuntimeError):
            continue
    if not candidates:
        raise RuntimeError(f"No available tool implements capability: {capability}")
    return candidates[0]
```

- [ ] **Step 4: Verify GREEN**

Run the Task 3 command. Expected: capability and existing registry tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add -- src/agent/capabilities src/agent/tooling/registry.py tests/agent/test_capability_catalog.py
git commit -m "feat(agent): add scientific capability catalog"
```

## Task 4: Compile workflow plans before execution

**Files:**
- Create: `src/agent/planning/compiler.py`
- Modify: `src/agent/orchestrators/base.py`
- Modify: `src/agent/planning/__init__.py`
- Modify: `src/agent/runtime/workflow_executor.py`
- Test: `tests/agent/test_plan_compiler.py`
- Test: `tests/agent/test_workflow_executor.py`

- [ ] **Step 1: Write failing compiler tests**

Create `tests/agent/test_plan_compiler.py`:

```python
import pytest

from src.agent.orchestrators import WorkflowStep
from src.agent.planning import PlanCompilationError, PlanCompiler, WorkflowPlan
from src.agent.workflows import WorkflowCatalog


def test_compiler_accepts_bound_upstream_output():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                output_key="target",
                capability="target.structure.search",
                output_contract="TargetEvidenceSet@1",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_binding="$.outputs.target",
                input_transform="identity",
                output_key="molecules",
                capability="molecule.generate",
                output_contract="CandidateSet@1",
            ),
            WorkflowStep(
                "properties",
                "property_calculator",
                input_binding="$.outputs.molecules",
                input_transform="smiles_text",
                capability="molecule.properties",
                output_contract="PropertyAssessmentSet@1",
            ),
        ],
    )

    compiled = PlanCompiler().compile(
        plan,
        WorkflowCatalog().require("target_driven_design"),
    )

    assert compiled.dependencies == {
        "target_search": (),
        "generate": ("target_search",),
        "properties": ("generate",),
    }


def test_compiler_rejects_unknown_upstream_output():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "properties",
                "property_calculator",
                input_binding="$.outputs.missing",
                capability="molecule.properties",
            )
        ],
    )

    with pytest.raises(PlanCompilationError, match="missing"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


def test_compiler_rejects_capability_tool_mismatch():
    plan = WorkflowPlan(
        workflow_name="admet_assessment",
        steps=[
            WorkflowStep(
                "unsafe",
                "property_calculator",
                capability="molecule.activity",
            )
        ],
    )

    with pytest.raises(PlanCompilationError, match="does not implement"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("admet_assessment"),
        )


```

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_plan_compiler.py tests\agent\test_workflow_executor.py -q -p no:cacheprovider
```

Expected: missing fields and compiler imports.

- [ ] **Step 3: Add backward-compatible step fields**

Append to `WorkflowStep` in `src/agent/orchestrators/base.py`:

```python
capability: str | None = None
input_binding: str | None = None
input_transform: str = "identity"
output_contract: str | None = None
```

- [ ] **Step 4: Implement PlanCompiler**

Create `src/agent/planning/compiler.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import re

from src.agent.capabilities import capability_for_tool, get_capability
from src.agent.workflows import WorkflowPolicy

from .task_planner import WorkflowPlan


class PlanCompilationError(ValueError):
    pass


@dataclass(frozen=True)
class CompiledPlan:
    plan: WorkflowPlan
    dependencies: dict[str, tuple[str, ...]]


class PlanCompiler:
    OUTPUT_SELECTOR = re.compile(r"^\$\.outputs\.([A-Za-z0-9_-]+)$")

    def compile(self, plan: WorkflowPlan, policy: WorkflowPolicy) -> CompiledPlan:
        seen_steps: set[str] = set()
        producers: dict[str, str] = {}
        dependencies: dict[str, tuple[str, ...]] = {}
        allowed = set(policy.allowed_tools)

        for step in plan.steps:
            if step.name in seen_steps:
                raise PlanCompilationError(f"Duplicate step name: {step.name}")
            seen_steps.add(step.name)
            if step.tool_name not in allowed:
                raise PlanCompilationError(
                    f"Tool {step.tool_name} is not allowed by {policy.name}"
                )

            expected = capability_for_tool(step.tool_name)
            requested = get_capability(step.capability) if step.capability else expected
            if step.tool_name not in requested.tool_names:
                raise PlanCompilationError(
                    f"Tool {step.tool_name} does not implement {requested.name}"
                )
            selector = step.input_binding
            if selector is None and step.input_from:
                selector = f"$.outputs.{step.input_from}"
            step_dependencies: tuple[str, ...] = ()
            if selector:
                match = self.OUTPUT_SELECTOR.fullmatch(selector)
                if not match:
                    raise PlanCompilationError(f"Unsupported input binding: {selector}")
                output_key = match.group(1)
                if output_key not in producers:
                    raise PlanCompilationError(
                        f"Binding references missing output: {output_key}"
                    )
                step_dependencies = (producers[output_key],)
            dependencies[step.name] = step_dependencies

            if step.output_key:
                if step.output_key in producers:
                    raise PlanCompilationError(
                        f"Duplicate output key: {step.output_key}"
                    )
                producers[step.output_key] = step.name

        return CompiledPlan(plan=plan, dependencies=dependencies)
```

Add these exports to `src/agent/planning/__init__.py`:

```python
from .compiler import CompiledPlan, PlanCompilationError, PlanCompiler

__all__.extend(["CompiledPlan", "PlanCompilationError", "PlanCompiler"])
```

- [ ] **Step 5: Enforce compilation in WorkflowExecutor preflight**

Add a `compiler` dependency to `WorkflowExecutor.__init__`:

```python
from src.agent.planning import PlanCompilationError, PlanCompiler

def __init__(self, planner=None, orchestrator=None, compiler=None):
    self.planner = planner or TaskPlanner()
    self.orchestrator = orchestrator
    self.compiler = compiler or PlanCompiler()
```

In `preflight()`, preserve the existing unauthorized/missing-tool checks and their error codes. Immediately after those checks return successfully, compile the plan and return this structured failure when compilation fails:

```python
try:
    self.compiler.compile(plan, policy)
except (PlanCompilationError, KeyError) as exc:
    error = AgentExecutionError(
        code=AgentErrorCode.VALIDATION_ERROR,
        message="Workflow plan failed compilation",
        details={"skill": policy.name, "reason": str(exc)},
    )
    return WorkflowExecution(
        plan=plan,
        result=AgentResult(
            trace_id=context.trace_id,
            success=False,
            message=error.message,
            skill_name=policy.name,
            error=error,
            metadata={"preflight": error.details},
        ),
        events=[],
    )
```

- [ ] **Step 6: Verify GREEN**

Run the Task 4 command. Expected: compiler and executor tests pass.

- [ ] **Step 7: Commit Task 4**

```powershell
git add -- src/agent/orchestrators/base.py src/agent/planning/compiler.py src/agent/planning/__init__.py src/agent/runtime/workflow_executor.py tests/agent/test_plan_compiler.py
git commit -m "feat(agent): compile workflow plans before execution"
```

## Task 5: Resolve explicit upstream data bindings

**Files:**
- Create: `src/agent/planning/bindings.py`
- Modify: `src/agent/planning/__init__.py`
- Modify: `src/agent/planning/task_planner.py`
- Modify: `src/agent/orchestrators/workflow.py`
- Test: `tests/agent/test_binding_resolver.py`
- Modify: `tests/agent/test_task_planner.py`
- Modify: `tests/agent/test_workflow_orchestrator.py`

- [ ] **Step 1: Write failing binding tests**

Create `tests/agent/test_binding_resolver.py`:

```python
import pytest

from src.agent.planning import BindingResolutionError, BindingResolver


def test_binding_resolver_reads_upstream_output_as_smiles_text():
    value = BindingResolver().resolve(
        "$.outputs.molecules",
        "smiles_text",
        request={"query": "design"},
        outputs={
            "molecules": [
                {"smiles": "CCO"},
                {"smiles": "OCC"},
                {"smiles": "CCN"},
            ]
        },
    )
    assert value == "CCO\nOCC\nCCN"


def test_binding_resolver_preserves_raw_target_records():
    targets = [{"gene_symbol": "EGFR", "similarity": 0.91}]
    value = BindingResolver().resolve(
        "$.outputs.targets",
        "identity",
        request={"query": "analyze"},
        outputs={"targets": targets},
    )
    assert value == targets


def test_binding_resolver_rejects_untrusted_selector_syntax():
    with pytest.raises(BindingResolutionError, match="Unsupported"):
        BindingResolver().resolve(
            "$.outputs[*].secret",
            "identity",
            request={},
            outputs={},
        )
```

Append these data-flow invariants to `tests/agent/test_plan_compiler.py`:

```python
def test_compiler_rejects_target_design_that_ignores_target_evidence():
    plan = WorkflowPlan(
        workflow_name="target_driven_design",
        steps=[
            WorkflowStep(
                "target_search",
                "target_database_search",
                output_key="target",
                capability="target.structure.search",
            ),
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                input_data="original query only",
                output_key="molecules",
                capability="molecule.generate",
            ),
        ],
    )
    with pytest.raises(PlanCompilationError, match="target evidence"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("target_driven_design"),
        )


def test_compiler_rejects_lead_generation_that_ignores_baseline_properties():
    plan = WorkflowPlan(
        workflow_name="hit_to_lead_optimization",
        steps=[
            WorkflowStep(
                "baseline_properties",
                "property_calculator",
                output_key="baseline",
                capability="molecule.properties",
            ),
            WorkflowStep(
                "molecule_generation",
                "llm_molecular_generator",
                input_data="original query only",
                output_key="candidates",
                capability="molecule.generate",
            ),
        ],
    )
    with pytest.raises(PlanCompilationError, match="baseline properties"):
        PlanCompiler().compile(
            plan,
            WorkflowCatalog().require("hit_to_lead_optimization"),
        )
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_binding_resolver.py tests\agent\test_plan_compiler.py tests\agent\test_task_planner.py tests\agent\test_workflow_orchestrator.py -q -p no:cacheprovider
```

Expected: missing binding resolver imports.

- [ ] **Step 3: Implement the restricted resolver**

Create `src/agent/planning/bindings.py`:

```python
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any


class BindingResolutionError(ValueError):
    pass


class BindingResolver:
    OUTPUT = re.compile(r"^\$\.outputs\.([A-Za-z0-9_-]+)$")

    def resolve(
        self,
        selector: str,
        transform: str,
        request: Mapping[str, Any],
        outputs: Mapping[str, Any],
    ) -> Any:
        if selector == "$.request.query":
            value = request.get("query")
        else:
            match = self.OUTPUT.fullmatch(selector)
            if not match:
                raise BindingResolutionError(f"Unsupported binding selector: {selector}")
            key = match.group(1)
            if key not in outputs:
                raise BindingResolutionError(f"Missing bound output: {key}")
            value = outputs[key]

        if transform == "identity":
            return value
        if transform == "smiles_text":
            return self._smiles_text(value)
        raise BindingResolutionError(f"Unsupported binding transform: {transform}")

    @classmethod
    def _smiles_text(cls, value: Any) -> str:
        smiles: list[str] = []

        def collect(item: Any) -> None:
            if isinstance(item, str):
                if item.strip():
                    smiles.append(item.strip())
            elif isinstance(item, Mapping):
                direct = item.get("smiles")
                if isinstance(direct, str) and direct.strip():
                    smiles.append(direct.strip())
                for key in ("molecules", "candidates", "data"):
                    if key in item:
                        collect(item[key])
            elif isinstance(item, (list, tuple)):
                for child in item:
                    collect(child)

        collect(value)
        return "\n".join(dict.fromkeys(smiles))
```

Add these exports to `src/agent/planning/__init__.py`:

```python
from .bindings import BindingResolutionError, BindingResolver

__all__.extend(["BindingResolutionError", "BindingResolver"])
```

- [ ] **Step 4: Make planner data flow explicit**

For target-driven design steps in `TaskPlanner`, retain `input_from` for compatibility and add:

```python
# molecule_generation
input_binding="$.outputs.target",
input_transform="identity",
input_template=(
    "User request: {query}\n"
    "Validated target evidence: {input}"
),
capability="molecule.generate",
output_contract="CandidateSet@1",

# properties
input_binding="$.outputs.molecules",
input_transform="smiles_text",
capability="molecule.properties",
output_contract="PropertyAssessmentSet@1",

# admet
input_binding="$.outputs.molecules",
input_transform="smiles_text",
capability="molecule.admet",
output_contract="AdmetAssessmentSet@1",

# activity
input_binding="$.outputs.molecules",
input_transform="smiles_text",
capability="molecule.activity",
output_contract="ActivityPredictionSet@1",
```

For comprehensive target structure search, add:

```python
input_binding="$.outputs.targets",
input_transform="identity",
capability="target.structure.search",
output_contract="TargetStructureSet@1",
```

Extend `test_target_driven_design_plan_for_pde5_query()` in `tests/agent/test_task_planner.py` with:

```python
generation_step = plan.steps[1]
assert generation_step.input_binding == "$.outputs.target"
assert generation_step.input_transform == "identity"
assert "Validated target evidence" in generation_step.input_template
assert plan.steps[2].input_binding == "$.outputs.molecules"
assert plan.steps[3].input_binding == "$.outputs.molecules"
assert plan.steps[4].input_binding == "$.outputs.molecules"
```

In `_lead_optimization_plan()`, make generation consume the baseline and keep candidate-property input explicit:

```python
# molecule_generation
input_binding="$.outputs.baseline",
input_transform="identity",
input_template=(
    "User optimization request: {query}\n"
    "Computed baseline properties: {input}"
),
capability="molecule.generate",
output_contract="CandidateSet@1",

# candidate_properties
input_binding="$.outputs.candidates",
input_transform="smiles_text",
capability="molecule.properties",
output_contract="PropertyAssessmentSet@1",
```

Add this assertion to the existing lead-optimization planner test:

```python
generation_step = next(
    step for step in plan.steps if step.name == "molecule_generation"
)
assert generation_step.input_binding == "$.outputs.baseline"
candidate_step = next(
    step for step in plan.steps if step.name == "candidate_properties"
)
assert candidate_step.input_binding == "$.outputs.candidates"
```

Extend `PlanCompiler.compile()` after the generic step loop and before returning `CompiledPlan`:

```python
step_capabilities = {
    step.name: (
        step.capability or capability_for_tool(step.tool_name).name
    )
    for step in plan.steps
}

if plan.workflow_name == "target_driven_design":
    target_step = next(
        (
            step
            for step in plan.steps
            if step_capabilities[step.name] == "target.structure.search"
        ),
        None,
    )
    generation_step = next(
        (
            step
            for step in plan.steps
            if step_capabilities[step.name] == "molecule.generate"
        ),
        None,
    )
    expected_binding = (
        f"$.outputs.{target_step.output_key}"
        if target_step and target_step.output_key
        else None
    )
    if (
        generation_step is None
        or not expected_binding
        or generation_step.input_binding != expected_binding
    ):
        raise PlanCompilationError(
            "Target-driven generation must consume target evidence"
        )

if plan.workflow_name == "hit_to_lead_optimization":
    generation_index = next(
        (
            index
            for index, step in enumerate(plan.steps)
            if step_capabilities[step.name] == "molecule.generate"
        ),
        None,
    )
    baseline_step = next(
        (
            step
            for index, step in enumerate(plan.steps)
            if generation_index is not None
            and index < generation_index
            and step_capabilities[step.name] == "molecule.properties"
            and step.output_key
        ),
        None,
    )
    generation_step = (
        plan.steps[generation_index]
        if generation_index is not None
        else None
    )
    expected_binding = (
        f"$.outputs.{baseline_step.output_key}"
        if baseline_step
        else None
    )
    if (
        generation_step is None
        or not expected_binding
        or generation_step.input_binding != expected_binding
    ):
        raise PlanCompilationError(
            "Hit-to-lead generation must consume baseline properties"
        )
```

- [ ] **Step 5: Use BindingResolver in the orchestrator**

Add the resolver imports to `src/agent/orchestrators/workflow.py`:

```python
from src.agent.planning import BindingResolutionError, BindingResolver
```

Replace `_resolve_input()` with the same compatibility branches plus the new binding branch. Do not return before applying `input_template`:

```python
@classmethod
def _resolve_input(
    cls,
    context: AgentContext,
    step: WorkflowStep,
    outputs: Mapping[str, Any],
) -> Any:
    if step.input_binding:
        input_data = BindingResolver().resolve(
            step.input_binding,
            step.input_transform,
            request={"query": context.query, "metadata": context.metadata},
            outputs=outputs,
        )
    elif step.input_from:
        raw_input = outputs.get(step.input_from)
        input_data = (
            raw_input
            if step.metadata.get("input_mode") == "raw"
            else cls._smiles_text(raw_input)
        )
    else:
        input_data = context.query if step.input_data is None else step.input_data

    if step.input_template:
        return step.input_template.format(input=input_data, query=context.query)
    return input_data
```

Replace the direct `_resolve_input()` call in `run()` with this guarded block. The existing checkpoint/tool-execution branch runs only in the `else` block:

```python
try:
    input_data = self._resolve_input(context, step, outputs)
except BindingResolutionError as exc:
    input_data = {
        "binding": step.input_binding,
        "resolution_error": str(exc),
    }
    input_hash = self._input_hash(input_data)
    tool_version = str(getattr(tool, "version", "1") if tool else "missing")
    model_version = str(
        getattr(getattr(tool, "llm_model", None), "model_name", "")
    )
    checkpoint = None
    result = ToolResult.error_result(
        tool_name=step.tool_name,
        code=AgentErrorCode.INVALID_INPUT,
        message="Workflow input binding could not be resolved",
        details={
            "step": step.name,
            "selector": step.input_binding,
            "reason": str(exc),
        },
    )
else:
    input_hash = self._input_hash(input_data)
    tool_version = str(
        step.metadata.get(
            "tool_version",
            getattr(tool, "version", "1") if tool is not None else "missing",
        )
    )
    model_version = str(
        step.metadata.get(
            "model_version",
            getattr(getattr(tool, "llm_model", None), "model_name", ""),
        )
    )
    checkpoint = self._compatible_checkpoint(
        context.trace_id,
        step,
        input_hash,
        tool_version,
        model_version,
    )
    if checkpoint:
        result = self._result_from_checkpoint(step.tool_name, checkpoint)
        reused_steps.append(step.name)
    elif tool is None:
        result = ToolResult.error_result(
            tool_name=step.tool_name,
            code=AgentErrorCode.INTERNAL_ERROR,
            message=f"Tool not found: {step.tool_name}",
            details={"step": step.name},
        )
    else:
        self._save_checkpoint(
            context,
            step,
            status="running",
            input_hash=input_hash,
            tool_version=tool_version,
            model_version=model_version,
        )
        result = self._execute_step(tool, input_data, step)
```

- [ ] **Step 6: Verify GREEN**

Run the Task 5 command. Expected: all binding, planner, and orchestrator tests pass.

- [ ] **Step 7: Commit Task 5**

```powershell
git add -- src/agent/planning/bindings.py src/agent/planning/compiler.py src/agent/planning/__init__.py src/agent/planning/task_planner.py src/agent/orchestrators/workflow.py tests/agent/test_binding_resolver.py tests/agent/test_plan_compiler.py tests/agent/test_task_planner.py tests/agent/test_workflow_orchestrator.py
git commit -m "feat(agent): bind downstream steps to upstream outputs"
```

## Task 6: Sanitize generated molecule candidates with RDKit

**Files:**
- Create: `src/agent/validators/molecule_candidates.py`
- Modify: `src/agent/validators/result_validator.py`
- Modify: `src/agent/validators/__init__.py`
- Test: `tests/agent/test_generated_candidate_validation.py`
- Test: `tests/agent/test_domain_result_validators.py`

- [ ] **Step 1: Write failing molecule-sanitization tests**

Create `tests/agent/test_generated_candidate_validation.py`:

```python
from src.agent.contracts import ObservationStatus, ToolResult
from src.agent.validators import AgentResultValidator


def test_generator_result_keeps_only_valid_unique_canonical_smiles():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[
            {"smiles": "CCO", "source": "llm"},
            {"smiles": "OCC", "source": "llm"},
            {"smiles": "CC(C)((", "source": "llm"},
            {"smiles": "CCN", "source": "llm"},
        ],
        quality={"requested_count": 4},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert [item["smiles"] for item in validated.data] == ["CCO", "CCN"]
    assert validated.status == ObservationStatus.PARTIAL
    assert validated.quality["valid_count"] == 2
    assert validated.quality["unique_count"] == 2
    assert validated.quality["invalid_count"] == 1
    assert validated.quality["duplicate_count"] == 1


def test_generator_result_fails_when_no_valid_candidate_remains():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CC(C)(("}, {"smiles": "not a smiles"}],
        quality={"requested_count": 2},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.success is False
    assert validated.data is None
    assert validated.error.code.value == "invalid_output"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_generated_candidate_validation.py tests\agent\test_domain_result_validators.py -q -p no:cacheprovider
```

Expected: invalid and duplicate candidates remain in the result.

- [ ] **Step 3: Implement the sanitizer**

Create `src/agent/validators/molecule_candidates.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CandidateValidationUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class CandidateSanitization:
    candidates: list[dict[str, Any]]
    invalid_count: int
    duplicate_count: int

    @property
    def valid_count(self) -> int:
        return len(self.candidates)


def sanitize_generated_candidates(data: Any) -> CandidateSanitization:
    try:
        from rdkit import Chem
    except Exception as exc:
        raise CandidateValidationUnavailable(
            "RDKit is required to validate generated molecules"
        ) from exc

    raw = data
    if isinstance(data, dict):
        raw = data.get("molecules") or data.get("candidates") or data.get("data") or []
    if not isinstance(raw, list):
        raw = []

    accepted: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid_count = 0
    duplicate_count = 0
    for item in raw:
        candidate = dict(item) if isinstance(item, dict) else {"smiles": str(item)}
        smiles = str(candidate.get("smiles") or "").strip()
        mol = Chem.MolFromSmiles(smiles) if smiles else None
        if mol is None:
            invalid_count += 1
            continue
        canonical = Chem.MolToSmiles(mol)
        if canonical in seen:
            duplicate_count += 1
            continue
        seen.add(canonical)
        candidate["smiles"] = canonical
        candidate["validation"] = {"valid": True, "method": "RDKit"}
        accepted.append(candidate)

    return CandidateSanitization(
        candidates=accepted,
        invalid_count=invalid_count,
        duplicate_count=duplicate_count,
    )
```

- [ ] **Step 4: Enforce sanitization in AgentResultValidator**

At the beginning of successful-result validation, before generic SMILES warnings, call the sanitizer for `llm_molecular_generator`:

```python
from src.agent.contracts import ObservationStatus
from .molecule_candidates import (
    CandidateValidationUnavailable,
    sanitize_generated_candidates,
)

if result.tool_name == "llm_molecular_generator":
    try:
        sanitized = sanitize_generated_candidates(result.data)
    except CandidateValidationUnavailable as exc:
        result.success = False
        result.status = ObservationStatus.UNAVAILABLE
        result.data = None
        result.formatted = ""
        result.message = str(exc)
        result.error = AgentExecutionError(
            code=AgentErrorCode.TOOL_UNAVAILABLE,
            message=str(exc),
            details={"tool_name": result.tool_name},
        )
        return result

    requested = int(result.quality.get("requested_count") or sanitized.valid_count)
    result.quality = {
        **result.quality,
        "valid_count": sanitized.valid_count,
        "unique_count": sanitized.valid_count,
        "invalid_count": sanitized.invalid_count,
        "duplicate_count": sanitized.duplicate_count,
        "validation_method": "RDKit",
    }
    if not sanitized.candidates:
        result.success = False
        result.status = ObservationStatus.FAILED
        result.data = None
        result.formatted = ""
        result.message = "Generated output contained no valid unique SMILES"
        result.error = AgentExecutionError(
            code=AgentErrorCode.INVALID_OUTPUT,
            message=result.message,
            details=result.quality,
        )
        return result
    result.data = sanitized.candidates
    if sanitized.valid_count < requested:
        result.status = ObservationStatus.PARTIAL
        result.warnings.append(
            f"Generated {sanitized.valid_count} valid unique SMILES out of {requested} requested"
        )
```

Add these exports to `src/agent/validators/__init__.py`:

```python
from .molecule_candidates import (
    CandidateSanitization,
    CandidateValidationUnavailable,
    sanitize_generated_candidates,
)

__all__.extend(
    [
        "CandidateSanitization",
        "CandidateValidationUnavailable",
        "sanitize_generated_candidates",
    ]
)
```

- [ ] **Step 5: Verify GREEN**

Run the Task 6 command. Expected: all tests pass and RDKit emits no accepted invalid candidate.

- [ ] **Step 6: Commit Task 6**

```powershell
git add -- src/agent/validators/molecule_candidates.py src/agent/validators/result_validator.py src/agent/validators/__init__.py tests/agent/test_generated_candidate_validation.py
git commit -m "fix(agent): reject invalid generated molecules"
```

## Task 7: Build an evidence ledger and claim-safe renderer

**Files:**
- Create: `src/agent/evidence/ledger.py`
- Create: `src/agent/evidence/renderer.py`
- Create: `src/agent/evidence/__init__.py`
- Test: `tests/agent/test_evidence_ledger.py`

- [ ] **Step 1: Write failing evidence tests**

Create `tests/agent/test_evidence_ledger.py`:

```python
import pytest

from src.agent.contracts import ScientificClaim, ToolProvenance, ToolResult
from src.agent.evidence import (
    EvidenceLedger,
    UnsupportedClaimError,
    render_claim_template,
)


def test_ledger_registers_tool_execution_with_provenance():
    ledger = EvidenceLedger(trace_id="trace-evidence")
    evidence_id = ledger.register_tool_result(
        step_id="properties",
        input_digest="sha-input",
        result=ToolResult.success_result(
            "property_calculator",
            data={"molecular_weight": 180.16},
            provenance=ToolProvenance(
                tool_name="property_calculator",
                tool_version="1",
            ),
        ),
    )

    record = ledger.get(evidence_id)
    assert record["step_id"] == "properties"
    assert record["provenance"]["tool_name"] == "property_calculator"
    assert record["scientific_usable"] is True


def test_demo_or_fallback_result_is_not_scientifically_usable():
    ledger = EvidenceLedger(trace_id="trace-demo")
    evidence_id = ledger.register_tool_result(
        step_id="activity",
        input_digest="sha-input",
        result=ToolResult.success_result(
            "activity_predictor",
            data={"pic50": 7.1},
            provenance=ToolProvenance(
                tool_name="activity_predictor",
                demo_mode=True,
            ),
        ),
    )
    assert ledger.get(evidence_id)["scientific_usable"] is False


def test_ledger_accepts_and_renders_only_evidence_backed_claims():
    ledger = EvidenceLedger(trace_id="trace-claim")
    evidence_id = ledger.register_tool_result(
        step_id="properties",
        input_digest="sha-input",
        result=ToolResult.success_result(
            "property_calculator",
            data={"molecular_weight": 180.16},
            provenance=ToolProvenance(tool_name="property_calculator"),
        ),
    )
    claim = ScientificClaim(
        claim_id="mw-aspirin",
        subject="aspirin",
        metric="molecular_weight",
        value=180.16,
        unit="g/mol",
        evidence_ids=(evidence_id,),
    )
    ledger.accept_claim(claim)
    rendered = render_claim_template(
        "阿司匹林分子量为 {{claim:mw-aspirin}}。",
        ledger.claims(),
    )
    assert rendered == "阿司匹林分子量为 180.16 g/mol。"


def test_ledger_rejects_claim_with_unknown_evidence():
    ledger = EvidenceLedger(trace_id="trace-unknown-evidence")
    claim = ScientificClaim(
        claim_id="unsupported-pic50",
        subject="CCO",
        metric="pic50",
        value=7.1,
        unit=None,
        evidence_ids=("missing-evidence",),
    )
    with pytest.raises(ValueError, match="Unknown evidence"):
        ledger.accept_claim(claim)


def test_renderer_rejects_unknown_claim_id():
    with pytest.raises(UnsupportedClaimError, match="unknown"):
        render_claim_template("结果 {{claim:unknown}}", {})
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_evidence_ledger.py -q -p no:cacheprovider
```

Expected: missing `src.agent.evidence`.

- [ ] **Step 3: Implement EvidenceLedger**

Create `src/agent/evidence/ledger.py`:

```python
from __future__ import annotations

import hashlib
import json
from typing import Any

from src.agent.contracts import ScientificClaim, ToolProvenance, ToolResult


class EvidenceLedger:
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        self._records: dict[str, dict[str, Any]] = {}
        self._claims: dict[str, ScientificClaim] = {}

    def register_tool_result(
        self,
        step_id: str,
        input_digest: str,
        result: ToolResult,
    ) -> str:
        provenance = result.provenance or ToolProvenance(
            tool_name=result.tool_name,
            tool_version=str(result.quality.get("tool_version") or "1"),
            model_name=result.quality.get("model"),
            model_version=result.quality.get("model_version"),
            demo_mode=bool(result.quality.get("demo_mode", False)),
            fallback_used=bool(result.quality.get("fallback_used", False)),
            input_digest=input_digest,
        )
        payload = {
            "trace_id": self.trace_id,
            "step_id": step_id,
            "tool_name": result.tool_name,
            "status": result.status.value if result.status else None,
            "input_digest": input_digest,
            "provenance": provenance.to_dict(),
            "evidence": result.evidence,
            "artifacts": [item.to_dict() for item in result.artifacts],
            "scientific_usable": bool(
                result.success
                and not provenance.demo_mode
                and not provenance.fallback_used
            ),
        }
        digest = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        evidence_id = f"evidence-{digest[:20]}"
        self._records[evidence_id] = {"evidence_id": evidence_id, **payload}
        return evidence_id

    def get(self, evidence_id: str) -> dict[str, Any]:
        return dict(self._records[evidence_id])

    def accept_claim(self, claim: ScientificClaim) -> None:
        missing = [
            evidence_id
            for evidence_id in claim.evidence_ids
            if evidence_id not in self._records
        ]
        if missing:
            raise ValueError(f"Unknown evidence ids: {missing}")
        unusable = [
            evidence_id
            for evidence_id in claim.evidence_ids
            if not self._records[evidence_id]["scientific_usable"]
        ]
        if unusable:
            raise ValueError(f"Scientifically unusable evidence ids: {unusable}")
        self._claims[claim.claim_id] = claim

    def claims(self) -> dict[str, ScientificClaim]:
        return dict(self._claims)

    def to_list(self) -> list[dict[str, Any]]:
        return [dict(self._records[key]) for key in sorted(self._records)]
```

- [ ] **Step 4: Implement the claim renderer**

Create `src/agent/evidence/renderer.py`:

```python
from __future__ import annotations

import re
from collections.abc import Mapping

from src.agent.contracts import ScientificClaim


class UnsupportedClaimError(ValueError):
    pass


CLAIM_PATTERN = re.compile(r"\{\{claim:([A-Za-z0-9_-]+)\}\}")


def render_claim_template(
    template: str,
    claims: Mapping[str, ScientificClaim],
) -> str:
    def replace(match: re.Match[str]) -> str:
        claim_id = match.group(1)
        if claim_id not in claims:
            raise UnsupportedClaimError(f"Unknown claim id: {claim_id}")
        claim = claims[claim_id]
        suffix = f" {claim.unit}" if claim.unit else ""
        return f"{claim.value}{suffix}"

    rendered = CLAIM_PATTERN.sub(replace, template)
    if "{{claim:" in rendered:
        raise UnsupportedClaimError("Malformed claim placeholder")
    return rendered
```

Create `src/agent/evidence/__init__.py`:

```python
from .ledger import EvidenceLedger
from .renderer import (
    UnsupportedClaimError,
    render_claim_template,
)

__all__ = [
    "EvidenceLedger",
    "UnsupportedClaimError",
    "render_claim_template",
]
```

- [ ] **Step 5: Verify GREEN**

Run the Task 7 command. Expected: `5 passed`.

- [ ] **Step 6: Commit Task 7**

```powershell
git add -- src/agent/evidence tests/agent/test_evidence_ledger.py
git commit -m "feat(agent): add evidence ledger and claim renderer"
```

## Task 8: Integrate evidence and terminal outcomes into the orchestrator

**Files:**
- Modify: `src/agent/orchestrators/workflow.py`
- Modify: `src/agent/runtime/task_state.py`
- Modify: `src/agent/contracts/result.py`
- Test: `tests/agent/test_scientific_trust_runtime.py`
- Test: `tests/agent/test_agent_event_stream.py`
- Test: `tests/agent/test_workflow_orchestrator.py`

- [ ] **Step 1: Write failing runtime trust tests**

Create `tests/agent/test_scientific_trust_runtime.py`:

```python
from src.agent.contracts import AgentContext, ObservationStatus, ToolResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep


class PartialGenerator:
    name = "llm_molecular_generator"

    def execute(self, query):
        return ToolResult.success_result(
            self.name,
            data=[{"smiles": "CCO"}],
            message="generated one",
            quality={"requested_count": 2, "model": "gmm-llama:latest"},
            status=ObservationStatus.PARTIAL,
        )


def test_partial_tool_result_produces_partial_terminal_event_and_evidence():
    orchestrator = WorkflowOrchestrator()
    result = orchestrator.run(
        context=AgentContext(
            query="generate two molecules",
            trace_id="partial-trust",
            active_skill="molecular_design",
        ),
        steps=[
            WorkflowStep(
                "generate",
                "llm_molecular_generator",
                output_key="molecules",
            )
        ],
        tools={"llm_molecular_generator": PartialGenerator()},
    )

    assert result.to_legacy_dict()["status"] == "partial"
    assert result.metadata["evidence_ledger"][0]["step_id"] == "generate"
    assert result.metadata["evidence_ledger"][0]["provenance"]["model_name"] == "gmm-llama:latest"


def test_failed_required_tool_never_reports_completed_outcome():
    class FailedTool:
        name = "property_calculator"

        def execute(self, query):
            return {"success": False, "message": "invalid smiles"}

    result = WorkflowOrchestrator().run(
        context=AgentContext(query="CC(C)((", trace_id="invalid-run"),
        steps=[WorkflowStep("properties", "property_calculator")],
        tools={"property_calculator": FailedTool()},
    )
    assert result.to_legacy_dict()["status"] == "failed"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_scientific_trust_runtime.py tests\agent\test_agent_event_stream.py tests\agent\test_workflow_orchestrator.py -q -p no:cacheprovider
```

Expected: missing evidence ledger metadata and partial terminal distinction.

- [ ] **Step 3: Register every observation in the run ledger**

In `WorkflowOrchestrator.run()`, initialize:

```python
from src.agent.evidence import EvidenceLedger

ledger = EvidenceLedger(context.trace_id)
```

Immediately after result validation, register the result using the already-computed `input_hash`:

```python
evidence_id = ledger.register_tool_result(
    step_id=step.name,
    input_digest=input_hash,
    result=result,
)
result.quality = {
    **result.quality,
    "evidence_id": evidence_id,
}
```

Add the serialized ledger to `AgentResult.metadata`:

```python
"evidence_ledger": ledger.to_list(),
"claims": [
    claim.to_dict()
    for claim in ledger.claims().values()
],
```

- [ ] **Step 4: Add a partial terminal event**

Add to `TaskEventType`:

```python
TASK_PARTIAL = "task_partial"
```

Update `AgentTaskState.add_event()`:

```python
elif event == TaskEventType.TASK_PARTIAL:
    self.status = "partial"
    self.progress = 1.0
```

Select terminal events in `WorkflowOrchestrator` with:

```python
completion_event = (
    TaskEventType.TASK_COMPLETED
    if agent_result.success
    else TaskEventType.TASK_PARTIAL
    if agent_result.partial
    else TaskEventType.TASK_FAILED
)
```

The completion payload remains `agent_result.to_legacy_dict()` so old consumers retain final content while new consumers receive an unambiguous status.

- [ ] **Step 5: Verify GREEN**

Run the Task 8 command. Expected: all runtime and event tests pass.

- [ ] **Step 6: Commit Task 8**

```powershell
git add -- src/agent/orchestrators/workflow.py src/agent/runtime/task_state.py src/agent/contracts/result.py tests/agent/test_scientific_trust_runtime.py tests/agent/test_agent_event_stream.py tests/agent/test_workflow_orchestrator.py
git commit -m "feat(agent): persist evidence-backed run outcomes"
```

## Task 9: Add deterministic acceptance truth gates

**Files:**
- Modify: `src/agent/evaluation/scientific.py`
- Modify: `tests/agent/test_real_acceptance_checks.py`
- Modify: `tests/agent/test_evaluation_runner.py`

- [ ] **Step 1: Write failing truth-gate tests**

Append to `tests/agent/test_real_acceptance_checks.py`:

```python
from src.agent.evaluation.scientific import (
    check_data_flow_evidence,
    check_generated_candidate_truth,
    check_scientific_claim_evidence,
)


def test_generation_truth_check_rejects_invalid_or_duplicate_candidates():
    check = check_generated_candidate_truth(
        {
            "tool_results": [
                {
                    "tool_name": "llm_molecular_generator",
                    "success": True,
                    "data": [{"smiles": "CCO"}, {"smiles": "OCC"}],
                    "quality": {
                        "requested_count": 2,
                        "valid_count": 2,
                        "unique_count": 1,
                        "validation_method": "RDKit",
                    },
                }
            ]
        }
    )
    assert check["passed"] is False
    assert check["reason"] == "generated_candidates_not_unique"


def test_claim_truth_check_rejects_claim_without_evidence():
    check = check_scientific_claim_evidence(
        {"claims": [{"claim_id": "pic50", "value": 7.2, "evidence_ids": []}]}
    )
    assert check == {"passed": False, "reason": "claim_without_evidence"}


def test_data_flow_check_requires_bound_generation_output():
    check = check_data_flow_evidence(
        {
            "plan": {
                "steps": [
                    {"step_id": "generate", "output_key": "molecules"},
                    {
                        "step_id": "properties",
                        "input_binding": "$.request.query",
                    },
                ]
            }
        }
    )
    assert check["passed"] is False
    assert check["reason"] == "generated_output_not_consumed"
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_real_acceptance_checks.py tests\agent\test_evaluation_runner.py -q -p no:cacheprovider
```

Expected: missing truth-check functions.

- [ ] **Step 3: Implement deterministic checks**

Add to `src/agent/evaluation/scientific.py`:

```python
def check_generated_candidate_truth(result: dict[str, Any]) -> dict[str, Any]:
    entries = result.get("tool_results") or []
    generator = next(
        (
            item
            for item in entries
            if item.get("tool_name") == "llm_molecular_generator"
        ),
        None,
    )
    if not generator:
        return {"passed": False, "reason": "generator_result_missing"}
    quality = generator.get("quality") or {}
    if quality.get("validation_method") != "RDKit":
        return {"passed": False, "reason": "rdkit_validation_missing"}
    if int(quality.get("unique_count") or 0) < int(quality.get("valid_count") or 0):
        return {"passed": False, "reason": "generated_candidates_not_unique"}
    return {"passed": True, "reason": "validated_unique_candidates"}


def check_scientific_claim_evidence(result: dict[str, Any]) -> dict[str, Any]:
    for claim in result.get("claims") or []:
        if not claim.get("evidence_ids"):
            return {"passed": False, "reason": "claim_without_evidence"}
    return {"passed": True, "reason": "all_claims_have_evidence"}


def check_data_flow_evidence(result: dict[str, Any]) -> dict[str, Any]:
    steps = ((result.get("plan") or {}).get("steps") or [])
    generation_outputs = {
        step.get("output_key")
        for step in steps
        if step.get("output_key") and step.get("step_id") in {"generate", "molecule_generation"}
    }
    if not generation_outputs:
        return {"passed": True, "reason": "generation_not_requested"}
    bindings = {step.get("input_binding") for step in steps}
    if not any(f"$.outputs.{key}" in bindings for key in generation_outputs):
        return {"passed": False, "reason": "generated_output_not_consumed"}
    return {"passed": True, "reason": "generated_output_consumed"}
```

In `_run_case()`, immediately after the existing `_truth_checks()` call, add the automatic gates below. They supplement rather than replace the current RDKit, RG-MPNN, Vina, RAG, target-evidence, anti-hallucination, and provenance checks:

```python
def as_truth_status(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "passed" if check.get("passed") else "failed",
        "reason": check.get("reason", "automatic_truth_check"),
    }


serialized_tool_results = [
    {"tool_name": item.tool_name, **item.to_legacy_dict()}
    for item in tool_results
]
if any(
    item.tool_name == "llm_molecular_generator"
    for item in tool_results
):
    truth_checks["generated_candidate_contract"] = as_truth_status(
        check_generated_candidate_truth(
            {"tool_results": serialized_tool_results}
        )
    )

claims = (
    primary_execution.result.metadata.get("claims", [])
    if primary_execution
    else []
)
truth_checks["scientific_claim_evidence"] = as_truth_status(
    check_scientific_claim_evidence({"claims": claims})
)

if primary_execution and any(
    step.name in {"generate", "molecule_generation"}
    for step in primary_execution.plan.steps
):
    truth_checks["compiled_data_flow"] = as_truth_status(
        check_data_flow_evidence(
            {
                "plan": {
                    "steps": [
                        {
                            "step_id": step.name,
                            "output_key": step.output_key,
                            "input_binding": step.input_binding,
                        }
                        for step in primary_execution.plan.steps
                    ]
                }
            }
        )
    )
```

- [ ] **Step 4: Verify GREEN**

Run the Task 9 command. Expected: all evaluation tests pass.

- [ ] **Step 5: Commit Task 9**

```powershell
git add -- src/agent/evaluation/scientific.py tests/agent/test_real_acceptance_checks.py tests/agent/test_evaluation_runner.py
git commit -m "test(agent): gate releases on scientific evidence"
```

## Task 10: Run the foundation release gate and document the result

**Files:**
- Modify: `docs/handoff/latest.md`
- Modify: `docs/PROJECT_STANDARDS.md`
- Verify: all files changed by Tasks 1–9

- [ ] **Step 1: Run focused Agent tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
```

Expected: all Agent tests pass. Any real dependency skip must show its explicit reason.

- [ ] **Step 2: Run anti-hallucination and platform-health tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py tests\agent\test_real_acceptance_checks.py -q -p no:cacheprovider
```

Expected: all available tests pass; dependency-unavailable cases remain failed/partial/skipped rather than successful.

- [ ] **Step 3: Run compilation checks**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
```

Expected: exit code 0 and no output.

- [ ] **Step 4: Run contract acceptance**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract
```

Expected: overall status `passed`; no hard anti-hallucination failure.

- [ ] **Step 5: Run replay acceptance**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode replay --replay-input outputs\agent_evaluation\agent_acceptance_report.json
```

Expected: replay performs no real tool/model call and reproduces truth-check decisions from the input report. If no report exists, run contract first and use its generated report path.

- [ ] **Step 6: Run controlled real acceptance when dependencies are available**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode real --case-set golden --repeat 1
```

Expected: RDKit-backed cases can pass; missing Ollama, RG-MPNN, target data, Vina, receptor preparation, or external model must be reported as partial/failed/skipped. No unavailable dependency may be converted to success.

- [ ] **Step 7: Update engineering standards**

Add this exact rule to the Agent section of `docs/PROJECT_STANDARDS.md`:

```markdown
- Agent plans must compile before execution. Multi-step plans must bind downstream inputs to versioned upstream outputs. Scientific claims require accepted evidence, and generated molecular candidates must pass RDKit validation and canonical deduplication before they are returned to downstream tools or the frontend.
```

- [ ] **Step 8: Update the handoff**

Record the branch, commits, changed files, exact command results, real dependency state, failed/partial/skipped reasons, and the next plan name `industrial-agent-langgraph-harness` in `docs/handoff/latest.md`. Do not copy API keys, environment values, absolute secret-store paths, raw user prompts, or local model files.

- [ ] **Step 9: Commit documentation and verification record**

```powershell
git add -- docs/PROJECT_STANDARDS.md docs/handoff/latest.md
git commit -m "docs: record agent trust foundation verification"
```

## Final exit checklist

- [ ] Current branch is not `main`.
- [ ] No unrelated file is staged.
- [ ] Existing target-design and frontend work was not overwritten.
- [ ] All current scientific tool results expose explicit status.
- [ ] Demo/fallback state survives through provenance.
- [ ] Plan compilation rejects unauthorized tools and invalid bindings.
- [ ] Generated candidates are RDKit-valid, canonical, and unique.
- [ ] Downstream property/ADMET/activity steps consume generated outputs.
- [ ] Accepted claims require evidence IDs.
- [ ] Partial scientific work emits a partial terminal status.
- [ ] Completion events carry the persisted final result.
- [ ] Contract and replay acceptance pass.
- [ ] Real dependency failures are reported honestly.
- [ ] No API key, token, database credential, user privacy data, model weight, local database, index, or calculation output is committed.
