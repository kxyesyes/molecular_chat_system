# Agent 当前架构与维护入口

Pi 入口核对日期：2026-10-23。当前分支的网页通用 Agent 唯一运行时为官方 Pi SDK **1.4.0**；Python 保留领域授权、科学执行和结果校验，Supervisor 的确定性工作流直接调用执行器。安装与配置见 [Pi 运行手册](PI_RUNTIME.md)。
本文记录本分支源码维护入口，不表示已合并或生产部署；云端与本地真实模型/科学工具端到端验收均为 **PENDING（待验收）**，不能宣称替换已完整验证。此前领域拆分与隔离验收仅作历史证据，见[领域 API 交接](handoff/domain-api-separation.md)与[集成验收记录](handoff/main-integration-acceptance.md)。

协作约束见 [AGENTS.md](../AGENTS.md) 和 [项目规范](PROJECT_STANDARDS.md)。[旧问题清单](issues_and_improvement_plan.md) 仅供历史追溯。

## 1. 先分清运行入口

| 类别 | 实际位置与调用 | 维护边界 |
|---|---|---|
| 正式网页聊天 | [main.py](../main.py) → [MolecularChatApp](../src/web/app.py) 注册 `/ws` → [ChatHandler.handle_websocket](../src/web/chat_handler.py) → [WebDecisionRuntime](../src/web/decision_runtime.py) → [PiAgentRuntime](../src/agent/pi_runtime/runtime.py) | Pi 是唯一通用 Agent 循环；没有组装 WebDecisionRuntime 时以 `pi_runtime_unavailable` / 1011 失败关闭，不回退旧聊天。`_create_chat_agent()` 保留 Supervisor 的工具/工作流组装职责，不是另一套 Web 通用循环。 |
| 工作流 HTTP API | [agent_workflow_routes.py](../src/web/routes/agent_workflow_routes.py) 的 `/api/agent/workflows/plan`、`/run` | 使用应用注入的 `_create_supervisor_agent()`，注册表审计、specialist 委派；正式应用的 `run` 经 `ModelRequestGate.submit_background()` 转交 TaskManager，再调用 `SupervisorAgent.run()`，模型使用权覆盖实际 worker 生命周期。没有注入 gate 的独立构造仍兼容直接 submit。 |
| 领域 HTTP API | [api_routes.py](../src/web/routes/api_routes.py) 兼容注册入口 → 同目录八个领域注册模块 | 原30个操作按原顺序注册；正式应用对活性预测、活性模型、分子工具和报告使用 `register_*_routes()` 的窄依赖入口。旧 `setup_*_routes(..., _support=...)` 仅保留给直接调用者，并通过 `route_compat.lazy_dependency` 动态解析；不能每次 setup 新建线程池或运行时。 |
| 隔离模型决策验收 | [decision_lab.py](../src/web/decision_lab.py)、[decision_chat.py](../src/web/decision_chat.py)、[run_decision_chat_acceptance.py](../scripts/run_decision_chat_acceptance.py) | 独立 loopback 验收应用；与正式 Web 一样使用 Pi，由服务端组装权限、工具和续接存储。浏览器不能任意覆盖。隔离验收不代表生产部署或真实科学验收通过。 |

`MEDCHAT_CHAT_PROFILE` 默认 `pi`；`semantic_v1` 在同一 Pi 上启用语义准入，`decision_a2` 仅为临时迁移别名，`legacy` 被拒绝。科学入口仍需路由、计划、验证、任务运行时和恢复保护，Pi 不可用时不自动回退。

## 2. 意图到证据的定位顺序

```text
ChatHandler → WebDecisionRuntime → PiAgentRuntime → 官方 Pi（runtime/pi）
  ↔ Python ScientificSession：准入、工具授权、科学执行与证据校验

确定性工作流 API → SupervisorAgent
  → Router + TaskPlanner + Compiler/Bindings：决定任务与数据依赖
  → WorkflowExecutor / DelegatedWorkflowExecutor：直接执行
  → WorkflowRunSession：步骤执行、校验、事件、检查点、结果汇总
  → 工具适配 / specialist → 实际领域工具
  → ToolResult / AgentResult → 脱敏展示与证据记录
```

