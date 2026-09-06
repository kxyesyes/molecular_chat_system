# MedChat Industrial Agent LangGraph Canary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 LangGraph 在严格 allowlist 和确定性 canary 下调度低风险真实科学工具，同时保持 legacy 默认、单次工具执行、科学门禁和一键回滚。

**Architecture:** 将 `WorkflowOrchestrator.run()` 的权威步骤语义提取为 `WorkflowRunSession`，legacy Python loop 和 LangGraph driver 共用同一 session。`CanaryHarness` 在工具调用前按 workflow allowlist、executor 能力和稳定 hash bucket 选择唯一权威后端；delegated specialist 入口在本批次保持 legacy control cohort。

**Tech Stack:** Python 3.10、dataclasses、LangGraph 0.2.76 `StateGraph`、现有 FastAPI Agent runtime、SQLiteAgentStateStore、pytest、真实 RDKit/Ollama/Vina 验收。

所有行为变更严格按 TDD 执行：先写可观测的失败契约，确认失败原因正确，再做最小实现并运行相关回归。

---

## File map

新增文件：

- `src/agent/runtime/run_session.py`：单请求权威工作流状态机，拥有 start/step/finish 生命周期。
- `src/agent/harness/canary.py`：allowlist、稳定 bucket 和权威后端选择。
- `src/agent/harness/langgraph_execution.py`：只通过 `WorkflowRunSession` 调度真实步骤的 LangGraph driver。
- `tests/agent/test_workflow_run_session.py`：session 生命周期、工具单次调用与 legacy 奇偶性。
- `tests/agent/test_harness_canary.py`：分桶、allowlist、delegated 回退和配置 fail-closed。
- `tests/agent/test_langgraph_execution_harness.py`：真实节点调度、事件、checkpoint 和异常边界。

修改文件：

- `src/agent/orchestrators/workflow.py`：保留公共 `run()`，改为创建并驱动 session。
- `src/agent/runtime/workflow_executor.py`：新增 `PreparedWorkflow`、`prepare()` 和可复用 session 准备边界。
- `src/agent/runtime/__init__.py`：导出 session 类型。
- `src/agent/harness/base.py`：新增脱敏权威执行 metadata。
- `src/agent/harness/factory.py`、`__init__.py`：注册 `langgraph_canary` 与安全回退。
- `src/agent/supervisor.py`：持久化 `harness_execution`，delegated executor 保持 legacy。
- `src/agent/evaluation/scientific.py`、`scripts/run_agent_acceptance.py`：记录 canary backend、工具尝试数和真实验收分布。
- `.env.example`、`docs/handoff/latest.md`：安全默认和阶段交接。

不修改前端、WebSocket 公共事件 schema、科学工具实现、SQLite schema 或真实科研资产。

### Task 1: 建立 `WorkflowRunSession` 生命周期契约

**Files:**
- Create: `src/agent/runtime/run_session.py`
- Modify: `src/agent/runtime/__init__.py`
- Modify: `src/agent/orchestrators/workflow.py`
- Test: `tests/agent/test_workflow_run_session.py`

- [ ] **Step 1: 写 session 单次生命周期和工具尝试失败测试**

```python
def test_session_executes_each_step_once_and_emits_one_terminal_event():
    tool = CountingTool("property_calculator")
    orchestrator = WorkflowOrchestrator(event_bus=AgentEventBus())
    session = orchestrator.create_session(
        context=AgentContext(query="CCO", trace_id="session-once"),
        steps=[WorkflowStep("properties", tool.name, output_key="properties")],
        tools={tool.name: tool},
    )

    session.start()
    advance = session.execute_step(0)
    result = session.finish()

    assert advance.step_id == "properties"
    assert advance.terminal is True
    assert tool.calls == ["CCO"]
    assert [event.event.value for event in orchestrator.event_bus.events].count(
        "task_completed"
    ) == 1
    with pytest.raises(SessionLifecycleError, match="already executed"):
        session.execute_step(0)
    with pytest.raises(SessionLifecycleError, match="already finished"):
        session.finish()


def test_session_counts_attempt_before_tool_exception():
    session = build_session(tool=RaisingTool("property_calculator"))
    session.start()
    session.execute_step(0)
    assert session.tool_attempt_count == 1
```

