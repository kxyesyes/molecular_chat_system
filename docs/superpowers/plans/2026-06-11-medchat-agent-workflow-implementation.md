# MedChat Agent Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a stable workflow-oriented MedChat Agent that can turn natural language drug-design goals into traceable tool execution, structured results, progress events, and reusable reports.

**Architecture:** Keep the existing `src/agent` skeleton and upgrade it incrementally. The implementation strengthens contracts first, then adds task planning and event streaming, then wires comprehensive evaluation and target-driven design workflows into the homepage chat experience.

**Tech Stack:** FastAPI, WebSocket, Python 3.10+, dataclasses/Pydantic-style contracts, RDKit, SQLite, AutoDock Vina adapters, Ollama/ModelScope-compatible LLMs, pytest, vanilla JS.

---

## File Structure

### New Files

- `src/agent/contracts/domain.py`  
  Defines structured domain objects: `MoleculeCandidate`, `TargetCandidate`, `StructureCandidate`, `DockingCandidate`, and `WorkflowArtifact`.

- `src/agent/planning/__init__.py`  
  Exports planner interfaces.

- `src/agent/planning/task_planner.py`  
  Converts a routed skill and user query into executable workflow steps.

- `src/agent/runtime/event_bus.py`  
  Lightweight event collector/emitter for Agent task events.

- `src/agent/validators/__init__.py`  
  Exports validation helpers.

- `src/agent/validators/result_validator.py`  
  Validates SMILES, tool outputs, artifacts, and partial workflow results.

- `tests/agent/test_domain_contracts.py`  
  Verifies domain objects serialize cleanly and remain frontend-safe.

- `tests/agent/test_task_planner.py`  
  Verifies user tasks map to expected workflow plans.

- `tests/agent/test_agent_event_stream.py`  
  Verifies workflow execution emits structured task events.

- `tests/agent/test_comprehensive_workflow.py`  
  Verifies comprehensive molecular evaluation executes expected tools in order.

- `tests/agent/test_target_driven_design_workflow.py`  
  Verifies PDE5-style target-driven design planning and partial execution behavior.

### Modified Files

- `src/agent/contracts/result.py`  
  Add `warnings`, `evidence`, `artifacts`, and `quality` fields to `ToolResult` and `AgentResult`.

- `src/agent/contracts/context.py`  
  Add session, model, and workflow metadata fields.

- `src/agent/runtime/task_state.py`  
  Add planning events and validation warning events.

- `src/agent/orchestrators/workflow.py`  
  Add event emission, timeouts, partial results, artifacts, and validation hooks.

- `src/agent/orchestrators/base.py`  
  Extend `WorkflowStep` with timeout, required flag, and output key metadata.

- `src/agent/react_agent.py`  
  Route workflow skills through planner + orchestrator before falling back to legacy ReAct.

- `src/web/chat_handler.py`  
  Stream structured `agent_event` messages to the frontend.

- `src/web/templates/index.html`  
  Add a compact Agent task panel container below the chat welcome area.

- `src/web/static/js/home/main.js`  
  Render structured Agent events, step cards, partial results, and workflow completion summaries.

- `scripts/health_check.py`  
  Add Agent contract, planner, and tool registration checks.

---

## Task 1: Extend Agent Result Contracts

**Files:**
- Create: `src/agent/contracts/domain.py`
- Modify: `src/agent/contracts/result.py`
- Modify: `src/agent/contracts/context.py`
- Modify: `src/agent/contracts/__init__.py`
- Test: `tests/agent/test_domain_contracts.py`

- [ ] **Step 1: Write failing contract tests**

Create `tests/agent/test_domain_contracts.py`:

```python
from src.agent.contracts import AgentContext, ToolResult
from src.agent.contracts.domain import MoleculeCandidate, WorkflowArtifact


def test_molecule_candidate_serializes_for_frontend():
    candidate = MoleculeCandidate(
        smiles="CCO",
        source="generated",
        properties={"qed": 0.42},
        warnings=["demo warning"],
    )

    payload = candidate.to_dict()

    assert payload["smiles"] == "CCO"
    assert payload["source"] == "generated"
    assert payload["properties"]["qed"] == 0.42
    assert payload["warnings"] == ["demo warning"]


def test_tool_result_preserves_warnings_evidence_and_artifacts():
    artifact = WorkflowArtifact(
        artifact_type="csv",
        path="outputs/agent/demo.csv",
        label="候选分子表",
    )

    result = ToolResult.success_result(
        tool_name="molecular_design",
        data={"count": 1},
        message="生成完成",
        warnings=["1 个候选分子被剔除"],
        evidence=[{"source": "rdkit", "message": "SMILES valid"}],
        artifacts=[artifact],
        quality={"confidence": 0.8, "validated": True},
    )

    legacy = result.to_legacy_dict()

    assert legacy["warnings"] == ["1 个候选分子被剔除"]
    assert legacy["evidence"][0]["source"] == "rdkit"
    assert legacy["artifacts"][0]["path"] == "outputs/agent/demo.csv"
    assert legacy["quality"]["validated"] is True


def test_agent_context_carries_session_and_workflow_metadata():
    context = AgentContext(
        query="针对 PDE5 设计候选分子",
        trace_id="trace-1",
        session_id="session-1",
        workflow_name="target_driven_design",
        metadata={"target": "PDE5"},
    )

    assert context.session_id == "session-1"
    assert context.workflow_name == "target_driven_design"
    assert context.metadata["target"] == "PDE5"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/agent/test_domain_contracts.py -q
```

