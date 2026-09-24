# Agent 当前架构与维护入口

核对日期：2026-09-24。源码基线：已合并的 main 提交 `493dfdcf94959a260f690f73a06a7fbc9a57c72a`（含 RAG、活性和对接工具类型契约）。
本文记录该源码基线的维护入口，不表示已生产部署或通过真实科研验收；后续调用关系变化也应同步更新本页。

协作约束见 [AGENTS.md](../AGENTS.md) 和 [项目规范](PROJECT_STANDARDS.md)。[旧问题清单](issues_and_improvement_plan.md) 仅供历史追溯。

## 1. 先分清三类入口

| 类别 | 实际位置与调用 | 维护边界 |
|---|---|---|
| 正式网页聊天 | [main.py](../main.py) → [MolecularChatApp](../src/web/app.py) 注册 `/ws` → [ChatHandler.handle_websocket](../src/web/chat_handler.py) | `_create_chat_agent()` 构造 `SupervisorAgent`；没有 ChatHandler 时以 1011 失败关闭，不回退到旧聊天实现。普通聊天不等于科研工具调用。 |
| 工作流 HTTP API | [agent_workflow_routes.py](../src/web/routes/agent_workflow_routes.py) 的 `/api/agent/workflows/plan`、`/run` | 使用应用注入的 `_create_supervisor_agent()`，注册表审计、specialist 委派；正式应用的 `run` 经 `ModelRequestGate.submit_background()` 转交 TaskManager，再调用 `SupervisorAgent.run()`，模型使用权覆盖实际 worker 生命周期。没有注入 gate 的独立构造仍兼容直接 submit。 |
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
| 长历史或证据挤掉当前问题 | [prompt_budget.py](../src/web/prompt_budget.py)、[chat_handler.py](../src/web/chat_handler.py) | 分区字符预算不是精确 token 计数；保留完整问题和科学约束，历史/证据整块取舍，不截断 SMILES 或 JSON。输入自身超限应明确拒绝；关键状态放不下时直接保留工具结果，不强行交模型解读。 |

[HarnessFactory](../src/agent/harness/factory.py) 默认 `legacy`；这指现有执行器适配，不是启用 `ReActMolecularAgent`。`shadow` 是旁路计划比较；`langgraph_canary` 是有界灰度，委派执行器明确不扩展该灰度范围。未知模式或可选依赖不可用会记录 warning 并回到既有执行器，不代表科学工具结果被允许模拟。

[ModelDecisionLoop](../src/agent/harness/decision_loop.py) 通过决策协议、任务约束和预算控制动态追加动作，复用 `WorkflowRunSession`。其隔离入口、允许工具集合和续接存储必须由服务端组装，不接受浏览器任意覆盖。

### 已接入 Registry 的类型契约

[build_tool_registry](../src/agent/tooling/factory.py) 按规范工具名选择以下专用 `LegacyPythonToolAdapter` 子类；没有为它们建立第二个执行器或结果规范化循环。

| 规范工具名与实现 | 输入和结果边界 |
|---|---|
| `rag_search`：[rag_contract.py](../src/agent/tooling/rag_contract.py) | 接受字符串或字符串 query；检查检索记录的 source_index、有限相似度与来源结构，保留额外 CSV 字段。来源字段形状正确不等于来源真实，索引/manifest/行映射验证仍由 RAG 服务负责。`rag_database_search` 经工厂别名归一化进入同一适配。 |
| `activity_predictor`：[activity_contract.py](../src/agent/tooling/activity_contract.py) | 兼容字符串与结构化分子/靶点输入；检查原始和规范化成功、partial、失败观察及已知失败快照，复用活性科学校验。不训练、加载或启用模型，不改活性阈值。缺模型/demo 不能因通过类型校验就成为真实预测。 |
| `molecular_docking`：[docking_contract.py](../src/agent/tooling/docking_contract.py) | 兼容文本、单层 query 包装和直接结构；可执行结构须显式给出 receptor、ligand/SMILES、center 与 size，不能借作业模型默认值补 box。复用 pose/能量/输入证明和 OpenSandbox hash、gvisor、image、cleanup 检查；不可信结果按现有规则清除。通过已验证快照传递的沙盒要求不能被外层 local 标记降低。 |

