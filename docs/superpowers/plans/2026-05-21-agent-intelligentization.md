# MedChat Agent 智能化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 MedChat 从“多子功能 + 规则触发工具”升级为稳定、可观测、可扩展的药物设计 Agent 编排平台。

**Architecture:** 保留现有 `SkillRouter + ReActMolecularAgent + Tool` 基础，不推翻重来。新增统一契约层、执行编排层、任务追踪层，把分子设计、分子对接、反向寻靶、靶点搜索、活性预测、ADMET、RAG 逐步纳入同一套 Agent 运行协议。

**Tech Stack:** FastAPI, WebSocket, Python 3.10+, Pydantic, RDKit, AutoDock Vina, SQLite, Ollama/ModelScope, pytest, Node static checks.

---

## 1. 当前架构判断

项目已经具备 Agent 雏形，不需要从零重写：

- `src/agent/react_agent.py` 已经负责 ReAct 执行、工具选择、结果聚合。
- `src/agent/router.py` 和 `src/agent/skills/skill_registry.py` 已经负责规则路由和 LLM 语义路由。
- `src/agent/skills/*.py` 已经把业务能力拆成分子设计、活性预测、反向寻靶、靶点搜索、ADMET、分子对接、RAG 等 Skill。
- `src/agent/tools/*.py` 已经封装了属性计算、分子生成、对接、反向寻靶、靶点数据库等工具。
- `src/web/chat_handler.py` 已经把 WebSocket 用户消息接入 Agent 和 RAG。

主要问题不在“有没有 Agent”，而在“Agent 工程契约不够硬”：

- 工具返回结构不统一，前端和主回答生成时容易出现特判。
- 错误、超时、部分结果、空结果没有统一协议。
- Web API 直调服务与 Agent Tool 调服务之间存在重复路径。
- 长任务缺少统一任务状态和 trace_id。
- 模型后端、工具依赖和执行策略散落在多个文件里。
- 当前日志里已经出现过 `Bearer ` 空 key、模型名混乱、长任务 timeout、外部工具不可用等典型生产化问题。

## 2. 目标架构

建议目标分层：

```text
Web / Page / API
    |
Agent Gateway
    |
Intent Router
    |
Workflow Orchestrator
    |
Domain Skills
    |
Tool Adapters
    |
Domain Services / External Engines / Local Databases
```

职责边界：

- Web 层只负责输入、状态流、渲染和下载，不写业务判断。
- Agent Gateway 负责创建上下文、trace_id、用户配置、模型选择。
- Intent Router 负责选择 Skill，不直接执行业务。
- Workflow Orchestrator 负责多工具步骤、超时、重试、部分结果。
- Domain Skill 负责“怎么完成任务”的流程描述。
- Tool Adapter 负责把 RDKit、Vina、数据库、模型调用包装成稳定接口。
- Domain Service 保留现有业务能力，继续支持页面直接调用。

## 3. 文件结构规划

新增：

- `src/agent/contracts/__init__.py`：导出统一契约对象。
- `src/agent/contracts/context.py`：定义 `AgentContext`。
- `src/agent/contracts/result.py`：定义 `ToolResult`、`AgentResult`。
- `src/agent/contracts/errors.py`：定义 `AgentErrorCode`、`AgentExecutionError`。
- `src/agent/orchestrators/__init__.py`：导出编排器。
- `src/agent/orchestrators/base.py`：定义编排器基类。
- `src/agent/orchestrators/workflow.py`：实现通用工作流编排。
- `src/agent/runtime/__init__.py`：导出运行时组件。
- `src/agent/runtime/task_state.py`：定义任务状态、进度和部分结果。
- `src/agent/runtime/limits.py`：统一超时、并发、重试配置。
- `tests/agent/test_contracts.py`：契约层单元测试。
- `tests/agent/test_workflow_orchestrator.py`：编排层测试。
- `docs/agent_intelligentization_plan.md`：面向汇报的精简版路线图，可由本计划提炼。

修改：

- `src/agent/react_agent.py`：逐步改为使用 `AgentContext` 和 `AgentResult`。
- `src/agent/tools/base_tool.py`：新增标准工具执行协议，保留旧工具兼容。
- `src/agent/tools/__init__.py`：注册工具时声明工具能力、超时、是否长任务。
- `src/web/chat_handler.py`：接收 Agent 标准状态事件，减少字符串特判。
- `src/web/routes/api_routes.py`：长任务 API 逐步返回标准错误和任务状态。
- `scripts/health_check.py`：增加 Agent contract、工具注册、模型后端检查。

