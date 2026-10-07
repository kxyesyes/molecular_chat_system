# Agent 当前架构与维护入口

核对日期：2026-10-07。本页描述当前源码和维护边界，不代表某台服务器已启用相应模式。
基线与本轮验证见 [latest handoff](handoff/latest.md)。
协作规则见 [AGENTS.md](../AGENTS.md)，工程规范见 [PROJECT_STANDARDS.md](PROJECT_STANDARDS.md)。
历史计划、PR 阶段结论和测试数量留在交接记录，不作为当前架构声明。

## 1. 用户入口和模式必须分开核对

| 入口 | 源码路径与调用 | 边界 |
|---|---|---|
| 默认网页聊天 | [main.py](../main.py) → [app.py](../src/web/app.py) 的 `/ws` → [ChatHandler](../src/web/chat_handler.py) → `_create_chat_agent()` 构造的 [SupervisorAgent](../src/agent/supervisor.py) | 默认 legacy profile；科学结果经过注册表与工作流，普通聊天不等于科学工具执行。不是旧 ReAct 自由循环。 |
| 显式 Web 决策模式 | `resolve_web_chat_profile()` → [WebDecisionRuntime](../src/web/decision_runtime.py) → ChatHandler 的决策桥接 → [decision_chat.py](../src/web/decision_chat.py) / [ModelDecisionLoop](../src/agent/harness/decision_loop.py) | 由服务端选 profile，不能凭浏览器字段任意扩权；已接入应用不等于默认或生产已启用。 |
| 工作流 HTTP API | [agent_workflow_routes.py](../src/web/routes/agent_workflow_routes.py) 的 plan/run → 应用注入 `_create_supervisor_agent()` | 复用 Registry；正式 run 经 ModelRequestGate/TaskManager 执行 Supervisor。独立路由构造的兼容路径不等于生产完整组装。 |
| 领域 HTTP API | [api_routes.py](../src/web/routes/api_routes.py) facade → 同目录领域路由 | 不是全部都先经过聊天 Agent；必须保留各领域输入、会话归属、权限和服务生命周期。 |
| 独立决策验收 | [decision_lab.py](../src/web/decision_lab.py)、[run_decision_chat_acceptance.py](../scripts/run_decision_chat_acceptance.py) | loopback 隔离应用/CLI；不自动切换网页或生产配置，不能用实验入口通过替代真实网页验收。 |

### 配置是不同的控制面

以下是源码默认值，不是本机运行环境探测结果：

| 控制项 | 默认 / 支持项 | 来源与含义 |
|---|---|---|
| `MEDCHAT_CHAT_PROFILE` | `legacy`；可选 `decision_a2`、`semantic_v1` | app.py 的 profile 解析；semantic_v1 将 decision_a2 与语义准入组合，要求受支持的主模型适配器和异步组装 |
| `MEDCHAT_DECISION_WIRE_MODE` | `native`；可选 `json` | 决策协议传输形式，不是换科学模型 |
| `AGENT_HARNESS_MODE` | `legacy`；`shadow`、`langgraph_canary` | [HarnessFactory](../src/agent/harness/factory.py) 对工作流执行适配；legacy 指现有执行器 |
| `AGENT_LANGGRAPH_CANARY_PERCENT` | `0` | 有界 LangGraph 灰度，不自动扩展委派执行范围 |
| 任务后端与沙盒 | 开发/未放量保持 local 和 canary 0 | [TaskRuntimeConfig](../src/task_runtime/config.py)、[Temporal runbook](runbooks/temporal_docking_canary.md)、[OpenSandbox 部署说明](../deployment/opensandbox/README.md)；与 Web profile 无等价关系 |

Web profile/协议非法值拒绝；Harness 未知模式或可选依赖不可用则记录 warning 并回到既有执行器。
`AGENT_HARNESS_MODE=langgraph` 不是已开放的全量执行模式。执行器兼容回退不允许科学结果模拟。
修改文档或导入模块都不授权放量、切换主模型、启用沙盒或重启用户服务。

## 2. 意图到证据的真实维护路径

```text
默认 Web / 工作流 API
  → Supervisor → Router + WorkflowCatalog
  → TaskPlanner → Compiler / Bindings → Harness / WorkflowExecutor
显式 Web 决策模式
  → WebDecisionRuntime → ModelDecisionLoop（准入、预算、授权、续接）
两条科学执行路径
  → WorkflowRunSession → Registry adapter / specialist → 领域工具
  → 领域 Validator → ToolResult / AgentResult → 状态/事件/来源 → 安全展示
```