| 问题 | 优先查找的源码 | 必须保留的契约 |
|---|---|---|
| 路由错、靶点别名或否定意图丢失 | [router.py](../src/agent/router.py)、[routing/hybrid.py](../src/agent/routing/hybrid.py)、[target_request.py](../src/agent/contracts/target_request.py) | 不靠关键词强行运行工具；输入不完整应澄清，显式否定约束不能被删去。 |
| 步骤或上下游输入错 | [task_planner.py](../src/agent/planning/task_planner.py)、[step_templates.py](../src/agent/planning/step_templates.py)、[compiler.py](../src/agent/planning/compiler.py)、[bindings.py](../src/agent/planning/bindings.py) | Planner 保留选择、参数解析和计划元数据；五类纯模板只构造步骤。`output_key` 本身不是数据流证明；检查实际 selector、转换和工具收到的输入，保留生成候选的可信来源。 |
| 工具归属、别名或重复实例 | [tooling/factory.py](../src/agent/tooling/factory.py)、[registration.py](../src/agent/tooling/registration.py)、[specialists](../src/agent/specialists) | 未知归属失败关闭；别名归一化；工作流工厂复用注册表已有工具，不建立第二个池。 |
| 状态、重试、恢复或候选对齐不一致 | [run_session.py](../src/agent/runtime/run_session.py)、[workflow_executor.py](../src/agent/runtime/workflow_executor.py)、[delegated_executor.py](../src/agent/runtime/delegated_executor.py) | 普通/委派执行共用 Session 生命周期；委派层保留授权、调用和结果信封差异，不能另写完整终态循环。 |
| 工具异常、超时或旧返回格式 | [tooling/adapters.py](../src/agent/tooling/adapters.py)、[tools/base_tool.py](../src/agent/tools/base_tool.py) | `ToolResult` 中的状态、错误、warnings、artifacts、evidence、quality 不能退化为一段成功文本。 |
| 数值、候选结构或来源不可信 | [validators](../src/agent/validators)、[contracts/result.py](../src/agent/contracts/result.py) | 保留领域校验、候选对齐和 partial/failed；模型缺失或 demo 不等于真实预测，未执行 Vina 不得给出已计算结合能。 |
| 长历史或证据挤掉当前问题 | [prompt_budget.py](../src/web/prompt_budget.py)、[chat_prompt_builder.py](../src/web/chat_prompt_builder.py)、[chat_handler.py](../src/web/chat_handler.py) | 分区字符预算不是精确 token 计数；保留完整问题和科学约束，历史/证据整块取舍，不截断 SMILES 或 JSON。输入自身超限应明确拒绝；关键状态放不下时直接保留工具结果，不强行交模型解读。 |
| 结构结果的自然语言追问缺少原始数值 | [web_business.py](../src/web/web_business.py) 的 `model_target_context()`、[web_request.py](../src/web/web_request.py)、[decision_policy.py](../src/agent/scientific_session/decision_policy.py)；回归 [test_pi_web_target_context.py](../tests/agent/test_pi_web_target_context.py) | 只读聊天可接收同一连接、同一会话已接受的结构元数据白名单，封存在 `prior_target_context` 并计入既有输入预算；模型解释成功后只保留原始记录，不把解释升级为科学证据。新连接不继承该上下文，科学执行仍按本轮编译计划；检索元数据不代表坐标检查、受体准备或对接验证。 |
| 性质或 ADMET 自然追问丢失预测值 | [web_business.py](../src/web/web_business.py) 的 `model_candidate_context()`、[web_request.py](../src/web/web_request.py)、[decision_policy.py](../src/agent/scientific_session/decision_policy.py)；回归 [test_pi_web_candidate_context.py](../tests/agent/test_pi_web_candidate_context.py) | `prior_candidate_context` 只投影当前连接、当前会话已接受的候选计算记录；保留数值、单位、任务类型和无效端点状态，ADMET 按原请求关注端点筛选。只读解释不能授权新计算，也不能成为新科学证据；新连接不继承记录，原有输入预算继续生效。“预测数值/结果”等名词不单独构成计算指令。 |
| 活性预测的报告解释缺少数值或模型范围 | [web_business.py](../src/web/web_business.py) 的 `model_activity_context()`、[web_request.py](../src/web/web_request.py)、[decision_policy.py](../src/agent/scientific_session/decision_policy.py)；回归 [test_pi_web_activity_context.py](../tests/agent/test_pi_web_activity_context.py) | `prior_activity_context` 只在无新科学执行的解释中传递当前会话已接受的活性记录，保留结构、靶点、数值、单位、阈值、失败／分歧状态和声明的物种范围，并计入既有输入预算。家族预测不能当作亚型实验效力，混合物种不能当作人源特异预测；文献查询和解释后保留原记录，不采纳模型文字作为证据。讨论实验优先级时，单个分子的阈值判定一致不代表不同候选的排序一致，数值小差异也不能证明实验优势。完整只读问句由概念契约识别，追加的新科学操作仍须执行或明确澄清。 |
| 活性复核卡片泄漏原始状态或显示过多小数 | [activity_evidence_inspection.py](../src/agent/activity_evidence_inspection.py)；回归 [test_activity_inspection_presentation.py](../tests/agent/test_activity_inspection_presentation.py) | 摘要默认约四位有效数字并使用中文状态，保留分歧、阈值和已记录的物种范围；明确请求“原始”／`original`／`raw` 数值时保留完整精度。完整审计文本与科学记录不变，展示舍入不得改写判定或触发重算；缺失物种标为未标明。 |
| partial/失败展示与安全摘要 | [agent_result_presentation.py](../src/web/agent_result_presentation.py)、[chat_handler.py](../src/web/chat_handler.py) | 纯投影保留科学正文和失败步骤；兼容入口传入当前 sanitizer 覆写。诊断、WebSocket、历史及资源清理仍由 handler 拥有；不能因提取改变状态或让主模型改写 partial 结果。 |