## 4. 分阶段实施

### Task 1: 统一 Agent 契约层

**Files:**

- Create: `src/agent/contracts/context.py`
- Create: `src/agent/contracts/result.py`
- Create: `src/agent/contracts/errors.py`
- Create: `src/agent/contracts/__init__.py`
- Create: `tests/agent/test_contracts.py`

- [ ] **Step 1: 创建失败测试**

测试目标：

```python
def test_tool_result_success_payload():
    result = ToolResult.success_result(
        tool_name="property_calculator",
        data={"qed": 0.72},
        message="属性计算完成",
    )
    assert result.success is True
    assert result.tool_name == "property_calculator"
    assert result.error is None
    assert result.data["qed"] == 0.72


def test_tool_result_error_payload():
    result = ToolResult.error_result(
        tool_name="molecular_docking",
        code=AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
        message="Vina 不可用",
    )
    assert result.success is False
    assert result.error.code == AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE
    assert result.data is None
```

Run:

```bash
pytest tests/agent/test_contracts.py -v
```

Expected: FAIL because contracts do not exist yet.

- [ ] **Step 2: 实现契约类**

核心字段：

```python
@dataclass
class AgentContext:
    query: str
    trace_id: str
    user_id: str | None = None
    active_skill: str | None = None
    temperature: float = 0.7
    mol_count: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)
```

```python
class AgentErrorCode(str, Enum):
    VALIDATION_ERROR = "validation_error"
    TOOL_TIMEOUT = "tool_timeout"
    EXTERNAL_TOOL_UNAVAILABLE = "external_tool_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    EMPTY_RESULT = "empty_result"
    INTERNAL_ERROR = "internal_error"
```

```python
@dataclass
class ToolResult:
    tool_name: str
    success: bool
    message: str
    data: Any = None
    formatted: str = ""
    error: AgentExecutionError | None = None
    elapsed_ms: int | None = None
```

- [ ] **Step 3: 运行测试**

Run:

```bash
pytest tests/agent/test_contracts.py -v
```

Expected: PASS.

### Task 2: 给旧 Tool 增加兼容包装器

**Files:**

- Modify: `src/agent/tools/base_tool.py`
- Modify: `src/agent/tools/__init__.py`
- Create: `tests/agent/test_tool_adapter_compat.py`

- [ ] **Step 1: 为旧工具输出写兼容测试**

测试目标：

```python
class DummyLegacyTool:
    name = "dummy"

    def execute(self, query):
        return {"success": True, "message": "ok", "data": {"value": 1}, "formatted": "OK"}


def test_legacy_tool_is_wrapped_as_tool_result():
    wrapped = execute_tool_compat(DummyLegacyTool(), "CCO")
    assert wrapped.success is True
    assert wrapped.tool_name == "dummy"
    assert wrapped.data == {"value": 1}
```

- [ ] **Step 2: 实现 `execute_tool_compat`**

要求：

- 旧工具返回 dict 时转换成 `ToolResult`。
- 旧工具抛异常时转换为 `INTERNAL_ERROR`。
- 记录耗时 `elapsed_ms`。
- 不改变现有工具类构造方式。

- [ ] **Step 3: 运行测试**

Run:

```bash
pytest tests/agent/test_tool_adapter_compat.py -v
```

Expected: PASS.

### Task 3: 建立通用 Workflow Orchestrator

**Files:**

- Create: `src/agent/orchestrators/base.py`
- Create: `src/agent/orchestrators/workflow.py`
- Create: `src/agent/orchestrators/__init__.py`
- Create: `tests/agent/test_workflow_orchestrator.py`

- [ ] **Step 1: 写编排器测试**

测试目标：

```python
def test_workflow_runs_tools_in_order():
    context = AgentContext(query="分析 CCO", trace_id="test-trace")
    workflow = WorkflowOrchestrator()
    result = workflow.run(
        context=context,
        steps=[
            WorkflowStep(name="properties", tool_name="property_calculator", input_data="CCO"),
            WorkflowStep(name="admet", tool_name="admet_predictor", input_data="CCO"),
        ],
        tools={
            "property_calculator": FakeTool("property_calculator"),
            "admet_predictor": FakeTool("admet_predictor"),
        },
    )
    assert result.success is True
    assert [item.tool_name for item in result.tool_results] == ["property_calculator", "admet_predictor"]
```