这是维护导航，不表示所有 HTTP 领域接口都经同一聊天路径。
遇到问题先确认实际入口/profile，再按下表定位：

| 问题 | 优先源码 | 不得破坏的契约 |
|---|---|---|
| 路由、否定意图、靶点别名 | [router.py](../src/agent/router.py)、[routing/hybrid.py](../src/agent/routing/hybrid.py)、[target_request.py](../src/agent/contracts/target_request.py) | 输入不足澄清，不能把搜索自动升级为设计或 docking |
| 工作流选择、参数和顺序 | [task_planner.py](../src/agent/planning/task_planner.py)、[workflow_selection.py](../src/agent/planning/workflow_selection.py)、[request_parsing.py](../src/agent/planning/request_parsing.py)、[step_templates.py](../src/agent/planning/step_templates.py) | 选择/解析已拆成模块；模板只描述步骤，不复制执行器 |
| 上下游数据流 | [compiler.py](../src/agent/planning/compiler.py)、[bindings.py](../src/agent/planning/bindings.py)、[run_session.py](../src/agent/runtime/run_session.py) | output_key 不等于已消费；检查真正送入下游的候选、靶点记录、类型和来源 |
| 工具归属、schema、别名 | [factory.py](../src/agent/tooling/factory.py)、[registration.py](../src/agent/tooling/registration.py)、[specialists](../src/agent/specialists) | 未知归属拒绝、别名归一化、复用已有工具池 |
| 状态、重试、恢复 | [workflow_executor.py](../src/agent/runtime/workflow_executor.py)、[delegated_executor.py](../src/agent/runtime/delegated_executor.py)、run_session.py | required/optional 失败语义、checkpoint 身份、取消/资源排空不被另写循环绕过 |
| 模型动态动作 | [decision_loop.py](../src/agent/harness/decision_loop.py)、[decision_policy.py](../src/agent/harness/decision_policy.py)、[ordinary_chat_policy.py](../src/agent/harness/ordinary_chat_policy.py) | 动作受能力、开关、请求、证据、预算和批准约束；模型提议不等于执行授权 |
| 错误、超时与兼容返回 | [adapters.py](../src/agent/tooling/adapters.py)、[result.py](../src/agent/contracts/result.py) | 保留状态、错误、warnings、artifacts、quality、evidence、provenance |
| 候选/数值真实性 | [validators](../src/agent/validators)、[candidate_ranker.py](../src/agent/tools/candidate_ranker.py)、[lead_optimization_verifier.py](../src/agent/tools/lead_optimization_verifier.py) | 缺证据不是优势，优化必须有匹配前后比较；主模型不能补造分数 |
| 提示预算 | [prompt_budget.py](../src/web/prompt_budget.py)、[chat_prompt_builder.py](../src/web/chat_prompt_builder.py) | 字符预算非精确 token；不截断 SMILES/JSON，放不下的关键工具事实不强行交模型润色 |
| 结果不显示、partial 丢失 | [agent_result_presentation.py](../src/web/agent_result_presentation.py)、[scientific_report.py](../src/web/scientific_report.py)、[home/main.js](../src/web/static/js/home/main.js) | task 终态、科学正文、失败步骤和来源投影一致；工具完成不等于回答完成 |

### 工具契约覆盖

当前 capability catalog 中各能力均在 factory 中配置输入/输出 schema，
详见唯一清单 [AGENT_TOOL_CONTRACTS.md](AGENT_TOOL_CONTRACTS.md)。
不再使用“只有 RAG/活性/对接三个工具类型化”的历史描述。
四个 staged docking helper 仍是受限兼容例外；不能因为存在 run 方法就开放给主模型。
类型 schema 不替代执行证据或领域校验，也不能把失败字典规范化成成功文本。

## 3. 模型和科学事实的边界

