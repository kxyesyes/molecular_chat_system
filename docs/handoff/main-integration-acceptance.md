# main 集成验收与任务书对账（2026-09-25）

## 范围与执行顺序

用户批准先核对任务书，再在隔离环境验证完整页面链路，只修复实际复现的问题。
基线为 `c2aee30f3cffc9ca88e2e89b09880a57ea64f801`（PR #61 squash），
独立分支 `codex/main-integration-acceptance`。不改原始混杂工作树，不推送、合并、部署或启用外部模型。

1. 核对 AGENTS、项目规范、用户任务书、源码、提交历史及现有验收夹具。
2. 运行隔离聚焦/联合测试和首页 Node 契约；复用真实首页、ChatHandler、Supervisor、Session、SQLite。
3. 浏览器验证无效输入、普通问答、真实 RDKit 评分、候选引用、刷新/标签页隔离和 partial 终态。
4. 补充有界 HTTP→WebSocket 集成回归；故障只注入测试夹具，不伪造科学成功。
5. 更新当前维护说明和最新状态，保留历史失败记录。独立审查后交付本地变更。

## 证据边界

- 浏览器生成器固定返回五个合成候选，并在页面明确标为 offline fixture；不是 Ollama 生成质量验收。
- 性质与类药性使用真实 RDKit；普通问答使用离线模型替身，只验证路由/调用链，不验证回答质量或外部连通性。
- `tests/scientific_reference_browser_lab.py` 是测试应用，不是完整生产应用；缺少的注册工具应如实拒绝。
- 本轮未启动原项目 6001 服务，未读取真实密钥、模型权重或生产数据库。
- 测试通过不能替代部署、真实模型、真实 Vina 或生产负载验收。

## T00–T11 对账

以下表示当前源码包含该有界实现，不将历史测试数量当成本轮结果；提交来自当前 main 历史。

| 项目 | 当前证据 | 保留边界 / 剩余项 |
|---|---|---|
| T00 | fetch 后基线 c2aee30，独立 worktree；原始13项改动未动 | 本轮测试见下文，不继承历史“通过” |
| T01 | #42 b45f2f6，共享 `src/rag/retrieval.py` 行映射/来源校验；#47 归位 | 异步兼容空列表仍不能独立区分检索失败与无命中 |
| T02 | #33 e2cb6f7 规范状态；#57 9eed743 Web partial | success 布尔值不能代替规范终态 |
| T03 | #43 d11b8bb，`src/web/prompt_budget.py` | 字符预算而非精确 token 预算 |
| T04 | #33，`src/agent/tooling/registration.py` 注册/权限/别名审计 | 注册有效不保证可选工具环境可用 |
| T05 | #44 acdc11f，`src/web/model_lifecycle.py` 构造、消费者与排空 | 不强杀挂起 worker；分子生成模型独立 |
| T06 | #41/#45/#46，静态备份、旧私有聊天删除及展示/历史去重 | 公共兼容 URL、导出和占位资产有意保留，不是全部遗留代码清除 |
| T07 | #34 37aa558，`src/agent/runtime/delegated_executor.py` 共享 Session | 委派 canary 未开放；非真实后端全验收 |
| T08 | #36/#54，共享 `src/target_identifiers.py` 与有界英文筛选解析 | 多靶点、未知、否定、选择性仍要求澄清 |
| T09 | #60 4ff483d，owner/version/24小时期限/实际挂载 ACK/恢复/执行前复核 | 不等于完整聊天历史、跨会话记忆或任意硬崩溃 exactly-once |
| T10-A | #49/#50/#51，RAG/活性/对接专用类型边界 | 其他工具仍是通用兼容边界，整体未收尾 |
| T10-B | #53 5f56053，五类纯 `step_templates.py` | 工作流选择及参数解析仍在 Planner；后续先证明行为等价 |
| T11-A | #47 RAG 服务/索引归位，已有预算/展示/生命周期小模块 | 多领域 `api_routes.py`、ChatHandler 提示/展示后续分解待单独批准 |
| T11-B | #55/#56，MolecularAgent/ReAct 共享核心薄适配 | 公共接口仍保留，不应无依据删除 |
| T11-C | #39/#48/#52 维护指南与历史问题分离；本轮补最新状态 | 历史交接保留当时记录，以当前章节和 Git/PR 为准 |