- [ ] **Step 2: 实现编排器**

要求：

- 顺序执行工具。
- 任一工具失败时保留前序结果。
- 支持 `continue_on_error`。
- 输出 `AgentResult`。

- [ ] **Step 3: 运行测试**

Run:

```bash
pytest tests/agent/test_workflow_orchestrator.py -v
```

Expected: PASS.

### Task 4: 先接入综合评估和先导物优化两个工作流

**Files:**

- Modify: `src/agent/skills/comprehensive_evaluation_skill.py`
- Modify: `src/agent/skills/hit_to_lead_skill.py`
- Modify: `src/agent/react_agent.py`
- Create: `tests/agent/test_workflow_skills.py`

- [ ] **Step 1: 为 workflow skill 声明工作流步骤**

综合评估建议步骤：

```text
property_calculator -> admet_predictor -> drug_likeness_assessment -> reverse_target_prediction
```

先导物优化建议步骤：

```text
property_calculator -> llm_molecular_generator -> admet_predictor -> activity_prediction
```

- [ ] **Step 2: ReActAgent 检测到 workflow skill 时走 orchestrator**

要求：

- 原有 ReAct 循环保留。
- 只有声明 `workflow_steps` 的 Skill 进入 orchestrator。
- 非 workflow skill 行为不变。

- [ ] **Step 3: 测试路由和工具顺序**

Run:

```bash
pytest tests/agent/test_workflow_skills.py -v
```

Expected: PASS.

### Task 5: 长任务状态与部分结果

**Files:**

- Create: `src/agent/runtime/task_state.py`
- Create: `src/agent/runtime/limits.py`
- Modify: `src/web/chat_handler.py`
- Modify: `src/reverse_target/predictor.py`
- Modify: `src/reverse_target/pharmacophore_refiner.py`
- Create: `tests/agent/test_task_state.py`

- [ ] **Step 1: 定义任务事件**

事件类型：

```text
task_started
tool_started
tool_progress
partial_result
tool_completed
tool_failed
task_completed
task_failed
```

- [ ] **Step 2: 反向寻靶返回部分结果**

要求：

- 2D 召回完成后先发 `partial_result`。
- 3D 精修超时时保留已完成候选。
- 前端分页可继续展示已有结果。

- [ ] **Step 3: 测试超时不丢结果**

Run:

```bash
pytest tests/test_reverse_target_pharmacophore.py tests/agent/test_task_state.py -v
```

Expected: PASS.

### Task 6: 统一模型后端配置

**Files:**

- Modify: `config/settings.yaml`
- Modify: `.env.example`
- Modify: `src/web/app.py`
- Modify: `src/agent/tools/__init__.py`
- Modify: `src/agent/modelscope_model.py`
- Modify: `scripts/health_check.py`
- Create: `tests/agent/test_model_backend_config.py`

- [ ] **Step 1: 配置模型角色**

建议角色：

```yaml
models:
  chat:
    provider: ollama
    name: gmm-llama:latest
  molecule_generation:
    provider: ollama
    name: gmm-llama:latest
  semantic_router:
    provider: ollama
    name: gmm-llama:latest
  fallback:
    provider: none
```

- [ ] **Step 2: 空 API key 不再构造 `Bearer `**

要求：

- `MODELSCOPE_API_KEY` 为空时跳过 ModelScope 初始化。
- 日志明确输出“ModelScope 未配置，使用 Ollama 或本地回退”。

- [ ] **Step 3: 健康检查覆盖模型角色**

Run:

```bash
python scripts/health_check.py --strict
```

Expected: 模型服务可用时通过；未配置可选云模型时不阻塞本地部署。

### Task 7: 前端 Agent 状态协议收敛

**Files:**

- Modify: `src/web/chat_handler.py`
- Modify: `src/web/static/js/main.js`
- Modify: `src/web/static/js/mobile-adapter.js` if status rendering is shared
- Create: `tests/agent/test_websocket_agent_events.py`

- [ ] **Step 1: 标准化 WebSocket 消息**

统一字段：

```json
{
  "type": "agent_event",
  "trace_id": "xxx",
  "event": "tool_started",
  "skill": "reverse_target_prediction",
  "tool": "reverse_target_tool",
  "message": "正在执行反向寻靶",
  "progress": 0.35
}
```

- [ ] **Step 2: 前端只按 `event` 渲染**

要求：

- 删除散落的中文字符串状态特判。
- 保留旧消息类型兼容。