[PiAgentRuntime](../src/agent/pi_runtime/runtime.py) 连接 [Python 桥接](../src/agent/pi_runtime/bridge.py) 与 [官方 SDK 会话](../runtime/pi/session.mjs)。Pi 管理迭代和上下文；[ScientificSession](../src/agent/scientific_session/controller.py) 保留领域授权、预算、工具执行和证据校验，复用 `WorkflowRunSession`。主模型事务与凭据留在 Python；Pi 不开放内置文件/终端工具或自动扩展发现。

旧 `src/agent/harness/`、`HarnessFactory`、`ModelDecisionLoop` 与 legacy/shadow/canary/LangGraph 通用运行时包装已删除；历史文档中的这些名称不是可启用选项。Node.js 最低 **22.19.0**，推荐 **24.15.0**，必须按 [运行手册](PI_RUNTIME.md) 安装锁定的 `runtime/pi` 依赖。

[Pi checkpoint 校验](../src/agent/pi_runtime/checkpoint.py) 拒绝旧通用循环 checkpoint 的执行续接；历史数据仍可读取，不清库、不自动转换。回滚必须恢复完整 Git 发布版本及匹配的 Python/Node 依赖、npm manifest/锁文件并执行对应版本的 `npm ci`，不能靠 profile 切换回退。停止 Pi 子进程也不等于科学 worker 已取消，仍需取消、等待与排空。

### 已接入 Registry 的类型契约

[build_tool_registry](../src/agent/tooling/factory.py) 按规范工具名选择以下专用 `LegacyPythonToolAdapter` 子类；没有为它们建立第二个执行器或结果规范化循环。

| 规范工具名与实现 | 输入和结果边界 |
|---|---|
| `rag_search`：[rag_contract.py](../src/agent/tooling/rag_contract.py) | 接受字符串或字符串 query；检查检索记录的 source_index、有限相似度与来源结构，保留额外 CSV 字段。来源字段形状正确不等于来源真实，索引/manifest/行映射验证仍由 RAG 服务负责。`rag_database_search` 经工厂别名归一化进入同一适配。 |
| `activity_predictor`：[activity_contract.py](../src/agent/tooling/activity_contract.py) | 兼容字符串与结构化分子/靶点输入；检查原始和规范化成功、partial、失败观察及已知失败快照，复用活性科学校验。不训练、加载或启用模型，不改活性阈值。缺模型/demo 不能因通过类型校验就成为真实预测。 |
| `molecular_docking`：[docking_contract.py](../src/agent/tooling/docking_contract.py) | 兼容文本、单层 query 包装和直接结构；可执行结构须显式给出 receptor、ligand/SMILES、center 与 size，不能借作业模型默认值补 box。复用 pose/能量/输入证明和 OpenSandbox hash、gvisor、image、cleanup 检查；不可信结果按现有规则清除。通过已验证快照传递的沙盒要求不能被外层 local 标记降低。 |

活性请求明确限定物种时，模型选择和检查点身份必须保留该物种；分类与回归模型的登记范围都须明确匹配。`mixed`、`all` 或 `any` 范围不再视为单一物种专属证据，因此以前依赖这些范围接受物种限定的请求现在会返回模型不匹配；未限定物种的家族预测不受影响。

已有活性结果讨论后接“然后／再预测或计算”时，`WebTurnProjection` 仅在前半段完整满足只读概念或活性记录查看契约时分拆。记录查看复用已有确定性展示；复杂讨论由配置中的模型解释当前会话已接受的活性记录，并复用普通解释的字段展示转换。后半段完整交给原有科学校验与执行流程，不能把讨论中的亚型或物种要求串入新预测。缺少记录或解释未生成时保留部分完成状态，新计算拒绝时同时保留旧结果解释及拒绝原因。解释文字不写入科学证据。回归入口：[test_pi_web_mixed_activity.py](../tests/agent/test_pi_web_mixed_activity.py)。

