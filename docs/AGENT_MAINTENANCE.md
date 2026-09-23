# Agent 当前架构与维护入口

核对日期：2026-09-24。源码基线：`main` 的 `85c4f3061ac33be1391e7b6cf7538c040f66d21c`。
本文是维护导航，不是生产部署或真实科研验收通过声明。后续合并改变下列调用关系时，应同步更新本页和基线；本地候选补丁不算已经上线。

协作约束见 [AGENTS.md](../AGENTS.md) 和 [项目规范](PROJECT_STANDARDS.md)。[旧问题清单](issues_and_improvement_plan.md) 仅供历史追溯。

## 1. 先分清三类入口

| 类别 | 实际位置与调用 | 维护边界 |
|---|---|---|
| 正式网页聊天 | [main.py](../main.py) → [MolecularChatApp](../src/web/app.py) 注册 `/ws` → [ChatHandler.handle_websocket](../src/web/chat_handler.py) | `_create_chat_agent()` 构造 `SupervisorAgent`；没有 ChatHandler 时以 1011 失败关闭，不回退到旧聊天实现。普通聊天不等于科研工具调用。 |
| 工作流 HTTP API | [agent_workflow_routes.py](../src/web/routes/agent_workflow_routes.py) 的 `/api/agent/workflows/plan`、`/run` | 使用应用注入的 `_create_supervisor_agent()`，注册表审计、specialist 委派；`run` 经 `get_task_manager().submit()` 后调用 `SupervisorAgent.run()`。不要把它和聊天入口的工具装配方式写成完全相同。 |
| 隔离模型决策验收 | [decision_lab.py](../src/web/decision_lab.py)、[decision_chat.py](../src/web/decision_chat.py)、[run_decision_chat_acceptance.py](../scripts/run_decision_chat_acceptance.py) | 独立 loopback 验收应用；`ChatHandler.process_decision_message()` 是显式服务端桥接，正式 `/ws` 不根据浏览器参数自动启用它。不能把隔离验收通过描述为已切换生产 Agent。 |

当前正式科学入口仍包含路由、计划和工作流执行。仓库同时有模型决策循环，但“代码已存在”不代表正式聊天已使用该循环，也不意味着可以删掉验证、任务运行时或恢复保护。

## 2. 意图到证据的定位顺序

```text
ChatHandler / 工作流 API
  → SupervisorAgent：组织上下文、选择策略与执行路径
  → Router + TaskPlanner + Compiler/Bindings：决定任务与数据依赖
  → Harness / WorkflowExecutor：准备一次执行
  → WorkflowRunSession：步骤执行、校验、事件、检查点、结果汇总
  → 工具适配 / specialist → 实际领域工具
  → ToolResult / AgentResult → 脱敏展示与证据记录
```

| 问题 | 优先查找的源码 | 必须保留的契约 |
|---|---|---|
| 路由错、靶点别名或否定意图丢失 | [router.py](../src/agent/router.py)、[routing/hybrid.py](../src/agent/routing/hybrid.py)、[target_request.py](../src/agent/contracts/target_request.py) | 不靠关键词强行运行工具；输入不完整应澄清，显式否定约束不能被删去。 |
| 步骤或上下游输入错 | [task_planner.py](../src/agent/planning/task_planner.py)、[compiler.py](../src/agent/planning/compiler.py)、[bindings.py](../src/agent/planning/bindings.py) | `output_key` 本身不是数据流证明；检查实际 selector、转换和工具收到的输入，保留生成候选的可信来源。 |
| 工具归属、别名或重复实例 | [tooling/factory.py](../src/agent/tooling/factory.py)、[registration.py](../src/agent/tooling/registration.py)、[specialists](../src/agent/specialists) | 未知归属失败关闭；别名归一化；工作流工厂复用注册表已有工具，不建立第二个池。 |
| 状态、重试、恢复或候选对齐不一致 | [run_session.py](../src/agent/runtime/run_session.py)、[workflow_executor.py](../src/agent/runtime/workflow_executor.py)、[delegated_executor.py](../src/agent/runtime/delegated_executor.py) | 普通/委派执行共用 Session 生命周期；委派层保留授权、调用和结果信封差异，不能另写完整终态循环。 |
| 工具异常、超时或旧返回格式 | [tooling/adapters.py](../src/agent/tooling/adapters.py)、[tools/base_tool.py](../src/agent/tools/base_tool.py) | `ToolResult` 中的状态、错误、warnings、artifacts、evidence、quality 不能退化为一段成功文本。 |
| 数值、候选结构或来源不可信 | [validators](../src/agent/validators)、[contracts/result.py](../src/agent/contracts/result.py) | 保留领域校验、候选对齐和 partial/failed；模型缺失或 demo 不等于真实预测，未执行 Vina 不得给出已计算结合能。 |