PR #60、#61 均已合并；历史交接中的“尚未发布”和类药性待修问题不再是当前开放项。
这不是“整个任务书全部完成”，也不是“生产已更新”。

## 初步实测

- 8文件隔离聚焦：456 passed、0 skipped、7 warnings，21.74s，exit 0。警告为SWIG与FastAPI弃用；未抑制测试警告。
- 五个首页Node脚本及main/scientific_references两个JS语法检查通过。
- 真实页面无效输入拒绝、无卡片，注册科研工具调用数均0。
- 问候与药物设计知识问题返回离线模型NOTICE，科研调用均0；不称外部模型回答质量通过。
- CCO性质链路显示真实MW46.07、QED0.407、评分0.693及证据边界。
- 5张明确标注合成的候选卡片经实际挂载确认；第二个输入是CCN，点选第三个输入是CCC；刷新显示恢复提示，新标签页不继承，清除后不恢复。
- 一次“请分析 SMILES: CCO 的成药性”走综合评价，被夹具缺少工具的preflight拒绝；没有执行科学工具，不计成功。

### 已发现展示不足

真实partial页面保留成功RDKit正文与失败步骤，但任务面板进度仍为100%，task_partial标签为泛称“Agent事件”。
源码 `resolveAgentEventPresentation` 又将任何 `*completed` 当作整项“已完成”，包括planning_completed。
此为展示语义问题，不表示后端将partial包装为科研成功。不扩大到面板多轮结构重写。

用户随后批准最小方案；书面设计见 `docs/superpowers/specs/2026-09-25-agent-terminal-labels-design.md`（21e4e66）。
用户已进一步确认书面设计、授权进入TDD；实施计划见 `docs/superpowers/plans/2026-09-25-agent-terminal-labels.md`。
原Node测试明确断言partial为100%，它记录的是旧约定，不证明新要求已满足。新回归应同时保留warning样式和终止执行态断言。

TDD实际RED（均exit1）：中间态“已完成”不等于“执行中”、partial“100%”不等于“部分完成”、
事件“Agent事件”不等于“部分完成”；两个缓存版本断言也在改模板前分别失败。
最小修复仅使用五个精确终态标签，优先于数值progress；中间completed继续百分比/执行中。
补三个事件中文名，保留原CSS、终态集合和转义；main.js缓存版本更新为20260925-terminal-labels-v1。
五个Node回归及main.js语法检查均exit0；独立主任务复跑同样通过。
修复后实际浏览器的CCO partial：面板顶部与末事件均显示“部分完成”，正文保留真实MW46.07/QED0.407、
来源边界和tool_unavailable失败步骤；加载动画移除，没有假综合评分。截图/AX均核对，未向页面注入事件冒充实际执行。

### 保留的探针失败

第一次partial探针误用了不存在的 `AgentErrorCode.TOOL_EXECUTION_FAILED`，造成AttributeError，系统如实返回internal_error，保留此前成功性质。
该次不能作为“显式工具不可用”证据；随后只在测试进程改为实际枚举TOOL_UNAVAILABLE，另用空临时目录重测。未修改生产代码。

## 本轮测试与审查

基线联合：**5741 passed、7 skipped、7 warnings，366.18s，exit 0**。
范围为 `tests/agent` 加 `test_agent_session.py`、`test_agent_session_entrypoints.py`、`test_agent_task_ownership.py`、
`test_phase2_phase3_routes.py`、`test_web_app_lifecycle.py`、`test_static_placeholder_cleanup.py`；不是全仓回归。
此轮collection早于新增集成测试，不把新增6例算进该数字。