Expected: FAIL because `src.agent.contracts.domain` and extended fields do not exist yet.

- [ ] **Step 3: Implement domain contracts**

Create `src/agent/contracts/domain.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowArtifact:
    artifact_type: str
    path: str
    label: str
    mime_type: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_type": self.artifact_type,
            "path": self.path,
            "label": self.label,
            "mime_type": self.mime_type,
            "metadata": self.metadata,
        }


@dataclass
class MoleculeCandidate:
    smiles: str
    source: str
    name: str | None = None
    rank: int | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    admet: dict[str, Any] = field(default_factory=dict)
    activity: dict[str, Any] = field(default_factory=dict)
    docking: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "smiles": self.smiles,
            "source": self.source,
            "name": self.name,
            "rank": self.rank,
            "properties": self.properties,
            "admet": self.admet,
            "activity": self.activity,
            "docking": self.docking,
            "warnings": self.warnings,
            "evidence": self.evidence,
        }


@dataclass
class TargetCandidate:
    gene_symbol: str
    protein_name: str | None = None
    uniprot_id: str | None = None
    organism: str | None = None
    confidence: float | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gene_symbol": self.gene_symbol,
            "protein_name": self.protein_name,
            "uniprot_id": self.uniprot_id,
            "organism": self.organism,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


@dataclass
class StructureCandidate:
    structure_id: str
    source: str
    local_file_path: str | None = None
    score: float | None = None
    docking_recommended: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "structure_id": self.structure_id,
            "source": self.source,
            "local_file_path": self.local_file_path,
            "score": self.score,
            "docking_recommended": self.docking_recommended,
            "metadata": self.metadata,
        }


@dataclass
class DockingCandidate:
    ligand_smiles: str
    target: str
    score: float | None = None
    receptor_file: str | None = None
    ligand_file: str | None = None
    pose_file: str | None = None
    interactions: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ligand_smiles": self.ligand_smiles,
            "target": self.target,
            "score": self.score,
            "receptor_file": self.receptor_file,
            "ligand_file": self.ligand_file,
            "pose_file": self.pose_file,
            "interactions": self.interactions,
            "warnings": self.warnings,
        }
```

- [ ] **Step 4: Extend `ToolResult` and `AgentResult`**

Modify `src/agent/contracts/result.py`:

```python
from .domain import WorkflowArtifact
```

Add fields to `ToolResult`:

```python
warnings: list[str] = field(default_factory=list)
evidence: list[dict[str, Any]] = field(default_factory=list)
artifacts: list[WorkflowArtifact] = field(default_factory=list)
quality: dict[str, Any] = field(default_factory=dict)
```

Update `success_result()` signature:

```python
warnings: list[str] | None = None,
evidence: list[dict[str, Any]] | None = None,
artifacts: list[WorkflowArtifact] | None = None,
quality: dict[str, Any] | None = None,
```

Set those fields in the returned object:

```python
warnings=warnings or [],
evidence=evidence or [],
artifacts=artifacts or [],
quality=quality or {},
```

Update `to_legacy_dict()`:

```python
"warnings": self.warnings,
"evidence": self.evidence,
"artifacts": [artifact.to_dict() for artifact in self.artifacts],
"quality": self.quality,
```

Add matching aggregate fields to `AgentResult`:

```python
warnings: list[str] = field(default_factory=list)
evidence: list[dict[str, Any]] = field(default_factory=list)
artifacts: list[WorkflowArtifact] = field(default_factory=list)
```

In `from_tool_results()`, aggregate:

```python
warnings = [warning for item in tool_results for warning in item.warnings]
evidence = [entry for item in tool_results for entry in item.evidence]
artifacts = [artifact for item in tool_results for artifact in item.artifacts]
```

Return those fields on `AgentResult`.

- [ ] **Step 5: Extend `AgentContext`**

Modify `src/agent/contracts/context.py`:

```python
session_id: str | None = None
workflow_name: str | None = None
model_name: str | None = None
stream: bool = True
```

Update `with_skill()` to preserve the new fields.

- [ ] **Step 6: Export domain objects**

Modify `src/agent/contracts/__init__.py`:

```python
from .domain import DockingCandidate, MoleculeCandidate, StructureCandidate, TargetCandidate, WorkflowArtifact
```

- [ ] **Step 7: Run tests**

Run:

```bash
python -m pytest tests/agent/test_domain_contracts.py tests/agent/test_contracts.py -q
```

Expected: PASS.

---

## Task 2: Add Workflow Planning Layer

**Files:**
- Create: `src/agent/planning/__init__.py`
- Create: `src/agent/planning/task_planner.py`
- Modify: `src/agent/orchestrators/base.py`
- Test: `tests/agent/test_task_planner.py`

- [ ] **Step 1: Write failing planner tests**

Create `tests/agent/test_task_planner.py`:

```python
from src.agent.contracts import AgentContext
from src.agent.planning.task_planner import TaskPlanner


def test_comprehensive_evaluation_plan_for_smiles_query():
    planner = TaskPlanner()
    context = AgentContext(
        query="全面分析 CCO",
        trace_id="trace-1",
        active_skill="comprehensive_evaluation",
    )

    plan = planner.plan(context)

    assert plan.workflow_name == "comprehensive_evaluation"
    assert [step.tool_name for step in plan.steps] == [
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ]


def test_target_driven_design_plan_for_pde5_query():
    planner = TaskPlanner()
    context = AgentContext(
        query="针对 PDE5 设计 20 个类药候选分子，并筛选适合 docking 的前 5 个",
        trace_id="trace-2",
        active_skill="target_driven_design",
    )

    plan = planner.plan(context)

    assert plan.workflow_name == "target_driven_design"
    assert [step.tool_name for step in plan.steps] == [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "molecular_docking",
    ]
    assert plan.metadata["target_hint"] == "PDE5"
    assert plan.metadata["requested_count"] == 20
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/agent/test_task_planner.py -q
```

Expected: FAIL because planning layer does not exist.

- [ ] **Step 3: Extend `WorkflowStep`**

Modify `src/agent/orchestrators/base.py` so `WorkflowStep` includes:

```python
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowStep:
    name: str
    tool_name: str
    input_data: Any = None
    continue_on_error: bool | None = None
    required: bool = True
    timeout_seconds: float | None = None
    output_key: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

- [ ] **Step 4: Implement planner**

Create `src/agent/planning/task_planner.py`:

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from src.agent.contracts import AgentContext
from src.agent.orchestrators.base import WorkflowStep


@dataclass
class WorkflowPlan:
    workflow_name: str
    steps: list[WorkflowStep]
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskPlanner:
    """Convert routed skills and user text into deterministic workflow plans."""

    def plan(self, context: AgentContext) -> WorkflowPlan:
        skill = context.active_skill or ""
        query = context.query

        if skill in {"comprehensive_evaluation", "comprehensive_evaluation_skill"}:
            return self._comprehensive_plan(query)

        if skill in {"target_driven_design", "target_database_search"} and self._looks_like_design(query):
            return self._target_driven_design_plan(query)

        if skill == "hit_to_lead_optimization":
            return self._lead_optimization_plan(query)

        return WorkflowPlan(
            workflow_name=skill or "single_step",
            steps=[],
            metadata={"reason": "no deterministic workflow matched"},
        )

    def _comprehensive_plan(self, query: str) -> WorkflowPlan:
        return WorkflowPlan(
            workflow_name="comprehensive_evaluation",
            steps=[
                WorkflowStep("properties", "property_calculator", query, output_key="properties"),
                WorkflowStep("admet", "admet_predictor", query, output_key="admet", continue_on_error=True),
                WorkflowStep("activity", "activity_predictor", query, output_key="activity", continue_on_error=True),
                WorkflowStep("reverse_target", "reverse_target_predictor", query, output_key="targets", continue_on_error=True),
                WorkflowStep("target_structures", "target_database_search", query, output_key="structures", continue_on_error=True),
            ],
            metadata={"input_type": "molecule"},
        )

    def _target_driven_design_plan(self, query: str) -> WorkflowPlan:
        target_hint = self._extract_target_hint(query)
        requested_count = self._extract_requested_count(query, default=20)
        return WorkflowPlan(
            workflow_name="target_driven_design",
            steps=[
                WorkflowStep("target_search", "target_database_search", target_hint, output_key="target"),
                WorkflowStep("molecule_generation", "llm_molecular_generator", query, output_key="molecules"),
                WorkflowStep("properties", "property_calculator", query, output_key="properties"),
                WorkflowStep("admet", "admet_predictor", query, output_key="admet", continue_on_error=True),
                WorkflowStep("activity", "activity_predictor", query, output_key="activity", continue_on_error=True),
                WorkflowStep("docking", "molecular_docking", query, output_key="docking", continue_on_error=True),
            ],
            metadata={"target_hint": target_hint, "requested_count": requested_count},
        )

    def _lead_optimization_plan(self, query: str) -> WorkflowPlan:
        return WorkflowPlan(
            workflow_name="hit_to_lead_optimization",
            steps=[
                WorkflowStep("baseline_properties", "property_calculator", query, output_key="baseline"),
                WorkflowStep("molecule_generation", "llm_molecular_generator", query, output_key="candidates"),
                WorkflowStep("candidate_admet", "admet_predictor", query, output_key="admet", continue_on_error=True),
                WorkflowStep("candidate_activity", "activity_predictor", query, output_key="activity", continue_on_error=True),
            ],
            metadata={"input_type": "lead_molecule"},
        )

    @staticmethod
    def _looks_like_design(query: str) -> bool:
        return any(token in query.lower() for token in ["设计", "生成", "候选", "design", "generate"])

    @staticmethod
    def _extract_target_hint(query: str) -> str:
        match = re.search(r"(PDE\d+[A-Z]?|EGFR|KRAS|BRAF|JAK2|ALK|MET|CDK2|KDR)", query, re.I)
        return match.group(1).upper() if match else query.strip()

    @staticmethod
    def _extract_requested_count(query: str, default: int) -> int:
        digit_match = re.search(r"(\d+)\s*个", query)
        if digit_match:
            return max(1, min(100, int(digit_match.group(1))))
        chinese_numbers = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "十": 10, "二十": 20}
        for text, value in chinese_numbers.items():
            if f"{text}个" in query:
                return value
        return default
```

Create `src/agent/planning/__init__.py`:

```python
from .task_planner import TaskPlanner, WorkflowPlan

__all__ = ["TaskPlanner", "WorkflowPlan"]
```

- [ ] **Step 5: Run tests**

Run:

