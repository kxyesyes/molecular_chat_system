# MedChat Industrial Agent LangGraph Shadow Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复靶点证据为空仍继续生成和候选身份/数量漂移两个 P0 问题，并在不改变默认执行链的前提下加入可选 LangGraph shadow harness。

**Architecture:** `SupervisorAgent` 通过框架无关的 Harness 门面调用现有 `WorkflowExecutor`；legacy 结果始终是唯一权威结果。Shadow 模式使用同一份已编译计划做无工具副作用的 LangGraph 状态推演，并把脱敏差异写入 metadata。候选数据以 `CandidateSet@1` 流转，下游结果按稳定 `candidate_id` 对齐，靶点驱动生成受非空真实证据前置条件约束。

**Tech Stack:** Python 3.10、dataclasses、FastAPI 现有 Agent runtime、RDKit、pytest、可选 `langgraph==0.2.76` / `langchain-core==0.2.43`。

---

## File map

新增文件及单一职责：

- `src/agent/contracts/candidates.py`：定义 `CandidateRecord`、`CandidateSet` 和稳定 candidate ID。
- `src/agent/validators/candidate_alignment.py`：将下游批量结果按 canonical SMILES 映射回 candidate ID。
- `src/agent/validators/semantic_inputs.py`：执行工作流步骤的科学语义前置条件检查。
- `src/agent/harness/base.py`：框架无关 Harness 协议和比较结果类型。
- `src/agent/harness/legacy.py`：现有 `WorkflowExecutor` 的薄适配器。
- `src/agent/harness/langgraph_backend.py`：无工具执行权限的 LangGraph 计划推演器。
- `src/agent/harness/shadow.py`：运行 legacy、调用 simulator 并生成差异摘要。
- `src/agent/harness/factory.py`：解析 feature flag、延迟检测可选依赖和安全回退。
- `requirements-agent-harness.txt`：与当前 LangChain 0.2 依赖族兼容的可选依赖。

重点修改文件：

- `src/agent/validators/molecule_candidates.py`、`result_validator.py`：生成结果实体化。
- `src/agent/orchestrators/base.py`、`workflow.py`：声明并执行语义门，保存 skipped 状态，执行候选对齐。
- `src/agent/planning/task_planner.py`、`compiler.py`：在计划中声明和编译前置条件/候选来源。
- `src/agent/supervisor.py`：通过 Harness 执行两条现有入口路径。
- `src/agent/persistence/base.py`、`sqlite_store.py`：在现有 `agent_runs.metadata_json` 中原子合并 shadow 摘要，不新增表。
- `src/agent/evaluation/scientific.py`：真实验收记录 shadow 摘要和候选身份闭环。
- `tests/agent/`：按任务新增聚焦回归。

任何任务都不得修改前端、WebSocket 公共事件协议、外部模型边界或默认 `legacy` 行为。

### Task 1: 建立 `CandidateSet@1` 领域契约

**Files:**
- Create: `src/agent/contracts/candidates.py`
- Modify: `src/agent/contracts/__init__.py`
- Test: `tests/agent/test_candidate_contracts.py`

- [ ] **Step 1: 写 candidate ID 和序列化失败测试**

```python
from src.agent.contracts import CandidateRecord, CandidateSet, ObservationStatus


def test_candidate_set_serializes_stable_identity_and_counts():
    first = CandidateRecord.from_smiles(
        candidate_index=1,
        source_index=3,
        original_smiles="OCC",
        canonical_smiles="CCO",
        generation_provenance={"model_name": "gmm-llama:latest"},
    )
    repeated = CandidateRecord.from_smiles(
        candidate_index=1,
        source_index=3,
        original_smiles="OCC",
        canonical_smiles="CCO",
        generation_provenance={"model_name": "gmm-llama:latest"},
    )
    candidate_set = CandidateSet(
        requested_count=2,
        candidates=(first,),
        invalid_count=1,
        duplicate_count=0,
        rejected=({"source_index": 2, "reason": "invalid_smiles"},),
        status=ObservationStatus.PARTIAL,
    )

    payload = candidate_set.to_dict()

    assert first.candidate_id == repeated.candidate_id
    assert first.candidate_id.startswith("cand-001-")
    assert payload["version"] == "1"
    assert payload["valid_count"] == 1
    assert payload["unique_count"] == 1
    assert payload["candidates"][0]["smiles"] == "CCO"
    assert payload["status"] == "partial"
```

- [ ] **Step 2: 运行测试并确认因契约不存在而失败**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_candidate_contracts.py -q -p no:cacheprovider
```

Expected: collection FAIL，提示无法从 `src.agent.contracts` 导入 `CandidateRecord`。

- [ ] **Step 3: 实现不可变候选契约并导出**

`src/agent/contracts/candidates.py`：

```python
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Any

from .scientific import ObservationStatus