7个skip：目录symlink不可用1项、显式关闭性能1项、POSIX权限2项、两独立store2项、配置runtime1项。
7个warnings：SWIG弃用3项、FastAPI on_event弃用4项。未弱化跳过、时限或警告策略。

新增 `tests/agent/test_main_integration_acceptance.py`：实际HTTP→WS会话、规范事件、真实RDKit数值与partial；
使用总10秒/128消息界限和ping/pong处理屏障，验证最终答复唯一。无效输入和普通问答不触发科学工具；
故障只覆盖测试进程中的类药性工具，成功性质保留，综合评分不编造。初次完成后 **6 passed，3.26s，exit 0**。
保留此前两轮测试自身失败：第一轮6 failed/3.92s/exit1，相对WS地址被解析为testserver，造成同源会话握手失败；
改为已有夹具使用的绝对同源地址。第二轮6 failed/3.05s/exit1：正则误认pIC50名称中的50为数值（1例）、
普通非流式回复应为message而非complete（3例）、实际工具输入为完整查询而非仅CCO（2例）。
这些是验收接线/断言错误，不伪报为生产缺陷；调整测试以符合实际公共契约，未改业务实现。
独立SPEC同样6 passed、exit 0，APPROVED；没有把独立模块测试写成独立浏览器验收。

独立QUALITY首次 **6 passed、0 skipped、2.72s，exit 0**，但提出P2：消息接收10秒并未约束TestClient退出时的worker排空。
独立内存探针令性质工具延迟11.5秒，收到超时后仍在13.312秒才返回；真正永久挂起的worker可能使测试进程无法结束。
这是新增验收的测试运行边界问题，不能更改生产取消/资源清理来掩盖；初审不计批准。
现已把全部六个场景放在30秒外层子进程截止时间内，覆盖导入、执行与TestClient退出；
只继承运行所需环境白名单，每次使用临时配置与数据目录。新增超时测试验证子进程已就绪、超时后被结束并回收。
该增量RED为1 failed、6 passed（1.91s）；GREEN为7 passed（21.27s），再次7 passed（21.99s）。
原六例与51个原断言保留；未改生产worker排空。
窄范围SPEC复审APPROVED，独立7 passed（21.55s）；QUALITY复审APPROVED，独立7 passed（22.27s）。
QUALITY另用真实Supervisor永久等待探针验证外层15秒截止：15.047s返回，子进程回收、临时目录删除和环境隔离断言均通过。
这仅验证测试保护，不代表生产worker具有强制终止功能。

第二次联合回归为 **5747 passed、7 skipped、7 warnings，408.16s，exit 0**。
此轮collection在子进程保护修复之前，覆盖旧版新增六例，不作为最终七例版本的冻结验收。

最终Python版本联合回归：**5748 passed、7 skipped、7 warnings，380.52s，exit 0**。
新增七例测试SHA256为34f03cc6874ab242e023946456444694fab65740a7ab996ef77f01272dbeb849，运行前后未改。
前端修复并行完成后另外重跑静态模板和新增集成测试；其结果单独记录，避免把collection时点混同。

离线contract两次均passed，第二次解析正确的reports字段明确 **34/34，pass_rate=1.0**。
第一轮自制摘要误读顶层字段而打印空对象，脚本本身exit0/statuspassed；只修正运行包装后再次核对，未改验收脚本。
contract中的16个legacy REAL契约案例不是16个真实模型调用。报告位于自动清理的临时目录，仅在此记录汇总。
`src scripts` 与新增Python测试的compileall通过，字节码写入临时目录并已清理。
最终Python测试文件另以compile()内存编译通过；第二次带临时目录递归清理的compileall命令被执行策略拒绝、未运行，
不计为通过。不以替换删除工具绕过拒绝；src/scripts Python生产源码本批未修改。

## 可复核命令