```bash
python -m pytest tests/agent/test_task_planner.py tests/agent/test_workflow_orchestrator.py -q
```

Expected: PASS.

---

## Task 3: Add Structured Agent Event Streaming

**Files:**
- Create: `src/agent/runtime/event_bus.py`
- Modify: `src/agent/runtime/task_state.py`
- Modify: `src/agent/orchestrators/workflow.py`
- Test: `tests/agent/test_agent_event_stream.py`

- [ ] **Step 1: Write failing event tests**

Create `tests/agent/test_agent_event_stream.py`:

```python
from src.agent.contracts import AgentContext, ToolResult
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.runtime.event_bus import AgentEventBus


class FakeTool:
    name = "fake_tool"

    def execute(self, query):
        return {"success": True, "message": "ok", "data": {"query": query}, "formatted": "OK"}


def test_workflow_emits_structured_events():
    event_bus = AgentEventBus()
    orchestrator = WorkflowOrchestrator(event_bus=event_bus)
    context = AgentContext(query="CCO", trace_id="trace-events", active_skill="demo")

    result = orchestrator.run(
        context=context,
        steps=[WorkflowStep(name="fake", tool_name="fake_tool", input_data="CCO")],
        tools={"fake_tool": FakeTool()},
    )

    events = [event.to_dict() for event in event_bus.events]

    assert result.success is True
    assert events[0]["event"] == "task_started"
    assert any(event["event"] == "tool_started" for event in events)
    assert any(event["event"] == "tool_completed" for event in events)
    assert events[-1]["event"] == "task_completed"
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/agent/test_agent_event_stream.py -q
```

Expected: FAIL because `AgentEventBus` does not exist and orchestrator does not emit events.

- [ ] **Step 3: Extend task event types**

Modify `src/agent/runtime/task_state.py`:

```python
PLANNING_STARTED = "planning_started"
PLANNING_COMPLETED = "planning_completed"
VALIDATION_WARNING = "validation_warning"
```

Add those members to `TaskEventType`.

- [ ] **Step 4: Implement event bus**

Create `src/agent/runtime/event_bus.py`:

```python
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.agent.runtime.task_state import TaskEvent, TaskEventType


class AgentEventBus:
    """Collects task events and optionally forwards them to a callback."""

    def __init__(self, on_event: Callable[[TaskEvent], None] | None = None):
        self.events: list[TaskEvent] = []
        self.on_event = on_event

    def emit(
        self,
        trace_id: str,
        event: TaskEventType,
        message: str,
        skill: str | None = None,
        tool: str | None = None,
        progress: float | None = None,
        payload: Any = None,
    ) -> TaskEvent:
        task_event = TaskEvent(
            trace_id=trace_id,
            event=event,
            message=message,
            skill=skill,
            tool=tool,
            progress=progress,
            payload=payload,
        )
        self.events.append(task_event)
        if self.on_event:
            self.on_event(task_event)
        return task_event
```

- [ ] **Step 5: Update WorkflowOrchestrator**

Modify `src/agent/orchestrators/workflow.py`:

```python
from src.agent.runtime.event_bus import AgentEventBus
from src.agent.runtime.task_state import TaskEventType
```

Add constructor:

```python
def __init__(self, event_bus: AgentEventBus | None = None):
    self.event_bus = event_bus
```

At the beginning of `run()`:

```python
self._emit(context, TaskEventType.TASK_STARTED, "Agent 工作流开始", progress=0.0)
```

Before each tool:

```python
self._emit(
    context,
    TaskEventType.TOOL_STARTED,
    f"正在执行 {step.name}",
    tool=step.tool_name,
    progress=index / total,
)
```

After success:

```python
self._emit(
    context,
    TaskEventType.TOOL_COMPLETED,
    result.message or f"{step.name} 完成",
    tool=step.tool_name,
    progress=(index + 1) / total,
    payload=result.to_legacy_dict(),
)
```

After failure:

```python
self._emit(
    context,
    TaskEventType.TOOL_FAILED,
    result.message or f"{step.name} 失败",
    tool=step.tool_name,
    progress=(index + 1) / total,
    payload=result.to_legacy_dict(),
)
```

At completion:

```python
completion_event = TaskEventType.TASK_COMPLETED if agent_result.success or agent_result.partial else TaskEventType.TASK_FAILED
self._emit(context, completion_event, message, progress=1.0, payload=agent_result.to_legacy_dict())
```

Add helper:

```python
def _emit(self, context, event, message, tool=None, progress=None, payload=None):
    if self.event_bus:
        self.event_bus.emit(
            trace_id=context.trace_id,
            event=event,
            message=message,
            skill=context.active_skill,
            tool=tool,
            progress=progress,
            payload=payload,
        )
```

- [ ] **Step 6: Run tests**

Run:

```bash
python -m pytest tests/agent/test_agent_event_stream.py tests/agent/test_workflow_orchestrator.py -q
```

Expected: PASS.

---

## Task 4: Add Result Validation

**Files:**
- Create: `src/agent/validators/__init__.py`
- Create: `src/agent/validators/result_validator.py`
- Modify: `src/agent/orchestrators/workflow.py`
- Test: `tests/agent/test_result_validator.py`

- [ ] **Step 1: Write failing validator tests**

Create `tests/agent/test_result_validator.py`:

```python
from src.agent.contracts import ToolResult
from src.agent.validators.result_validator import AgentResultValidator


def test_validator_marks_invalid_smiles_candidate():
    validator = AgentResultValidator()
    result = ToolResult.success_result(
        tool_name="llm_molecular_generator",
        data={"molecules": [{"smiles": "not-a-smiles"}]},
        message="生成完成",
    )

    validated = validator.validate_tool_result(result)

    assert validated.success is True
    assert any("非法 SMILES" in warning for warning in validated.warnings)


def test_validator_warns_missing_artifact_path():
    validator = AgentResultValidator()
    result = ToolResult.success_result(
        tool_name="molecular_docking",
        data={"pose_file": "outputs/missing_pose.pdbqt"},
        message="对接完成",
    )

    validated = validator.validate_tool_result(result)

    assert any("文件不存在" in warning for warning in validated.warnings)
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
python -m pytest tests/agent/test_result_validator.py -q
```

Expected: FAIL because validator does not exist.

- [ ] **Step 3: Implement validator**

Create `src/agent/validators/result_validator.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.agent.contracts import ToolResult


class AgentResultValidator:
    """Validates common agent tool outputs without blocking partial success."""

    def validate_tool_result(self, result: ToolResult) -> ToolResult:
        if not result.success:
            return result

        warnings = list(result.warnings)
        warnings.extend(self._validate_smiles_payload(result.data))
        warnings.extend(self._validate_file_payload(result.data))
        result.warnings = warnings
        if warnings:
            result.quality = {**result.quality, "validated": False}
        else:
            result.quality = {**result.quality, "validated": True}
        return result

    def _validate_smiles_payload(self, data: Any) -> list[str]:
        warnings: list[str] = []
        smiles_values = self._collect_smiles(data)
        if not smiles_values:
            return warnings

        try:
            from rdkit import Chem
        except Exception:
            return ["RDKit 不可用，无法校验 SMILES"]

        for smiles in smiles_values:
            if not smiles or Chem.MolFromSmiles(str(smiles)) is None:
                warnings.append(f"非法 SMILES 已标记: {smiles}")
        return warnings

    def _validate_file_payload(self, data: Any) -> list[str]:
        warnings: list[str] = []
        if not isinstance(data, dict):
            return warnings

        for key in ["pose_file", "protein_file", "ligand_file", "local_file_path"]:
            value = data.get(key)
            if value and not Path(str(value)).exists():
                warnings.append(f"{key} 文件不存在: {value}")
        return warnings

    def _collect_smiles(self, data: Any) -> list[str]:
        values: list[str] = []
        if isinstance(data, dict):
            if "smiles" in data:
                values.append(str(data["smiles"]))
            for item in data.get("molecules", []) or data.get("candidates", []) or []:
                if isinstance(item, dict) and "smiles" in item:
                    values.append(str(item["smiles"]))
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "smiles" in item:
                    values.append(str(item["smiles"]))
        return values
```

Create `src/agent/validators/__init__.py`:

```python
from .result_validator import AgentResultValidator

__all__ = ["AgentResultValidator"]
```

- [ ] **Step 4: Hook validator into orchestrator**

Modify `src/agent/orchestrators/workflow.py`:

```python
from src.agent.validators import AgentResultValidator
```

Add constructor parameter:

```python
def __init__(self, event_bus=None, validator=None):
    self.event_bus = event_bus
    self.validator = validator or AgentResultValidator()
```

After each `execute_tool_compat()` result:

```python
result = self.validator.validate_tool_result(result)
if result.warnings:
    self._emit(
        context,
        TaskEventType.VALIDATION_WARNING,
        "工具结果存在校验警告",
        tool=step.tool_name,
        payload={"warnings": result.warnings},
    )
```

- [ ] **Step 5: Run tests**

Run:

```bash
python -m pytest tests/agent/test_result_validator.py tests/agent/test_agent_event_stream.py -q
```

Expected: PASS.

---

## Task 5: Wire Planner Into ReAct Agent

**Files:**
- Modify: `src/agent/react_agent.py`
- Test: `tests/agent/test_react_agent_workflow_routing.py`

- [ ] **Step 1: Write failing routing tests**

Create `tests/agent/test_react_agent_workflow_routing.py`:

```python
from src.agent.react_agent import ReActMolecularAgent


class FakeSkill:
    name = "comprehensive_evaluation"
    workflow_steps = True


def test_workflow_skill_uses_planner_and_returns_legacy_dict(monkeypatch):
    agent = ReActMolecularAgent(llm=None)

    def fake_execute_workflow_skill(query, result):
        result["success"] = True
        result["active_skill"] = "comprehensive_evaluation"
        result["final_answer"] = "workflow ok"
        result["tools_used"] = ["property_calculator"]
        return result

    monkeypatch.setattr(agent, "_execute_workflow_skill", fake_execute_workflow_skill)

    response = agent.execute("全面分析 CCO", active_skill=FakeSkill())

    assert response["success"] is True
    assert response["active_skill"] == "comprehensive_evaluation"
    assert response["final_answer"] == "workflow ok"
```

- [ ] **Step 2: Run test and verify current behavior**

Run:

```bash
python -m pytest tests/agent/test_react_agent_workflow_routing.py -q
```

Expected: PASS if existing workflow routing is compatible. If it fails, fix `_active_skill` handling before proceeding.

- [ ] **Step 3: Replace ad hoc workflow execution with planner**

Modify `_execute_workflow_skill()` in `src/agent/react_agent.py` to:

1. Create `AgentContext`.
2. Call `TaskPlanner.plan(context)`.
3. Use `WorkflowOrchestrator` with available tools.
4. Convert `AgentResult.to_legacy_dict()` back to the existing return format.

Implementation shape:

```python
from uuid import uuid4
from src.agent.contracts import AgentContext
from src.agent.planning import TaskPlanner
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.runtime.event_bus import AgentEventBus
```

Inside `_execute_workflow_skill()`:

```python
context = AgentContext(
    query=query,
    trace_id=f"agent-{uuid4().hex[:12]}",
    active_skill=self._active_skill.name if self._active_skill else None,
    temperature=getattr(self, "current_temperature", 0.7),
    mol_count=getattr(self, "current_mol_count", 5),
)
plan = TaskPlanner().plan(context)
orchestrator = WorkflowOrchestrator(event_bus=AgentEventBus())
agent_result = orchestrator.run(
    context=context,
    steps=plan.steps,
    tools=self.tools,
    continue_on_error=True,
)
legacy = agent_result.to_legacy_dict()
result.update(legacy)
result["tools_used"] = [item.tool_name for item in agent_result.tool_results]
result["agent_events"] = [event.to_dict() for event in orchestrator.event_bus.events]
return result
```

- [ ] **Step 4: Run routing and existing agent tests**

Run:

```bash
python -m pytest tests/agent/test_react_agent_workflow_routing.py tests/agent/test_workflow_skills.py tests/test_llm_molecular_generator.py -q
```

Expected: PASS.

---

## Task 6: Stream Agent Events Through WebSocket

**Files:**
- Modify: `src/web/chat_handler.py`
- Test: `tests/agent/test_chat_handler_agent_events.py`

- [ ] **Step 1: Write event extraction test**

Create `tests/agent/test_chat_handler_agent_events.py`:

```python
from src.web.chat_handler import ChatHandler


def test_agent_events_are_extracted_from_agent_response():
    handler = ChatHandler(model=None, rag_service=None, agent_system=None, config={})
    response = {
        "agent_events": [
            {"type": "agent_event", "event": "task_started", "message": "开始"},
            {"type": "agent_event", "event": "task_completed", "message": "完成"},
        ]
    }

    events = handler._extract_agent_events(response)

    assert len(events) == 2
    assert events[0]["event"] == "task_started"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
python -m pytest tests/agent/test_chat_handler_agent_events.py -q
```

Expected: FAIL because helper does not exist.

- [ ] **Step 3: Add helper to ChatHandler**

Modify `src/web/chat_handler.py`:

```python
def _extract_agent_events(self, agent_response: dict) -> list[dict]:
    events = agent_response.get("agent_events", [])
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]
```

- [ ] **Step 4: Send events after agent execution**

In `_process_message()`, after `agent_result = await agent_task`, before sending `agent_result`, add:

```python
for event in self._extract_agent_events(agent_result):
    await websocket.send_text(json.dumps(event, ensure_ascii=False))
```

Keep the existing `agent_result` message for backward compatibility.

- [ ] **Step 5: Run tests**

Run:

```bash
python -m pytest tests/agent/test_chat_handler_agent_events.py -q
```

Expected: PASS.

---

## Task 7: Add Homepage Agent Task Panel

**Files:**
- Modify: `src/web/templates/index.html`
- Modify: `src/web/static/js/home/main.js`
- Test: `tests/home_agent_task_panel_test.js`

- [ ] **Step 1: Add lightweight DOM test**

Create `tests/home_agent_task_panel_test.js`:

```javascript
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'src', 'web', 'templates', 'index.html'), 'utf8');
const js = fs.readFileSync(path.join(__dirname, '..', 'src', 'web', 'static', 'js', 'home', 'main.js'), 'utf8');

if (!html.includes('id="agentTaskPanel"')) {
  throw new Error('agentTaskPanel container missing');
}

if (!js.includes('handleAgentEvent')) {
  throw new Error('handleAgentEvent function missing');
}

if (!js.includes('agent_event')) {
  throw new Error('agent_event websocket branch missing');
}
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
node tests/home_agent_task_panel_test.js
```

Expected: FAIL because panel and handler do not exist.

- [ ] **Step 3: Add panel markup**

Modify `src/web/templates/index.html` near the homepage chat area:

```html
<section id="agentTaskPanel" class="agent-task-panel" hidden>
  <div class="agent-task-panel__header">
    <div>
      <span class="agent-task-panel__eyebrow">AGENT WORKFLOW</span>
      <h3>任务执行进度</h3>
    </div>
    <span id="agentTaskStatus" class="agent-task-panel__status">准备中</span>
  </div>
  <div id="agentTaskSteps" class="agent-task-steps"></div>
  <div id="agentTaskPartial" class="agent-task-partial"></div>
</section>
```

Add CSS in the same template or existing homepage CSS block:

```css
.agent-task-panel {
  width: min(960px, calc(100vw - 48px));
  margin: 18px auto 0;
  padding: 18px;
  border: 1px solid rgba(226, 232, 240, 0.9);
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 18px 50px rgba(15, 23, 42, 0.08);
}

.agent-task-panel__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.agent-task-panel__eyebrow {
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.08em;
  color: #64748b;
}

.agent-task-panel h3 {
  margin: 4px 0 0;
  font-size: 18px;
  color: #0f172a;
}

.agent-task-panel__status {
  padding: 6px 12px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4338ca;
  font-weight: 700;
  font-size: 13px;
}

.agent-task-steps {
  display: grid;
  gap: 10px;
  margin-top: 16px;
}

.agent-task-step {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border-radius: 14px;
  background: #f8fafc;
  color: #334155;
}
```