[HarnessFactory](../src/agent/harness/factory.py) 默认 `legacy`；这指现有执行器适配，不是启用 `ReActMolecularAgent`。`shadow` 是旁路计划比较；`langgraph_canary` 是有界灰度，委派执行器明确不扩展该灰度范围。未知模式或可选依赖不可用会记录 warning 并回到既有执行器，不代表科学工具结果被允许模拟。

[ModelDecisionLoop](../src/agent/harness/decision_loop.py) 通过决策协议、任务约束和预算控制动态追加动作，复用 `WorkflowRunSession`。其隔离入口、允许工具集合和续接存储必须由服务端组装，不接受浏览器任意覆盖。

## 3. 模型、RAG、持久化各自负责什么

- **主聊天模型**：应用配置加载与切换在 [app.py](../src/web/app.py)、[user_llm_config.py](../src/web/user_llm_config.py)，OpenAI-compatible 适配在 [openai_compatible_model.py](../src/agent/openai_compatible_model.py)。配置值、Key、运行时用户文件不得进入文档或 Git。
- **分子生成模型**：应用独立构造 `molecular_generator_model`，默认本地 Ollama `gmm-llama:latest`；主模型切换不能重绑它。设计页主模型推荐入口另在 [design_routes.py](../src/web/routes/design_routes.py)、[molecular_design/service.py](../src/molecular_design/service.py)，不要与 SMILES 生成模型混为一谈。
- **RAG**：当前基线 `RAGSystem` 仍在 `app.py`；索引/manifest 在 [rag_index.py](../src/web/rag_index.py)，工具入口在 [rag_search_tool.py](../src/agent/tools/rag_search_tool.py)。行映射统一补丁尚未进入本基线，不能声称所有入口已共享检索核心。
- **执行证据**：[event_bus.py](../src/agent/runtime/event_bus.py) 与 [SQLiteAgentStateStore](../src/agent/persistence/sqlite_store.py) 记录运行、事件、检查点及续接；[redaction.py](../src/agent/persistence/redaction.py) 负责敏感内容处理。排障用 trace_id 对齐实际步骤、工具结果和 artifact，不能只看最终文本。
- **后台工作**：[TaskManager](../src/task_runtime/manager.py) 是工作流 API 使用的提交入口；[TaskRuntime](../src/task_runtime/runtime.py) 是带 staging、幂等、后端选择和收尾的异步科学任务门面，两者不是同一个类。Temporal/OpenSandbox 等边界见 [task_runtime](../src/task_runtime)；不要因都是“任务”就机械合并或删除资源清理。
- **聊天与续接**：本基线正式 WebSocket 的历史列表按连接创建；这不等于身份鉴权、断线持久化或跨轮候选引用已完整实现。隔离决策续接已有独立保护，不能据此宣称主聊天“第 3 个分子”等科研对象引用已可靠打通。

## 4. 兼容代码与迁移状态

| 保留项 | 原因与删除门槛 |
|---|---|
| 已删除的旧 Skill 对象层与保留名称 | `src/agent/skills/`、`BaseSkill`、`SkillRegistry` 已移除，声明信息由 [WorkflowCatalog/WorkflowPolicy](../src/agent/workflows/catalog.py) 承接；[删除守卫测试](../tests/agent/test_no_legacy_skill_layer.py) 防止旧 import 回流。`SkillRouter`、`selected_skill`、`active_skill`、`skill_name` 是保留的兼容名称，不代表旧 Skill 对象仍存在，也不代表工作流已删除。 |
| [react_agent.py](../src/agent/react_agent.py)、[agent_executor.py](../src/agent/agent_executor.py) | 旧接口仍有导出或测试支持，不是正式聊天工厂。先核对调用者并迁移科学断言，不能仅因名称旧就删除。 |
| [routes/page_routes.py](../src/web/routes/page_routes.py)、[routes/websocket_routes.py](../src/web/routes/websocket_routes.py) | `routes/__init__.py` 仍公开导出。页面兼容层已委托 `register_main_routes()`；WebSocket 兼容注册直接委托 ChatHandler。不得在正式应用重复注册 `/ws`。 |
| app.py 内旧私有聊天/提示函数、静态备份 | 本基线仍保留；T06 的删除候选已经单独核对，但本地已审补丁尚未合并。公共占位脚本不能因无业务逻辑就移除并造成 404。 |
| 工具输入/输出 schema | `tooling/factory.py` 仍使用 `LegacyQueryInput(query: Any)`，`output_schema=None`；并非没有校验（适配器、领域和 Session 校验仍在）。T10 需逐工具迁移，不另造工作流 DSL。 |