讨论分类／回归分歧与阈值适用范围时，紧连的 `pIC50=5` 等端点等式属于文本，不作为未标注的结构候选；显式 `SMILES:` 及其逗号分隔候选仍严格校验。完整解释请求复用已接受的活性上下文，附加的新计算不能被解释识别吞掉。回归入口：[test_activity_threshold_discussion.py](../tests/agent/test_activity_threshold_discussion.py)。

活性解释上下文保留分类与回归各自已记录的模型标识、任务、端点和单位，缺失时不补造；不同标识不证明训练数据或误差独立。预测一致不等于实验验证，不能替代靶点／物种匹配的实验数据，也不是有效实验结果必须满足的前提。解释不应将两项预测笼统称为同一模型的输出，除非原记录支持；这些身份字段用于解释依据，不主动堆入结果正文。

活性解释的展示转换同时处理已知字段和值组合：分类／回归一致性显示为“一致／不一致”，`species = mixed` 显示为“混合物种”。不全局替换布尔词或 `mixed`；显式字段提问保留对应原词。转换仅影响普通与混合请求的解释正文，不改写科学记录。回归：[test_activity_explanation_display.py](../tests/agent/test_activity_explanation_display.py)。

三个输出视图的 `schema_version` 均为类元数据 `1`，不是新增 HTTP 字段。它们校验观察而不把 `model_dump()` 投影写回科学结果；既有 `execute_tool_compat` 继续负责结果规范化，合法状态、warnings、artifacts、evidence、quality 与来源不应在类型迁移中丢失。工具归属、一次调用内的超时/并发槽与关闭仍沿用既有适配层。

其余工具尚使用 `LegacyQueryInput(query: Any)` / `output_schema=None`，但并非没有领域或 Session 校验。`prepare_receptor`、`prepare_ligand`、`run_docking`、`get_docking_result` 四个辅助工具也未因本次契约迁移开启其 `run()` 执行路径。类型契约和合成 pose 测试不替代真实 Vina 或模型验收。

## 3. 模型、RAG、持久化各自负责什么

- **主聊天模型**：应用配置加载与切换在 [app.py](../src/web/app.py)、[user_llm_config.py](../src/web/user_llm_config.py)，OpenAI-compatible 适配在 [openai_compatible_model.py](../src/agent/openai_compatible_model.py)。[model_lifecycle.py](../src/web/model_lifecycle.py) 让聊天、设计推荐、后台工作流共享排空边界：在途请求固定模型，切换等待实际消费者结束后关闭旧客户端。取消或任务表终态不等于底层 worker 已退出；挂起的 worker 仍可能让排空持续等待，本机制不强杀它。配置值、Key、运行时用户文件不得进入文档或 Git。
- **分子生成模型**：应用独立构造 `molecular_generator_model`，默认本地 Ollama `gmm-llama:latest`；主模型切换不能重绑它。设计页主模型推荐入口另在 [design_routes.py](../src/web/routes/design_routes.py)、[molecular_design/service.py](../src/molecular_design/service.py)，不要与 SMILES 生成模型混为一谈。
- **RAG**：唯一 `RAGSystem` 在 [rag/service.py](../src/rag/service.py)，索引/manifest、不可变快照与原子写入在 [rag/index.py](../src/rag/index.py)，同步/异步检索共用 [rag/retrieval.py](../src/rag/retrieval.py) 的行映射与来源校验。工具 [rag_search_tool.py](../src/agent/tools/rag_search_tool.py) 接受应用显式注入的同一服务，不读取全局 Web 单例或另起 embedding 配置。`app.py.RAGSystem` 和 [web/rag_index.py](../src/web/rag_index.py) 保留同一对象兼容导出；测试注入点使用规范领域模块。未初始化或来源/索引不兼容时，同步服务抛出异常，工具适配器返回失败；异步兼容接口记录 warning 并返回空列表，不能仅凭空列表区分检索不可用与无命中。任何路径都不能把 FAISS 标签直接当原始行号。Web 展示投影另在 [rag_presentation.py](../src/web/rag_presentation.py)，保留 source_index/provenance。
- **执行证据**：[event_bus.py](../src/agent/runtime/event_bus.py) 与 [SQLiteAgentStateStore](../src/agent/persistence/sqlite_store.py) 记录运行、事件、检查点及续接；[redaction.py](../src/agent/persistence/redaction.py) 负责敏感内容处理。排障用 trace_id 对齐实际步骤、工具结果和 artifact，不能只看最终文本。
- **后台工作**：[TaskManager](../src/task_runtime/manager.py) 是工作流 API 使用的提交入口；[TaskRuntime](../src/task_runtime/runtime.py) 是带 staging、幂等、后端选择和收尾的异步科学任务门面，两者不是同一个类。Temporal/OpenSandbox 等边界见 [task_runtime](../src/task_runtime)；不要因都是“任务”就机械合并或删除资源清理。
- **聊天与续接**：[agent_session_config.py](../src/web/agent_session_config.py) 组装服务端匿名会话，正式 WebSocket/工作流从 scope 取身份，任务访问按服务端归属过滤；它不是实名登录授权。聊天历史仍按连接创建，六处终态共用 `_append_history` 原位保留末20条，不是完整断线历史持久化。隔离决策续接与本分支首页科研引用各自保留保护；首页“第3个分子”通过实际展示ACK、版本、归属和失效校验解析，不从历史文本猜SMILES。详见下方科研续接定位。