Python为MedChat Conda。隔离runner来自 `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`：
只将repo替换为本分支目录；清除非白名单业务环境，临时cwd/config/DB，全部real/canary开关0。

```powershell
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_main_integration_acceptance.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_scientific_reference_browser_lab.py tests/agent/test_scientific_reference_resilience.py tests/agent/test_scientific_reference_web.py tests/agent/test_scientific_reference_execution.py tests/agent/test_drug_likeness_evidence.py tests/agent/test_chat_handler_partial_results.py tests/agent/test_explicit_molecular_input.py tests/agent/test_property_report_boundaries.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_agent_task_ownership.py tests/test_phase2_phase3_routes.py tests/test_web_app_lifecycle.py tests/test_static_placeholder_cleanup.py
node tests/home_scientific_references_test.js
node tests/home_structured_molecule_render_test.js
node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/frontend_safe_render_test.js
```

contract使用同样隔离包装，子进程执行 `python -B scripts/run_agent_acceptance.py --mode contract --output <临时目录>/contract.json`；
编译使用 `python -X pycache_prefix=<临时目录> -m compileall -q src scripts tests/agent/test_main_integration_acceptance.py`。
浏览器基线复用 `python -B -m tests.scientific_reference_browser_lab --state-dir <独立临时目录> --port 6018`。
partial另起6019进程，只在内存覆盖 `DrugLikenessAssessment.execute` 返回明确标记的TOOL_UNAVAILABLE；其他业务代码与属性计算不变。

## 下一批建议

本轮完成后，优先单独评估ChatHandler中partial/失败结果的纯展示职责提取：保留现有公共接口、科学事实直通、错误/来源信息与资源清理。
先以行为等价回归界定边界，不把大文件自动等同于必须重构；暂不扩大到Planner、真实模型切换或生产部署。
其他工具类型化、多领域路由拆分仍是独立任务，不随本批自动获准。

## 清理与文件范围

本轮四个自建浏览器标签页已关闭；6018/6019测试服务核对PID/可执行路径后已停止，监听数为0。
停止采用进程终止，不声称浏览器服务优雅退出；没有触碰6001或生产进程。
以下两个已核对为普通目录的离线夹具临时目录，其删除被执行策略拒绝，仍保留；没有绕过策略：

- `C:/Users/xkx52/AppData/Local/Temp/medchat-main-acceptance-adb236369e8c4fc08646bbe753285b7b`
- `C:/Users/xkx52/AppData/Local/Temp/medchat-partial-acceptance-rlgndd5e`

这些目录仅含本轮隔离状态/合成候选，不提交。常规pytest子进程临时目录由测试自身清理。
原始工作树仍为f377443、原有13项混杂改动；本批未改动它。

本批交付文件：
`src/web/static/js/home/main.js`、`src/web/templates/index.html`、
`tests/home_agent_task_panel_test.js`、`tests/home_workflow_completion_behavior_test.js`、`tests/home_scientific_references_test.js`、
`tests/agent/test_main_integration_acceptance.py`、`docs/AGENT_MAINTENANCE.md`、
`docs/handoff/main-integration-acceptance.md`、`docs/handoff/latest.md`、
`docs/superpowers/specs/2026-09-25-agent-terminal-labels-design.md`、`docs/superpowers/plans/2026-09-25-agent-terminal-labels.md`。

前端修改后，`tests/test_static_placeholder_cleanup.py`及新增集成测试联合 **14 passed，48.70s，exit0**。
前端独立SPEC/QUALITY复审均APPROVED，无阻断项。
SPEC独立执行五个Node、main.js语法、diff检查及静态模板7 passed；QUALITY独立执行五个Node、四个JS语法及范围diff检查，均通过。
QUALITY的文档措辞建议已采纳：六个HTTP→WS场景加一个进程超时保护，共七项；不把后者冒称HTTP链路。
本批仅本地交付；没有PR、推送、合并或部署。发布仍须另行授权。