| 组件 | 能做什么 | 不能据此声称什么 |
|---|---|---|
| 外部主模型 | 理解意图、提出受控动作、解释已验证证据 | 没调用工具也算出了 pIC50、ADMET、结合能或实验结论 |
| 本地 Ollama `gmm-llama:latest` | 生成候选 SMILES；由候选校验再决定可用结构/数量/唯一性 | 文本条数等于有效唯一候选数；主模型可替代专用生成器 |
| RDKit | 真实描述符和结构/规则判断 | 规则判断等于实验结果 |
| ADMET / RG-MPNN | 有可用后端/真实权重时输出带方法与模型来源的预测 | demo、缺模型、回退或 unknown 是真实成功；训练完成等于生产已启用 |
| 靶点库 / 反向寻靶 / RAG | 数据库、结构、相似度和检索来源证据 | 相似度命中等于实验证明；无检索结果仍可编造引用 |
| Vina | 经完整 receptor/ligand/box 和受控执行产生 pose、能量与 artifact | 工具注册/合成 pose/缺输入时已有真实 binding energy |

主模型配置由 [user_llm_config.py](../src/web/user_llm_config.py) 管理，网页从仓库外用户私有配置读取；
不从仓库 .env 或 OPENAI_COMPATIBLE_* 自动恢复旧 Key。独立验收 CLI 另用显式环境变量。
这是应用功能和测试注入的两种边界，不能为测试去读取用户配置。
保存配置与连接成功、模型可用是不同状态，参见 [README](../README.md#主模型接入与一次保存)。

应用独立构造 molecular_generator_model；主模型切换不得重绑它。
[model_lifecycle.py](../src/web/model_lifecycle.py) 管理请求租约/排空；取消等待者或任务表终态不等于 worker 已退出。
设计页主模型推荐在 [design_routes.py](../src/web/routes/design_routes.py) 和
[molecular_design/service.py](../src/molecular_design/service.py)，不要与专用 SMILES 生成器混淆。

## 4. RAG、状态、任务与安全边界

- RAG 服务在 [rag/service.py](../src/rag/service.py)，索引/manifest 在 [rag/index.py](../src/rag/index.py)，检索行映射/来源校验在 [rag/retrieval.py](../src/rag/retrieval.py)。
  工具接受应用注入的同一服务，不另建全局 Web 单例。FAISS 标签不能直接当原始行号。
  未初始化/索引错误与合法无命中要区分；异步兼容接口可 warning+空列表，不能只凭空列表认定无记录。
- [event_bus.py](../src/agent/runtime/event_bus.py)、[SQLiteAgentStateStore](../src/agent/persistence/sqlite_store.py) 保存运行、事件、checkpoint 和续接；
  [redaction.py](../src/agent/persistence/redaction.py) 负责脱敏。用 trace_id 对齐真实调用、输入绑定和 artifact，不只看最终文字。
- [TaskManager](../src/task_runtime/manager.py) 的后台提交与 [TaskRuntime](../src/task_runtime/runtime.py) 的 staging、幂等、后端选择、收尾不是一个类。
  Temporal 对已接受任务拥有执行权，SQLite 是查询投影，不能因结果不明就 local 重跑。
- OpenSandbox 是受控 docking 执行边界，不是让整个聊天 Agent 任意执行 shell 的通用沙盒。
  真实验收需要服务、权限、隔离、pose/hash/provenance 和 cleanup 证据，Windows 静态测试不替代 Linux/QEMU 验收。
- [agent_session_config.py](../src/web/agent_session_config.py)、[request_auth.py](../src/web/request_auth.py) 等负责服务端会话和请求边界。
  匿名会话归属不等于实名登录/RBAC；不能为测试移除 Origin、CSRF、所有权或本机配置权限校验。
- 前端动态数据用 [safe_render.js](../src/web/static/js/shared/safe_render.js) 及现有安全 DOM 投影；
  不把模型、工具或历史文本直接拼进 innerHTML/onclick。
- TTL、私有文件权限、保留与显式清理见 [数据留存策略](security_data_retention_policy.md)。文档更新不执行清理作业。

### 跨轮科研对象引用

[scientific_references.py](../src/agent/contracts/scientific_references.py) 和
[专用存储](../src/agent/persistence/scientific_references.py) 绑定展示顺序、来源观察、修订和归属。
确认引用固定 24 小时，不随恢复/回放续期。
[Web 投影](../src/web/scientific_references.py)、[确认/恢复路由](../src/web/routes/scientific_reference_routes.py)、
[前端 ACK](../src/web/static/js/home/scientific_references.js) 共同验证实际显示对象；
“第3个分子”不能从历史文本猜 SMILES，跨会话/失效/版本变化失败关闭。

[resolved_molecule.py](../src/agent/contracts/resolved_molecule.py) 将引用送入 Supervisor/Planner/Session；
新 SMILES 优先，改靶点只继承结构，不继承旧分数；分发/checkpoint 复用前再验证。
聊天历史的有界连接内缓存不等于完整断线持久化，也不保证任意时刻崩溃后恰好执行一次。

## 5. 兼容层与历史资料

| 项目 | 当前边界 |
|---|---|
| 旧 Skill 对象 | `src/agent/skills/`、BaseSkill、SkillRegistry 已删除；[WorkflowCatalog](../src/agent/workflows/catalog.py) 承接声明；[删除守卫](../tests/agent/test_no_legacy_skill_layer.py) 防回流 |
| SkillRouter / active_skill / selected_skill / skill_name | 保留接口名，不代表旧 Skill 对象还存在 |
| [agent_executor.py](../src/agent/agent_executor.py)、[react_agent.py](../src/agent/react_agent.py) | 公共兼容薄适配，委托 Supervisor/Session；不是另一个生产工具执行器 |
| [page_routes.py](../src/web/routes/page_routes.py)、[websocket_routes.py](../src/web/routes/websocket_routes.py) | 兼容注册入口，不得在应用重复注册 /ws |
| `.agent/skills/` | .gitignore 排除的本机编码辅助资源，不是可部署运行时模块 |
| plans/specs/旧 handoff | 当时基线和授权的历史材料；已实现/待办必须在当前源码重新核实 |

## 6. 验证入口

以下是维护命令，**不是本页声称已运行的结果**。先读 [tests/conftest.py](../tests/conftest.py)，
隔离用户配置与数据库；不要将临时探针指向真实资产/用户库。
离线测试与真实工具验收分开；CPU CI 与 GPU 部署 profile 不混用。

```powershell
python -B -m pytest tests/agent/test_app_supervisor_entrypoint.py tests/agent/test_web_decision_runtime.py tests/agent/test_ordinary_web_runtime.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_tool_contract_inventory.py tests/agent/test_tool_registry.py tests/agent/test_registration_consistency.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_task_planner.py tests/agent/test_binding_resolver.py tests/agent/test_workflow_run_session.py tests/agent/test_workflow_resume.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_no_legacy_skill_layer.py tests/agent/test_lead_optimization_verifier.py tests/agent/test_real_acceptance_checks.py -q -p no:cacheprovider
node tests/home_agent_task_panel_test.js
node tests/frontend_safe_render_test.js
node tests/home_scientific_references_test.js
git diff --check
```

全量/分区命令见 [AGENTS.md](../AGENTS.md) 和 [.github/workflows/quality.yml](../.github/workflows/quality.yml)。

[Agent 验收 CLI](../scripts/run_agent_acceptance.py)：

| 模式 | 证明什么 | 不证明什么 |
|---|---|---|
| contract | 路由、计划、受控 stub/契约 | 真实模型或科学工具闭环 |
| replay | 从已有结构化报告离线复核 forbidden/truth/provenance | 新一轮真实执行；必须保留原报告，输出另一个路径 |
| real | 默认 golden；也支持 diverse、all-real 或 dataset，repeat 用于稳定性 | 主模型连接成功不等于 Web 实际入口完成任务 |
| all | contract + 选定 real | 依赖缺失可忽略 |

当前脚本 exit 0 也可能是 partial；real 顶层将部分失败汇总为 partial，
且 external_main_model 不在 required 检查列表中。
必须查看 checks、逐 case status、truth_checks、warnings、tool_provenance、artifacts 和 stability，
不能用顶层标签或退出码宣称“全通过”。
科学验收还要检查生成候选进入下游、反向靶点进入检索、真实 pose/能量/模型状态；
缺模型、demo/fallback、不完整输入、错误来源必须如实失败或部分完成。

## 7. 修改时的最小检查顺序

1. 确认仓库、分支、HEAD、已有改动和实际入口/profile；历史目录不等于当前部署。
2. 从用户入口复现，定位路由/输入绑定/执行/呈现边界，保留首个失败。
3. 代码变更补正式组装、兼容、partial/failed、取消/恢复、来源与权限测试；不得削弱门禁。
4. 更新相应指导页和 latest，记录真实命令、环境、结果、未运行项及待确认事项；不写秘密。
5. 精确暂存，独立审查/CI；仅在明确授权后合并/部署。本页不授予生产操作权限。