三个输出视图的 `schema_version` 均为类元数据 `1`，不是新增 HTTP 字段。它们校验观察而不把 `model_dump()` 投影写回科学结果；既有 `execute_tool_compat` 继续负责结果规范化，合法状态、warnings、artifacts、evidence、quality 与来源不应在类型迁移中丢失。工具归属、一次调用内的超时/并发槽与关闭仍沿用既有适配层。

其余工具尚使用 `LegacyQueryInput(query: Any)` / `output_schema=None`，但并非没有领域或 Session 校验。`prepare_receptor`、`prepare_ligand`、`run_docking`、`get_docking_result` 四个辅助工具也未因本次契约迁移开启其 `run()` 执行路径。类型契约和合成 pose 测试不替代真实 Vina 或模型验收。

## 3. 模型、RAG、持久化各自负责什么

- **主聊天模型**：应用配置加载与切换在 [app.py](../src/web/app.py)、[user_llm_config.py](../src/web/user_llm_config.py)，OpenAI-compatible 适配在 [openai_compatible_model.py](../src/agent/openai_compatible_model.py)。[model_lifecycle.py](../src/web/model_lifecycle.py) 让聊天、设计推荐、后台工作流共享排空边界：在途请求固定模型，切换等待实际消费者结束后关闭旧客户端。取消或任务表终态不等于底层 worker 已退出；挂起的 worker 仍可能让排空持续等待，本机制不强杀它。配置值、Key、运行时用户文件不得进入文档或 Git。
- **分子生成模型**：应用独立构造 `molecular_generator_model`，默认本地 Ollama `gmm-llama:latest`；主模型切换不能重绑它。设计页主模型推荐入口另在 [design_routes.py](../src/web/routes/design_routes.py)、[molecular_design/service.py](../src/molecular_design/service.py)，不要与 SMILES 生成模型混为一谈。
- **RAG**：唯一 `RAGSystem` 在 [rag/service.py](../src/rag/service.py)，索引/manifest、不可变快照与原子写入在 [rag/index.py](../src/rag/index.py)，同步/异步检索共用 [rag/retrieval.py](../src/rag/retrieval.py) 的行映射与来源校验。工具 [rag_search_tool.py](../src/agent/tools/rag_search_tool.py) 接受应用显式注入的同一服务，不读取全局 Web 单例或另起 embedding 配置。`app.py.RAGSystem` 和 [web/rag_index.py](../src/web/rag_index.py) 保留同一对象兼容导出；测试注入点使用规范领域模块。未初始化或来源/索引不兼容时，同步服务抛出异常，工具适配器返回失败；异步兼容接口记录 warning 并返回空列表，不能仅凭空列表区分检索不可用与无命中。任何路径都不能把 FAISS 标签直接当原始行号。Web 展示投影另在 [rag_presentation.py](../src/web/rag_presentation.py)，保留 source_index/provenance。
- **执行证据**：[event_bus.py](../src/agent/runtime/event_bus.py) 与 [SQLiteAgentStateStore](../src/agent/persistence/sqlite_store.py) 记录运行、事件、检查点及续接；[redaction.py](../src/agent/persistence/redaction.py) 负责敏感内容处理。排障用 trace_id 对齐实际步骤、工具结果和 artifact，不能只看最终文本。
- **后台工作**：[TaskManager](../src/task_runtime/manager.py) 是工作流 API 使用的提交入口；[TaskRuntime](../src/task_runtime/runtime.py) 是带 staging、幂等、后端选择和收尾的异步科学任务门面，两者不是同一个类。Temporal/OpenSandbox 等边界见 [task_runtime](../src/task_runtime)；不要因都是“任务”就机械合并或删除资源清理。
- **聊天与续接**：[agent_session_config.py](../src/web/agent_session_config.py) 组装服务端匿名会话，正式 WebSocket/工作流从 scope 取身份，任务访问按服务端归属过滤；它不是实名登录授权。聊天历史仍按连接创建，六处终态共用 `_append_history` 原位保留末 20 条；不是断线历史持久化或科研对象续接。隔离决策续接已有独立保护，但首页“第 3 个分子”还需要实际展示顺序、版本、归属和失效检查，不能从历史文本猜 SMILES。