## 4. 兼容代码与迁移状态

| 保留项 | 原因与删除门槛 |
|---|---|
| 已删除的旧 Skill 对象层与保留名称 | `src/agent/skills/`、`BaseSkill`、`SkillRegistry` 已移除，声明信息由 [WorkflowCatalog/WorkflowPolicy](../src/agent/workflows/catalog.py) 承接；[删除守卫测试](../tests/agent/test_no_legacy_skill_layer.py) 防止旧 import 回流。`SkillRouter`、`selected_skill`、`active_skill`、`skill_name` 是保留的兼容名称，不代表旧 Skill 对象仍存在，也不代表工作流已删除。 |
| [agent_executor.py](../src/agent/agent_executor.py) | MolecularAgent 保留公开接口，仅适配参数和返回形状；委托 Supervisor/Session，不再逐工具 should_use 或直接执行。partial 为 success=false，拒绝/取消状态和失败观察保留。不是正式聊天工厂。 |
| [react_agent.py](../src/agent/react_agent.py) | ReAct 保留公共构造、execute/set_llm/should_use_tools/format_result 及 ReActStep 导出；执行委托 Supervisor/Session，删除私有 action 文本循环和关键词回退，不承诺旧自由推理轨迹。规范结果保留失败/partial及有序观察，tools_used 来自真实开始事件；主模型不替换专用分子生成器，不关闭借入工具。不是生产入口切换。 |
| [routes/page_routes.py](../src/web/routes/page_routes.py)、[routes/websocket_routes.py](../src/web/routes/websocket_routes.py) | `routes/__init__.py` 仍公开导出。页面兼容层已委托 `register_main_routes()`；WebSocket 兼容注册直接委托 ChatHandler。不得在正式应用重复注册 `/ws`。 |
| 已删除的 app.py 旧私有聊天/提示函数、静态备份 | `_handle_websocket` 及仅服务它的三个提示 helper、旧应用级历史已删除，正式 `/ws` 仍唯一委托 ChatHandler。无引用的 `activity_prediction_v2.legacy.backup.js` 已删除；公共占位 `script.js`、`activity_prediction_v2.js` 保留，真实页面与静态路由测试检查加载次序、200/404 和清理。 |
| 工具输入/输出 schema | RAG、活性、对接已接入上表专用类型边界；其他工具仍为通用兼容边界。逐工具迁移，不将“已迁移三个工具”说成所有工具都已类型化，也不另造工作流 DSL。 |

### 历史拆分基线（非 Pi 验收）

以下 T 编号与 PR 状态记录此前的拆分基线，不是当前 Pi 替换的验收结论。该基线已包含：状态汇总和工具归属、共享 Session 委派（T02/T04/T07）、靶点身份对齐（T08）、匿名会话归属、T01 RAG 统一、T03 输入预算、T05 消费者排空、T06 三类清理、T11-A RAG 服务/索引归位、T10-A 三个优先工具类型契约，以及独立批准的测试分层与新旧模板签名适配。

本次 T10-B 将 ADMET、综合评价、靶点设计、分子生成、先导优化的步骤描述归入 `step_templates.py`。`WorkflowPlan` 的定义/导入身份、TaskPlanner 的辅助解析及其他分支不动；模板不调用工具、不复制执行器。后续如需继续拆分选择与解析，须单独证明行为等价。