已进入本基线的清理：状态汇总和工具归属、共享 Session 委派（T02/T04/T07）、靶点身份对齐（T08），以及单独批准的逻辑/集成/性能测试分离。

尚待发布或另行设计：T01 RAG 统一、T03 分段输入预算、T05-B 全消费者切换/排空、T06 局部清理、PR #37 匿名归属；T09 科研对象跨轮不是匿名身份本身；T10 类型化工具与 T11 其余结构迁移不能标成已完成。页面模板新旧签名兼容也仍是已知发布前置项。

本节只说明核对基线，避免把未合并改动描述成当前行为；不复制各批历史测试数量。每批合并后应删去相应“待发布”表述并更新源码定位。

## 5. 验证入口与常见误判

在安装项目依赖的 Python/Conda 环境执行，先查看 [tests/conftest.py](../tests/conftest.py) 的用户配置隔离。涉及数据库的独立探针必须用临时路径，禁止读写用户运行库或真实 Key。

```powershell
python -B -m pytest tests/agent/test_app_supervisor_entrypoint.py tests/test_web_app_lifecycle.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_supervisor_delegation.py tests/agent/test_workflow_run_session.py tests/agent/test_workflow_resume.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_candidate_alignment.py tests/agent/test_registration_consistency.py tests/test_rag_index_manifest.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_no_legacy_skill_layer.py -q -p no:cacheprovider
python -B -m pytest tests/agent -q -p no:cacheprovider
python -B -m pytest tests -q -p no:cacheprovider
node tests/home_agent_task_panel_test.js
git diff --check
```

这些是维护命令，不是本页宣称已执行的全仓验收；结果以当前提交的实际输出为准。真实服务测试必须保持显式 opt-in。CI 的 CPU 根依赖与 GPU 部署依赖是不同 profile，本机通过不能覆盖旧版 FastAPI/Starlette 兼容问题。

[run_agent_acceptance.py](../scripts/run_agent_acceptance.py) 的模式边界：

- `contract`：路由、计划、工具契约等离线检查；不证明真实模型与科研工具闭环可用。
- `replay --replay-input <report.json>`：读取结构化报告，离线重新检查；缺报告应失败，不能重跑真实工具伪装回放。
- `real`：真实工具案例与依赖检查；`all` 包含真实模式。本轮维护不启用这两者；缺模型、样例或依赖必须如实记录 partial/failed/skipped。
- 脚本退出码 0 允许报告状态为 `partial`；必须读结构化 status/truth checks，而不是把 exit 0 等同于全部科研通过。

独立性能用例 `MEDCHAT_RUN_TASK_STORE_OFFLOAD_PERF=1` 保留原 50ms 调度断言，仅在适当的受控 runner 使用；默认功能测试验证线程 offload、同步进展和真实 SQLite。此拆分不是原 CI 慢点已经定位或性能已修复的证据。

## 6. 修改时的最小检查顺序

1. 先确认当前分支和工作树，不覆盖原始混杂改动；为本任务建立独立分支。
2. 从实际入口复现，再定位路由、绑定、适配或呈现边界；不要只复制表达式做“通过”探针。
3. 修改契约时同时测正式组装路径、兼容路径、partial/failed、取消/清理和来源字段；削弱测试不是修复。
4. 保留首个失败及原因，记录当前 SHA、解释器、真实命令、退出码和 skipped 原因；不粘贴密钥或聊天隐私数据。
5. 独立审查、精确暂存、CI 门禁和授权齐备后再合并。文档、schema 或目录重命名都不能代替科学验收。