- [ ] **Step 4: Add JS event renderer**

Modify `src/web/static/js/home/main.js`:

```javascript
let agentTaskPanel;
let agentTaskStatus;
let agentTaskSteps;
let agentTaskPartial;
```

Initialize in `init()`:

```javascript
agentTaskPanel = document.getElementById('agentTaskPanel');
agentTaskStatus = document.getElementById('agentTaskStatus');
agentTaskSteps = document.getElementById('agentTaskSteps');
agentTaskPartial = document.getElementById('agentTaskPartial');
```

Add WebSocket message branch:

```javascript
if (data.type === 'agent_event') {
  handleAgentEvent(data);
  return;
}
```

Add renderer:

```javascript
function handleAgentEvent(event) {
  if (!agentTaskPanel || !agentTaskSteps || !agentTaskStatus) return;
  agentTaskPanel.hidden = false;
  agentTaskStatus.textContent = event.message || event.event || '执行中';

  const step = document.createElement('div');
  step.className = `agent-task-step agent-task-step--${event.event || 'default'}`;
  step.innerHTML = `
    <span>${escapeHtml(event.message || event.event || '任务事件')}</span>
    <strong>${event.tool || event.skill || ''}</strong>
  `;
  agentTaskSteps.appendChild(step);
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
```

If `escapeHtml` already exists, reuse the existing function instead of redefining it.

- [ ] **Step 5: Run checks**

Run:

```bash
node --check src/web/static/js/home/main.js
node tests/home_agent_task_panel_test.js
```

Expected: PASS.

---

## Task 8: Implement Comprehensive Evaluation Workflow End-to-End

**Files:**
- Modify: `src/agent/skills/comprehensive_evaluation_skill.py`
- Modify: `src/agent/planning/task_planner.py`
- Test: `tests/agent/test_comprehensive_workflow.py`

- [ ] **Step 1: Write workflow integration test**

Create `tests/agent/test_comprehensive_workflow.py`:

```python
from src.agent.contracts import AgentContext
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.planning import TaskPlanner


class FakeTool:
    def __init__(self, name):
        self.name = name

    def execute(self, query):
        return {
            "success": True,
            "message": f"{self.name} ok",
            "data": {"query": query},
            "formatted": f"{self.name} formatted",
        }


def test_comprehensive_workflow_executes_all_available_steps():
    context = AgentContext(
        query="全面分析 CCO",
        trace_id="trace-comprehensive",
        active_skill="comprehensive_evaluation",
    )
    plan = TaskPlanner().plan(context)
    tools = {step.tool_name: FakeTool(step.tool_name) for step in plan.steps}

    result = WorkflowOrchestrator().run(context=context, steps=plan.steps, tools=tools, continue_on_error=True)

    assert result.success is True
    assert result.metadata["step_count"] == 5
    assert [item.tool_name for item in result.tool_results] == [
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "reverse_target_predictor",
        "target_database_search",
    ]
```

- [ ] **Step 2: Run test**

Run:

```bash
python -m pytest tests/agent/test_comprehensive_workflow.py -q
```

Expected: PASS after previous planner/orchestrator tasks.

- [ ] **Step 3: Ensure skill declares workflow mode**

Modify `src/agent/skills/comprehensive_evaluation_skill.py` so the class contains:

```python
workflow_steps = True
allowed_tools = [
    "property_calculator",
    "admet_predictor",
    "activity_predictor",
    "reverse_target_predictor",
    "target_database_search",
]
max_iterations_override = 8
```

- [ ] **Step 4: Run related tests**

Run:

```bash
python -m pytest tests/agent/test_comprehensive_workflow.py tests/agent/test_workflow_skills.py -q
```

Expected: PASS.

---

## Task 9: Implement Target-Driven Design Workflow

**Files:**
- Create or modify: `src/agent/skills/target_driven_design_skill.py`
- Modify: `src/agent/skills/skill_registry.py`
- Modify: `src/agent/planning/task_planner.py`
- Test: `tests/agent/test_target_driven_design_workflow.py`

- [ ] **Step 1: Write target-driven workflow test**

Create `tests/agent/test_target_driven_design_workflow.py`:

```python
from src.agent.contracts import AgentContext
from src.agent.planning import TaskPlanner


def test_pde5_target_driven_design_extracts_target_and_count():
    context = AgentContext(
        query="针对 PDE5 设计 20 个类药候选分子，并筛选适合 docking 的前 5 个",
        trace_id="trace-pde5",
        active_skill="target_driven_design",
    )

    plan = TaskPlanner().plan(context)

    assert plan.workflow_name == "target_driven_design"
    assert plan.metadata["target_hint"] == "PDE5"
    assert plan.metadata["requested_count"] == 20
    assert plan.steps[0].tool_name == "target_database_search"
    assert plan.steps[-1].tool_name == "molecular_docking"
```

- [ ] **Step 2: Run test**

Run:

```bash
python -m pytest tests/agent/test_target_driven_design_workflow.py -q
```

Expected: PASS if Task 2 planner already supports this path.

- [ ] **Step 3: Add explicit skill**

Create `src/agent/skills/target_driven_design_skill.py`:

```python
from .base_skill import BaseSkill


class TargetDrivenDesignSkill(BaseSkill):
    name = "target_driven_design"
    description = "根据靶点名称自动完成靶点搜索、候选分子生成、性质筛选、ADMET、活性预测和对接排序"
    trigger_keywords = [
        "针对",
        "靶点设计",
        "候选分子",
        "适合 docking",
        "PDE",
        "EGFR",
        "KRAS",
        "BRAF",
    ]
    allowed_tools = [
        "target_database_search",
        "llm_molecular_generator",
        "property_calculator",
        "admet_predictor",
        "activity_predictor",
        "molecular_docking",
    ]
    workflow_steps = True
    max_iterations_override = 10
    system_prompt = (
        "你是靶点驱动分子设计助手。必须先确认靶点，再生成候选分子，"
        "然后完成性质、ADMET、活性和 docking 筛选。不要编造不存在的结构文件或对接结果。"
    )
```

- [ ] **Step 4: Register skill before atomic skills**

Modify `src/agent/skills/skill_registry.py`:

```python
from .target_driven_design_skill import TargetDrivenDesignSkill
```

Add `TargetDrivenDesignSkill` near other workflow skills, before `MolecularDesignSkill` and `TargetSearchSkill`.

- [ ] **Step 5: Run registry and workflow tests**

Run:

```bash
python -m pytest tests/agent/test_target_driven_design_workflow.py tests/agent/test_workflow_skills.py -q
```

Expected: PASS.

---

## Task 10: Add Agent Health Checks

**Files:**
- Modify: `scripts/health_check.py`
- Test: `tests/test_agent_health_check.py`

- [ ] **Step 1: Write health check test**

Create `tests/test_agent_health_check.py`:

```python
from scripts.health_check import check_agent_components


def test_agent_components_health_check_returns_statuses():
    results = check_agent_components()

    names = {item.name for item in results}

    assert "Agent Contracts" in names
    assert "Agent Planner" in names
    assert "Agent Skill Registry" in names
```

- [ ] **Step 2: Run test and verify failure**

Run:

```bash
python -m pytest tests/test_agent_health_check.py -q
```

Expected: FAIL because `check_agent_components` does not exist.

- [ ] **Step 3: Implement health check**

Modify `scripts/health_check.py`:

```python
def check_agent_components() -> list[CheckResult]:
    results: list[CheckResult] = []
    try:
        from src.agent.contracts import AgentContext, AgentResult, ToolResult
        results.append(CheckResult("Agent Contracts", True, "Agent contracts import succeeded"))
    except Exception as exc:
        results.append(CheckResult("Agent Contracts", False, str(exc)))

    try:
        from src.agent.planning import TaskPlanner
        planner = TaskPlanner()
        results.append(CheckResult("Agent Planner", True, planner.__class__.__name__))
    except Exception as exc:
        results.append(CheckResult("Agent Planner", False, str(exc)))

    try:
        from src.agent.skills import SkillRegistry
        registry = SkillRegistry()
        results.append(CheckResult("Agent Skill Registry", True, f"{len(registry.skills)} skills registered"))
    except Exception as exc:
        results.append(CheckResult("Agent Skill Registry", False, str(exc)))

    return results
```

Add these results to the main health check output list.

- [ ] **Step 4: Run tests and script**

Run:

```bash
python -m pytest tests/test_agent_health_check.py -q
python scripts/health_check.py
```

Expected: pytest PASS and health check includes Agent components.

---

## Task 11: Manual End-to-End Verification

**Files:**
- No code changes.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
python -m pytest tests/agent tests/test_llm_molecular_generator.py tests/test_target_search.py -q
```

Expected: PASS.

- [ ] **Step 2: Run frontend static checks**

Run:

```bash
node --check src/web/static/js/home/main.js
node tests/home_agent_task_panel_test.js
```

Expected: PASS.

- [ ] **Step 3: Start app**

Run:

```bash
python main.py --no-reload --host 127.0.0.1 --port 6001
```

Expected: app starts without route import errors.

- [ ] **Step 4: Test comprehensive workflow from homepage**

In browser, open:

```text
http://127.0.0.1:6001
```

Submit:

```text
全面分析 CCO
```

Expected:

- Agent task panel appears.
- Steps show property, ADMET, activity, reverse target, target structure search.
- Final response includes structured summary.
- Partial failure still shows completed steps.

- [ ] **Step 5: Test target-driven design workflow**

Submit:

```text
针对 PDE5 设计 20 个类药候选分子，并筛选适合 docking 的前 5 个
```

Expected:

- Agent recognizes target-driven design.
- Target search step starts first.
- Molecule generation step uses requested count metadata.
- If docking environment is unavailable, workflow returns partial results and a clear docking environment warning instead of crashing.

---

## Self-Review Checklist

- Spec coverage: This plan implements the approved design path: contracts, planning, events, validation, workflow integration, frontend task display, target-driven design, and health checks.
- Placeholder scan: No unfinished placeholder markers or vague deferred implementation steps are present.
- Type consistency: `AgentContext`, `ToolResult`, `AgentResult`, `WorkflowStep`, `WorkflowPlan`, `TaskPlanner`, `AgentEventBus`, and `AgentResultValidator` are named consistently across tasks.
- Scope control: This plan does not attempt full multi-agent parallelism, long-term persistent memory, or full report generation. Those remain later phases after the workflow foundation is stable.