## 4. 兼容代码与迁移状态

| 保留项 | 原因与删除门槛 |
|---|---|
| 已删除的旧 Skill 对象层与保留名称 | `src/agent/skills/`、`BaseSkill`、`SkillRegistry` 已移除，声明信息由 [WorkflowCatalog/WorkflowPolicy](../src/agent/workflows/catalog.py) 承接；[删除守卫测试](../tests/agent/test_no_legacy_skill_layer.py) 防止旧 import 回流。`SkillRouter`、`selected_skill`、`active_skill`、`skill_name` 是保留的兼容名称，不代表旧 Skill 对象仍存在，也不代表工作流已删除。 |
| [react_agent.py](../src/agent/react_agent.py)、[agent_executor.py](../src/agent/agent_executor.py) | 旧接口仍有导出或测试支持，不是正式聊天工厂。先核对调用者并迁移科学断言，不能仅因名称旧就删除。 |
| [routes/page_routes.py](../src/web/routes/page_routes.py)、[routes/websocket_routes.py](../src/web/routes/websocket_routes.py) | `routes/__init__.py` 仍公开导出。页面兼容层已委托 `register_main_routes()`；WebSocket 兼容注册直接委托 ChatHandler。不得在正式应用重复注册 `/ws`。 |
| 已删除的 app.py 旧私有聊天/提示函数、静态备份 | `_handle_websocket` 及仅服务它的三个提示 helper、旧应用级历史已删除，正式 `/ws` 仍唯一委托 ChatHandler。无引用的 `activity_prediction_v2.legacy.backup.js` 已删除；公共占位 `script.js`、`activity_prediction_v2.js` 保留，真实页面与静态路由测试检查加载次序、200/404 和清理。 |
| 工具输入/输出 schema | RAG、活性、对接已接入上表专用类型边界；其他工具仍为通用兼容边界。逐工具迁移，不将“已迁移三个工具”说成所有工具都已类型化，也不另造工作流 DSL。 |

本基线已包含：状态汇总和工具归属、共享 Session 委派（T02/T04/T07）、靶点身份对齐（T08）、匿名会话归属、T01 RAG 统一、T03 输入预算、T05 消费者排空、T06 三类清理、T11-A RAG 服务/索引归位、T10-A 三个优先工具类型契约，以及独立批准的测试分层与新旧模板签名适配。

仍未完成：T09 科研对象跨轮引用；T10-B Planner 职责整理；T11 其他领域路由、聊天提示/展示职责拆分和旧 Agent 支持面收缩。其他工具类型化仍须按实际契约逐项评估。其设计与实现需分别验证，不能把匿名身份、三个工具迁移或文档更新视为整个任务书已完成。正式首页仍未切换到隔离模型决策入口。

本节只说明核对基线，避免把未合并改动描述成当前行为；不复制各批历史测试数量。每批合并后应删去相应“待发布”表述并更新源码定位。

## 5. 验证入口与常见误判

在安装项目依赖的 Python/Conda 环境执行，先查看 [tests/conftest.py](../tests/conftest.py) 的用户配置隔离。涉及数据库的独立探针必须用临时路径，禁止读写用户运行库或真实 Key。

```powershell
python -B -m pytest tests/agent/test_app_supervisor_entrypoint.py tests/test_web_app_lifecycle.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_supervisor_delegation.py tests/agent/test_workflow_run_session.py tests/agent/test_workflow_resume.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_candidate_alignment.py tests/agent/test_registration_consistency.py tests/test_rag_index_manifest.py -q -p no:cacheprovider
python -B -m pytest tests/test_rag_service_boundary.py tests/test_rag_index_manifest.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_rag_tool_contract.py tests/agent/test_activity_tool_contract.py tests/agent/test_activity_contract_integration.py tests/agent/test_docking_tool_contract.py tests/agent/test_docking_contract_integration.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_chat_input_budget.py tests/test_model_request_lifecycle.py tests/test_design_model_switch.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_legacy_chat_entry_cleanup.py tests/agent/test_chat_local_cleanup.py tests/test_static_placeholder_cleanup.py tests/test_main_routes_template_compat.py -q -p no:cacheprovider
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