T09 科研对象跨轮引用已通过 PR #60 合并，具体有界能力见下节；T11-B 两个旧 Agent 的公共接口已薄适配，不再把这项列作完全未实施。两个无状态聊天模块已通过 PR #63 合并，原 helper 保留薄委托。当前领域路由拆分按八个模块维护：`docking_routes`、`molecule_utility_routes`、`docking_report_routes`、`reverse_target_routes`、`activity_prediction_routes`、`activity_model_routes`、`molecule_properties_routes`、`agent_metrics_routes`；发布/验证以本批交接为准。固定 API 基线保留各已验证 FastAPI/Pydantic profile 的完整描述，不抹去框架差异；未知版本要求补旧实现基线。性质端点的既有启发式 ADMET 标签问题单独登记，搬迁测试不构成科学有效性证明。

### 路由兼容边界（2026-10-09）

四个仍需要动态旧调用兼容的模块已经拆成两层：`register_activity_prediction_routes`、
`register_activity_model_routes`、`register_molecule_utility_routes` 和
`register_docking_report_routes` 只接受窄依赖，`api_routes.setup_api_routes()` 只调用这些生产入口；
同名 `setup_*_routes` 仅作为旧直接调用的兼容包装器保留 `_support`。因此 `_support` 不再进入正式应用注册路径，
但旧调用者的注册后替换行为仍保持不变。对应边界回归见
`tests/test_route_compatibility.py` 与 `tests/test_api_route_boundary.py`。

该历史台账当时仍列出其他工具类型化、Planner 选择与参数解析拆分及历史残差逐项核对；完整第1–8项见[完成台账](handoff/remaining-through-step8.md)。不能把匿名身份、三个工具迁移、纯模板提取或文档更新视为整个任务书已完成；其中首页尚未切换的描述已被本页第 1 节 Pi 入口取代。

本节只说明核对基线，避免把未合并改动描述成当前行为；不复制各批历史测试数量。每批合并后应删去相应“待发布”表述并更新源码定位。

### 科研对象续接（已合并，非生产部署声明）

- 权威对象：[scientific_references.py](../src/agent/contracts/scientific_references.py)；专用原子存储：[persistence/scientific_references.py](../src/agent/persistence/scientific_references.py)。确认的展示顺序、来源观察、修订和归属共同定位候选，固定24小时，不随恢复/重放续期。
- Web投影及解析：[web/scientific_references.py](../src/web/scientific_references.py)；确认/恢复路由：[scientific_reference_routes.py](../src/web/routes/scientific_reference_routes.py)。来源版本变化、失效、跨会话、协议损坏失败关闭，不从客户端SMILES或聊天历史重建事实。
- 首页：[scientific_references.js](../src/web/static/js/home/scientific_references.js) 只在标签页保存不透明指针；实际挂载与严格顺序ACK之后才可引用。刷新恢复有效数据时需先进入可见聊天模式，多集合需明确选择，清除选择不删除科研历史。
- 实际输入：[resolved_molecule.py](../src/agent/contracts/resolved_molecule.py) → Supervisor/AgentContext/Planner → Session。新SMILES优先，改靶点只继承结构、不继承旧分数；工具分发和checkpoint复用前再次复核来源，不跨长工具持有SQLite锁。
- 验收入口：`tests/agent/test_scientific_reference_*.py`、`tests/home_scientific_references_test.js`；显式离线浏览器夹具 `python -B -m tests.scientific_reference_browser_lab --state-dir <本任务临时目录> --port 6017`。夹具不是生产启动方式，不读取用户模型配置；生成是合成候选，性质调用真实RDKit。

取消等待方会先排空已开始的科学worker，再释放请求租约；这不是强制停止外部计算进程的保证。浏览器服务进程重启验证针对空闲时中断与持久化恢复，不等于运行中任意点硬崩溃后恰好执行一次。完整证据和保留限制见[第三增量交接](handoff/scientific-reference-acceptance.md)。

## 5. 验证入口与常见误判

在本机 Windows 使用项目 Conda 环境 `MedChat` 执行（先运行
`conda activate MedChat`），再查看 [tests/conftest.py](../tests/conftest.py) 的用户配置隔离。
涉及数据库的独立探针必须用临时路径，禁止读写用户运行库或真实 Key。

