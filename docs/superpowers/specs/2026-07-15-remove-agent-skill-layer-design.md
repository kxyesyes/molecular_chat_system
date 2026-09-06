# MedChat Agent Skill 层移除设计

日期：2026-07-15
状态：已获用户确认
范围：移除 `src/agent/skills/` 面向对象 Skill 体系，保留现有科研工作流、工具权限、安全校验和兼容响应字段。

## 1. 背景与目标

当前 `src/agent/skills/` 为每个科研意图定义一个 `BaseSkill` 子类，并由
`SkillRegistry` 提供名称、描述、触发关键词、工具白名单和旧 ReAct system prompt。
现代聊天主链路已经由 `SupervisorAgent`、`TaskPlanner`、`WorkflowExecutor` 和
`WorkflowOrchestrator` 执行确定性工作流，Skill 类不再承担实际编排，却仍被 Router、
Supervisor、健康检查、验收脚本和测试直接依赖。

本次改造目标是删除 Skill 类体系及其目录，将仍有价值的声明信息迁移为轻量、不可变的
Workflow Policy，消除“Skill 对象 + Workflow Plan”两套抽象，同时保持现有科研行为不变。

## 2. 范围

### 2.1 删除

- 删除 `src/agent/skills/` 下的 `BaseSkill`、所有领域 Skill 子类、`SkillRegistry` 和包入口。
- 删除旧 ReAct 执行流对 Skill system prompt、`is_workflow` 和
  `max_iterations_override` 的依赖。
- 删除生产代码、脚本和测试中对 `src.agent.skills` 的导入。
- 删除重复维护的 Skill 工具白名单。

### 2.2 保留

- 保留当前工作流名称，例如 `target_driven_design`、
  `comprehensive_evaluation` 和 `docking_simulation`。
- 保留 `TaskPlanner` 中现有步骤顺序、`input_from/output_key` 数据流和
  required/optional 失败语义。
- 保留 `ToolResult`、`AgentResult`、`AgentResultValidator`、
  `execute_tool_compat`、事件流、checkpoint 和幂等逻辑。
- 保留现有 RDKit、Ollama、RG-MPNN、靶点检索、RAG 和 Vina 工具实现。
- 暂时保留 API、事件、数据库和评测数据中的 `active_skill`、`skill_name` 字段；
  这些字段作为向后兼容名称，值表示 workflow name，不再表示 Skill 对象。

### 2.3 不在本次范围

- 不引入 LangGraph、Temporal、MCP 或新的数据库。
- 不重写 Router 的意图评分算法。
- 不改变前端展示协议。
- 不修改科学工具模型、权重、输入资产或输出格式。
- 不清理与本任务无关的旧 Agent 文件。

## 3. 目标结构

新增 `src/agent/workflows/catalog.py`，定义：

- `WorkflowPolicy`：不可变声明对象，包含 `name`、`description`、
  `allowed_tools` 和 `is_multi_step`。
- `WorkflowCatalog`：按名称查询 policy、列出 policy、构建 LLM 路由目录。
- `WORKFLOW_POLICIES`：所有工作流的唯一声明数据源。

触发评分继续由 `HybridSkillRouter` 当前规则实现。为控制本次改造范围，路由类名和
`RouteDecision.selected_skill` 暂时保留作为兼容接口，但它们内部不得再导入或返回
`BaseSkill`。高层 `SkillRouter.route()` 统一返回 `WorkflowPolicy`；底层
`HybridSkillRouter.decide()` 继续在 `RouteDecision.selected_skill` 中返回 workflow name。

`WorkflowExecutor` 不再接收 `BaseSkill`，而是接收 `WorkflowPolicy`，并从
`policy.allowed_tools` 执行权限预检。`SupervisorAgent` 通过 `WorkflowCatalog` 解析
路由名称并将 policy 传给 executor。

## 4. 数据流

聊天请求的数据流保持为：

1. `ChatHandler` 调用 Router。
2. Router 选择 workflow name。
3. `WorkflowCatalog` 返回对应 `WorkflowPolicy`。
4. `TaskPlanner` 根据 `AgentContext.active_skill` 生成现有 `WorkflowPlan`。
5. `WorkflowExecutor` 使用 policy 工具白名单做预检。
6. `WorkflowOrchestrator` 执行步骤、校验结果并发出事件。
7. 前端继续接收原有 `active_skill`、events、tool results 和最终结果。

本次不改变 `AgentContext.active_skill`，避免同时修改数据库 schema、报告 schema 和前端协议。

## 5. 兼容策略

- `active_skill` 和 `skill_name` 字段继续存在，语义调整为 workflow identifier。
- `HybridSkillRouter`、`SkillRouter` 类名暂时保留，避免破坏现有 Python 调用方；
  后续可在独立破坏性变更中改名为 Workflow Router。
- 不保留 `BaseSkill`、具体 Skill 类或 `SkillRegistry` 的导入兼容层；这些属于本次明确删除对象。
- 测试和脚本必须改为从 `WorkflowCatalog` 获取 policy。
- 旧 ReAct 流若仍被直接调用，只允许接收 workflow policy 或 workflow name，
  不再注入领域 persona prompt；科学安全继续由工具白名单和 Validator 强制执行。

## 6. 错误与安全行为

- 未知 workflow name 必须返回结构化失败，不得回退到任意工具执行。
- 工具白名单必须来自 `WORKFLOW_POLICIES` 唯一数据源。
- 缺少 policy、缺少工具或越权工具必须在 WorkflowExecutor 预检阶段失败。
- demo/fallback、无效 SMILES、缺 docking 参数及工具不可用的处理保持现状。
- 删除 Skill system prompt 后，不允许把其中科研红线仅迁移为自然语言提示；
  已有 Validator 和确定性规则继续作为强制安全边界。

## 7. 测试设计

实施采用 TDD，先新增会失败的架构守卫和行为测试，再修改生产代码。

### 7.1 架构守卫

- `src/agent/skills/` 不存在。
- `src/`、`scripts/` 和 `tests/` 中不存在 `src.agent.skills` 或相对 skills 导入。
- Workflow Catalog 恰好声明当前支持的工作流，名称唯一。
- 每个 policy 的工具白名单与迁移前一致。

### 7.2 行为回归

- 普通聊天继续 abstain，不执行科研工具。
- 分子性质、活性、反向寻靶、靶点搜索、分子设计、docking、综合评价、
  hit-to-lead、target-driven design 和 RAG 的路由结果不变。
- WorkflowExecutor 继续拒绝越权或缺失工具。
- comprehensive 和 target-driven design 的工具顺序、数据流、partial 行为不变。
- Supervisor 聊天入口和后台 workflow API 均能使用 Workflow Policy。
- Agent contract、真实科研验收和反幻觉测试继续通过。

## 8. 验证命令

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract
```

真实工具测试不因本次抽象删除而强制运行；若运行，缺少模型或外部依赖必须继续如实报告。

## 9. 完成标准

- `src/agent/skills/` 已删除且没有残余生产导入。
- Workflow Policy 是工作流元数据和工具白名单的唯一来源。
- 主聊天入口、后台 workflow 入口、健康检查和 acceptance runner 可运行。
- 相关测试、Agent 测试、compileall 和 contract acceptance 通过。
- 不覆盖当前工作树中的无关改动，不提交本地索引、密钥或运行产物。