def build_candidate_id(source_index: int, canonical_smiles: str) -> str:
    digest = hashlib.sha256(canonical_smiles.encode("utf-8")).hexdigest()[:8]
    return f"cand-{source_index:03d}-{digest}"


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    source_index: int
    original_smiles: str
    canonical_smiles: str
    validation: dict[str, Any] = field(default_factory=dict)
    generation_provenance: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_smiles(
        cls,
        *,
        candidate_index: int,
        source_index: int,
        original_smiles: str,
        canonical_smiles: str,
        generation_provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "CandidateRecord":
        return cls(
            candidate_id=build_candidate_id(candidate_index, canonical_smiles),
            source_index=source_index,
            original_smiles=original_smiles,
            canonical_smiles=canonical_smiles,
            validation={"valid": True, "method": "RDKit"},
            generation_provenance=generation_provenance or {},
            metadata=metadata or {},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "source_index": self.source_index,
            "original_smiles": self.original_smiles,
            "canonical_smiles": self.canonical_smiles,
            "smiles": self.canonical_smiles,
            "validation": dict(self.validation),
            "generation_provenance": dict(self.generation_provenance),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CandidateSet:
    requested_count: int
    candidates: tuple[CandidateRecord, ...]
    invalid_count: int = 0
    duplicate_count: int = 0
    rejected: tuple[dict[str, Any], ...] = ()
    status: ObservationStatus = ObservationStatus.SUCCEEDED
    version: str = "1"

    @property
    def valid_count(self) -> int:
        return len(self.candidates)

    @property
    def unique_count(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "requested_count": self.requested_count,
            "valid_count": self.valid_count,
            "unique_count": self.unique_count,
            "invalid_count": self.invalid_count,
            "duplicate_count": self.duplicate_count,
            "candidates": [item.to_dict() for item in self.candidates],
            "rejected": [dict(item) for item in self.rejected],
            "status": self.status.value,
        }
```

在 `src/agent/contracts/__init__.py` 导入并加入 `__all__`：

```python
from .candidates import CandidateRecord, CandidateSet, build_candidate_id
```

- [ ] **Step 4: 运行契约测试**

Run: 同 Step 2。

Expected: `1 passed`。

- [ ] **Step 5: 精确提交**

```powershell
git add -- src/agent/contracts/candidates.py src/agent/contracts/__init__.py tests/agent/test_candidate_contracts.py
git commit -m "feat(agent): add candidate set contract"
```

### Task 2: 用 RDKit 生成严格候选集

**Files:**
- Modify: `src/agent/validators/molecule_candidates.py`
- Modify: `src/agent/validators/result_validator.py`
- Modify: `tests/agent/test_generated_candidate_validation.py`

- [ ] **Step 1: 将现有测试改为要求结构化候选集，并新增 excess 测试**

```python
def test_generator_result_keeps_only_requested_valid_unique_candidates():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[
            {"smiles": "CCO"},
            {"smiles": "CCN"},
            {"smiles": "CCC"},
        ],
        quality={"requested_count": 2},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert [item["smiles"] for item in validated.data["candidates"]] == [
        "CCO",
        "CCN",
    ]
    assert validated.data["valid_count"] == 2
    assert validated.data["rejected"][0]["reason"] == "excess_candidate"
    assert validated.status == ObservationStatus.SUCCEEDED


def test_generator_partial_result_preserves_invalid_and_duplicate_rejections():
    result = ToolResult.success_result(
        "llm_molecular_generator",
        data=[{"smiles": "CCO"}, {"smiles": "OCC"}, {"smiles": "CC(C)(("}],
        quality={"requested_count": 3},
    )

    validated = AgentResultValidator().validate_tool_result(result)

    assert validated.status == ObservationStatus.PARTIAL
    assert validated.data["valid_count"] == 1
    assert validated.data["duplicate_count"] == 1
    assert validated.data["invalid_count"] == 1
    assert {item["reason"] for item in validated.data["rejected"]} == {
        "duplicate_smiles",
        "invalid_smiles",
    }
```

同时把原测试中的 `validated.data` 列表断言改为 `validated.data["candidates"]`，无有效候选时断言 `validated.data["status"] == "failed"`，不再断言 data 为 `None`。

- [ ] **Step 2: 运行聚焦测试，确认旧 sanitizer 不满足结构化契约**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_generated_candidate_validation.py -q -p no:cacheprovider
```

Expected: FAIL，旧结果仍为 list 且不会拒绝 excess candidate。

- [ ] **Step 3: 修改 sanitizer 生成 `CandidateSet`**

实现要点必须全部落地：

```python
@dataclass(frozen=True)
class CandidateSanitization:
    candidate_set: CandidateSet

    @property
    def candidates(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.candidate_set.candidates]

    @property
    def valid_count(self) -> int:
        return self.candidate_set.valid_count


def sanitize_generated_candidates(
    data: Any,
    *,
    requested_count: int | None = None,
    generation_provenance: dict[str, Any] | None = None,
) -> CandidateSanitization:
    from rdkit import Chem

    raw = _candidate_items(data)
    requested = requested_count if requested_count is not None else len(raw)
    accepted: list[CandidateRecord] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid_count = 0
    duplicate_count = 0

    for source_index, item in enumerate(raw, start=1):
        payload = dict(item) if isinstance(item, dict) else {"smiles": str(item)}
        original = str(payload.get("smiles") or "").strip()
        mol = Chem.MolFromSmiles(original) if original else None
        if mol is None:
            invalid_count += 1
            rejected.append({"source_index": source_index, "smiles": original, "reason": "invalid_smiles"})
            continue
        canonical = Chem.MolToSmiles(mol)
        if canonical in seen:
            duplicate_count += 1
            rejected.append({"source_index": source_index, "smiles": canonical, "reason": "duplicate_smiles"})
            continue
        seen.add(canonical)
        if len(accepted) >= requested:
            rejected.append({"source_index": source_index, "smiles": canonical, "reason": "excess_candidate"})
            continue
        accepted.append(
            CandidateRecord.from_smiles(
                candidate_index=len(accepted) + 1,
                source_index=source_index,
                original_smiles=original,
                canonical_smiles=canonical,
                generation_provenance=generation_provenance,
                metadata={key: value for key, value in payload.items() if key != "smiles"},
            )
        )

    status = (
        ObservationStatus.FAILED
        if not accepted
        else ObservationStatus.PARTIAL
        if len(accepted) < requested
        else ObservationStatus.SUCCEEDED
    )
    return CandidateSanitization(
        CandidateSet(
            requested_count=requested,
            candidates=tuple(accepted),
            invalid_count=invalid_count,
            duplicate_count=duplicate_count,
            rejected=tuple(rejected),
            status=status,
        )
    )
```

补充 `_candidate_items()`，只接受 list 或 dict 中 `molecules`、`candidates`、`data` 的 list，其他输入返回空 list；RDKit 导入失败继续抛 `CandidateValidationUnavailable`。

- [ ] **Step 4: 修改 generator result validator 使用新契约**

在调用 sanitizer 前读取 `requested_count`，并传入 provenance：

```python
requested = int(result.quality.get("requested_count") or 0) or None
sanitized = sanitize_generated_candidates(
    result.data,
    requested_count=requested,
    generation_provenance=(result.provenance.to_dict() if result.provenance else {}),
)
candidate_set = sanitized.candidate_set
result.data = candidate_set.to_dict()
result.status = candidate_set.status
result.quality = {
    **result.quality,
    "requested_count": candidate_set.requested_count,
    "valid_count": candidate_set.valid_count,
    "unique_count": candidate_set.unique_count,
    "invalid_count": candidate_set.invalid_count,
    "duplicate_count": candidate_set.duplicate_count,
    "validation_method": "RDKit",
    "output_contract": "CandidateSet@1",
}
if candidate_set.status == ObservationStatus.FAILED:
    result.success = False
    result.formatted = ""
    result.message = "Generated output contained no valid unique SMILES"
    result.error = AgentExecutionError(
        code=AgentErrorCode.INVALID_OUTPUT,
        message=result.message,
        details=result.quality,
    )
elif candidate_set.status == ObservationStatus.PARTIAL:
    result.warnings.append(
        f"Generated {candidate_set.valid_count} valid unique SMILES "
        f"out of {candidate_set.requested_count} requested"
    )
```

- [ ] **Step 5: 运行候选测试与编译检查**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_candidate_contracts.py tests\agent\test_generated_candidate_validation.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src\agent\contracts src\agent\validators
```

Expected: 全部 PASS，compileall exit 0。

- [ ] **Step 6: 精确提交**

```powershell
git add -- src/agent/validators/molecule_candidates.py src/agent/validators/result_validator.py tests/agent/test_generated_candidate_validation.py
git commit -m "feat(agent): enforce generated candidate set"
```

### Task 3: 对齐下游候选结果并拒绝第 11 条记录

**Files:**
- Create: `src/agent/validators/candidate_alignment.py`
- Modify: `src/agent/validators/__init__.py`
- Modify: `src/agent/orchestrators/workflow.py`
- Modify: `src/agent/planning/task_planner.py`
- Test: `tests/agent/test_candidate_alignment.py`

- [ ] **Step 1: 写 canonical SMILES 对齐、missing 和 extra 测试**

```python
from src.agent.contracts import ObservationStatus, ToolResult
from src.agent.validators.candidate_alignment import align_candidate_results


CANDIDATE_SET = {
    "version": "1",
    "requested_count": 2,
    "candidates": [
        {"candidate_id": "cand-001-a", "smiles": "CCO", "canonical_smiles": "CCO"},
        {"candidate_id": "cand-002-b", "smiles": "CCN", "canonical_smiles": "CCN"},
    ],
}


def test_alignment_maps_canonical_smiles_and_discards_unknown_records():
    result = ToolResult.success_result(
        "property_calculator",
        data=[
            {"smiles": "OCC", "properties": {"mw": 46.07}},
            {"smiles": "CCC", "properties": {"mw": 44.10}},
        ],
    )

    aligned = align_candidate_results(CANDIDATE_SET, result)

    assert aligned.data == [
        {"smiles": "OCC", "properties": {"mw": 46.07}, "candidate_id": "cand-001-a"}
    ]
    assert aligned.status == ObservationStatus.PARTIAL
    assert aligned.quality["candidate_alignment"] == {
        "source_count": 2,
        "aligned_count": 1,
        "missing_candidate_ids": ["cand-002-b"],
        "discarded_count": 1,
    }
    assert any("discarded 1" in warning.lower() for warning in aligned.warnings)


def test_alignment_fails_required_result_when_no_candidate_matches():
    result = ToolResult.success_result(
        "property_calculator",
        data=[{"smiles": "CCC", "properties": {"mw": 44.10}}],
    )

    aligned = align_candidate_results(CANDIDATE_SET, result, required=True)

    assert aligned.success is False
    assert aligned.error.code.value == "invalid_output"
```

- [ ] **Step 2: 运行并确认模块不存在**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_candidate_alignment.py -q -p no:cacheprovider
```

Expected: collection FAIL，提示 `candidate_alignment` 不存在。

- [ ] **Step 3: 实现对齐器**

`align_candidate_results(candidate_set, result, required=False)` 必须：

1. 只处理 `result.success` 且 `result.data` 为 list；其他成功形状返回 `INVALID_OUTPUT`，禁止猜测。
2. 用 RDKit canonicalize 候选和结果 SMILES；RDKit 不可用时返回 `TOOL_UNAVAILABLE`。
3. 为唯一匹配记录写入 `candidate_id`。
4. 丢弃未知 SMILES 和同一 candidate 的重复结果。
5. 把 aligned/missing/discarded 统计写入 `quality["candidate_alignment"]`。
6. 有部分匹配时标记 `ObservationStatus.PARTIAL`；无匹配时 `success=False`。

核心返回逻辑：

```python
result.data = aligned_records
result.quality = {
    **result.quality,
    "candidate_alignment": {
        "source_count": len(candidate_by_smiles),
        "aligned_count": len(aligned_records),
        "missing_candidate_ids": missing,
        "discarded_count": discarded_count,
    },
}
if not aligned_records:
    result.success = False
    result.status = ObservationStatus.FAILED
    result.message = "Downstream result did not match any generated candidate"
    result.error = AgentExecutionError(
        AgentErrorCode.INVALID_OUTPUT,
        result.message,
        result.quality["candidate_alignment"],
    )
elif missing or discarded_count:
    result.status = ObservationStatus.PARTIAL
    result.warnings.append(
        f"Candidate alignment missing {len(missing)} and discarded {discarded_count} records"
    )
return result
```

- [ ] **Step 4: 在计划中声明候选来源**

给 target-driven 的 `properties`、`admet`、`activity` 步骤增加：

```python
metadata={"candidate_source": "molecules"}
```

给 hit-to-lead 的 `candidate_properties` 增加：

```python
metadata={"candidate_source": "candidates"}
```

- [ ] **Step 5: 在 orchestrator 验证完成后执行对齐**

在 `result = self.validator.validate_tool_result(result)` 之后、evidence ledger 注册之前加入：

```python
candidate_source = step.metadata.get("candidate_source")
if candidate_source and result.success:
    result = align_candidate_results(
        outputs.get(str(candidate_source)),
        result,
        required=step.required,
    )
```

确保对齐后的数据才进入 ledger、checkpoint、事件和最终回答。

- [ ] **Step 6: 运行聚焦测试和 target workflow 回归**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_candidate_alignment.py tests\agent\test_target_driven_design_workflow.py tests\agent\test_scientific_trust_runtime.py -q -p no:cacheprovider
```

Expected: 全部 PASS。必要时只更新 fake target 数据，使其包含真实可用证据形状，不放宽生产验证。

- [ ] **Step 7: 精确提交**

```powershell
git add -- src/agent/validators/candidate_alignment.py src/agent/validators/__init__.py src/agent/orchestrators/workflow.py src/agent/planning/task_planner.py tests/agent/test_candidate_alignment.py tests/agent/test_target_driven_design_workflow.py tests/agent/test_scientific_trust_runtime.py
git commit -m "feat(agent): align downstream candidate results"
```

### Task 4: 阻止缺乏真实靶点证据的生成步骤

**Files:**
- Create: `src/agent/validators/semantic_inputs.py`
- Modify: `src/agent/validators/__init__.py`
- Modify: `src/agent/orchestrators/base.py`
- Modify: `src/agent/orchestrators/workflow.py`
- Modify: `src/agent/planning/task_planner.py`
- Modify: `src/agent/planning/compiler.py`
- Modify: `src/agent/supervisor.py`
- Test: `tests/agent/test_semantic_input_gates.py`
- Test: `tests/agent/test_plan_compiler.py`
- Test: `tests/agent/test_supervisor_agent.py`

- [ ] **Step 1: 写空证据阻断和有效证据放行测试**

测试使用计数 fake tools：

```python
def test_empty_target_records_skip_generation_and_return_partial():
    target = CountingTool("target_database_search", data=[])
    generator = CountingTool("llm_molecular_generator", data=[{"smiles": "CCO"}])
    execution = _run_target_design(target, generator)

    assert target.calls == 1
    assert generator.calls == 0
    assert execution.result.partial is True
    assert execution.result.success is False
    assert execution.result.metadata["skipped_steps"][0] == {
        "step_id": "molecule_generation",
        "status": "skipped_precondition",
        "requirement": "target_evidence",
        "reason": "target_evidence_missing",
    }
    assert [item.tool_name for item in execution.result.tool_results] == [
        "target_database_search"
    ]


def test_structured_target_evidence_is_consumed_by_generator():
    target_data = [{
        "gene_symbol": "PDE5A",
        "source": "local_target_db",
        "recommended_structures": [{"structure_id": "1UDT"}],
    }]
    target = CountingTool("target_database_search", data=target_data)
    generator = CountingTool("llm_molecular_generator", data=[{"smiles": "CCO"}])
    execution = _run_target_design(target, generator)

    assert generator.calls == 1
    assert "PDE5A" in str(generator.inputs[0])
    assert "1UDT" in str(generator.inputs[0])
```

- [ ] **Step 2: 运行测试并确认旧流程仍调用 generator**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_semantic_input_gates.py -q -p no:cacheprovider
```

Expected: 第一个测试 FAIL，`generator.calls == 1`。

- [ ] **Step 3: 为 WorkflowStep 增加显式前置条件**

在 `WorkflowStep` 增加不可变字段：

```python
preconditions: tuple[str, ...] = ()
```

target-driven `molecule_generation` 增加：

```python
preconditions=("target_evidence",),
```

`PlanCompiler.compile()` 对每个 precondition 只允许已注册名称，并要求 `target_evidence` 步骤依赖 `target.structure.search` 输出：

```python
SUPPORTED_PRECONDITIONS = frozenset({"target_evidence"})

unknown = set(step.preconditions) - self.SUPPORTED_PRECONDITIONS
if unknown:
    raise PlanCompilationError(
        f"Unsupported semantic preconditions for {step.name}: {sorted(unknown)}"
    )
```

同时在 `SupervisorAgent._step_to_dict()` 的返回结构中加入：

```python
"preconditions": list(step.preconditions),
```

并在 `tests/agent/test_supervisor_agent.py` 断言 target-driven 的 `molecule_generation` 计划公开 `preconditions == ["target_evidence"]`。

- [ ] **Step 4: 实现 `SemanticInputValidator`**

```python
@dataclass(frozen=True)
class SemanticDecision:
    allowed: bool
    requirement: str | None = None
    reason: str | None = None
    evidence_digest: str | None = None


class SemanticInputValidator:
    def validate(self, step: WorkflowStep, input_data: Any) -> SemanticDecision:
        for requirement in step.preconditions:
            if requirement == "target_evidence":
                records = self._target_records(input_data)
                usable = [item for item in records if self._usable_target_record(item)]
                if not usable:
                    return SemanticDecision(False, requirement, "target_evidence_missing")
                digest = hashlib.sha256(
                    json.dumps(usable, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
                return SemanticDecision(True, requirement, evidence_digest=digest)
        return SemanticDecision(True)
```

`_usable_target_record()` 必须要求非空的 `gene_symbol|target_identifier|uniprot_id|target_name`，并同时存在 `source|database|uniprot_id|recommended_structures|structures|artifacts|evidence` 中至少一项非空值。字符串 `None`、空 list、demo/fallback 标记不得通过。

- [ ] **Step 5: 在 orchestrator 中先做语义检查，再发 TOOL_STARTED**

把当前 `TOOL_STARTED` 移到输入解析和语义门通过之后。门失败时：

```python
skipped_steps = [{
    "step_id": step.name,
    "status": "skipped_precondition",
    "requirement": decision.requirement,
    "reason": decision.reason,
}]
for remaining in steps[index + 1:]:
    skipped_steps.append({
        "step_id": remaining.name,
        "status": "skipped_precondition",
        "requirement": decision.requirement,
        "reason": f"blocked_by:{step.name}",
    })
break
```

最终构建 `AgentResult` 后，如果有 skipped required step 且已有真实成功结果：

```python
agent_result.success = False
agent_result.partial = bool(results)
agent_result.outcome = RunOutcome.PARTIAL if results else RunOutcome.FAILED
agent_result.message = "Workflow returned partial results because a scientific precondition failed"
agent_result.metadata["skipped_steps"] = skipped_steps
```

不要创建伪造的 generator `ToolResult`，因此 `actual_tools` 不会把未执行工具算作已调用。

- [ ] **Step 6: 增加 plan compiler 前置条件测试并运行聚焦套件**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_semantic_input_gates.py tests\agent\test_plan_compiler.py tests\agent\test_task_planner.py tests\agent\test_target_driven_design_workflow.py tests\agent\test_supervisor_agent.py -q -p no:cacheprovider
```

Expected: 全部 PASS；空 evidence 流程的 generator 调用次数为 0。

- [ ] **Step 7: 精确提交**

```powershell
git add -- src/agent/validators/semantic_inputs.py src/agent/validators/__init__.py src/agent/orchestrators/base.py src/agent/orchestrators/workflow.py src/agent/planning/task_planner.py src/agent/planning/compiler.py src/agent/supervisor.py tests/agent/test_semantic_input_gates.py tests/agent/test_plan_compiler.py tests/agent/test_task_planner.py tests/agent/test_target_driven_design_workflow.py tests/agent/test_supervisor_agent.py
git commit -m "fix(agent): require usable target evidence"
```

### Task 5: 建立框架无关 Harness 和 legacy 适配器

**Files:**
- Create: `src/agent/harness/__init__.py`
- Create: `src/agent/harness/base.py`
- Create: `src/agent/harness/legacy.py`
- Test: `tests/agent/test_harness_legacy.py`

- [ ] **Step 1: 写 legacy 只执行一次且返回同一对象的测试**

```python
from src.agent.harness import LegacyHarness


def test_legacy_harness_delegates_once_and_preserves_execution():
    executor = RecordingExecutor()
    harness = LegacyHarness(executor)

    run = harness.execute(
        context=CONTEXT,
        policy=POLICY,
        all_tools={"property_calculator": object()},
        plan=PLAN,
    )

    assert executor.calls == 1
    assert run.authoritative is executor.execution
    assert run.shadow is None
```

- [ ] **Step 2: 运行并确认模块不存在**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_harness_legacy.py -q -p no:cacheprovider
```

Expected: collection FAIL。

- [ ] **Step 3: 实现 Harness 类型与 legacy 适配器**

`base.py`：

```python
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from src.agent.contracts import AgentContext
from src.agent.planning import WorkflowPlan
from src.agent.runtime.workflow_executor import WorkflowExecution
from src.agent.workflows import WorkflowPolicy


@dataclass(frozen=True)
class ShadowComparison:
    backend: str
    backend_version: str
    status: str
    plan_fingerprint: str
    matched: bool
    diff_categories: tuple[str, ...] = ()
    diffs: tuple[dict[str, Any], ...] = ()
    elapsed_ms: int = 0
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "backend_version": self.backend_version,
            "status": self.status,
            "plan_fingerprint": self.plan_fingerprint,
            "matched": self.matched,
            "diff_categories": list(self.diff_categories),
            "diffs": [dict(item) for item in self.diffs],
            "elapsed_ms": self.elapsed_ms,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class HarnessRun:
    authoritative: WorkflowExecution
    shadow: ShadowComparison | None = None


class HarnessBackend(Protocol):
    def execute(
        self,
        *,
        context: AgentContext,
        policy: WorkflowPolicy,
        all_tools: Mapping[str, Any],
        event_callback: Any = None,
        idempotency_key: str | None = None,
        plan: WorkflowPlan | None = None,
    ) -> HarnessRun: ...
```

`legacy.py` 只透传参数：

```python
class LegacyHarness:
    def __init__(self, executor: WorkflowExecutor):
        self.executor = executor

    def execute(self, **kwargs: Any) -> HarnessRun:
        return HarnessRun(authoritative=self.executor.execute(**kwargs))
```

- [ ] **Step 4: 运行测试和 import smoke test**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_harness_legacy.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -c "from src.agent.harness import LegacyHarness, HarnessRun, ShadowComparison"
```

Expected: PASS，import exit 0。

- [ ] **Step 5: 精确提交**

```powershell
git add -- src/agent/harness tests/agent/test_harness_legacy.py
git commit -m "feat(agent): add workflow harness boundary"
```

### Task 6: 实现可选 LangGraph plan simulator 和 Shadow 比较

**Files:**
- Create: `src/agent/harness/langgraph_backend.py`
- Create: `src/agent/harness/shadow.py`
- Create: `src/agent/harness/factory.py`
- Modify: `src/agent/harness/__init__.py`
- Create: `requirements-agent-harness.txt`
- Test: `tests/agent/test_harness_shadow.py`

- [ ] **Step 1: 写缺依赖回退、无重复工具调用、差异和脱敏测试**

至少包含：

```python
def test_shadow_harness_never_executes_authoritative_tool_twice():
    tool = CountingTool()
    legacy = real_legacy_harness(tool)
    simulator = RecordingSimulator(matched=True)

    run = ShadowHarness(legacy=legacy, simulator=simulator).execute(**REQUEST)

    assert tool.calls == 1
    assert simulator.calls == 1
    assert run.shadow.status == "matched"


def test_shadow_failure_does_not_change_authoritative_result():
    legacy = successful_legacy_harness()
    run = ShadowHarness(legacy=legacy, simulator=FailingSimulator()).execute(**REQUEST)

    assert run.authoritative.result.success is True
    assert run.shadow.status == "failed"
    assert run.shadow.error_code == "shadow_runtime_error"


def test_factory_falls_back_to_legacy_when_optional_dependency_is_missing(monkeypatch):
    monkeypatch.setenv("AGENT_HARNESS_MODE", "shadow")
    factory = HarnessFactory(langgraph_available=lambda: False)

    backend = factory.create(RecordingExecutor())

    assert isinstance(backend, LegacyHarness)
    assert factory.last_warning == "langgraph_optional_dependency_unavailable"


def test_shadow_metadata_contains_no_prompt_or_secret():
    comparison = make_comparison(query="secret prompt", api_key="sk-secret")
    serialized = json.dumps(comparison.to_dict())

    assert "secret prompt" not in serialized
    assert "sk-secret" not in serialized
    assert len(comparison.plan_fingerprint) == 64
```

- [ ] **Step 2: 运行测试并确认 shadow 模块不存在**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_harness_shadow.py -q -p no:cacheprovider
```

Expected: collection FAIL。

- [ ] **Step 3: 增加兼容可选依赖文件**

`requirements-agent-harness.txt` 内容必须仅为：

```text
# Compatible with deployment/requirements.txt LangChain 0.2 family.
langgraph==0.2.76
langchain-core==0.2.43
```

不得修改根 `requirements.txt`，不得自动安装依赖。

- [ ] **Step 4: 实现 `LangGraphPlanSimulator`**

实现约束：

- `langgraph.graph` 只能在构造或 `simulate()` 内延迟导入；
- simulator 输入只包含 `WorkflowPlan`、compiled dependencies 和权威执行摘要；
- 节点函数只追加 step ID 和决策，不持有 tools；
- 按 legacy 顺序连边，另行保存 data dependencies 用于比较；
- `recursion_limit=max(25, len(steps) * 3)`；
- fingerprint 对 workflow、step name/tool、binding、preconditions、required 和 dependencies 的规范 JSON 做 SHA-256。

核心图构造：

```python
class ShadowState(TypedDict):
    visited: Annotated[list[str], operator.add]
    decisions: Annotated[list[dict[str, Any]], operator.add]


builder = StateGraph(ShadowState)
for step in plan.steps:
    builder.add_node(
        step.name,
        lambda state, step_id=step.name: {
            "visited": [step_id],
            "decisions": [decision_by_step[step_id]],
        },
    )
builder.add_edge(START, plan.steps[0].name)
for left, right in zip(plan.steps, plan.steps[1:]):
    builder.add_edge(left.name, right.name)
builder.add_edge(plan.steps[-1].name, END)
graph = builder.compile()
state = graph.invoke(
    {"visited": [], "decisions": []},
    {"recursion_limit": max(25, len(plan.steps) * 3)},
)
```

空计划直接返回 visited/decisions 为空的成功模拟，不构造非法 START→END 图。

- [ ] **Step 5: 实现 `ShadowHarness` 和 deterministic diff**

顺序固定：先 `legacy.execute()`，再从 authoritative execution 构造只含 step 状态、skipped 摘要和终态的 simulator 输入。比较类别限定为设计规范中的七类。

超时实现使用模块级有界 executor：

```python
_SHADOW_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-shadow")

future = _SHADOW_POOL.submit(self.simulator.simulate, plan, compiled, summary)
try:
    comparison = future.result(timeout=self.timeout_seconds)
except FutureTimeoutError:
    future.cancel()
    comparison = ShadowComparison(
        backend="langgraph",
        backend_version=self.simulator.version,
        status="timeout",
        plan_fingerprint=plan_fingerprint(plan, compiled.dependencies),
        matched=False,
        diff_categories=("shadow_runtime",),
        error_code="shadow_timeout",
    )
```

executor 不得传给 simulator，simulator API 不得接受 tool registry。

- [ ] **Step 6: 实现安全工厂**

```python
class HarnessFactory:
    def __init__(self, mode: str | None = None, langgraph_available=None):
        self.mode = (mode or os.getenv("AGENT_HARNESS_MODE", "legacy")).strip().lower()
        self._available = langgraph_available or self._probe_langgraph
        self.last_warning: str | None = None

    def create(self, executor: WorkflowExecutor) -> HarnessBackend:
        self.last_warning = None
        if self.mode == "legacy":
            return LegacyHarness(executor)
        if self.mode == "shadow" and self._available():
            return ShadowHarness(LegacyHarness(executor), LangGraphPlanSimulator())
        self.last_warning = (
            "langgraph_execution_not_enabled"
            if self.mode == "langgraph"
            else "langgraph_optional_dependency_unavailable"
            if self.mode == "shadow"
            else "invalid_agent_harness_mode"
        )
        return LegacyHarness(executor)
```

probe 使用 `importlib.util.find_spec("langgraph")`，不得导入或修改环境。

- [ ] **Step 7: 在安装和未安装依赖两种环境运行测试**

先运行不依赖 LangGraph 的 fake simulator 测试：

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_harness_shadow.py -q -p no:cacheprovider
```

再执行依赖解析检查（不安装）：

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pip install --dry-run -r requirements-agent-harness.txt
```

Expected: pytest PASS；dry-run 显示 `langgraph-0.2.76` 与 `langchain-core-0.2.43`，无 resolver conflict。

- [ ] **Step 8: 精确提交**

```powershell
git add -- src/agent/harness requirements-agent-harness.txt tests/agent/test_harness_shadow.py
git commit -m "feat(agent): add optional langgraph shadow harness"
```

### Task 7: 将 Supervisor 和真实验收接入 Harness

**Files:**
- Modify: `src/agent/supervisor.py`
- Modify: `src/agent/evaluation/scientific.py`
- Modify: `src/agent/persistence/base.py`
- Modify: `src/agent/persistence/sqlite_store.py`
- Test: `tests/agent/test_supervisor_harness_integration.py`
- Test: `tests/agent/test_evaluation_runner.py`
- Test: `tests/agent/test_agent_persistence.py`

- [ ] **Step 1: 写默认 legacy、shadow metadata 和验收报告测试**

```python
def test_supervisor_defaults_to_legacy_without_shadow_metadata(monkeypatch):
    monkeypatch.delenv("AGENT_HARNESS_MODE", raising=False)
    result = build_supervisor().execute("计算 CCO 的性质", active_skill="admet_assessment")

    assert result["agent_result"].metadata.get("harness_shadow") is None
    assert PROPERTY_TOOL.calls == 1


def test_supervisor_attaches_shadow_summary_without_changing_result():
    factory = FakeShadowHarnessFactory(matched=True)
    result = build_supervisor(harness_factory=factory).execute(
        "计算 CCO 的性质",
        active_skill="admet_assessment",
    )

    assert result["success"] is True
    assert result["agent_result"].metadata["harness_shadow"]["status"] == "matched"
    assert PROPERTY_TOOL.calls == 1


def test_scientific_report_includes_redacted_harness_shadow():
    report = run_fake_scientific_case(shadow={
        "backend": "langgraph",
        "status": "matched",
        "plan_fingerprint": "a" * 64,
        "matched": True,
        "diff_categories": [],
        "diffs": [],
        "elapsed_ms": 3,
        "error_code": None,
    })

    assert report["harness_shadow"]["status"] == "matched"
    assert "prompt" not in report["harness_shadow"]


def test_state_store_merges_shadow_metadata_without_losing_request_metadata(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.sqlite3")
    store.start_run({
        "trace_id": "shadow-persist",
        "status": "running",
        "metadata": {"request_tag": "keep-me"},
    })

    store.update_run_metadata(
        "shadow-persist",
        {"harness_shadow": {"status": "matched", "plan_fingerprint": "a" * 64}},
    )

    metadata = store.get_run("shadow-persist")["metadata"]
    assert metadata["request_tag"] == "keep-me"
    assert metadata["harness_shadow"]["status"] == "matched"
```

- [ ] **Step 2: 运行测试并确认 Supervisor 尚未走 Harness**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_supervisor_harness_integration.py tests\agent\test_evaluation_runner.py tests\agent\test_agent_persistence.py -q -p no:cacheprovider
```

Expected: 新测试 FAIL；既有测试保持 PASS。

- [ ] **Step 3: 给 Supervisor 注入 HarnessFactory 并统一两条执行路径**

构造函数新增可选参数：

```python
harness_factory: HarnessFactory | None = None,
```

保存：

```python
self.harness_factory = harness_factory or HarnessFactory()
```

新增私有方法，`execute()` 和 `run()` 都调用它：

```python
def _execute_with_harness(self, executor: WorkflowExecutor, **kwargs: Any) -> WorkflowExecution:
    harness = self.harness_factory.create(executor)
    harness_run = harness.execute(**kwargs)
    execution = harness_run.authoritative
    if harness_run.shadow is not None:
        execution.result.metadata["harness_shadow"] = harness_run.shadow.to_dict()
    elif self.harness_factory.last_warning:
        execution.result.metadata["harness_shadow"] = {
            "status": "unavailable",
            "error_code": self.harness_factory.last_warning,
        }
    return execution
```

不得改变现有 capability failure、delegation、idempotency、event callback 和 legacy response 组装逻辑。

- [ ] **Step 4: 在现有 run metadata 列中持久化 shadow 摘要**

给 `AgentStateStore` Protocol 增加：

```python
def update_run_metadata(self, trace_id: str, metadata: dict[str, Any]) -> None: ...
```

`SQLiteAgentStateStore.update_run_metadata()` 必须在同一 lock/connection 事务中读取当前 `metadata_json`、与传入字段做浅层合并、调用 `redact_sensitive()`，再更新原列和 `updated_at`。trace 不存在时不插入幽灵 run，而是抛 `KeyError(trace_id)`。

`SupervisorAgent._execute_with_harness()` 在添加 `harness_shadow` 后，如果 orchestrator 带 `state_store`，调用：

```python
state_store.update_run_metadata(
    execution.result.trace_id,
    {"harness_shadow": execution.result.metadata["harness_shadow"]},
)
```

只有 run 已由 orchestrator 创建时调用；preflight 在 `start_run()` 前失败的结果只保留响应 metadata。

- [ ] **Step 5: 让 ScientificAcceptanceRunner 使用同一 Harness 门面**

Runner 构造函数接受可选 `harness_factory`，默认 `HarnessFactory()`。将直接 `WorkflowExecutor(...).execute(...)` 替换为 factory 创建的 backend，并将：

```python
harness_run.shadow.to_dict() if harness_run.shadow else None
```

写入每次 case run 的 `harness_shadow`。报告序列化继续经过 `redact_sensitive`。

- [ ] **Step 6: 运行 Supervisor、持久化和验收回归**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_supervisor_harness_integration.py tests\agent\test_supervisor_runtime_integration.py tests\agent\test_supervisor_agent.py tests\agent\test_evaluation_runner.py tests\agent\test_agent_persistence.py -q -p no:cacheprovider
```

Expected: 全部 PASS；默认模式不产生 shadow metadata，工具调用计数不变。

- [ ] **Step 7: 精确提交**

```powershell
git add -- src/agent/supervisor.py src/agent/evaluation/scientific.py src/agent/persistence/base.py src/agent/persistence/sqlite_store.py tests/agent/test_supervisor_harness_integration.py tests/agent/test_evaluation_runner.py tests/agent/test_agent_persistence.py
git commit -m "feat(agent): route supervisor through harness"
```

### Task 8: 扩展真实性检查、完成全量验收和交接

**Files:**
- Modify: `src/agent/evaluation/scientific.py`
- Modify: `tests/agent/test_real_acceptance_checks.py`
- Modify: `docs/handoff/latest.md`

- [ ] **Step 1: 写 GOLD-008 证据门和 candidate ID 闭环 truth check 测试**

```python
def test_target_design_truth_check_rejects_generation_after_empty_target_evidence():
    check = scientific._check_target_driven_generation_gate(
        tool_results=[
            ToolResult.success_result("target_database_search", data=[]),
            ToolResult.success_result("llm_molecular_generator", data={"candidates": []}),
        ],
        skipped_steps=[],
    )
    assert check == {
        "status": "failed",
        "reason": "generation_ran_without_usable_target_evidence",
    }


def test_candidate_identity_truth_check_requires_exact_downstream_subset():
    generated = ToolResult.success_result(
        "llm_molecular_generator",
        data={
            "requested_count": 2,
            "candidates": [
                {"candidate_id": "cand-001-a", "smiles": "CCO"},
                {"candidate_id": "cand-002-b", "smiles": "CCN"},
            ],
        },
    )
    downstream = ToolResult.success_result(
        "property_calculator",
        data=[
            {"candidate_id": "cand-001-a", "smiles": "CCO"},
            {"candidate_id": "unknown", "smiles": "CCC"},
        ],
    )

    check = scientific._check_candidate_identity_closed_loop([generated, downstream])

    assert check["status"] == "failed"
    assert check["reason"] == "unknown_downstream_candidate_id"
```

- [ ] **Step 2: 运行并确认 truth checks 尚不存在**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_real_acceptance_checks.py -q -p no:cacheprovider
```

Expected: FAIL，缺少两个 truth check。

- [ ] **Step 3: 实现并注册两个 truth checks**

规则必须为：

- target evidence 空且 generator 未执行、metadata 有 `skipped_precondition`：passed；
- target evidence 空但 generator 出现在 tool results：hard failed；
- generator 的所有 candidate 必须有非空唯一 ID；
- 下游 candidate ID 必须是生成 ID 的子集，不得出现 unknown；
- 对 required properties，若完全没有对齐记录则 failed；部分缺失为 partial；
- 检查只依赖结构化 tool results/metadata，不读取 LLM 自评文本。

Runner 对 `target_driven_design` 类别自动执行：

```text
target_driven_generation_gate
candidate_identity_closed_loop
```

并在报告注明 `automatic_truth_checks`。本阶段不修改 golden JSONL，避免把运行时基础真实性规则只绑定到单个数据文件。

- [ ] **Step 4: 运行所有聚焦 Agent 测试**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_candidate_contracts.py tests\agent\test_generated_candidate_validation.py tests\agent\test_candidate_alignment.py tests\agent\test_semantic_input_gates.py tests\agent\test_harness_legacy.py tests\agent\test_harness_shadow.py tests\agent\test_supervisor_harness_integration.py tests\agent\test_real_acceptance_checks.py -q -p no:cacheprovider
```

Expected: 全部 PASS。

- [ ] **Step 5: 运行 Agent 全量回归和编译检查**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py tests\agent\test_real_acceptance_checks.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
```

Expected: 所有测试 PASS；允许既有已知 warning，但不得新增 hard failure。

- [ ] **Step 6: 运行 legacy contract 验收**

```powershell
Remove-Item Env:AGENT_HARNESS_MODE -ErrorAction SilentlyContinue
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract
```

Expected: overall `passed`，默认报告无 shadow 执行结果。

- [ ] **Step 7: 安装可选依赖并运行 shadow contract 三次**

安装只修改本机环境，不写密钥：

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pip install -r requirements-agent-harness.txt
$env:AGENT_HARNESS_MODE = "shadow"
1..3 | ForEach-Object {
  C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract
}
```

Expected:

- 三次 contract 均 passed；
- 确定性计划 `matched=true`；
- 每个 fake/real 工具调用次数与 legacy 基线一致；
- shadow metadata 不包含 prompt、key 或绝对 artifact 内容。

- [ ] **Step 8: 运行真实 golden 验收三次并诚实记录依赖状态**

外部模型凭据只读取当前进程已有的 `OPENAI_COMPATIBLE_API_KEY`；不得在命令中写入字面量、不得打印其值。变量缺失时外部模型探针必须标记 skipped，而科学工具 truth checks 继续运行。

```powershell
$env:AGENT_HARNESS_MODE = "shadow"
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode real --repeat 3
```

Expected:

- 报告允许 `partial`；缺 RG-MPNN、靶点数据库记录或反向寻靶数据不得伪装成 passed；
- GOLD-008 无靶点证据时 generator 不执行，并以 partial 结束；
- Ollama/Vina 只有真实可用时才产生对应成功结果；
- 记录 shadow match rate 和 p50/p95 latency；
- 伪造科研结果数为 0。

- [ ] **Step 9: 测量 Shadow 热运行开销**

在 `tests/agent/test_harness_shadow.py` 增加不执行工具的 5 次 warmup + 100 次 plan simulation 基准测试，使用 `time.perf_counter_ns()`；断言 Windows MedChat 环境 p95 不超过 100 ms。使用 `@pytest.mark.skipif(os.getenv("MEDCHAT_RUN_PERF_TESTS") != "1", reason="performance test disabled")`，只测 simulator，不纳入普通全量测试的强制性能断言；通过 `MEDCHAT_RUN_PERF_TESTS=1` 显式启用：

```powershell
$env:MEDCHAT_RUN_PERF_TESTS = "1"
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_harness_shadow.py -q -p no:cacheprovider -k performance
```

Expected: p95 ≤ 100 ms；否则阶段状态为 partial，禁止进入 2B。

- [ ] **Step 10: 更新交接文档**

在 `docs/handoff/latest.md` 记录：

- 阶段 2A 目标和实际改动；
- 所有 commit；
- legacy/shadow 测试命令与真实结果；
- LangGraph/LangChain 固定版本；
- GOLD-008 是否成功阻断无证据生成；
- 候选对齐和第 11 条记录测试结果；
- shadow match rate、p50/p95、超时/失败类型；
- 真实依赖 unavailable/partial 原因；
- 明确声明尚未开放 `AGENT_HARNESS_MODE=langgraph`。

- [ ] **Step 11: 精确提交真实性检查和交接**

```powershell
git add -- src/agent/evaluation/scientific.py tests/agent/test_real_acceptance_checks.py tests/agent/test_harness_shadow.py docs/handoff/latest.md
git diff --cached --check
git commit -m "test(agent): verify shadow scientific trust gates"
```

- [ ] **Step 12: 最终工作树和提交范围审计**

```powershell
git status --short
git log --oneline --decorate -12
git diff main...HEAD --stat
git diff main...HEAD -- . ':!outputs' ':!scratch'
```

Expected: 工作树干净；没有 `.env`、key、token、outputs、数据库、模型、cache 或无关文件；所有新增提交仅属于阶段 2A。

## Completion criteria

- 默认 `legacy` 模式的公共行为、事件和响应保持兼容。
- 空靶点证据不能触发本地分子生成器。
- 所有有效候选有稳定 `candidate_id`，下游未知/额外记录不能进入结果。
- Shadow 不拥有或执行工具，真实工具调用次数不增加。
- Shadow 异常、超时或缺依赖不改变权威科学结果。
- 三轮真实验收无新增 hard failure，伪造科学结果为 0。
- 性能、差异、安全和真实依赖状态均写入脱敏报告与交接文档。
- `AGENT_HARNESS_MODE=langgraph` 仍不可接管执行。