- [ ] **Step 2: 运行测试并确认因 session API 不存在而失败**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_workflow_run_session.py -q -p no:cacheprovider
```

Expected: collection FAIL，提示无法导入 `WorkflowRunSession` 或 `create_session`。

- [ ] **Step 3: 实现 session 公共类型和生命周期守卫**

`src/agent/runtime/run_session.py` 建立以下类型：

```python
@dataclass(frozen=True)
class StepAdvance:
    step_id: str
    outcome: str
    terminal: bool
    reason: str | None = None


class SessionLifecycleError(RuntimeError):
    pass


class WorkflowRunSession:
    def __init__(
        self,
        *,
        orchestrator: WorkflowOrchestrator,
        context: AgentContext,
        steps: list[WorkflowStep],
        tools: Mapping[str, Any],
        continue_on_error: bool = False,
        idempotency_key: str | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.context = context
        self.steps = list(steps)
        self.tools = dict(tools)
        self.continue_on_error = continue_on_error
        self.idempotency_key = idempotency_key
        self.tool_attempt_count = 0
        self._started = False
        self._finished = False
        self._executed_indexes: set[int] = set()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def finished(self) -> bool:
        return self._finished

    def fail_runtime(self, error_code: str) -> None:
        if self._finished:
            raise SessionLifecycleError("session already finished")
        self._runtime_error_code = error_code
        self._terminal = True
```

`start()` 初始化原 `run()` 中的 context、`WorkflowState`、Evidence Ledger、results、outputs 和记录列表，并原样发布 task/planning 事件。`execute_step()` 在进入 `execute_tool_compat` 前增加 `tool_attempt_count`，并将原 for-loop 单步逻辑原样迁入。`fail_runtime()` 使 `finish()` 产生 `AgentErrorCode.INTERNAL_ERROR`、`RunOutcome.FAILED` 和唯一 `task_failed` 事件，不将 graph 异常变成成功文本。`finish()` 迁入 AgentResult、terminal event 和 run status 逻辑。

- [ ] **Step 4: 为 orchestrator 增加窄 `create_session()` 工厂并导出类型**

```python
def create_session(self, *, context, steps, tools, continue_on_error=False,
                   idempotency_key=None):
    from src.agent.runtime.run_session import WorkflowRunSession
    return WorkflowRunSession(
        orchestrator=self,
        context=context,
        steps=steps,
        tools=tools,
        continue_on_error=continue_on_error,
        idempotency_key=idempotency_key,
    )
```

- [ ] **Step 5: 运行 session 测试和原 orchestrator 聚焦回归**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_workflow_run_session.py tests\agent\test_workflow_orchestrator.py tests\agent\test_workflow_resume.py -q -p no:cacheprovider
```

Expected: PASS；工具调用数、checkpoint、候选对齐和终态事件无变化。

- [ ] **Step 6: 精确提交**

```powershell
git add -- src/agent/runtime/run_session.py src/agent/runtime/__init__.py src/agent/orchestrators/workflow.py tests/agent/test_workflow_run_session.py
git commit -m "refactor(agent): extract workflow run session"
```

### Task 2: 让 legacy 循环完全驱动 session

**Files:**
- Modify: `src/agent/orchestrators/workflow.py`
- Modify: `tests/agent/test_workflow_run_session.py`
- Test: `tests/agent/test_workflow_orchestrator.py`
- Test: `tests/agent/test_workflow_resume.py`

- [ ] **Step 1: 写 legacy 与手动 session 奇偶性测试**

```python
def test_legacy_run_matches_manual_session_driver():
    legacy_tool = CountingTool("property_calculator")
    session_tool = CountingTool("property_calculator")
    steps = [WorkflowStep("properties", "property_calculator", output_key="p")]

    legacy = WorkflowOrchestrator(event_bus=AgentEventBus())
    legacy_result = legacy.run(
        AgentContext(query="CCO", trace_id="legacy"),
        steps,
        {"property_calculator": legacy_tool},
    )

    driven = WorkflowOrchestrator(event_bus=AgentEventBus())
    session = driven.create_session(
        context=AgentContext(query="CCO", trace_id="driven"),
        steps=steps,
        tools={"property_calculator": session_tool},
    )
    session.start()
    session.execute_step(0)
    driven_result = session.finish()

    assert normalize_result(legacy_result) == normalize_result(driven_result)
    assert normalize_events(legacy.event_bus.events) == normalize_events(
        driven.event_bus.events
    )
    assert legacy_tool.calls == session_tool.calls == ["CCO"]
```

- [ ] **Step 2: 运行测试并确认它能捕获两套驱动差异**

Run: Task 1 Step 5 命令。

Expected: 在 `run()` 仍保留旧循环时，如存在事件或 metadata 差异则 FAIL；完全一致时进入下一步。

- [ ] **Step 3: 将 `WorkflowOrchestrator.run()` 改为唯一 legacy loop driver**

```python
def run(self, context, steps, tools, continue_on_error=False,
        idempotency_key=None):
    session = self.create_session(
        context=context,
        steps=steps,
        tools=tools,
        continue_on_error=continue_on_error,
        idempotency_key=idempotency_key,
    )
    session.start()
    for index in range(len(steps)):
        advance = session.execute_step(index)
        if advance.terminal:
            break
    return session.finish()
```

删除 `run()` 中已迁入 session 的重复步骤代码；保留 orchestrator 的绑定、checkpoint、持久化和格式化 helper，由 session 调用。

- [ ] **Step 4: 运行全部 orchestrator、resume、candidate 和 event 回归**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_workflow_orchestrator.py tests\agent\test_workflow_resume.py tests\agent\test_candidate_alignment.py tests\agent\test_agent_event_stream.py tests\agent\test_workflow_run_session.py -q -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 5: 精确提交**

```powershell
git add -- src/agent/orchestrators/workflow.py tests/agent/test_workflow_run_session.py
git commit -m "refactor(agent): drive legacy through run session"
```

### Task 3: 建立 `PreparedWorkflow` 与单一 preflight 边界

**Files:**
- Modify: `src/agent/runtime/workflow_executor.py`
- Modify: `src/agent/runtime/__init__.py`
- Modify: `tests/agent/test_workflow_executor.py`

- [ ] **Step 1: 写 prepare 结果、编译计划和 preflight 失败测试**

```python
def test_prepare_returns_one_compiled_authorized_request():
    executor = WorkflowExecutor(planner=SingleStepPlanner())
    prepared = executor.prepare(
        context=AgentContext(query="CCO", trace_id="prepared"),
        policy=FAKE_POLICY,
        all_tools={"property_calculator": FakeTool("property_calculator")},
    )
    assert isinstance(prepared, PreparedWorkflow)
    assert prepared.plan.workflow_name == FAKE_POLICY.name
    assert prepared.compiled.dependencies == {"properties": ()}
    assert set(prepared.tools) == {"property_calculator"}


def test_prepare_returns_existing_preflight_execution_without_session():
    prepared = WorkflowExecutor(planner=UnauthorizedPlanner()).prepare(
        context=AgentContext(query="CCO", trace_id="rejected"),
        policy=EMPTY_POLICY,
        all_tools={},
    )
    assert isinstance(prepared, WorkflowExecution)
    assert prepared.result.error.code == AgentErrorCode.UNAUTHORIZED_TOOL
```

- [ ] **Step 2: 运行测试并确认因 API 不存在而失败**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_workflow_executor.py -q -p no:cacheprovider
```

Expected: FAIL，缺少 `PreparedWorkflow` 或 `prepare()`。

- [ ] **Step 3: 实现不可变准备契约和 `prepare()`**

```python
@dataclass(frozen=True)
class PreparedWorkflow:
    context: AgentContext
    policy: WorkflowPolicy
    plan: WorkflowPlan
    compiled: CompiledPlan
    tools: Mapping[str, Any]
    orchestrator: WorkflowOrchestrator
    event_bus: AgentEventBus
    idempotency_key: str | None = None

    def create_session(self) -> WorkflowRunSession:
        return self.orchestrator.create_session(
            context=self.context,
            steps=self.plan.steps,
            tools=self.tools,
            continue_on_error=False,
            idempotency_key=self.idempotency_key,
        )

    def to_execution(self, result: AgentResult) -> WorkflowExecution:
        return WorkflowExecution(
            plan=self.plan,
            result=result,
            events=[event.to_dict() for event in self.event_bus.events],
        )
```

`prepare()` 只调用一次 planner/compiler，复用现有 unauthorized/missing 错误格式，创建 request-scoped EventBus 与 orchestrator。

- [ ] **Step 4: 使 `WorkflowExecutor.execute()` 消费 prepare 结果但仍调用 `orchestrator.run()`**

```python
prepared = self.prepare(
    context=context,
    policy=policy,
    all_tools=all_tools,
    event_callback=event_callback,
    idempotency_key=idempotency_key,
    plan=plan,
)
if isinstance(prepared, WorkflowExecution):
    return prepared
result = prepared.orchestrator.run(
    context=prepared.context,
    steps=prepared.plan.steps,
    tools=prepared.tools,
    continue_on_error=False,
    idempotency_key=prepared.idempotency_key,
)
return prepared.to_execution(result)
```

这一路径保留 injected orchestrator subclass 对 `run()` 的兼容行为。

- [ ] **Step 5: 运行 executor、concurrency 和 subclass 回归**

Run: Step 2 命令。

Expected: PASS，包括 `test_executor_preserves_injected_orchestrator_subclass_extensions`。

- [ ] **Step 6: 精确提交**

```powershell
git add -- src/agent/runtime/workflow_executor.py src/agent/runtime/__init__.py tests/agent/test_workflow_executor.py
git commit -m "feat(agent): prepare workflows for harness drivers"
```

### Task 4: 实现安全 allowlist 与稳定 canary 分桶

**Files:**
- Create: `src/agent/harness/canary.py`
- Create: `tests/agent/test_harness_canary.py`
- Modify: `src/agent/harness/__init__.py`

- [ ] **Step 1: 写 0/100%、非法配置、allowlist 和凭据不入 metadata 测试**

```python
def test_selector_is_stable_and_never_exposes_idempotency_key():
    selector = LangGraphCanarySelector(percent=100)
    first = selector.select(
        workflow_name="admet_assessment",
        trace_id="trace-a",
        idempotency_key="private-request-key",
        executor_supported=True,
    )
    second = selector.select(
        workflow_name="admet_assessment",
        trace_id="trace-b",
        idempotency_key="private-request-key",
        executor_supported=True,
    )
    assert first.backend == second.backend == "langgraph"
    assert first.bucket == second.bucket
    assert "private-request-key" not in json.dumps(first.to_dict())


@pytest.mark.parametrize(
    ("percent", "workflow", "supported", "backend", "reason"),
    [
        (0, "admet_assessment", True, "legacy", "canary_not_selected"),
        (100, "admet_assessment", True, "langgraph", "canary_selected"),
        (100, "docking_simulation", True, "legacy", "workflow_not_allowlisted"),
        (100, "admet_assessment", False, "legacy", "unsupported_delegated_executor"),
    ],
)
def test_selector_fails_closed(percent, workflow, supported, backend, reason):
    decision = LangGraphCanarySelector(percent=percent).select(
        workflow_name=workflow,
        trace_id="trace",
        idempotency_key=None,
        executor_supported=supported,
    )
    assert (decision.backend, decision.reason) == (backend, reason)
```

- [ ] **Step 2: 运行测试并确认因 selector 不存在而失败**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_harness_canary.py -q -p no:cacheprovider
```

Expected: collection FAIL。

- [ ] **Step 3: 实现决策契约与固定 allowlist**

```python
LANGGRAPH_CANARY_WORKFLOWS = frozenset({
    "admet_assessment",
    "activity_prediction",
    "reverse_target_prediction",
    "target_database_search",
    "rag_search",
})


@dataclass(frozen=True)
class CanaryDecision:
    backend: str
    reason: str
    bucket: int | None
    percent: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

Bucket 使用 `sha256(idempotency_key or trace_id)` 的前 8 个 hex 取模 100，不保存原文。Percent 非整数或超界时收敛为 0 并记录 `invalid_canary_percent`。

- [ ] **Step 4: 运行 selector 测试和凭据形状扫描**

Run: Step 2 命令。

Expected: PASS；序列化决策不包含 idempotency key。

- [ ] **Step 5: 精确提交**

```powershell
git add -- src/agent/harness/canary.py src/agent/harness/__init__.py tests/agent/test_harness_canary.py
git commit -m "feat(agent): add deterministic langgraph canary selector"
```

### Task 5: 实现 LangGraph 真实步骤 driver

**Files:**
- Create: `src/agent/harness/langgraph_execution.py`
- Create: `tests/agent/test_langgraph_execution_harness.py`
- Modify: `src/agent/harness/base.py`
- Modify: `src/agent/harness/__init__.py`

- [ ] **Step 1: 写单步、多步和工具单次执行测试**

```python
def test_langgraph_driver_executes_real_steps_once_in_plan_order():
    first = CountingTool("property_calculator")
    second = CountingTool("admet_predictor")
    run = LangGraphExecutionHarness(WorkflowExecutor()).execute(
        context=AgentContext(
            query="CCO", trace_id="canary-order", active_skill="admet_assessment"
        ),
        policy=ADMET_POLICY,
        all_tools={first.name: first, second.name: second},
        plan=two_step_plan(),
    )
    assert [item.tool_name for item in run.authoritative.result.tool_results] == [
        first.name,
        second.name,
    ]
    assert first.calls == ["CCO"]
    assert second.calls == ["CCO"]
    assert run.execution.backend == "langgraph"
    assert run.execution.tool_attempt_count == 2
```

- [ ] **Step 2: 写 graph 重入、工具前回退和工具后禁止回退测试**

```python
def test_graph_failure_after_tool_attempt_never_runs_legacy():
    tool = CountingTool("property_calculator")
    harness = LangGraphExecutionHarness(
        WorkflowExecutor(),
        graph_runner=RaiseAfterFirstNode(),
    )
    run = harness.execute(
        context=allowed_context("after-tool"),
        policy=ADMET_POLICY,
        all_tools={tool.name: tool},
        plan=one_step_plan(),
    )
    assert tool.calls == ["CCO"]
    assert run.authoritative.result.success is False
    assert run.execution.fallback_before_execution is False
    assert run.execution.error_code == "langgraph_runtime_after_tool"


def test_graph_failure_before_tool_uses_same_session_legacy_driver():
    tool = CountingTool("property_calculator")
    run = LangGraphExecutionHarness(
        WorkflowExecutor(), graph_runner=RaiseBeforeFirstNode()
    ).execute(
        context=allowed_context("before-tool"),
        policy=ADMET_POLICY,
        all_tools={tool.name: tool},
        plan=one_step_plan(),
    )
    assert tool.calls == ["CCO"]
    assert run.authoritative.result.success is True
    assert run.execution.fallback_before_execution is True
```

- [ ] **Step 3: 运行测试并确认因 driver 不存在而失败**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_langgraph_execution_harness.py -q -p no:cacheprovider
```

Expected: collection FAIL。

- [ ] **Step 4: 扩展 `HarnessRun` 的脱敏权威执行契约**

```python
@dataclass(frozen=True)
class HarnessExecutionMetadata:
    backend: str
    backend_version: str
    selection_reason: str
    canary_bucket: int | None
    plan_fingerprint: str
    tool_attempt_count: int
    fallback_before_execution: bool
    elapsed_ms: int
    error_code: str | None = None


@dataclass(frozen=True)
class HarnessRun:
    authoritative: WorkflowExecution
    shadow: ShadowComparison | None = None
    execution: HarnessExecutionMetadata | None = None
```

- [ ] **Step 5: 实现 StateGraph driver 和安全 graph state**

```python
class CanaryGraphState(TypedDict):
    visited_steps: Annotated[list[str], operator.add]
    step_outcomes: Annotated[list[dict[str, str]], operator.add]
    terminal: bool
    terminal_reason: str
```

每个 node 只调用 `session.execute_step(index)`；conditional edge 在 `advance.terminal` 时进入 `END`。Graph 编译在 `session.start()` 前完成。工具前 graph 异常调用同 session 的 Python loop；工具后异常调用 `session.fail_runtime()` 然后 `finish()`，不创建第二个 executor。

- [ ] **Step 6: 运行 LangGraph driver、session 和事件回归**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_langgraph_execution_harness.py tests\agent\test_workflow_run_session.py tests\agent\test_agent_event_stream.py -q -p no:cacheprovider
```

Expected: PASS；每个工具调用次数为 1，每个 run 只有一个 terminal event。

- [ ] **Step 7: 精确提交**

```powershell
git add -- src/agent/harness/base.py src/agent/harness/langgraph_execution.py src/agent/harness/__init__.py tests/agent/test_langgraph_execution_harness.py
git commit -m "feat(agent): execute allowlisted workflows with langgraph"
```

### Task 6: 将 canary selector 接入 Factory 和 Supervisor

**Files:**
- Modify: `src/agent/harness/canary.py`
- Modify: `src/agent/harness/factory.py`
- Modify: `src/agent/supervisor.py`
- Modify: `.env.example`
- Modify: `tests/agent/test_harness_canary.py`
- Modify: `tests/agent/test_supervisor_harness_integration.py`

- [ ] **Step 1: 写 factory 配置、delegated control cohort 和 metadata 持久化测试**

```python
def test_factory_canary_executes_allowlisted_direct_workflow(monkeypatch):
    monkeypatch.setenv("AGENT_HARNESS_MODE", "langgraph_canary")
    monkeypatch.setenv("AGENT_LANGGRAPH_CANARY_PERCENT", "100")
    supervisor, tool = build_supervisor()
    response = supervisor.execute("properties for CCO", active_skill="admet_assessment")
    metadata = response["agent_result"].metadata["harness_execution"]
    assert metadata["backend"] == "langgraph"
    assert metadata["selection_reason"] == "canary_selected"
    assert tool.calls == 1


def test_delegated_supervisor_stays_legacy_at_full_canary(monkeypatch):
    monkeypatch.setenv("AGENT_HARNESS_MODE", "langgraph_canary")
    monkeypatch.setenv("AGENT_LANGGRAPH_CANARY_PERCENT", "100")
    supervisor, tool = build_delegated_supervisor()
    response = supervisor.run("properties for CCO", skill_name="admet_assessment")
    assert response["result"]["metadata"]["harness_execution"]["backend"] == "legacy"
    assert response["result"]["metadata"]["harness_execution"][
        "selection_reason"
    ] == "unsupported_delegated_executor"
    assert tool.calls == 1
```

- [ ] **Step 2: 运行测试并确认 factory 仍拒绝真实 LangGraph 模式**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_harness_canary.py tests\agent\test_supervisor_harness_integration.py -q -p no:cacheprovider
```

Expected: 新 canary 断言 FAIL，旧 legacy/shadow 断言仍 PASS。

- [ ] **Step 3: 实现 `CanaryHarness` 路由器并在 Factory 注册**

```python
class CanaryHarness:
    def execute(self, **kwargs):
        decision = self.selector.select(
            workflow_name=kwargs["policy"].name,
            trace_id=kwargs["context"].trace_id,
            idempotency_key=kwargs.get("idempotency_key"),
            executor_supported=hasattr(self.executor, "prepare"),
        )
        if decision.backend == "langgraph":
            return self.langgraph.execute(selection=decision, **kwargs)
        run = self.legacy.execute(**kwargs)
        return replace(run, execution=legacy_metadata(decision, run.authoritative.plan))
```

`HarnessFactory` 只在 mode 为 `langgraph_canary` 且依赖可用时返回路由器。已有 `langgraph` 模式仍禁止，legacy/shadow 保持不变。

- [ ] **Step 4: 让 Supervisor 脱敏持久化 `harness_execution`**

`_execute_with_harness()` 将 `harness_run.execution.to_dict()` 写入 `AgentResult.metadata`，与 shadow 使用同一 `update_run_metadata()` 浅合并路径。持久化失败只追加 `harness_metadata_persistence_failed` warning，不改变 result status。

- [ ] **Step 5: 在 `.env.example` 增加 fail-closed 默认**

```dotenv
AGENT_HARNESS_MODE=legacy
AGENT_LANGGRAPH_CANARY_PERCENT=0
```

- [ ] **Step 6: 运行 factory、Supervisor、持久化和 delegated 回归**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_harness_canary.py tests\agent\test_supervisor_harness_integration.py tests\agent\test_agent_persistence.py tests\agent\test_supervisor_delegation.py -q -p no:cacheprovider
```

Expected: PASS。

- [ ] **Step 7: 精确提交**

```powershell
git add -- .env.example src/agent/harness/canary.py src/agent/harness/factory.py src/agent/supervisor.py tests/agent/test_harness_canary.py tests/agent/test_supervisor_harness_integration.py
git commit -m "feat(agent): route safe workflows through canary"
```

### Task 7: 扩展验收报告与发布门禁

**Files:**
- Modify: `src/agent/evaluation/scientific.py`
- Modify: `scripts/run_agent_acceptance.py`
- Modify: `tests/agent/test_real_acceptance_checks.py`
- Modify: `tests/agent/test_evaluation_runner.py`

- [ ] **Step 1: 写 canary 报告完整性、工具次数和凭据隔离测试**

```python
def test_scientific_report_records_canary_without_sensitive_inputs(tmp_path):
    report = run_single_case_with_canary(
        tmp_path,
        prompt="private prompt CCO",
        idempotency_key="private-key",
    )
    metadata = report["harness_execution"]
    assert metadata["backend"] == "langgraph"
    assert metadata["tool_attempt_count"] == len(report["actual_tools"])
    serialized = json.dumps(report)
    assert "private prompt CCO" not in serialized
    assert "private-key" not in serialized


def test_contract_canary_probe_keeps_legacy_case_count_and_one_tool_call():
    report = run_contract()
    assert report["metrics"]["case_count"] == 34
    assert report["harness_canary"]["authoritative_tool_calls"] == 1
    assert report["harness_canary"]["backend"] == "langgraph"
```

- [ ] **Step 2: 运行聚焦测试并确认因报告缺少字段而失败**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_real_acceptance_checks.py tests\agent\test_evaluation_runner.py -q -p no:cacheprovider
```

Expected: 新 canary 断言 FAIL。

- [ ] **Step 3: 在真实 runner 记录脱敏 `harness_execution`**

从 `execution.result.metadata` 读取执行 metadata；重复案例输出 `runs`，单次案例输出对象。增加确定性检查：`backend` 必须是 legacy/langgraph，`tool_attempt_count` 不得超过实际未复用工具数，metadata 必须通过 `redact_sensitive()`。

- [ ] **Step 4: 扩展 contract harness 探针但不改变 34 个 legacy case**

`run_contract()` 在 `AGENT_HARNESS_MODE=langgraph_canary` 时额外运行一个合成 `admet_assessment` 计划，验证 backend、plan fingerprint、tool attempt count 和权威工具调用次数。这一探针不加入 `metrics.case_count`。

- [ ] **Step 5: 运行验收单元测试和 contract**

```powershell
$env:AGENT_HARNESS_MODE='langgraph_canary'
$env:AGENT_LANGGRAPH_CANARY_PERCENT='100'
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_real_acceptance_checks.py tests\agent\test_evaluation_runner.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract --output outputs\agent_evaluation\agent_acceptance_canary_contract.json
```

Expected: pytest PASS；contract status `passed`，34/34 legacy cases 保持，canary probe 工具调用次数为 1。

- [ ] **Step 6: 精确提交**

```powershell
git add -- src/agent/evaluation/scientific.py scripts/run_agent_acceptance.py tests/agent/test_real_acceptance_checks.py tests/agent/test_evaluation_runner.py
git commit -m "test(agent): gate langgraph canary releases"
```

### Task 8: 全量回归、真实 Canary 验收与交接

**Files:**
- Modify: `docs/handoff/latest.md`
- Modify only if a verified defect requires it: files from Tasks 1–7

- [ ] **Step 1: 运行默认 legacy 全量 Agent 和安全回归**

```powershell
Remove-Item Env:AGENT_HARNESS_MODE -ErrorAction SilentlyContinue
Remove-Item Env:AGENT_LANGGRAPH_CANARY_PERCENT -ErrorAction SilentlyContinue
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py tests\agent\test_real_acceptance_checks.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
```

Expected: 无新失败；只允许已知 SWIG/FastAPI 弃用 warning 和显式性能 skip。

- [ ] **Step 2: 运行 100% 测试 canary 全量 Agent 回归**

```powershell
$env:AGENT_HARNESS_MODE='langgraph_canary'
$env:AGENT_LANGGRAPH_CANARY_PERCENT='100'
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract --output outputs\agent_evaluation\agent_acceptance_canary_contract.json
```

Expected: 无新失败；allowlist 探针为 langgraph，delegated 测试仍为 legacy。

- [ ] **Step 3: 运行真实 golden 案例三轮**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode real --case-set golden --repeat 3 --output outputs\agent_evaluation\agent_acceptance_canary_real.json
```

Expected: 无新 hard failure；GOLD-001/002 等 allowlist 低风险案例记录 langgraph backend；非 allowlist 案例仍为 legacy；现有靶点/RG-MPNN/反向寻靶依赖缺失继续如实 partial。

- [ ] **Step 4: 运行性能、工具单次和凭据扫描**

```powershell
$env:MEDCHAT_RUN_PERF_TESTS='1'
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent\test_harness_shadow.py tests\agent\test_langgraph_execution_harness.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pip check
git diff --check
```

Expected: 性能阈值 PASS；`pip check` 无损坏依赖；本阶段变更中凭据形状命中为 0。

- [ ] **Step 5: 更新交接文档**

`docs/handoff/latest.md` 记录分支、提交、allowlist、canary 分布、工具调用次数、事件完整性、真实案例状态、p50/p95、无凭据泄漏结论、已知 partial 原因和一键 legacy 回滚方式。

- [ ] **Step 6: 精确提交收尾**

```powershell
git add -- docs/handoff/latest.md
git commit -m "docs(agent): hand off langgraph canary stage"
git status --short
```

Expected: 工作树干净，未提交 `outputs/`、凭据、本地数据库或科研计算产物。

## 中止与回滚条件

任一以下情况发生时不得扩大 canary，立即保持或恢复 `AGENT_HARNESS_MODE=legacy`：

- 同一 step 的工具调用次数大于 1；
- LangGraph 工具后异常触发 legacy 重跑；
- ToolResult、Evidence Ledger、CandidateSet、provenance 或 terminal event 丢失；
- 出现伪 pIC50、伪 binding energy、demo/fallback 冒充真实或无证据 claim；
- API key、Authorization、完整 prompt 或绝对机器路径进入 metadata/日志/报告；
- 默认 legacy 回归出现新失败；
- 三轮真实验收出现相对 2A 基线的新 hard failure。