- [ ] **Step 3: 运行静态检查**

Run:

```bash
node --check src/web/static/js/main.js
```

Expected: no syntax errors.

### Task 8: Agent 生产化治理

**Files:**

- Modify: `src/agent/runtime/limits.py`
- Modify: `src/agent/react_agent.py`
- Modify: `src/docking/molecular_docking_service.py`
- Modify: `src/reverse_target/pharmacophore_refiner.py`
- Modify: `scripts/health_check.py`
- Create: `tests/agent/test_runtime_limits.py`

- [ ] **Step 1: 统一超时配置**

建议默认值：

```python
DEFAULT_LIMITS = {
    "llm_generate_seconds": 60,
    "tool_default_seconds": 30,
    "reverse_target_seconds": 120,
    "pharmacophore_single_candidate_seconds": 8,
    "docking_seconds": 180,
}
```

- [ ] **Step 2: 统一并发限制**

建议：

```python
reverse_target_concurrency = 2
docking_concurrency = 1
llm_concurrency = 2
```

- [ ] **Step 3: 测试超时和并发限制**

Run:

```bash
pytest tests/agent/test_runtime_limits.py -v
```

Expected: PASS.

## 5. 模块智能化优先级

建议顺序：

1. 靶点搜索：风险最低，最适合作为 Agent 契约试点。
2. 活性预测 / ADMET：输入输出清晰，适合标准 ToolResult。
3. 反向寻靶：重点解决长任务、部分结果、分页和证据解释。
4. 分子对接：重点解决外部二进制依赖、文件状态、队列与失败恢复。
5. 分子设计：最后接入多轮闭环，因为它牵涉生成、属性优化、历史状态和前端编辑器。

## 6. 验收标准

第一阶段验收：

- `pytest tests/agent -v` 通过。
- 主页聊天可继续调用分子生成。
- 靶点搜索页面和 API 不受影响。
- 反向寻靶可在超时时返回部分结果。
- `scripts/health_check.py --strict` 能检查 Agent contracts、工具注册、模型角色。

第二阶段验收：

- 用户输入“帮我综合评估 CCO”时，Agent 能依次调用属性、ADMET、类药性工具。
- 用户输入“这个分子可能作用哪些靶点”时，Agent 能触发反向寻靶并分页展示。
- 用户输入“找 EGFR 的结构并准备对接”时，Agent 能触发靶点搜索并返回推荐结构。
- 所有失败都以结构化中文错误返回，不出现浏览器弹窗式裸错误。

第三阶段验收：

- 一个复杂任务可以跨模块执行，例如：

```text
生成 5 个更适合 EGFR 的候选分子，筛掉 ADMET 差的，再给我推荐可用于对接的蛋白结构。
```

期望链路：

```text
target_database_search -> llm_molecular_generator -> admet_predictor -> property_calculator -> docking_preparation
```

## 7. 风险与处理

- 风险：一次性重构 `react_agent.py` 会影响主聊天。
  处理：先增加兼容层，旧执行路径保留，workflow skill 分批接入。

- 风险：不同模块返回字段差异大。
  处理：不要强行统一业务字段，只统一 envelope，即 `success/message/data/error/elapsed_ms`。

- 风险：长任务阻塞 WebSocket。
  处理：把反向寻靶和分子对接优先改为任务事件流。

- 风险：模型不可用导致 Agent 失效。
  处理：规则路由和本地工具必须能独立运行，LLM 只增强推理和解释。

- 风险：Windows 与 Linux 路径差异。
  处理：继续把路径放入 `.env` / YAML，并在 health check 中验证。

## 8. 推荐执行节奏

建议每次只完成一个任务并验证：

```bash
pytest tests/agent/test_contracts.py -v
pytest tests/agent/test_tool_adapter_compat.py -v
pytest tests/agent/test_workflow_orchestrator.py -v
python scripts/health_check.py --strict
```

每个阶段完成后再打开以下页面回归：

```text
/
/molecular-design
/molecular-docking
/reverse-target
/target-search
/activity-prediction
```

## 9. 下一步建议

下一步先执行 Task 1 和 Task 2。它们对现有业务影响最小，但会为后面所有 Agent 智能化打地基：

- Task 1 解决“Agent 和 Tool 之间说什么语言”的问题。
- Task 2 解决“旧工具如何平滑接入新协议”的问题。

完成这两步后，再接入靶点搜索作为第一个完整智能化试点。