Pi 的安装、健康检查与 SDK 离线契约见 [Pi 运行手册](PI_RUNTIME.md)；通用命令见[项目规范第 11 节](PROJECT_STANDARDS.md#11-测试规范)。Python 边界回归定位到 `tests/agent/test_pi_*.py`、`tests/test_web_chat_profile.py`、`tests/test_health_pi_runtime.py` 和 `tests/agent/test_supervisor_direct_execution.py`，不再使用已删除的旧 harness 测试作为当前验收入口。

```powershell
python -B -m pytest tests/agent/test_app_supervisor_entrypoint.py tests/test_web_app_lifecycle.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_supervisor_delegation.py tests/agent/test_workflow_run_session.py tests/agent/test_workflow_resume.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_candidate_alignment.py tests/agent/test_registration_consistency.py tests/test_rag_index_manifest.py -q -p no:cacheprovider
python -B -m pytest tests/test_rag_service_boundary.py tests/test_rag_index_manifest.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_rag_tool_contract.py tests/agent/test_activity_tool_contract.py tests/agent/test_activity_contract_integration.py tests/agent/test_docking_tool_contract.py tests/agent/test_docking_contract_integration.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_planner_step_templates.py tests/agent/test_planner_template_execution.py tests/agent/test_task_planner.py tests/agent/test_plan_compiler.py tests/agent/test_binding_resolver.py tests/agent/test_workflow_executor.py tests/agent/test_decision_loop.py -q -p no:cacheprovider
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

### 多行候选比较输入

首页使用原生三行 `textarea` 保留多个 SMILES 的换行；Enter 发送，Shift+Enter 换行。解析“以下／下列分子”的指标列表时，语法主语不作为未知指标；hERG／BBB 预测绑定到已有 ADMET 端点，显式 SMILES 字段仍整段校验。“不要给综合评分”仅限制展示，不应让性质与 ADMET 批量请求退出已验证的执行路径。“只列出这些／这两个／这两项指标”等完整展示指代句不新增计算义务；附带未知指标的句子仍保留检查。BBB 简称在端点选择去重前归并到 BBB_Martins，避免重复的假缺失行。未知指标、非法结构和实际工具禁用仍按原规则处理。回归：[test_candidate_list_workflow.py](../tests/agent/test_candidate_list_workflow.py)、[home_multiline_composer_test.js](../tests/home_multiline_composer_test.js)；真实页面另验粘贴、键盘发送及布局。

### 候选预测的优先级解释

已有候选结果的解释按端点读取阈值，不能借用另一个端点的阈值或默认填入 0.5。没有校准、逐分子不确定性或模型误差记录时，应说明这些信息未知，不把分数差异称为显著、可忽略或处于误差范围内，也不声称已经证明模型未经校准。可以在明确项目目标和假设后提出暂定实验顺序，并说明靶点活性、选择性等缺失证据如何影响后续判断；分类分数高不等于实际性能更优。不能替用户假定项目偏好来强行选出候选；证据不足时可提出并行验证或条件分支。可修饰位点不构成选择性更好或脱靶风险更低的证据。BBB 简表显示“血脑屏障通透性（分类分数）”，结构化原始端点名保留。BBB_Martins 的二分类任务定义参见 [TDC 官方说明](https://huggingface.co/tdc/BBB_Martins-AttentiveFP)；这不证明本地模型已完成概率校准。回归：[test_pi_web_candidate_context.py](../tests/agent/test_pi_web_candidate_context.py)、[test_candidate_list_workflow.py](../tests/agent/test_candidate_list_workflow.py)。

### 原生决策 JSON 纠正

原生响应仅在单一调用、函数名和调用标识合法、没有附带正文且参数 JSON 无法解析时，可使用原有一次协议纠正额度重新请求完整决策。它与 schema／多调用纠正共享次数、模型请求预算和截止时间；不执行或回放损坏提案，不保存原始响应，不自动补写 JSON。Pi 只接受服务器生成的固定纠正文本，修正后的决策仍经过原有工具授权和证据校验。检查点保留有限结构元数据，继续任务不重置额度，也不重复已接受的工具调用。回归覆盖传输、反馈投影、预算、授权和 Web 继续执行：[test_decision_protocol_recovery.py](../tests/agent/test_decision_protocol_recovery.py)、[test_pi_provider.py](../tests/agent/test_pi_provider.py)、[test_decision_transport_boundaries.py](../tests/agent/test_decision_transport_boundaries.py)、[test_pi_web_profile_runtime.py](../tests/agent/test_pi_web_profile_runtime.py)。

### 解释正文与文献检索展示

首页通用正文格式化不再凭“相关文献”等文字合成文献检索结果卡片；提到查阅资料不表示本轮执行过检索。已有正文、表格和安全链接继续正常渲染，明确调用的检索格式化接口保留。回归：[home_literature_presentation_test.js](../tests/home_literature_presentation_test.js)，并检查比较表格、科学记号、链接安全和消息运行时。

## 6. 修改时的最小检查顺序

1. 先确认当前分支和工作树，不覆盖原始混杂改动；为本任务建立独立分支。
2. 从实际入口复现，再定位路由、绑定、适配或呈现边界；不要只复制表达式做“通过”探针。
3. 修改契约时同时测正式组装路径、兼容路径、partial/failed、取消/清理和来源字段；削弱测试不是修复。
4. 保留首个失败及原因，记录当前 SHA、解释器、真实命令、退出码和 skipped 原因；不粘贴密钥或聊天隐私数据。
5. 独立审查、精确暂存、CI 门禁和授权齐备后再合并。文档、schema 或目录重命名都不能代替科学验收。

### 明确只读的模型证据讨论

`concept_request.py` 的 `_model_evidence_consultation()` 沿用完整分句契约，支持未核验的 AUC 前提、单分子正确率与分数差异解释，以及校准资料核对方法。每个分句必须完整匹配有界的证据角色，并有明确的不执行要求；未知分句、嵌入的新操作或新数值请求不能被任意说明文字吞掉。没有讨论对象的“只解释判断依据”不单独构成完整请求。该入口只覆盖列出的问法类型，不是通用语义分类器，其他表达继续走已有契约。

接受后复用零步骤的普通聊天路径与已有候选上下文，用户假设的 ROC-AUC 等信息不能写入已接受的候选记录。完整单句复述按文本转换处理，追加独立句子必须重新判定。回归入口：[test_read_only_scientific_consultation.py](../tests/agent/test_read_only_scientific_consultation.py)；同时验证概念路由、科学义务、候选上下文与展示回归。

在完整 Web 模式，遇到“不运行任何工具”等独立指令且已有概念契约未覆盖时，[decision_runtime.py](../src/web/decision_runtime.py) 将完整原文交给前端已配置的模型判断意图。只读问答可使用已接受的结果，但没有工具权限；请求同时要求执行、意图不明或分类失败时，不回退到科学执行。引文内的禁令、只禁止某一种操作的措辞不选择这个模式。分类也消耗本轮模型调用和输入预算。需要澄清时，同一连接和断线重连都根据原请求保存的权限续接。回归入口：[test_web_tool_free_consultation.py](../tests/agent/test_web_tool_free_consultation.py)。

当完整 Web 请求因活性靶点不明确被拒，而原文同时包含 IC50、EC50、Ki 或 Kd 及浓度值时，运行时也可复核整句意图。用户给出的测定数据可供解释，模型明确判为只读时进入零工具问答。混合请求保留原拒绝原因。已编译的计算计划、结构引用、改写后的请求与续接任务各自沿用原流程。

如果性质编译器返回通用能力拒绝，而用户明确限定“仅据这些信息”等证据范围，同一意图分类器会复核完整原文。只有只读解释可转入咨询。带新计算的混合请求保留原计划的拒绝结果。两种结果都在 `consultation_admission` 记录分类调用次数。例如，用户给出的指纹相似度可用于讨论覆盖范围，不能作为本次执行过相似度计算的证据。

完整 Web 聊天在发布回答前核对明确的三句要求。对随机划分与骨架泛化的因果解释、并列分数的 AUC 解读，前端配置的模型还会根据原题复核草稿，区分已观察到的结果和可能原因。一次回答最多安排一次内容改写（科学复核或句数纠正）和一次协议修复，两者都受本轮模型请求数和截止时间限制。例如，复核结果缺少必填的 decision.version 时，系统保留复核要求，并让模型在同一轮重新提交完整结构。再次违反协议时终止。草稿只作为该次出站请求的 assistant 消息，不写入用户对话历史或用作工具证据。再次超出句数或仍命中已知的无条件骨架结论时返回具体原因。“能由此认定……”这类推断问题若误入候选选择，会在未绑定候选时复核整句意图；混合执行要求沿用原拒绝。页面将正文里的 BBB 端点标识显示为 BBB，明细保留原始键。

涉及骨架或适用域时，问答策略提示模型核对训练集近邻和目标化学系列上的验证表现。涉及概率校准时，提示模型区分 Brier 总体误差和分组校准，并核对独立带标签样本中的预测概率与实际发生频率。两组说明只用于回答相关问题。维护依据见[骨架划分实证研究](https://doi.org/10.1021/acs.jcim.5c00475)与[概率校准文档](https://scikit-learn.org/stable/modules/calibration.html)。

若用户提供同序的 0/1 标签和多个模型的阳性概率，并请求比较 Brier 分数和分组校准，Web 可以直接核算支持的完整输入。示例：“标签：0,1,1；模型A: 0.21,0.22,0.22；模型B: 0.5,0.5,0.5。请核对各模型的 Brier 分数和分组校准误差。” 每个不同概率单独成组，按组样本数加权比较预测概率与实际阳性率的绝对差。后续讨论通过同一连接既有的历史保留规则获得实际展示的报告。[核算代码](../src/web/calibration_report.py)及[入口回归](../tests/agent/test_supplied_calibration.py)包含改变标签、相邻概率、相同得分和独立操作的例子。
